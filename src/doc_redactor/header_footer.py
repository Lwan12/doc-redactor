"""One-click header/footer removal (logic layer only; no UI here).

Two steps. ``identify_*`` returns a :class:`Plan` with candidates for the
preview. ``apply_*`` deletes only candidates that are confirmed, and refuses
a plan that still holds unconfirmed ones.

PDF, in this order (header-footer-flow.md section 4):

1. Tags. Content marked ``/Artifact <</Type /Pagination ...>>`` is the
   header/footer. These candidates come pre-confirmed.
2. Repeats. Without tags, text, images, and vector lines that sit at the
   same place on more than half of the pages (3 pages at least) are proposed.
   They need confirmation. A tagged file also gets this pass for untagged
   repeats (e.g. a signing envelope line), flagged separately.
3. Nothing found: nothing is deleted. There is no fixed-margin fallback.

Either way only the top and bottom part of a page is touched
(:data:`ZONE_FRACTION`); that limit never deletes anything by itself.

PDF deletion is real redaction (text, image, and line art removed from the
content stream, nothing painted over). The result is checked by
re-extracting the output. Word headers and footers are emptied part by part,
with their relationships (images, watermark) dropped.

Original text lives only in memory (``Element.text``, ``repr=False``).
``summary`` and ``preview_rows`` carry counts and positions only.

Order in a write: remove headers/footers first, then apply the rules.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace

import pymupdf

KIND_PDF = "pdf"
KIND_DOCX = "docx"

ROUTE_TAG = "tag"
ROUTE_REPEAT = "repeat"
ROUTE_WORD = "word"
ROUTE_NONE = "none"

SOURCE_TAG = "tag"
SOURCE_REPEAT = "repeat"
SOURCE_WORD = "word"

ROLE_HEADER = "header"
ROLE_FOOTER = "footer"
ROLE_SIGNATURE = "signature"

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_LINE = "line"

# Only the top and bottom fifth of a page can hold a candidate. Landscape
# A4 pages (595pt tall) have footers about 96pt from the bottom, so 15% is
# too tight. This is a limit, not a deletion band.
ZONE_FRACTION = 0.20
MATCH_TOL = 0.5
LINE_INFLATE = 0.1  # redaction box around a rule, in points
MIN_PAGES = 3
TOL_VERTICAL = 2.0
TOL_HORIZONTAL = 4.0
# Repeats only take thin rules. Filled boxes (stamp backgrounds, cover
# layers) may hide body content inside them; tags are trusted, repeats not.
MAX_RULE_THICKNESS = 3.0

# Wording from header-footer-flow.md section 6, verbatim.
UI_TEXT = {
    "option": "去掉页眉页脚",
    "option_hint": "Word 整块删除；PDF 按文件自带的页眉页脚删除，认不出的不删。",
    "label_header": "将删除：页眉",
    "label_footer": "将删除：页脚",
    "label_signature": "签署信息",
    "summary_line": "页眉页脚　{n} 页 · 将删除",
    "auto_found": "ⓘ 自动认出，请看预览确认",
    "not_found": "ⓘ 这个 PDF 没认出页眉页脚，没有删除。页眉页脚里的词仍按规则替换。",
    "watermark": "这个文件的水印在页眉里，会一起删除。",
    "written": "已删除 {n} 个文件的页眉页脚。",
    "file_kept": "这个文件会保留页眉页脚，页眉页脚里的词按规则替换。",
}

_SIGNING = re.compile(r"docu\s*sign\s+envelope\s+id", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")


class UnconfirmedCandidates(ValueError):
    """The plan still has candidates nobody confirmed. Nothing was changed."""

    def __init__(self, count: int) -> None:
        self.count = int(count)
        super().__init__(f"{self.count} 处页眉页脚还没确认，未删除")


class PlanMismatch(ValueError):
    """The plan was made for another file (or the file changed)."""

    def __init__(self) -> None:
        super().__init__("页眉页脚的识别结果和这个文件对不上，请重新扫描")


class HeaderFooterLeft(RuntimeError):
    """After deletion something is still visible, or body content was lost."""

    def __init__(self, left: int, lost: int) -> None:
        self.left = int(left)
        self.lost = int(lost)
        super().__init__(f"页眉页脚删除后核对未通过：仍可见 {self.left} 处，正文少了 {self.lost} 处")


Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Element:
    """One thing on one page: a text line or span, an image, or a vector path."""

    kind: str
    bbox: Box
    text: str = field(default="", repr=False, compare=False)
    marks: tuple[Box, ...] = field(default=(), repr=False, compare=False)
    digest: str = field(default="", repr=False, compare=False)
    page: int = 0


@dataclass(frozen=True)
class PageLayout:
    index: int
    width: float
    height: float
    items: tuple[Element, ...]


@dataclass(frozen=True)
class Candidate:
    id: str
    source: str
    role: str
    pages: tuple[int, ...]
    elements: tuple[Element, ...] = field(repr=False)
    needs_confirmation: bool
    confirmed: bool
    # Word only: part name and the section slots that use it.
    part: str = ""
    slots: tuple[str, ...] = ()
    text: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class Plan:
    kind: str
    route: str
    page_count: int
    candidates: tuple[Candidate, ...] = ()
    has_watermark: bool = False
    watermark_text: str = field(default="", repr=False, compare=False)
    page_sizes: tuple[tuple[float, float], ...] = ()
    skipped_rotated: int = 0
    # Header/footer rules left alone because body drawings sit inside their box.
    kept_crowded: int = 0


@dataclass(frozen=True)
class ApplyResult:
    pages: int = 0
    parts: int = 0
    removed: dict = field(default_factory=dict)
    left: int = 0
    body_lost: int = 0


# ---------------------------------------------------------------- pure logic


def label_for(role: str) -> str:
    return {
        ROLE_HEADER: UI_TEXT["label_header"],
        ROLE_FOOTER: UI_TEXT["label_footer"],
        ROLE_SIGNATURE: UI_TEXT["label_signature"],
    }[role]


def zone_of(bbox: Box, height: float, fraction: float = ZONE_FRACTION) -> str | None:
    """``header`` / ``footer`` if the box lies wholly in the top / bottom zone."""
    x0, y0, x1, y1 = bbox
    if y1 <= height * fraction:
        return ROLE_HEADER
    if y0 >= height * (1.0 - fraction):
        return ROLE_FOOTER
    return None


def normalize_text(text: str) -> str:
    """Whitespace collapsed, every run of digits becomes ``#`` (page numbers)."""
    return _DIGITS.sub("#", _SPACES.sub(" ", text).strip())


def role_for(element: Element, zone: str) -> str:
    if element.kind == KIND_TEXT and _SIGNING.search(element.text or ""):
        return ROLE_SIGNATURE
    return zone


def _key(element: Element, zone: str):
    x0, y0, x1, y1 = element.bbox
    if element.kind == KIND_TEXT:
        return (KIND_TEXT, zone, normalize_text(element.text))
    if element.kind == KIND_IMAGE:
        return (KIND_IMAGE, zone, element.digest or (round(x1 - x0), round(y1 - y0)))
    # Lines: width differs between portrait and landscape; thickness does not.
    return (KIND_LINE, zone, round(y1 - y0, 1))


def _anchors(element: Element, page: PageLayout, zone: str) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = element.bbox
    vertical = y0 if zone == ROLE_HEADER else page.height - y1
    return (vertical, x0, page.width - x1, (x0 + x1) / 2 - page.width / 2)


def same_place(a, b, tol_v: float = TOL_VERTICAL, tol_h: float = TOL_HORIZONTAL) -> bool:
    """Same distance from its edge, and left, right, or centre aligned."""
    if abs(a[0] - b[0]) > tol_v:
        return False
    return any(abs(a[i] - b[i]) <= tol_h for i in (1, 2, 3))


def find_repeats(pages, *, exclude: dict | None = None, min_pages: int = MIN_PAGES) -> list[Candidate]:
    """Things at the same place on more than half the pages (and >= 3 pages).

    ``exclude`` maps page index to boxes already covered (tags); elements
    overlapping them are left out. Returns one candidate per role, needing
    confirmation.
    """
    pages = list(pages)
    total = len(pages)
    if total < min_pages:
        return []
    exclude = exclude or {}
    groups: dict = {}
    for page in pages:
        for element in page.items:
            zone = zone_of(element.bbox, page.height)
            if zone is None:
                continue
            if element.kind == KIND_LINE and min(element.bbox[2] - element.bbox[0], element.bbox[3] - element.bbox[1]) > MAX_RULE_THICKNESS:
                continue
            if any(_overlaps(element.bbox, box) for box in exclude.get(page.index, ())):
                continue
            clusters = groups.setdefault(_key(element, zone), [])
            anchor = _anchors(element, page, zone)
            for cluster in clusters:
                if page.index not in cluster["pages"] and same_place(cluster["anchor"], anchor):
                    cluster["pages"].add(page.index)
                    cluster["items"].append(replace(element, page=page.index))
                    break
            else:
                clusters.append(
                    {
                        "anchor": anchor,
                        "zone": zone,
                        "pages": {page.index},
                        "items": [replace(element, page=page.index)],
                    }
                )
    need = max(min_pages, total // 2 + 1)
    by_role: dict[str, list[Element]] = {}
    for clusters in groups.values():
        for cluster in clusters:
            if len(cluster["pages"]) < need:
                continue
            for element in cluster["items"]:
                by_role.setdefault(role_for(element, cluster["zone"]), []).append(element)
    return [
        _candidate(f"{SOURCE_REPEAT}-{role}", SOURCE_REPEAT, role, items, confirmed=False)
        for role, items in sorted(by_role.items())
    ]


def _candidate(cid: str, source: str, role: str, items, *, confirmed: bool) -> Candidate:
    items = tuple(sorted(items, key=lambda e: (e.page, e.bbox[1], e.bbox[0])))
    return Candidate(
        id=cid,
        source=source,
        role=role,
        pages=tuple(sorted({e.page for e in items})),
        elements=items,
        needs_confirmation=not confirmed,
        confirmed=confirmed,
    )


def tag_candidates(pages) -> list[Candidate]:
    """Pre-confirmed candidates from tagged Pagination artifacts (in zone)."""
    by_role: dict[str, list[Element]] = {}
    for page in pages:
        for element in page.items:
            zone = zone_of(element.bbox, page.height)
            if zone is None:
                continue
            by_role.setdefault(zone, []).append(replace(element, page=page.index))
    return [
        _candidate(f"{SOURCE_TAG}-{role}", SOURCE_TAG, role, items, confirmed=True)
        for role, items in sorted(by_role.items())
    ]


def _same_box(a: Box, b: Box, tol: float = MATCH_TOL) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _covers(outer: Box, inner: Box) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def drop_crowded_lines(candidates, layouts) -> tuple[list[Candidate], int]:
    """Drop rule elements whose redaction box would also cover a body drawing.

    Line redaction removes every drawing wholly inside the box, so a table
    border right under the header rule would go too. Such a rule stays on
    that page (conservative: not deleting beats deleting body content).
    Returns the remaining candidates and how many elements were dropped.
    """
    drawings = {
        layout.index: [e.bbox for e in layout.items if e.kind == KIND_LINE] for layout in layouts
    }
    planned: dict[int, list[Box]] = {}
    for candidate in candidates:
        for element in candidate.elements:
            if element.kind == KIND_LINE:
                planned.setdefault(element.page, []).append(element.bbox)
    out, dropped = [], 0
    for candidate in candidates:
        keep = []
        for element in candidate.elements:
            if element.kind == KIND_LINE:
                area = _inflate(element.bbox, LINE_INFLATE)
                own = planned.get(element.page, [])
                crowded = any(
                    _covers(area, box) and not any(_same_box(box, mine) for mine in own)
                    for box in drawings.get(element.page, [])
                )
                if crowded:
                    dropped += 1
                    continue
            keep.append(element)
        if len(keep) == len(candidate.elements):
            out.append(candidate)
        elif keep:
            out.append(
                replace(candidate, elements=tuple(keep), pages=tuple(sorted({e.page for e in keep})))
            )
    return out, dropped


def build_pdf_plan(tagged, layouts, page_count: int, page_sizes=(), skipped_rotated: int = 0) -> Plan:
    tags = tag_candidates(tagged)
    exclude: dict[int, list[Box]] = {}
    for candidate in tags:
        for element in candidate.elements:
            exclude.setdefault(element.page, []).append(element.bbox)
    repeats = find_repeats(layouts, exclude=exclude)
    tags, crowded_tags = drop_crowded_lines(tags, layouts)
    repeats, crowded_repeats = drop_crowded_lines(repeats, layouts)
    if tags:
        route = ROUTE_TAG
    elif repeats:
        route = ROUTE_REPEAT
    else:
        route = ROUTE_NONE
    return Plan(
        kind=KIND_PDF,
        route=route,
        page_count=page_count,
        candidates=tuple(tags + repeats),
        page_sizes=tuple(page_sizes),
        skipped_rotated=skipped_rotated,
        kept_crowded=crowded_tags + crowded_repeats,
    )


def confirm(plan: Plan) -> Plan:
    """The user looked at the preview and kept this file's removal.

    Phase one has no per-block choice (flow doc section 5), so this confirms
    the whole file. Returns a new plan.
    """
    return replace(plan, candidates=tuple(replace(c, confirmed=True) for c in plan.candidates))


def unconfirmed(plan: Plan) -> int:
    return sum(1 for c in plan.candidates if not c.confirmed)


def require_confirmed(plan: Plan) -> None:
    pending = unconfirmed(plan)
    if pending:
        raise UnconfirmedCandidates(pending)


def pages_affected(plan: Plan) -> int:
    return len({p for c in plan.candidates for p in c.pages})


def summary(plan: Plan) -> dict:
    """Counts only, for the right-hand summary and logs. No original text."""
    info = {
        "kind": plan.kind,
        "route": plan.route,
        "candidates": len(plan.candidates),
        "needs_confirmation": any(c.needs_confirmation and not c.confirmed for c in plan.candidates),
        "by_role": dict(Counter(c.role for c in plan.candidates)),
        "items": sum(len(c.elements) for c in plan.candidates),
    }
    if plan.kind == KIND_PDF:
        pages = pages_affected(plan)
        info["pages"] = pages
        info["line"] = UI_TEXT["summary_line"].format(n=pages) if pages else None
        info["skipped_rotated"] = plan.skipped_rotated
    else:
        info["parts"] = len(plan.candidates)
        info["watermark_note"] = UI_TEXT["watermark"] if plan.has_watermark else None
    info["note"] = UI_TEXT["auto_found"] if any(c.needs_confirmation for c in plan.candidates) else None
    info["message"] = UI_TEXT["not_found"] if plan.kind == KIND_PDF and plan.route == ROUTE_NONE else None
    return info


def preview_rows(plan: Plan) -> list[dict]:
    """One row per candidate per page: where to draw the grey block and its label."""
    rows = []
    for candidate in plan.candidates:
        if plan.kind == KIND_DOCX:
            rows.append(_row(candidate, None, None, len(candidate.elements)))
            continue
        for page in candidate.pages:
            boxes = [e.bbox for e in candidate.elements if e.page == page]
            rows.append(_row(candidate, page, union(boxes), len(boxes)))
    return rows


def _row(candidate: Candidate, page, bbox, items: int) -> dict:
    return {
        "id": candidate.id,
        "page": page,
        "role": candidate.role,
        "label": label_for(candidate.role),
        "source": candidate.source,
        "bbox": bbox,
        "needs_confirmation": candidate.needs_confirmation,
        "confirmed": candidate.confirmed,
        "items": items,
    }


def removed_boxes(plan: Plan, page: int) -> list[Box]:
    return [e.bbox for c in plan.candidates if c.confirmed for e in c.elements if e.page == page]


def inside_removed(plan: Plan, page: int, bbox: Box) -> bool:
    """For the scan: a rule hit whose centre lies in a block to be deleted
    is counted under 「页眉页脚」 instead of on its own."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(_contains(box, cx, cy) for box in removed_boxes(plan, page))


def role_at(plan: Plan | None, page: int, bbox) -> str:
    """Role of the candidate (confirmed or not) whose block holds this box's centre."""
    if plan is None or bbox is None:
        return ""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for candidate in plan.candidates:
        for element in candidate.elements:
            if element.page == page and _contains(element.bbox, cx, cy):
                return candidate.role
    return ""


def pdf_line_roles(page, plan: Plan | None, lines: list[str]) -> dict[int, str]:
    """Plain-text line index -> role, for drawing the preview blocks.

    Lines of ``page.get_text("text")`` come in the same order as rawdict
    lines; they are paired by their text. A line counts as header/footer
    when most of its characters sit in a candidate block.
    """
    if plan is None or not plan.candidates:
        return {}
    raw = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            chars = [ch for span in line["spans"] for ch in span["chars"]]
            text = "".join(ch["c"] for ch in chars)
            roles = Counter()
            for ch in chars:
                if not ch["c"].strip():
                    continue
                roles[role_at(plan, page.number, ch["bbox"])] += 1
            role, count = roles.most_common(1)[0] if roles else ("", 0)
            total = sum(roles.values())
            raw.append((_SPACES.sub("", text), role if role and count * 2 > total else ""))
    out: dict[int, str] = {}
    j = 0
    for index, line in enumerate(lines):
        key = _SPACES.sub("", line)
        if not key:
            continue
        for k in range(j, min(len(raw), j + 50)):
            if raw[k][0] == key:
                if raw[k][1]:
                    out[index] = raw[k][1]
                j = k + 1
                break
    return out


def docx_pages(path) -> int | None:
    """Page count Word stored in docProps/app.xml, if any. Word itself has no
    fixed pages, so this is what the last save recorded."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("docProps/app.xml").decode("utf-8", "replace")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    found = re.search(r"<(?:\w+:)?Pages>(\d+)</", raw)
    return int(found.group(1)) if found else None


def union(boxes) -> Box | None:
    boxes = list(boxes)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _contains(box: Box, x: float, y: float) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _overlaps(a: Box, b: Box) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _inflate(box: Box, d: float) -> Box:
    return (box[0] - d, box[1] - d, box[2] + d, box[3] + d)


def _shrink_center(box: Box, frac: float = 0.25) -> Box:
    w, h = box[2] - box[0], box[3] - box[1]
    return (box[0] + w * frac, box[1] + h * frac, box[2] - w * frac, box[3] - h * frac)


def char_marks(chars, size: float) -> tuple[Box, ...]:
    """Thin redaction boxes inside the glyphs, merged along a run.

    MuPDF removes any glyph whose box touches a redaction box, and glyph
    boxes of tight lines overlap, so the boxes sit in the x-height band
    above the baseline and in the middle half of each glyph.
    ``chars`` is a list of (char, origin_y, bbox).
    """
    marks: list[list[float]] = []
    for ch, origin_y, bbox in chars:
        if not ch.strip():
            continue
        x0, _y0, x1, _y1 = bbox
        w = max(x1 - x0, 0.2)
        box = [x0 + w * 0.25, origin_y - size * 0.5, x1 - w * 0.25, origin_y - size * 0.2]
        last = marks[-1] if marks else None
        if last and abs(last[1] - box[1]) < 0.01 and 0 <= box[0] - last[2] <= size * 0.6:
            last[2] = max(last[2], box[2])
        else:
            marks.append(box)
    return tuple(tuple(m) for m in marks)




def _match_points(before, after, tol: float = MATCH_TOL) -> int:
    """How many of ``before`` (char, x, y) have no partner in ``after``.

    A rewritten content stream can move a glyph by a few hundredths of a
    point, so partners are matched within ``tol``.
    """
    pool: dict[str, list[list]] = {}
    for ch, x, y in after:
        pool.setdefault(ch, []).append([x, y, False])
    missing = 0
    for ch, x, y in before:
        for item in pool.get(ch, ()):
            if not item[2] and abs(item[0] - x) <= tol and abs(item[1] - y) <= tol:
                item[2] = True
                break
        else:
            missing += 1
    return missing


def check_text(before, after, removed: list[Box]) -> tuple[int, int]:
    """(left, lost). ``before``/``after`` are (char, cx, cy) for one page.

    left: characters still visible inside a removed box.
    lost: characters outside every removed box that disappeared.
    """
    left = sum(1 for ch, x, y in after if ch.strip() and any(_contains(b, x, y) for b in removed))
    keep = [(ch, x, y) for ch, x, y in before if ch.strip() and not any(_contains(b, x, y) for b in removed)]
    lost = _match_points(keep, [(ch, x, y) for ch, x, y in after if ch.strip()])
    return left, lost


def _same_box(a: Box, b: Box, tol: float = MATCH_TOL) -> bool:
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def check_boxes(before: list[Box], after: list[Box], removed: list[Box]) -> tuple[int, int]:
    """Images or line art. An item is the removed one only if its box is the
    same box (within ``MATCH_TOL``); anything else on the page must survive."""
    left = sum(1 for b in after if any(_same_box(b, r) for r in removed))
    keep = [("", (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in before if not any(_same_box(b, r) for r in removed)]
    lost = _match_points(keep, [("", (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in after])
    return left, lost


# ---------------------------------------------------------------- PDF reading

_PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
_SHOW = {b"Tj", b"TJ", b"'", b'"'}
_DROP = {b"Do", b"sh", b"BI"}
_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_NUMERIC = re.compile(rb"^[+-]?(\d+\.?\d*|\.\d+)$")
_PAGINATION = re.compile(rb"/Type\s*/Pagination\b")


def content_ops(data: bytes):
    """Yield (operator, start, end): ``data[start:end]`` is operands plus operator."""
    i, n, start = 0, len(data), 0
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
            continue
        if c == 0x25:  # %
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        if c == 0x28:  # (
            depth, i = 1, i + 1
            while i < n and depth:
                if data[i] == 0x5C:
                    i += 2
                    continue
                if data[i] == 0x28:
                    depth += 1
                elif data[i] == 0x29:
                    depth -= 1
                i += 1
            continue
        if c == 0x3C:  # <
            if data[i : i + 2] == b"<<":
                i += 2
            else:
                j = data.find(b">", i)
                i = n if j < 0 else j + 1
            continue
        if data[i : i + 2] == b">>":
            i += 2
            continue
        if c in b"[]{}":
            i += 1
            continue
        if c == 0x2F:  # /
            i += 1
            while i < n and data[i] not in _WS and data[i] not in _DELIM:
                i += 1
            continue
        j = i
        while j < n and data[j] not in _WS and data[j] not in _DELIM:
            j += 1
        word = data[i:j]
        if not word:
            # A stray delimiter (e.g. lone ">"). Skip it.
            i = j + 1
            continue
        if _NUMERIC.match(word) or word in (b"true", b"false", b"null"):
            i = j
            continue
        if word == b"BI":
            k = data.find(b"ID", j)
            k = n if k < 0 else k + 2
            m = re.compile(rb"[\s]EI(?=[\s]|$)").search(data, k)
            j = n if m is None else m.end()
        yield word, start, j
        start = i = j


def _pagination_flag(operands: bytes, properties) -> bool:
    if not operands.lstrip().startswith(b"/Artifact"):
        return False
    if _PAGINATION.search(operands):
        return True
    named = re.search(rb"/Artifact\s*/([^\s/<\[]+)\s*$", operands.strip())
    if named and properties is not None:
        resolved = properties(named.group(1).decode("latin-1"))
        return bool(resolved and _PAGINATION.search(resolved))
    return False


def pagination_only(data: bytes, properties=None) -> tuple[bytes, int]:
    """Content that paints only Pagination artifacts.

    Outside them: images, shadings and inline images are dropped, paths are
    ended with ``n``, and text is shown in render mode 3 (invisible) so text
    positions stay right. Returns (stream, number of Pagination sections).
    """
    out: list[bytes] = []
    stack: list[bool] = []
    tr_stack: list[bytes] = []
    tr = b"0"
    sections = 0
    for op, start, end in content_ops(data):
        chunk = data[start:end]
        operands = data[start : end - len(op)]
        inside = bool(stack and stack[-1])
        if op in (b"BDC", b"BMC"):
            flag = inside or (op == b"BDC" and _pagination_flag(operands, properties))
            if flag and not inside:
                sections += 1
            stack.append(flag)
        elif op == b"EMC":
            if stack:
                stack.pop()
        elif op == b"q":
            tr_stack.append(tr)
        elif op == b"Q":
            if tr_stack:
                tr = tr_stack.pop()
        elif op == b"Tr":
            parts = operands.split()
            if parts:
                tr = parts[-1]
        if inside:
            out.append(chunk)
        elif op in _SHOW:
            out.append(b" 3 Tr " + chunk + b" " + tr + b" Tr")
        elif op in _DROP:
            out.append(b" ")
        elif op in _PAINT:
            out.append(operands + b" n")
        else:
            out.append(chunk)
    return b"".join(out), sections


def _page_properties(document, page):
    def lookup(name: str):
        kind, value = document.xref_get_key(page.xref, f"Resources/Properties/{name}")
        if kind == "xref":
            return document.xref_object(int(value.split()[0])).encode("latin-1", "replace")
        if kind == "dict":
            return value.encode("latin-1", "replace")
        return None

    return lookup


def _text_from_trace(page) -> list[Element]:
    items = []
    for span in page.get_texttrace():
        if span.get("type") == 3:
            continue
        chars = span["chars"]
        text = "".join(chr(c[0]) for c in chars)
        if not text.strip():
            continue
        bbox = union(c[3] for c in chars)
        marks = char_marks([(chr(c[0]), c[2][1], c[3]) for c in chars], span["size"])
        items.append(Element(kind=KIND_TEXT, bbox=bbox, text=text, marks=marks))
    return items


def _text_from_dict(page) -> list[Element]:
    items = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            chars, marks = [], []
            for span in line["spans"]:
                span_chars = [(ch["c"], ch["origin"][1], ch["bbox"]) for ch in span["chars"]]
                chars.extend(span_chars)
                marks.extend(char_marks(span_chars, span["size"]))
            text = "".join(c[0] for c in chars)
            if not text.strip():
                continue
            items.append(Element(kind=KIND_TEXT, bbox=tuple(line["bbox"]), text=text, marks=tuple(marks)))
    return items


def _images(page) -> list[Element]:
    items = []
    for info in page.get_image_info(hashes=True):
        bbox = tuple(info["bbox"])
        if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
            continue
        digest = info.get("digest") or b""
        items.append(Element(kind=KIND_IMAGE, bbox=bbox, digest=digest.hex() if isinstance(digest, bytes) else str(digest)))
    return items


def _lines(page) -> list[Element]:
    items = []
    for path in page.get_drawings():
        rect = path.get("rect")
        if rect is None:
            continue
        width = path.get("width") or 0.0
        box = tuple(rect)
        if path.get("type") in ("s", "fs") and width:
            box = _inflate(box, width / 2)
        items.append(Element(kind=KIND_LINE, bbox=box))
    return items


def layout_of(page) -> PageLayout:
    items = _text_from_dict(page) + _images(page) + _lines(page)
    return PageLayout(index=page.number, width=page.rect.width, height=page.rect.height, items=tuple(items))


def tagged_layout_of(document, page) -> PageLayout | None:
    """Elements inside Pagination artifacts on this page, or None if none."""
    data = b"\n".join(document.xref_stream(x) or b"" for x in page.get_contents())
    if b"Pagination" not in data and b"/Artifact" not in data:
        return None
    stream, sections = pagination_only(data, _page_properties(document, page))
    if not sections:
        return None
    scratch = pymupdf.open()
    try:
        scratch.insert_pdf(document, from_page=page.number, to_page=page.number)
        copy = scratch[0]
        xrefs = copy.get_contents()
        scratch.update_stream(xrefs[0], stream)
        for xref in xrefs[1:]:
            scratch.update_stream(xref, b"")
        items = _text_from_trace(copy) + _images(copy) + _lines(copy)
        return PageLayout(index=page.number, width=copy.rect.width, height=copy.rect.height, items=tuple(items))
    finally:
        scratch.close()


def identify_pdf(document) -> Plan:
    """Step one. Reads only; ``document`` is an open ``pymupdf.Document``."""
    tagged, layouts, sizes, rotated = [], [], [], 0
    for page in document:
        sizes.append((round(page.rect.width, 2), round(page.rect.height, 2)))
        if page.rotation:
            # Coordinates of rotated pages are not handled yet; leave them alone.
            rotated += 1
            continue
        tag = tagged_layout_of(document, page)
        if tag is not None:
            tagged.append(tag)
        layouts.append(layout_of(page))
    return build_pdf_plan(tagged, layouts, document.page_count, sizes, rotated)


# ---------------------------------------------------------------- PDF writing


def _page_chars(page):
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                for ch in span["chars"]:
                    x0, y0, x1, y1 = ch["bbox"]
                    out.append((ch["c"], (x0 + x1) / 2, (y0 + y1) / 2))
    return out


def _check_plan_matches(document, plan: Plan) -> None:
    if plan.page_count != document.page_count:
        raise PlanMismatch()
    sizes = tuple((round(p.rect.width, 2), round(p.rect.height, 2)) for p in document)
    if plan.page_sizes and sizes != plan.page_sizes:
        raise PlanMismatch()
    by_page: dict[int, list[Element]] = {}
    for candidate in plan.candidates:
        for element in candidate.elements:
            if element.kind == KIND_TEXT:
                by_page.setdefault(element.page, []).append(element)
    for index, elements in by_page.items():
        chars = _page_chars(document[index])
        for element in elements:
            found = "".join(ch for ch, x, y in chars if _contains(element.bbox, x, y))
            if _SPACES.sub("", element.text) not in _SPACES.sub("", found):
                raise PlanMismatch()


def apply_pdf(document, plan: Plan) -> ApplyResult:
    """Step two. Redacts every element of the (all confirmed) plan in place.

    Raises UnconfirmedCandidates or PlanMismatch before touching anything,
    and HeaderFooterLeft if the re-extracted result still shows a removed
    item or lost body content. Save with ``garbage>=3`` so the removed
    objects are not kept in the file.
    """
    require_confirmed(plan)
    if not plan.candidates:
        return ApplyResult(removed={KIND_TEXT: 0, KIND_IMAGE: 0, KIND_LINE: 0})
    _check_plan_matches(document, plan)
    by_page: dict[int, list[Element]] = {}
    for candidate in plan.candidates:
        for element in candidate.elements:
            by_page.setdefault(element.page, []).append(element)
    before = {i: _snapshot(document[i]) for i in by_page}
    removed = Counter()
    for index, elements in sorted(by_page.items()):
        page = document[index]
        texts = [e for e in elements if e.kind == KIND_TEXT]
        images = [e for e in elements if e.kind == KIND_IMAGE]
        lines = [e for e in elements if e.kind == KIND_LINE]
        if texts:
            for element in texts:
                for mark in element.marks:
                    page.add_redact_annot(pymupdf.Rect(mark), fill=False, cross_out=False)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        if images:
            for element in images:
                page.add_redact_annot(pymupdf.Rect(_shrink_center(element.bbox)), fill=False, cross_out=False)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_NONE,
            )
        if lines:
            for element in lines:
                page.add_redact_annot(pymupdf.Rect(_inflate(element.bbox, LINE_INFLATE)), fill=False, cross_out=False)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=pymupdf.PDF_REDACT_TEXT_NONE,
            )
        removed.update({KIND_TEXT: len(texts), KIND_IMAGE: len(images), KIND_LINE: len(lines)})
    left, lost = _verify(document, plan, by_page, before)
    if left or lost:
        raise HeaderFooterLeft(left, lost)
    return ApplyResult(pages=len(by_page), removed=dict(removed), left=0, body_lost=0)


def _snapshot(page):
    return {
        KIND_TEXT: _page_chars(page),
        KIND_IMAGE: [e.bbox for e in _images(page)],
        KIND_LINE: [e.bbox for e in _lines(page)],
    }


def _verify(document, plan: Plan, by_page, before) -> tuple[int, int]:
    """Re-open the saved bytes and compare with the snapshot before removal."""
    # No garbage collection here: it would renumber objects of the live document.
    reopened = pymupdf.open("pdf", document.tobytes())
    try:
        left = lost = 0
        for index, elements in by_page.items():
            after = _snapshot(reopened[index])
            for kind in (KIND_TEXT, KIND_IMAGE, KIND_LINE):
                boxes = [e.bbox for e in elements if e.kind == kind]
                if kind == KIND_TEXT:
                    l, m = check_text(before[index][kind], after[kind], boxes)
                else:
                    l, m = check_boxes(before[index][kind], after[kind], boxes)
                left += l
                lost += m
        return left, lost
    finally:
        reopened.close()


# ---------------------------------------------------------------- Word

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CONTENT_TAGS = ("t", "instrText", "delText", "drawing", "pict", "object", "fldSimple", "sdt", "txbxContent")
_WATERMARK = ("PowerPlusWaterMarkObject", "PowerPlusPictureWatermark", "WordPictureWatermark", "textpath")


def _hf_parts(document):
    from docx.parts.hdrftr import FooterPart, HeaderPart

    for part in document.part.package.iter_parts():
        if isinstance(part, HeaderPart):
            yield ROLE_HEADER, part
        elif isinstance(part, FooterPart):
            yield ROLE_FOOTER, part


def _has_content(element) -> bool:
    for tag in _CONTENT_TAGS:
        for node in element.iter(f"{{{_W}}}{tag}"):
            if tag != "t" or (node.text or "").strip():
                return True
    return False


def _slots(document) -> dict[str, list[str]]:
    """partname -> ['节1·默认页眉', ...] for the preview."""
    names = {"default": "默认", "first": "首页", "even": "偶数页"}
    slots: dict[str, list[str]] = {}
    for number, section in enumerate(document.sections, start=1):
        for tag, word in (("headerReference", "页眉"), ("footerReference", "页脚")):
            for ref in section._sectPr.findall(f"{{{_W}}}{tag}"):
                rid = ref.get(f"{{{_R}}}id")
                kind = names.get(ref.get(f"{{{_W}}}type"), "默认")
                try:
                    part = document.part.related_parts[rid]
                except KeyError:
                    continue
                slots.setdefault(str(part.partname), []).append(f"节{number}·{kind}{word}")
    return slots


def identify_docx(document) -> Plan:
    """Every header/footer part with content. Word removes whole blocks, so
    candidates come pre-confirmed (flow doc section 3)."""
    slots = _slots(document)
    candidates, watermark, mark_text = [], False, ""
    for role, part in _hf_parts(document):
        element = part._element
        if not _has_content(element):
            continue
        xml = element.xml
        if role == ROLE_HEADER and any(mark in xml for mark in _WATERMARK):
            watermark = True
            found = re.search(r'<v:textpath[^>]*\bstring="([^"]*)"', xml)
            mark_text = mark_text or (found.group(1) if found else "")
        name = str(part.partname)
        text = "".join(node.text or "" for node in element.iter(f"{{{_W}}}t"))
        candidates.append(
            Candidate(
                id=f"{SOURCE_WORD}-{name.rsplit('/', 1)[-1]}",
                source=SOURCE_WORD,
                role=role,
                pages=(),
                elements=(),
                needs_confirmation=False,
                confirmed=True,
                part=name,
                slots=tuple(slots.get(name, ())),
                text=text,
            )
        )
    candidates.sort(key=lambda c: c.part)
    return Plan(
        kind=KIND_DOCX,
        route=ROUTE_WORD if candidates else ROUTE_NONE,
        page_count=0,
        candidates=tuple(candidates),
        has_watermark=watermark,
        watermark_text=mark_text,
    )


def apply_docx(document, plan: Plan) -> ApplyResult:
    """Empty each confirmed header/footer part and drop its relationships."""
    from docx.oxml import parse_xml

    require_confirmed(plan)
    parts = {str(part.partname): part for _role, part in _hf_parts(document)}
    if any(c.part not in parts for c in plan.candidates):
        raise PlanMismatch()
    for candidate in plan.candidates:
        part = parts[candidate.part]
        element = part._element
        for child in list(element):
            element.remove(child)
        element.append(parse_xml(f'<w:p xmlns:w="{_W}"/>'))
        for rid in list(part.rels):
            part.rels.pop(rid)
    left = sum(1 for c in plan.candidates if _has_content(parts[c.part]._element) or len(parts[c.part].rels))
    if left:
        raise HeaderFooterLeft(left, 0)
    return ApplyResult(parts=len(plan.candidates), removed={"parts": len(plan.candidates)})


def verify_docx_output(path) -> int:
    """Count header/footer parts in a saved .docx that still hold content or links."""
    import zipfile

    from lxml import etree

    left = 0
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for name in names:
            base = name.rsplit("/", 1)[-1]
            if not (name.startswith("word/") and (base.startswith("header") or base.startswith("footer")) and name.endswith(".xml")):
                continue
            if _has_content(etree.fromstring(zf.read(name))):
                left += 1
            rels = f"word/_rels/{base}.rels"
            if rels in names and b"<Relationship " in zf.read(rels):
                left += 1
    return left
