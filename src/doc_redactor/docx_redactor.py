"""Redact docx body, tables, headers/footers, text boxes, fields, content controls, revisions, and images."""

from __future__ import annotations

import zipfile
from pathlib import Path

from docx import Document
from docx.table import Table, _Cell

from doc_redactor.engine import Hit, RedactionEngine, merge_counts, redaction_label
from doc_redactor.image_redactor import is_raster_suffix, redact_image_bytes


def redact_docx(src: Path, dest: Path, engine: RedactionEngine) -> dict[str, int]:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")
    document = Document(str(src))
    counts: dict[str, int] = {}
    _redact_container(document, engine, counts)
    for section in document.sections:
        for header_footer in _owned_headers_footers(section):
            _redact_container(header_footer, engine, counts)
    scrub_docx_properties(document)
    dest.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(dest))
    merge_counts(counts, _redact_media(dest, engine))
    return counts


def _redact_container(container, engine: RedactionEngine, counts: dict[str, int]) -> None:
    for unit in iter_text_units(container._element):
        merge_counts(counts, _counts(_redact_paragraph(unit, engine)))


def iter_table_cells(table: Table):
    """Yield each physical ``w:tc`` once, in XML order.

    ``row.cells`` repeats merged cells, and deduping by ``id(cell._tc)`` breaks
    because lxml proxies are freed and ids reused. Walking ``w:tc`` directly
    visits every cell exactly once (gridSpan and vMerge included).
    """
    for tr in table._tbl.tr_lst:
        for tc in tr.tc_lst:
            yield _Cell(tc, table)


_CORE_TEXT_FIELDS = (
    "author",
    "title",
    "subject",
    "keywords",
    "comments",
    "last_modified_by",
    "category",
    "content_status",
    "identifier",
    "language",
    "version",
)
# app.xml fields that can carry names; Application, page/word counts stay.
_APP_TEXT_TAGS = ("Company", "Manager", "HyperlinkBase", "Template")
_EMPTY_CUSTOM = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    b'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>'
)


def scrub_docx_properties(document) -> None:
    """Blank core text properties, name fields in app.xml, and every custom property."""
    core = document.core_properties
    for field in _CORE_TEXT_FIELDS:
        setattr(core, field, "")
    from lxml import etree

    package = document.part.package
    # A saved thumbnail is a picture of page 1, original text included.
    for rid, rel in list(package.rels.items()):
        if rel.reltype.endswith("/thumbnail"):
            del package.rels[rid]
    for part in package.iter_parts():
        name = str(part.partname)
        if name == "/docProps/app.xml":
            root = etree.fromstring(part.blob)
            for element in list(root):
                if etree.QName(element).localname in _APP_TEXT_TAGS:
                    root.remove(element)
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        elif name == "/docProps/custom.xml":
            part._blob = _EMPTY_CUSTOM


def _owned_headers_footers(section):
    """Yield header/footer parts this section defines.

    Reading ``.paragraphs`` on a missing part creates an empty header. Skip
    parts that are only linked to the previous section.
    """
    parts = (
        section.header,
        section.footer,
        section.first_page_header,
        section.first_page_footer,
        section.even_page_header,
        section.even_page_footer,
    )
    for part in parts:
        if not part.is_linked_to_previous:
            yield part


def _redact_paragraph(unit: TextUnit, engine: RedactionEngine) -> list[str]:
    """Replace each hit inside one text unit, keeping the first involved run's formatting.

    Matches are applied from the right. The placeholder is written into the
    first segment that overlaps the hit; text inside the hit is removed from
    the later segments. Every stored copy of the unit gets the same change.
    """
    categories: list[str] = []
    for _ in range(10000):
        hits = unit.find_hits(engine)
        if not hits:
            break
        hit = hits[-1]
        if not unit.apply(hit, engine.label(hit.category)):
            break
        categories.append(hit.category)
    else:
        raise RuntimeError("同一段落命中过多，已停止")
    categories.reverse()
    return categories


# ---------------------------------------------------------------------------
# One walk over the XML for scanning and for writing.
#
# python-docx's ``.paragraphs`` only sees direct ``w:p`` children and
# ``.runs`` only direct ``w:r`` children, so text inside text boxes, content
# controls (``w:sdt``), simple fields (``w:fldSimple``), hyperlinks and
# tracked insertions (``w:ins``) was never read. This walk visits every
# ``w:p`` under a part and every ``w:r`` a paragraph owns, at any depth.

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_W_P = _W + "p"
_W_R = _W + "r"
_W_DELTEXT = _W + "delText"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_INSERTED = (_W + "ins", _W + "moveTo")
_MC_ALT = _MC + "AlternateContent"
_MC_BRANCHES = (_MC + "Choice", _MC + "Fallback")

KIND_TEXT = "text"
KIND_DELETED = "deleted"


class _RunSegment:
    """Visible text of one ``w:r`` (``w:t``, tabs, breaks). Not ``w:delText``."""

    __slots__ = ("_r", "writable")

    def __init__(self, r, writable: bool = True) -> None:
        self._r = r
        self.writable = writable

    @property
    def text(self) -> str:
        return self._r.text

    @text.setter
    def text(self, value: str) -> None:
        # CT_R.text keeps w:rPr and rebuilds w:t / w:tab / w:br.
        self._r.text = value


class _DelTextSegment:
    """One ``w:delText``: text removed under tracked changes, still in the file."""

    __slots__ = ("_el", "writable")

    def __init__(self, element) -> None:
        self._el = element
        self.writable = True

    @property
    def text(self) -> str:
        return self._el.text or ""

    @text.setter
    def text(self, value: str) -> None:
        self._el.text = value
        self._el.set(_XML_SPACE, "preserve")


class TextUnit:
    """One paragraph's text as the scanner sees it, plus where to write it back.

    ``kind`` is ``"text"`` for what the reader sees (plain runs, hyperlinks,
    fields, content controls, insertions), or ``"deleted"`` for the paragraph
    as it read before tracked changes: kept runs plus ``w:delText``, without
    insertions. A deleted unit only reports hits that touch deleted text, and
    only rewrites deleted text, so the kept runs stay for the text unit.

    ``copies`` holds the segment lists of every stored copy: a text box saved
    as both a DrawingML and a VML shape is one unit with two copies, so one
    decision covers both.
    """

    __slots__ = ("kind", "copies")

    def __init__(self, kind: str, copies: list[list]) -> None:
        self.kind = kind
        self.copies = copies

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.copies[0])

    @property
    def is_deletion(self) -> bool:
        return self.kind == KIND_DELETED

    def find_hits(self, engine: RedactionEngine) -> list[Hit]:
        """Hits in this unit. The one place that decides what a unit reports."""
        hits = engine.find_hits(self.text)
        if not self.is_deletion:
            return hits
        writable = [span for span in _spans(self.copies[0]) if span[0].writable]
        return [hit for hit in hits if any(s[1] < hit.end and s[2] > hit.start for s in writable)]

    def apply(self, hit: Hit, replacement: str) -> bool:
        applied = False
        for segments in self.copies:
            if _apply_hit(_spans(segments), hit, replacement=replacement):
                applied = True
        return applied


def iter_text_units(root):
    """Yield every text unit under ``root`` (a body, header or footer element), in XML order.

    A paragraph with tracked deletions yields its deleted unit first. That
    unit only rewrites ``w:delText``, so the offsets of the text unit that
    follows stay valid.
    """
    for group in _iter_paragraph_groups(root):
        owned = [list(_own_runs(p)) for p in group]
        if any(r.find(_W_DELTEXT) is not None for runs in owned for r, _inserted in runs):
            yield TextUnit(KIND_DELETED, [_before_changes(runs) for runs in owned])
        yield TextUnit(KIND_TEXT, [[_RunSegment(r) for r, _inserted in runs] for runs in owned])


def _before_changes(runs) -> list:
    """Segments of the paragraph as it read before tracked changes."""
    segments: list = []
    for r, inserted in runs:
        if inserted:
            continue
        deleted = list(r.iter(_W_DELTEXT))
        if deleted:
            segments.extend(_DelTextSegment(el) for el in deleted)
        else:
            segments.append(_RunSegment(r, writable=False))
    return segments


def _iter_paragraph_groups(element):
    """Yield lists of ``w:p``: one paragraph, or the mirrored copies of one."""
    for child in element:
        tag = child.tag
        if not isinstance(tag, str):
            continue
        if tag == _W_P:
            yield [child]
            # Text boxes anchored in this paragraph hold their own paragraphs.
            yield from _iter_paragraph_groups(child)
        elif tag == _MC_ALT:
            yield from _iter_alternate(child)
        else:
            yield from _iter_paragraph_groups(child)


def _iter_alternate(alternate):
    """mc:AlternateContent stores the same content once per branch.

    When every branch holds the same paragraphs with the same text, pair them
    into one group per paragraph. Otherwise every branch is walked on its own,
    so nothing is skipped.
    """
    branches = [
        list(_iter_paragraph_groups(child))
        for child in alternate
        if isinstance(child.tag, str) and child.tag in _MC_BRANCHES
    ]
    if len(branches) >= 2 and _mirrored(branches):
        for groups in zip(*branches):
            yield [p for group in groups for p in group]
        return
    for groups in branches:
        yield from groups


def _mirrored(branches) -> bool:
    first = branches[0]
    for other in branches[1:]:
        if len(other) != len(first):
            return False
        for a, b in zip(first, other):
            if len(a) != len(b):
                return False
            if any(_paragraph_key(x) != _paragraph_key(y) for x, y in zip(a, b)):
                return False
    return True


def _paragraph_key(p) -> tuple[str, str]:
    runs = [r for r, _inserted in _own_runs(p)]
    live = "".join(r.text for r in runs)
    deleted = "".join(el.text or "" for r in runs for el in r.iter(_W_DELTEXT))
    return live, deleted


def _own_runs(element, inserted: bool = False):
    """``(w:r, inside a tracked insertion)`` this paragraph owns, at any depth, in XML order.

    Stops at nested ``w:p`` (text box paragraphs are their own units) and does
    not look inside a run.
    """
    for child in element:
        tag = child.tag
        if not isinstance(tag, str) or tag == _W_P:
            continue
        if tag == _W_R:
            yield child, inserted
        else:
            yield from _own_runs(child, inserted or tag in _INSERTED)


def _spans(segments):
    spans = []
    position = 0
    for segment in segments:
        segment_text = segment.text
        if segment_text:
            # Empty runs (a drawing, a field mark) never take part in a hit,
            # so a hit around them never clears them.
            spans.append((segment, position, position + len(segment_text), segment_text))
        position += len(segment_text)
    return spans


def iter_document_paragraphs(document):
    """Yield text units in the same order as redact_docx (body, then headers/footers).

    Scanning, writing and the after-write check all use this one walk. Each
    unit has ``.text``; the name is kept for existing callers.
    """
    yield from iter_text_units(document._element)
    for section in document.sections:
        for header_footer in _owned_headers_footers(section):
            yield from iter_text_units(header_footer._element)


def apply_hits_to_paragraph(unit: TextUnit, hits: list[Hit], replacement_for) -> list[str]:
    """Apply stored hits to one text unit from the right. replacement_for(category) -> str."""
    ordered = sorted(hits, key=lambda item: item.start, reverse=True)
    categories: list[str] = []
    for hit in ordered:
        text = unit.text
        rebuilt = Hit(
            start=hit.start,
            end=hit.end,
            category=hit.category,
            text=text[hit.start : hit.end] if 0 <= hit.start <= hit.end <= len(text) else hit.text,
        )
        if not unit.apply(rebuilt, replacement_for(hit.category)):
            continue
        categories.append(hit.category)
    categories.reverse()
    return categories


def _apply_hit(spans, hit: Hit, replacement: str | None = None) -> bool:
    """Write the placeholder into the first writable span the hit touches.

    The hit's text is removed from the other writable spans it touches.
    Read-only spans (kept runs seen from a deleted unit) are left alone.
    """
    involved = [span for span in spans if span[1] < hit.end and span[2] > hit.start and span[0].writable]
    if not involved:
        return False
    if replacement is None:
        replacement = redaction_label(hit.category)
    for number, (segment, start, end, text) in enumerate(involved):
        local_start = max(hit.start, start) - start
        local_end = min(hit.end, end) - start
        middle = replacement if number == 0 else ""
        segment.text = text[:local_start] + middle + text[local_end:]
    return True


def _counts(categories: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for category in categories:
        counts[category] = counts.get(category, 0) + 1
    return counts


def _redact_media(path: Path, engine: RedactionEngine) -> dict[str, int]:
    with zipfile.ZipFile(path, "r") as archive:
        names = [info.filename for info in archive.infolist() if _is_media_image(info.filename)]
        if not names:
            return {}
        payloads = {name: archive.read(name) for name in names}
        infos = archive.infolist()

    counts: dict[str, int] = {}
    replaced: dict[str, bytes] = {}
    for name, payload in payloads.items():
        suffix = Path(name).suffix
        new_bytes, image_counts = redact_image_bytes(payload, suffix, engine)
        replaced[name] = new_bytes
        merge_counts(counts, image_counts)

    buffer_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(buffer_path, "w") as target:
        for info in infos:
            data = replaced.get(info.filename, source.read(info.filename))
            cloned = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            cloned.compress_type = info.compress_type
            cloned.external_attr = info.external_attr
            cloned.flag_bits = info.flag_bits
            cloned.create_system = info.create_system
            target.writestr(cloned, data)
    buffer_path.replace(path)
    return counts


def _is_media_image(name: str) -> bool:
    lowered = name.lower()
    if not lowered.startswith("word/media/"):
        return False
    return is_raster_suffix(Path(lowered).suffix)
