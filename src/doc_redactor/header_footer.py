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
# Signing envelope line, found by its fixed text; deleted without confirmation.
SOURCE_SIGN = "sign"

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
# Repetition is only trusted at the very edge of the page (review M1):
# at most this many text rows counted from the edge,
MAX_EDGE_ROWS = 2
# rows of one header/footer no further apart than this (points),
MAX_BAND_GAP = 12.0
# and some white space between the header/footer and what follows.
MIN_BAND_GAP = 1.0
# A rule with another drawing this close (points) belongs to a table.
RULE_TOUCH = 1.5
# 「按每页重复认出」的部分默认不删，要按文件确认（产品 10-19 定 b）。只在这里改。
REPEAT_CONFIRMED_BY_DEFAULT = False

# Wording from header-footer-flow.md section 6, verbatim.
UI_TEXT = {
    "option": "去掉页眉页脚",
    "option_hint": "Word 整块删除；PDF 按文件自带的页眉页脚删除，认不出的不删。",
    "label_header": "将删除：页眉",
    "label_footer": "将删除：页脚",
    "label_signature": "签署信息",
    "summary_line": "页眉页脚　{n} 页 · 将删除",
    "pending": "⚠ 待确认，不删",
    "not_found": "ⓘ 没认出页眉页脚，这份不删",
    "not_found_next": "可从原 Word 重新导出 PDF 再放进来，或先这样写出",
    # Write failure for a file with rotated pages (方向.md: never skip silently).
    "rotated": "有 {n} 页是旋转页，页眉页脚删不了，这份没写出。可取消「去掉页眉页脚」再写出",
    "watermark": "这个文件的水印在页眉里，会一起删除。",
    "written": "已删除 {n} 个文件的页眉页脚。",
    "file_kept": "这个文件会保留页眉页脚，页眉页脚里的词按规则替换。",
}

_SIGNING = re.compile(r"docu\s*sign\s+envelope\s+id", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
# Where digits may differ from page to page: page-number phrases only.
_PAGE_PHRASE = re.compile(
    r"(?:page|p\.)\s*\d+(?:\s*(?:of|/)\s*\d+)?"
    r"|第\s*\d+\s*页(?:\s*[,，]?\s*共\s*\d+\s*页)?"
    r"|\b\d+\s*(?:/|of)\s*\d+\b",
    re.IGNORECASE,
)
# A line that is nothing but a number and punctuation, such as 「3」 or 「- 3 -」.
_NUMBER_ONLY = re.compile(r"^[\W_]*\d+[\W_]*$")
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


class RotatedPages(ValueError):
    """Rotated pages are not handled: the file is not written rather than
    keeping their headers silently (方向.md 验收口径 2). Nothing was changed."""

    def __init__(self, count: int) -> None:
        self.count = int(count)
        super().__init__(UI_TEXT["rotated"].format(n=self.count))


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
    # Text only: (char, centre x, centre y) of its own glyphs, for the check.
    chars: tuple = field(default=(), repr=False, compare=False)


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
    # Text left alone because its removal marks would touch a body glyph.
    kept_tight: int = 0


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


def repeat_text_key(text: str) -> str:
    """Text used to compare a line across pages.

    Digits are ignored only where they can be page numbers: a line that is
    just a number, or a page-number phrase such as 「Page 3 of 12」 or
    「第 3 页」. A table row, a subject number or a listing title that differs
    only in its numbers stays different (review M1).
    """
    collapsed = _SPACES.sub(" ", text).strip()
    if _NUMBER_ONLY.match(collapsed):
        return _DIGITS.sub("#", collapsed)
    return _PAGE_PHRASE.sub(lambda m: _DIGITS.sub("#", m.group(0)), collapsed)


def role_for(element: Element, zone: str) -> str:
    """Signing lines are taken by ``sign_candidates`` before repeats run."""
    return zone


def _key(element: Element, zone: str):
    x0, y0, x1, y1 = element.bbox
    if element.kind == KIND_TEXT:
        return (KIND_TEXT, zone, repeat_text_key(element.text))
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


def _parity_groups(pages) -> dict[int, set[int]]:
    groups: dict[int, set[int]] = {0: set(), 1: set()}
    for page in pages:
        groups[page.index % 2].add(page.index)
    return groups


def _need(count: int, min_pages: int) -> int:
    return max(min_pages, count // 2 + 1)


def _kept_pages(found: set[int], total: int, parity: dict[int, set[int]], min_pages: int) -> set[int]:
    """Pages of a cluster that count as repeating.

    More than half of all pages (and at least ``min_pages``); otherwise odd and
    even pages are judged on their own, for different odd/even headers (S2).
    """
    if len(found) >= _need(total, min_pages):
        return set(found)
    kept: set[int] = set()
    for group in parity.values():
        mine = found & group
        if group and len(mine) >= _need(len(group), min_pages):
            kept |= mine
    return kept


def _cluster(pages, exclude, min_pages):
    """Group in-zone elements by key and place; returns accepted (element, zone) per page."""
    groups: dict = {}
    for page in pages:
        for element in page.items:
            zone = zone_of(element.bbox, page.height)
            if zone is None:
                continue
            if element.kind == KIND_LINE and _thick(element):
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
    return [cluster for clusters in groups.values() for cluster in clusters]


def _thick(element: Element) -> bool:
    x0, y0, x1, y1 = element.bbox
    return min(x1 - x0, y1 - y0) > MAX_RULE_THICKNESS


def _extent(element: Element, zone: str, height: float) -> tuple[float, float]:
    """(near, far) distance from the page edge. Text uses its glyph cores, so
    ascenders of a tight neighbour line do not merge two rows."""
    if element.kind == KIND_TEXT and element.marks:
        y0 = min(m[1] for m in element.marks)
        y1 = max(m[3] for m in element.marks)
    else:
        y0, y1 = element.bbox[1], element.bbox[3]
    if zone == ROLE_HEADER:
        return y0, y1
    return height - y1, height - y0


def _box_extent(element: Element, zone: str, height: float) -> tuple[float, float]:
    y0, y1 = element.bbox[1], element.bbox[3]
    return (y0, y1) if zone == ROLE_HEADER else (height - y1, height - y0)


def _rows(elements, zone: str, height: float):
    """Elements grouped into rows by vertical overlap, from the edge inwards.

    ``near``/``far`` use glyph cores (for overlap and the gap to the body);
    ``bnear``/``bfar`` use full boxes (for the spacing inside a header).
    """
    ordered = sorted(elements, key=lambda e: _extent(e, zone, height))
    rows: list[dict] = []
    for element in ordered:
        near, far = _extent(element, zone, height)
        bnear, bfar = _box_extent(element, zone, height)
        if rows and near < rows[-1]["far"]:
            row = rows[-1]
            row["items"].append(element)
            row["far"] = max(row["far"], far)
            row["bnear"] = min(row["bnear"], bnear)
            row["bfar"] = max(row["bfar"], bfar)
        else:
            rows.append({"near": near, "far": far, "bnear": bnear, "bfar": bfar, "items": [element]})
    return rows


def _h_overlap(a: Box, b: Box) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0])


def _table_rule(rule: Element, page: PageLayout, band: set) -> bool:
    """A rule stacked on another drawing, or with a drawing outside the
    header/footer right next to it, is part of a table (M1)."""
    x0, y0, x1, y1 = rule.bbox
    for other in page.items:
        if other.kind != KIND_LINE or other is rule:
            continue
        ox0, oy0, ox1, oy1 = other.bbox
        if not _h_overlap(rule.bbox, other.bbox):
            continue
        stacked = min(y1, oy1) > max(y0, oy0)
        near = (oy0 - y1 <= RULE_TOUCH and oy1 >= y0 - RULE_TOUCH)
        if stacked or (near and id(other) not in band):
            return True
    return False


def edge_band(page: PageLayout, zone: str, accepted: set, covered: set) -> set:
    """ids of ``accepted`` elements that form this page's header or footer.

    Walks rows from the page edge: every element of a row must repeat (or be
    covered by tags), at most ``MAX_EDGE_ROWS`` text rows, rows close
    together, and a gap before what follows. If the row after the band
    would also qualify, the edge is unclear and nothing is taken.
    """
    limit = page.height * ZONE_FRACTION
    items = []
    for element in page.items:
        near, far = _extent(element, zone, page.height)
        if near >= limit:
            continue
        if element.kind == KIND_LINE and _thick(element):
            # Filled boxes, page frames, table outlines: never removed and not
            # content; a table is caught by its text or by _table_rule.
            continue
        items.append(element)
    rows = _rows(items, zone, page.height)
    band: list[dict] = []
    text_rows = 0
    last_content = None
    stop = None
    for row in rows:
        if all(id(e) in covered and id(e) not in accepted for e in row["items"]):
            # Taken by another rule (tags, the signing line): not part of
            # this header, so it neither counts as a row nor sets the gap.
            continue
        ok = all(id(e) in accepted or id(e) in covered for e in row["items"])
        content = any(e.kind != KIND_LINE for e in row["items"])
        if not ok:
            stop = row
            break
        if content:
            if last_content is not None and row["bnear"] - last_content["bfar"] > MAX_BAND_GAP:
                stop = row
                break
            if text_rows == MAX_EDGE_ROWS:
                return set()  # a third repeated row: not clearly a header
            text_rows += 1
            last_content = row
        band.append(row)
    while band and not any(id(e) in accepted for r in band for e in r["items"]):
        band.pop()
    if not band:
        return set()
    if stop is not None and stop["near"] - band[-1]["far"] < MIN_BAND_GAP:
        # Body touches the last row: drop rows from the inside until a gap opens.
        while band and stop["near"] - band[-1]["far"] < MIN_BAND_GAP:
            stop = band.pop()
    ids = {id(e) for r in band for e in r["items"] if id(e) in accepted}
    ids_band = {id(e) for r in band for e in r["items"]}
    return {i for i in ids if not _is_table_rule(i, page, ids_band)}


def _is_table_rule(eid: int, page: PageLayout, band: set) -> bool:
    for element in page.items:
        if id(element) == eid:
            return element.kind == KIND_LINE and _table_rule(element, page, band)
    return False


def find_repeats(pages, *, exclude: dict | None = None, min_pages: int = MIN_PAGES) -> list[Candidate]:
    """Things at the same place on more than half the pages (and >= 3 pages),
    in the outermost rows of the page only.

    ``exclude`` maps page index to boxes already covered (tags); elements
    overlapping them are left out. Returns one candidate per role, needing
    confirmation.
    """
    pages = list(pages)
    total = len(pages)
    if total < min_pages:
        return []
    exclude = exclude or {}
    parity = _parity_groups(pages)
    by_index = {page.index: page for page in pages}

    def passing(clusters):
        out = []
        for cluster in clusters:
            kept = _kept_pages(cluster["pages"], total, parity, min_pages)
            if kept:
                out.append((cluster, kept))
        return out

    first = passing(_cluster(pages, exclude, min_pages))
    # Map each passing element back to the original page element.
    accepted: dict[int, dict[int, tuple]] = {}
    for cluster, kept in first:
        for item in cluster["items"]:
            if item.page in kept:
                accepted.setdefault(item.page, {})[(item.kind, item.bbox, item.text)] = cluster
    keep_ids: dict[int, set] = {}
    for index, marks in accepted.items():
        page = by_index[index]
        ids = {id(e) for e in page.items if (e.kind, e.bbox, e.text) in marks}
        covered = {
            id(e) for e in page.items if any(_overlaps(e.bbox, box) for box in exclude.get(index, ()))
        }
        chosen: set = set()
        for zone in (ROLE_HEADER, ROLE_FOOTER):
            chosen |= edge_band(page, zone, ids, covered)
        keep_ids[index] = {
            (e.kind, e.bbox, e.text) for e in page.items if id(e) in chosen
        }
    by_role: dict[str, list[Element]] = {}
    for cluster, kept in first:
        items = [i for i in cluster["items"] if (i.kind, i.bbox, i.text) in keep_ids.get(i.page, set())]
        still = _kept_pages({i.page for i in items}, total, parity, min_pages)
        for element in items:
            if element.page in still:
                by_role.setdefault(role_for(element, cluster["zone"]), []).append(element)
    return [
        _candidate(f"{SOURCE_REPEAT}-{role}", SOURCE_REPEAT, role, items, confirmed=REPEAT_CONFIRMED_BY_DEFAULT)
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


def sign_candidates(layouts, exclude: dict) -> list[Candidate]:
    """Lines holding the fixed text 「DocuSign Envelope ID」 in the top or bottom
    zone of any page, not already covered by tags. Only that line goes; no
    repetition needed, no confirmation (产品 10-19)."""
    items = []
    for page in layouts:
        for element in page.items:
            if element.kind != KIND_TEXT or not _SIGNING.search(element.text or ""):
                continue
            if zone_of(element.bbox, page.height) is None:
                continue
            if any(_overlaps(element.bbox, box) for box in exclude.get(page.index, ())):
                continue
            items.append(replace(element, page=page.index))
    if not items:
        return []
    return [_candidate(f"{SOURCE_SIGN}-{ROLE_SIGNATURE}", SOURCE_SIGN, ROLE_SIGNATURE, items, confirmed=True)]


def build_pdf_plan(tagged, layouts, page_count: int, page_sizes=(), skipped_rotated: int = 0) -> Plan:
    tags = tag_candidates(tagged)
    exclude: dict[int, list[Box]] = {}
    for candidate in tags:
        for element in candidate.elements:
            exclude.setdefault(element.page, []).append(element.bbox)
    signs = sign_candidates(layouts, exclude)
    for candidate in signs:
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
        # The signing line alone does not make a header/footer: still 「没认出」.
        route = ROUTE_NONE
    return Plan(
        kind=KIND_PDF,
        route=route,
        page_count=page_count,
        candidates=tuple(tags + signs + repeats),
        page_sizes=tuple(page_sizes),
        skipped_rotated=skipped_rotated,
        kept_crowded=crowded_tags + crowded_repeats,
    )


def pages_of(plan: Plan | None, sources) -> set[int]:
    """Pages touched by candidates of the given sources."""
    if plan is None:
        return set()
    return {p for c in plan.candidates if c.source in sources for p in c.pages}


def has_source(plan: Plan | None, source: str) -> bool:
    return plan is not None and any(c.source == source for c in plan.candidates)


def plan_for_write(plan: Plan, repeat_confirmed: bool) -> Plan:
    """What actually goes on write: tags, the signing line and Word parts
    always; repeats only once the user confirmed this file. Returns a new,
    fully confirmed plan."""
    keep = tuple(
        replace(c, confirmed=True)
        for c in plan.candidates
        if c.source != SOURCE_REPEAT or repeat_confirmed
    )
    return replace(plan, candidates=keep)


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
    info["note"] = UI_TEXT["pending"] if any(c.needs_confirmation and not c.confirmed for c in plan.candidates) else None
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


def block_at(plan: Plan | None, page: int, bbox) -> tuple[str, str]:
    """(role, source) of the candidate whose block holds this box's centre."""
    if plan is None or bbox is None:
        return "", ""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for candidate in plan.candidates:
        for element in candidate.elements:
            if element.page == page and _contains(element.bbox, cx, cy):
                return candidate.role, candidate.source
    return "", ""


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


def pdf_line_blocks(page, plan: Plan | None, lines: list[str]) -> dict[int, tuple[str, str]]:
    """Plain-text line index -> (role, source), for drawing the preview blocks.

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
            blocks = Counter()
            for ch in chars:
                if not ch["c"].strip():
                    continue
                blocks[block_at(plan, page.number, ch["bbox"])] += 1
            best, count = blocks.most_common(1)[0] if blocks else (("", ""), 0)
            total = sum(blocks.values())
            raw.append((_SPACES.sub("", text), best if best[0] and count * 2 > total else None))
    out: dict[int, tuple[str, str]] = {}
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


def pdf_line_roles(page, plan: Plan | None, lines: list[str]) -> dict[int, str]:
    return {i: role for i, (role, _source) in pdf_line_blocks(page, plan, lines).items()}


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


def _subtract(points, remove, tol: float = MATCH_TOL):
    """``points`` minus one partner (same char, within ``tol``) per item of ``remove``."""
    used = [False] * len(points)
    for ch, x, y in remove:
        for i, (c2, x2, y2) in enumerate(points):
            if not used[i] and c2 == ch and abs(x2 - x) <= tol and abs(y2 - y) <= tol:
                used[i] = True
                break
    return [p for p, u in zip(points, used) if not u]


def check_text(before, after, removed) -> tuple[int, int]:
    """(left, lost) for one page, glyph by glyph.

    ``before``/``after`` are (char, cx, cy) of the page; ``removed`` lists the
    candidates' own glyphs. left: removed glyphs still there. lost: any other
    glyph that disappeared, wherever it sits, also inside a removed block (S1).
    """
    before = [p for p in before if p[0].strip()]
    after = [p for p in after if p[0].strip()]
    removed = [p for p in removed if p[0].strip()]
    left = len(removed) - _match_points(removed, after)
    keep = _subtract(before, removed)
    rest = _subtract(after, [r for r in removed if _match_points([r], after) == 0])
    lost = _match_points(keep, rest)
    return left, lost


def _centre(ch: str, bbox) -> tuple:
    x0, y0, x1, y1 = bbox
    return (ch, (x0 + x1) / 2, (y0 + y1) / 2)


def _page_glyphs(page):
    """(char, bbox) of every glyph on the page."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"].strip():
                        out.append((ch["c"], tuple(ch["bbox"])))
    return out


def _snap_chars(own, glyphs, tol: float = 2.0) -> tuple:
    """Candidate glyphs in the page's own coordinates (tags are read from a
    scratch copy, whose boxes can differ a little)."""
    pool = [_centre(ch, box) for ch, box in glyphs]
    out = []
    for ch, x, y in own:
        best = None
        for c2, x2, y2 in pool:
            if c2 == ch and abs(x2 - x) <= tol and abs(y2 - y) <= tol:
                d = abs(x2 - x) + abs(y2 - y)
                if best is None or d < best[0]:
                    best = (d, (c2, x2, y2))
        out.append(best[1] if best else (ch, x, y))
    return tuple(out)


def tight_elements(plan: Plan, document) -> set:
    """(page, bbox) of text candidates whose removal marks touch a glyph that
    is not part of any candidate. Redaction removes every glyph its box
    touches, so such an element is left on that page."""
    by_page: dict[int, list[Element]] = {}
    for candidate in plan.candidates:
        for element in candidate.elements:
            if element.kind == KIND_TEXT:
                by_page.setdefault(element.page, []).append(element)
    tight = set()
    for index, elements in by_page.items():
        glyphs = _page_glyphs(document[index])
        own = [p for e in elements for p in e.chars]
        foreign = []
        centres = [_centre(ch, box) for ch, box in glyphs]
        free = _subtract(centres, own, tol=MATCH_TOL)
        free_set = {(c, round(x, 3), round(y, 3)) for c, x, y in free}
        for (ch, box), (c, x, y) in zip(glyphs, centres):
            if (c, round(x, 3), round(y, 3)) in free_set:
                foreign.append(box)
        for element in elements:
            if any(_overlaps(mark, box) for mark in element.marks for box in foreign):
                tight.add((index, element.bbox))
    return tight


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


_INVISIBLE = (b"3", b"7")


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
            if op in _SHOW and tr in _INVISIBLE:
                # Invisible text inside a header is still read by any text
                # extraction: show it here so it is found and removed.
                out.append(b" 0 Tr " + chunk + b" " + tr + b" Tr")
            else:
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
        own = tuple(_centre(chr(c[0]), c[3]) for c in chars if chr(c[0]).strip())
        items.append(Element(kind=KIND_TEXT, bbox=bbox, text=text, marks=marks, chars=own))
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
            own = tuple(_centre(c, bbox) for c, _o, bbox in chars if c.strip())
            items.append(
                Element(kind=KIND_TEXT, bbox=tuple(line["bbox"]), text=text, marks=tuple(marks), chars=own)
            )
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
            # Coordinates of rotated pages are not handled; apply_pdf refuses
            # the whole file (RotatedPages) instead of skipping them.
            rotated += 1
            continue
        tag = tagged_layout_of(document, page)
        if tag is not None:
            tagged.append(tag)
        layouts.append(layout_of(page))
    plan = build_pdf_plan(tagged, layouts, document.page_count, sizes, rotated)
    return _settle_text(plan, document)


def _settle_text(plan: Plan, document) -> Plan:
    """Give text candidates the page's own glyph positions and leave out any
    whose removal would also cut a body glyph (S1)."""
    if not plan.candidates:
        return plan
    glyphs: dict[int, list] = {}
    candidates = []
    for candidate in plan.candidates:
        items = []
        for element in candidate.elements:
            if element.kind == KIND_TEXT:
                if element.page not in glyphs:
                    glyphs[element.page] = _page_glyphs(document[element.page])
                element = replace(element, chars=_snap_chars(element.chars, glyphs[element.page]))
            items.append(element)
        candidates.append(replace(candidate, elements=tuple(items)))
    snapped = replace(plan, candidates=tuple(candidates))
    tight = tight_elements(snapped, document)
    if not tight:
        return snapped
    out = []
    for candidate in snapped.candidates:
        keep = tuple(
            e for e in candidate.elements if not (e.kind == KIND_TEXT and (e.page, e.bbox) in tight)
        )
        if keep:
            out.append(replace(candidate, elements=keep, pages=tuple(sorted({e.page for e in keep}))))
    route = plan.route
    if not out:
        route = ROUTE_NONE
    elif route == ROUTE_TAG and not any(c.source == SOURCE_TAG for c in out):
        route = ROUTE_REPEAT
    return replace(snapped, candidates=tuple(out), route=route, kept_tight=len(tight))


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
    if tight_elements(plan, document):
        # The page changed since the plan was made: a removal would cut body text.
        raise PlanMismatch()


def apply_pdf(document, plan: Plan) -> ApplyResult:
    """Step two. Redacts every element of the (all confirmed) plan in place.

    Raises UnconfirmedCandidates or PlanMismatch before touching anything,
    and HeaderFooterLeft if the re-extracted result still shows a removed
    item or lost body content. Save with ``garbage>=3`` so the removed
    objects are not kept in the file.
    """
    require_confirmed(plan)
    if plan.skipped_rotated:
        raise RotatedPages(plan.skipped_rotated)
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
                    own = [p for e in elements if e.kind == KIND_TEXT for p in e.chars]
                    l, m = check_text(before[index][kind], after[kind], own)
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
