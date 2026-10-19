"""Redact PDF text by deletion, and raster images by OCR plus black boxes.

PDF placeholders stay on one line. The full label shrinks down to 6pt.
If it still does not fit, the category's short code is used. If that does
not fit either, the box stays blank. A half-written label is never emitted.
"""

from __future__ import annotations

import difflib
import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image

from doc_redactor.engine import Hit, RedactionEngine, merge_counts
from doc_redactor.image_redactor import redact_image

MIN_PLACEHOLDER_SIZE = 6.0
MSG_REPL_HINT = (
    "PDF 里位置窄时，会先把字缩小；还放不下就换成短码（如 [SP]），并在 PDF 最后加一页说明。Word 里总是写完整的替换文字。"
)
# Non-breaking spaces keep the count on the same line as 处. A normal space
# lets the result column wrap as "2 / 处".
MSG_FIT_SHORT = "ⓘ 这个 PDF 有\u00a0{n}\u00a0处位置太窄，已换成短码，最后一页有对照说明。"
MSG_FIT_BLANK = "⚠ 另有\u00a0{m}\u00a0处太窄，连短码也放不下，只留了空白，原文已删除。"
MSG_UNLOCATED = "{n} 处无法定位，未写出"
MSG_PDF_WIDTH = "PDF 只能在原来的位置写字，空位和原文一样宽，看的人能看出原文大概多长。"
LEGEND_TITLE = "替换说明"
LEGEND_FOOTER = "本页由 doc-redactor 生成，只说明替换文字的意思，不含任何原文。"

# Shared by aliases. The title is the name printed on the legend page.
_SHORT_GROUPS = (
    ("[PR]", "项目号", ("PROJECT_ID", "项目号")),
    ("[PN]", "方案号清单", ("方案号清单", "字母加数字")),
    ("[SP]", "申办方", ("SPONSOR", "申办方")),
    ("[SD]", "药名", ("DRUG", "药名")),
    ("[NCT]", "NCT", ("NCT", "NCT 试验登记号")),
    ("[EM]", "邮箱", ("EMAIL", "邮箱")),
    ("[PH]", "手机", ("PHONE", "手机号", "手机号（中国大陆）")),
)
_BUILTIN_SHORT = {
    key: (code, title) for code, title, keys in _SHORT_GROUPS for key in keys
}
_CODE_RANK = {code: index for index, (code, _title, _keys) in enumerate(_SHORT_GROUPS)}
_FONT_INFO: dict[str, dict] = {}
_HEIGHT_EPSILON = 1e-5

# Scan the whole page, not only what the CropBox shows. Text placed outside the
# page or cut off by the CropBox is invisible on screen, but pypdf and
# pdftotext still read it.
PAGE_TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_MEDIABOX_CLIP
# A line ending in one of these, between two letters, is one word broken over
# two lines. pdftotext joins it back, so the scan must too.
_BREAK_HYPHENS = "-\u00ad\u2010"


class UnlocatedText(ValueError):
    """Plain text has a hit whose glyphs could not be placed on the page.

    The file must not be written: a partial PDF would keep the original text.
    ``count`` is the number of such hits. The message does not include them.
    """

    def __init__(self, count: int) -> None:
        self.count = int(count)
        super().__init__(MSG_UNLOCATED.format(n=self.count))


class RedactionCounts(dict):
    """Category counts plus how many boxes used a short code or stayed blank.

    ``json.dumps`` and ``==`` only see the category keys, so a report stays
    free of the extra tallies.
    """

    def __init__(self, counts: dict | None = None, short_count: int = 0, blank_count: int = 0):
        super().__init__()
        if counts:
            self.update(counts)
        self.short_count = int(short_count)
        self.blank_count = int(blank_count)


@dataclass
class PlaceholderStats:
    short_count: int = 0
    blank_count: int = 0
    used: list[tuple[str, str, str]] = field(default_factory=list)


class ShortPlan:
    """One short code per category, stable for a single PDF."""

    def __init__(self, category_order: list[str] | None = None) -> None:
        self._custom: dict[str, tuple[str, str]] = {}
        seen: set[str] = set()
        number = 0
        for name in category_order or []:
            if not name or name in _BUILTIN_SHORT or name in seen:
                continue
            seen.add(name)
            number += 1
            self._custom[name] = (f"[R{number}]", name)
        self._next = number + 1

    def resolve(self, category: str) -> tuple[str, str]:
        builtin = _BUILTIN_SHORT.get(category)
        if builtin is not None:
            return builtin
        known = self._custom.get(category)
        if known is not None:
            return known
        code = (f"[R{self._next}]", category)
        self._next += 1
        self._custom[category] = code
        return code


def legend_lines(used: list[tuple[str, str, str]]) -> list[str]:
    """One line per short code actually used. The first full label wins."""
    chosen: dict[str, tuple[str, str]] = {}
    for code, title, full in used:
        if code not in chosen:
            chosen[code] = (title, full)

    def sort_key(code: str) -> tuple:
        if code in _CODE_RANK:
            return (0, _CODE_RANK[code], 0, code)
        body = code[1:-1] if code.startswith("[") and code.endswith("]") else code
        if body.startswith("R") and body[1:].isdigit():
            return (1, int(body[1:]), 0, code)
        return (2, 0, 0, code)

    lines = []
    for code in sorted(chosen, key=sort_key):
        title, full = chosen[code]
        lines.append(f"{code} = {title}（{full}）")
    return lines


def fit_notice(short_count: int, blank_count: int, unlocated: int = 0) -> str:
    """One sentence per reason, joined by a space so the column can wrap them."""
    parts = []
    if short_count > 0:
        parts.append(MSG_FIT_SHORT.format(n=short_count))
    if blank_count > 0:
        parts.append(MSG_FIT_BLANK.format(m=blank_count))
    if unlocated > 0:
        parts.append(f"{unlocated}\u00a0处无法定位，未写出")
    return " ".join(parts)


def _font_for(text: str) -> str:
    if any(ord(ch) > 255 for ch in text):
        return "china-s"
    return "helv"


def _fontinfo(fontname: str) -> dict:
    cached = _FONT_INFO.get(fontname)
    if cached is not None:
        return cached
    document = pymupdf.open()
    try:
        page = document.new_page()
        xref = page.insert_font(fontname=fontname)
        fontdict = pymupdf.CheckFontInfo(document, xref)[1]
        glyphs = document.get_char_widths(xref)
        cached = {
            "ascender": float(fontdict["ascender"]),
            "descender": float(fontdict["descender"]),
            "ordering": int(fontdict["ordering"]),
            "glyphs": glyphs,
        }
    finally:
        document.close()
    _FONT_INFO[fontname] = cached
    return cached


def _line_box(fontsize: float, info: dict) -> float:
    ascender = info["ascender"]
    descender = info["descender"]
    factor = 1.2 if ascender - descender <= 1 else ascender - descender
    return fontsize * factor - descender * fontsize


def _single_line_fits(text: str, fontsize: float, max_width: float, max_height: float, info: dict) -> bool:
    """True only when insert_textbox would keep the whole string on one line."""
    if not text or "\n" in text or max_width <= 0 or max_height <= 0:
        return False
    if _line_box(fontsize, info) - max_height > _HEIGHT_EPSILON:
        return False
    ordering = info["ordering"]
    glyphs = info["glyphs"]

    def pixlen(value: str) -> float:
        if ordering >= 0:
            return len(value) * fontsize
        return sum(glyphs[ord(ch)][1] for ch in value) * fontsize

    space = fontsize if ordering >= 0 else glyphs[32][1] * fontsize
    words = text.expandtabs(1).split(" ")
    rest = max_width
    for index, word in enumerate(words):
        width = pixlen(word)
        if width > max_width or rest < width:
            return False
        rest -= width
        if index < len(words) - 1:
            rest -= space
    return True


def fitted_fontsize(rect, text: str, *, min_size: float = MIN_PLACEHOLDER_SIZE) -> float | None:
    box = pymupdf.Rect(rect)
    if not text or box.is_empty or box.width <= 0 or box.height <= 0:
        return None
    info = _fontinfo(_font_for(text))
    half = int(math.floor(min(float(box.height), 72.0) * 2 + 1e-9))
    min_half = int(round(min_size * 2))
    if half < min_half:
        return None
    while half >= min_half:
        size = half / 2.0
        if _single_line_fits(text, size, float(box.width), float(box.height), info):
            return size
        half -= 1
    return None


def placeholder_choice(rect, full: str, short: str) -> tuple[str, str]:
    if fitted_fontsize(rect, full) is not None:
        return "full", full
    if short and fitted_fontsize(rect, short) is not None:
        return "short", short
    return "blank", ""


def placeholder_rect(rects, full: str, short: str, text: str = ""):
    """Box that receives the placeholder. Other segments of the hit stay blank.

    The first segment is the default. A one-character first segment, or a first
    segment that cannot hold even the short code, uses the widest segment.
    """
    boxes = []
    for rect in rects or []:
        box = pymupdf.Rect(rect)
        if box.is_empty or box.width <= 0 or box.height <= 0:
            continue
        boxes.append(box)
    if not boxes:
        return None
    if len(boxes) == 1:
        return boxes[0]
    first_len = len(text.split("\n", 1)[0]) if text else 0
    if first_len == 1 or placeholder_choice(boxes[0], full, short)[0] == "blank":
        return max(boxes, key=lambda box: (box.width, box.height))
    return boxes[0]


def pdf_fit_counts(hits, label, category_order: list[str] | None = None) -> tuple[int, int]:
    """How many enabled hits with rectangles need a short code or a blank."""
    plan = ShortPlan(category_order)
    short_count = 0
    blank_count = 0
    for hit in hits:
        if not getattr(hit, "enabled", True):
            continue
        rects = getattr(hit, "rects", None) or []
        if not rects:
            continue
        full = label(hit.category)
        code, _title = plan.resolve(hit.category)
        anchor = placeholder_rect(rects, full, code, getattr(hit, "text", "") or "")
        if anchor is None:
            continue
        kind, _shown = placeholder_choice(anchor, full, code)
        if kind == "short":
            short_count += 1
        elif kind == "blank":
            blank_count += 1
    return short_count, blank_count


def page_textpage(page):
    """Text page of everything drawn on ``page``, inside the CropBox or not."""
    return page.get_textpage(flags=PAGE_TEXT_FLAGS, clip=pymupdf.INFINITE_RECT())


def find_page_hits(engine: RedactionEngine, text: str) -> list[Hit]:
    """Hits in one page's text, plus words broken by a hyphen at a line end.

    The page text is matched as it is. Then a copy with every ``letter-\\nletter``
    break removed is matched too; a hit there that crosses a removed break is
    mapped back to the original offsets (it then covers both line fragments
    and the hyphen) and kept unless it overlaps a hit already found.
    """
    hits = list(engine.find_hits(text))
    kept: list[int] = []
    breaks: list[int] = []
    index = 0
    while index < len(text):
        ch = text[index]
        if (
            ch in _BREAK_HYPHENS
            and index > 0
            and text[index - 1].isalpha()
            and text[index + 1 : index + 2] == "\n"
            and text[index + 2 : index + 3].isalpha()
        ):
            breaks.append(index)
            index += 2
            continue
        kept.append(index)
        index += 1
    if not breaks:
        return hits
    joined = "".join(text[i] for i in kept)
    extra: list[Hit] = []
    for hit in engine.find_hits(joined):
        if hit.end <= hit.start:
            continue
        start = kept[hit.start]
        end = kept[hit.end - 1] + 1
        if not any(start < position < end for position in breaks):
            continue
        if any(start < other.end and other.start < end for other in hits + extra):
            continue
        extra.append(Hit(start=start, end=end, category=hit.category, text=text[start:end]))
    return sorted(hits + extra, key=lambda item: item.start)


def prepare_pdf(document) -> None:
    """Drop text a reader would take instead of the glyphs. Call right after opening.

    Scanning and writing both call it, so the scan sees the glyphs that are
    really drawn and the written file carries no tagged copy of them:
    ``/ActualText``, ``/Alt`` and ``/E`` on marked content, and the structure
    tree (``/StructTreeRoot``) with its ``/Alt`` texts.
    """
    catalog = document.pdf_catalog()
    for key in ("StructTreeRoot", "MarkInfo"):
        if document.xref_get_key(catalog, key)[0] != "null":
            document.xref_set_key(catalog, key, "null")
    streams: set[int] = set()
    for page in document:
        streams.update(page.get_contents())
    for xref in range(1, document.xref_length()):
        try:
            keys = document.xref_get_keys(xref)
        except Exception:
            continue
        if document.xref_is_stream(xref) and document.xref_get_key(xref, "Subtype") == ("name", "/Form"):
            streams.add(xref)
        # Property lists stored as their own objects (``/Span /MC0 BDC``).
        tagged = [key for key in ("ActualText", "Alt") if key in keys]
        if tagged:
            for key in tagged + (["E"] if "E" in keys else []):
                document.xref_set_key(xref, key, "null")
    for xref in sorted(streams):
        try:
            data = document.xref_stream(xref)
        except Exception:
            continue
        if not data or b"BDC" not in data and b"DP" not in data:
            continue
        cleaned = strip_marked_content_properties(data)
        if cleaned != data:
            document.update_stream(xref, cleaned)


_PDF_WHITESPACE = b" \t\r\n\f\x00"
_PDF_DELIMITERS = b"()<>[]{}/%"


def strip_marked_content_properties(data: bytes) -> bytes:
    """Turn ``/Tag <<...>> BDC`` and ``/Tag /Name BDC`` into ``/Tag BMC`` (and DP into MP).

    The property list is where ``/ActualText``, ``/Alt`` and ``/E`` live.
    ``/OC`` (optional content) is kept: dropping it would show hidden layers.
    Strings, comments and inline images are skipped so their bytes are never
    taken for operators.
    """
    out: list[tuple[int, int, bytes]] = []
    operands: list[tuple[int, int, bytes]] = []  # (start, end, kind)
    i = 0
    n = len(data)
    while i < n:
        c = data[i : i + 1]
        if c in (b" ", b"\t", b"\r", b"\n", b"\f", b"\x00"):
            i += 1
            continue
        if c == b"%":
            while i < n and data[i : i + 1] not in (b"\r", b"\n"):
                i += 1
            continue
        if c == b"(":
            start = i
            i = _skip_literal(data, i)
            operands.append((start, i, b"str"))
            continue
        if data.startswith(b"<<", i):
            start = i
            i = _skip_dict(data, i)
            operands.append((start, i, b"dict"))
            continue
        if c == b"<":
            start = i
            end = data.find(b">", i)
            i = n if end < 0 else end + 1
            operands.append((start, i, b"str"))
            continue
        if c == b"[":
            start = i
            i = _skip_array(data, i)
            operands.append((start, i, b"array"))
            continue
        if c in (b"]", b">", b")", b"{", b"}"):
            i += 1
            continue
        start = i
        i += 1
        while i < n and data[i] not in _PDF_WHITESPACE and data[i] not in _PDF_DELIMITERS:
            i += 1
        token = data[start:i]
        if c == b"/":
            operands.append((start, i, b"name"))
            continue
        if token[:1].isdigit() or token[:1] in (b"-", b"+", b".") or token in (b"true", b"false", b"null"):
            operands.append((start, i, b"num"))
            continue
        # An operator.
        if token in (b"BDC", b"DP") and len(operands) >= 2:
            tag, props = operands[-2], operands[-1]
            if tag[2] == b"name" and props[2] in (b"dict", b"name") and data[tag[0] : tag[1]] != b"/OC":
                out.append((props[0], i, b"BMC" if token == b"BDC" else b"MP"))
        elif token == b"ID":
            # Inline image data runs to the next whitespace-delimited EI.
            j = i + 1
            while j < n:
                found = data.find(b"EI", j)
                if found < 0:
                    j = n
                    break
                before = data[found - 1 : found]
                after = data[found + 2 : found + 3]
                if before in (b" ", b"\t", b"\r", b"\n", b"\f", b"\x00") and (
                    not after or after in (b" ", b"\t", b"\r", b"\n", b"\f", b"\x00")
                ):
                    j = found + 2
                    break
                j = found + 2
            i = j
        operands = []
    if not out:
        return data
    pieces = []
    last = 0
    for start, end, replacement in out:
        pieces.append(data[last:start])
        pieces.append(replacement)
        last = end
    pieces.append(data[last:])
    return b"".join(pieces)


def _skip_literal(data: bytes, i: int) -> int:
    depth = 0
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:  # backslash
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _skip_dict(data: bytes, i: int) -> int:
    depth = 0
    n = len(data)
    while i < n:
        if data.startswith(b"<<", i):
            depth += 1
            i += 2
            continue
        if data.startswith(b">>", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
            continue
        c = data[i : i + 1]
        if c == b"(":
            i = _skip_literal(data, i)
            continue
        if c == b"<":
            end = data.find(b">", i)
            i = n if end < 0 else end + 1
            continue
        i += 1
    return n


def _skip_array(data: bytes, i: int) -> int:
    depth = 0
    n = len(data)
    while i < n:
        c = data[i : i + 1]
        if c == b"(":
            i = _skip_literal(data, i)
            continue
        if data.startswith(b"<<", i):
            i = _skip_dict(data, i)
            continue
        if c == b"<":
            end = data.find(b">", i)
            i = n if end < 0 else end + 1
            continue
        if c == b"[":
            depth += 1
        elif c == b"]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def redact_pdf(src: Path, dest: Path, engine: RedactionEngine) -> RedactionCounts:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")
    document = pymupdf.open(src)
    try:
        if document.is_encrypted:
            raise ValueError(f"不支持加密的 PDF: {src.name}")
        prepare_pdf(document)
        counts: dict[str, int] = {}
        items: list[tuple] = []
        unlocated = 0
        page_total = document.page_count
        for index in range(page_total):
            page = document[index]
            textpage = page_textpage(page)
            text = page.get_text("text", textpage=textpage)
            found = find_page_hits(engine, text)
            located = locate_text_hits(page, found, textpage)
            for hit, usable in zip(found, located):
                if not usable:
                    unlocated += 1
                    continue
                items.append((index, usable, hit.category, hit.text))
                counts[hit.category] = counts.get(hit.category, 0) + 1
        if unlocated:
            raise UnlocatedText(unlocated)
        order = list(getattr(engine, "category_order", []) or [])
        stats = redact_text_placeholders(document, items, engine.label, order)
        merge_counts(counts, _redact_images(document, engine))
        if stats.used:
            append_legend(document, legend_lines(stats.used))
        clear_pdf_hidden(document)
        dest.parent.mkdir(parents=True, exist_ok=True)
        document.save(dest, garbage=4, clean=True)
    finally:
        document.close()
    return RedactionCounts(counts, stats.short_count, stats.blank_count)


_INFO_FIELDS = ("title", "author", "subject", "keywords", "creator", "producer")


def clear_pdf_hidden(document) -> None:
    """Drop document info, XMP, annotations (including widgets), bookmarks, and attachments.

    CLI (`redact_pdf`) and the GUI write path (`scan._write_pdf`) both call this
    immediately before save, after text redactions have already been applied.
    MuPDF writes its own /Producer on save; the original producer string is removed.
    """
    for page in document:
        widget = page.first_widget
        while widget is not None:
            nxt = widget.next
            page.delete_widget(widget)
            widget = nxt
        annot = page.first_annot
        while annot is not None:
            nxt = annot.next
            page.delete_annot(annot)
            annot = nxt
        # Links are not in the annotation chain above. Their URI is still read
        # by pypdf and get_links().
        for link in page.get_links():
            page.delete_link(link)
    for name in list(document.embfile_names() or []):
        document.embfile_del(name)
    document.set_toc([])
    document.set_metadata({key: "" for key in _INFO_FIELDS})
    document.set_xml_metadata("")
    document.del_xml_metadata()
    # Custom Info keys (/Sponsor, /ContractNo...) stay after set_metadata.
    if document.xref_get_key(-1, "Info")[0] != "null":
        document.xref_set_key(-1, "Info", "null")
    _drop_catalog_attachments(document)
    _rename_layers(document)
    # MuPDF keeps the first /ID string of the original on save; anything could
    # sit there. Write a fresh random one (both halves, the second is redone on save).
    import secrets

    new_id = secrets.token_hex(16)
    document.xref_set_key(-1, "ID", f"[<{new_id}> <{new_id}>]")


def _rename_layers(document) -> None:
    """Optional content groups keep working; only their names are replaced."""
    for number, xref in enumerate(sorted(document.get_ocgs() or {}), start=1):
        document.xref_set_key(xref, "Name", f"(Layer {number})")


def _drop_catalog_attachments(document) -> None:
    catalog = document.pdf_catalog()
    for key in ("AcroForm", "PageMode", "PageLabels"):
        kind, _value = document.xref_get_key(catalog, key)
        if kind != "null":
            document.xref_set_key(catalog, key, "null")
    kind, value = document.xref_get_key(catalog, "Names")
    if kind != "xref":
        return
    names_xref = int(value.replace("0 R", "").strip())
    embedded_kind, _embedded = document.xref_get_key(names_xref, "EmbeddedFiles")
    if embedded_kind != "null":
        document.xref_set_key(names_xref, "EmbeddedFiles", "null")


def redact_text_placeholders(document, items, label, category_order: list[str] | None = None) -> PlaceholderStats:
    """Delete each hit's rectangles and write one single-line placeholder.

    ``items`` are ``(page_index, rects, category)`` or
    ``(page_index, rects, category, text)``. Every rectangle is deleted.
    The label sits on one segment (see ``placeholder_rect``). The other
    segments of a cross-line hit stay blank. ``resolve`` runs once per hit.
    """
    plan = ShortPlan(category_order)
    stats = PlaceholderStats()
    by_page: dict[int, list[tuple]] = {}
    page_total = document.page_count
    for item in items:
        page_index, rects, category = item[0], item[1], item[2]
        text = item[3] if len(item) > 3 else ""
        if page_index < 0 or page_index >= page_total or not rects:
            continue
        by_page.setdefault(page_index, []).append((rects, category, text))
    for page_index, jobs in by_page.items():
        page = document[page_index]
        fills: list[tuple] = []
        placed = False
        glyphs = _GlyphIndex(_page_glyphs(page))
        # Images and vector paths are not deleted, only covered by the white
        # fill; a box that meets one keeps its whole height (M1, PR #40).
        under = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
        under += [pymupdf.Rect(path["rect"]) for path in page.get_drawings()]
        trimmed: list[tuple] = []
        for rects, category, text in jobs:
            usable = []
            for rect in rects:
                box = pymupdf.Rect(rect)
                if box.is_empty or box.width <= 0 or box.height <= 0:
                    continue
                if any(box.intersects(shape) for shape in under):
                    cut = box
                else:
                    cut = _redaction_box(box, glyphs)
                page.add_redact_annot(cut, fill=(1, 1, 1), cross_out=False)
                if cut != box:
                    trimmed.append((box, _hit_glyphs(box, glyphs)))
                usable.append(box)
                placed = True
            if not usable:
                continue
            full = label(category)
            code, title = plan.resolve(category)
            anchor = placeholder_rect(usable, full, code, text) or usable[0]
            fills.append((anchor, full, code, title))
        if not placed:
            continue
        # images=0 keeps image xobjects intact so a later OCR pass can see them.
        page.apply_redactions(images=0, graphics=0, text=0)
        if trimmed:
            # A trimmed box that left a glyph of its own line behind is redone
            # with the whole box: never keep hit text to save a neighbour.
            _redo_trimmed(page, trimmed)
        for box, full, code, title in fills:
            kind = _write_choice(page, box, full, code)
            if kind == "short":
                stats.short_count += 1
                stats.used.append((code, title, full))
            elif kind == "blank":
                stats.blank_count += 1
    return stats


# MuPDF removes every glyph whose box touches a redaction rectangle. A hit's
# rectangle is its glyph boxes from rawdict (ascender to descender; the
# height depends on the font: Helvetica about 1.37 × the font size, Times
# 1.334, Courier 1.249, the built-in CJK font 1.200), so with lines closer than that it also touched the
# line above or below and removed it (#38). The redaction box is trimmed
# vertically to stay clear of other lines' glyphs; the placeholder still
# uses the whole box.
_CORE = 0.15  # the trimmed box must keep this inner part of the hit's height
_MIN_CORE_OVERLAP = 0.3


def _page_glyphs(page) -> list[tuple[pymupdf.Rect, int]]:
    """(glyph box, line number) for every visible character of the page."""
    out: list[tuple[pymupdf.Rect, int]] = []
    line_no = 0
    for block in page.get_text("rawdict").get("blocks", ()):
        for line in block.get("lines", ()):
            line_no += 1
            for span in line.get("spans", ()):
                for ch in span.get("chars", ()):
                    if not (ch.get("c") or "").strip():
                        continue
                    box = pymupdf.Rect(ch["bbox"])
                    if box.width > 0 and box.height > 0:
                        out.append((box, line_no))
    return out


class _GlyphIndex:
    """The page's glyphs on a coarse grid, so a box only looks at the glyphs
    near it instead of every glyph of the page (S1, PR #40)."""

    STEP_Y = 4.0
    STEP_X = 24.0

    def __init__(self, glyphs):
        self.items = list(glyphs)
        self.cells: dict[tuple[int, int], list[int]] = {}
        for index, (glyph, _line) in enumerate(self.items):
            for cell in self._cells(glyph, 0):
                self.cells.setdefault(cell, []).append(index)

    def _cells(self, box, pad):
        for ky in range(int(box.y0 // self.STEP_Y) - pad, int(box.y1 // self.STEP_Y) + pad + 1):
            for kx in range(int(box.x0 // self.STEP_X) - pad, int(box.x1 // self.STEP_X) + pad + 1):
                yield ky, kx

    def near(self, box: pymupdf.Rect):
        """Each glyph that may meet ``box`` (one cell of slack), once."""
        found: set[int] = set()
        for cell in self._cells(box, 1):
            found.update(self.cells.get(cell, ()))
        return [self.items[index] for index in sorted(found)]


def _near(glyphs, box):
    return glyphs.near(box) if isinstance(glyphs, _GlyphIndex) else glyphs


def _centre_in(glyph: pymupdf.Rect, box: pymupdf.Rect) -> bool:
    cx = (glyph.x0 + glyph.x1) / 2
    cy = (glyph.y0 + glyph.y1) / 2
    return box.x0 <= cx <= box.x1 and box.y0 <= cy <= box.y1


def _own_line(box: pymupdf.Rect, glyphs) -> int | None:
    """The rawdict line the hit box was built from: the line of the glyph
    inside it whose top and bottom match the box best."""
    best, score = None, None
    for glyph, line_no in _near(glyphs, box):
        if not _centre_in(glyph, box):
            continue
        diff = abs(glyph.y0 - box.y0) + abs(glyph.y1 - box.y1)
        if score is None or diff < score:
            best, score = line_no, diff
    return best


def _redaction_box(box: pymupdf.Rect, glyphs) -> pymupdf.Rect:
    """``box`` trimmed so it touches no glyph of another line, or ``box``
    itself when trimming would not leave enough of the hit's height."""
    own = _own_line(box, glyphs)
    if own is None:
        return box
    y0, y1 = box.y0, box.y1
    mid = (box.y0 + box.y1) / 2
    for glyph, line_no in _near(glyphs, box):
        if line_no == own:
            continue
        if min(glyph.x1, box.x1) <= max(glyph.x0, box.x0):
            continue
        if min(glyph.y1, y1) <= max(glyph.y0, y0):
            continue
        if (glyph.y0 + glyph.y1) / 2 >= mid:
            y1 = min(y1, glyph.y0)
        else:
            y0 = max(y0, glyph.y1)
    if (y0, y1) == (box.y0, box.y1):
        return box
    core0 = box.y0 + _CORE * box.height
    core1 = box.y1 - _CORE * box.height
    if min(y1, core1) - max(y0, core0) < _MIN_CORE_OVERLAP:
        return box
    return pymupdf.Rect(box.x0, y0, box.x1, y1)


def _hit_glyphs(box: pymupdf.Rect, glyphs) -> list[pymupdf.Rect]:
    """Glyph boxes of the hit itself: its own line, centre inside the box."""
    own = _own_line(box, glyphs)
    return [glyph for glyph, line_no in _near(glyphs, box) if line_no == own and _centre_in(glyph, box)]


def _same_box(a: pymupdf.Rect, b: pymupdf.Rect, tol: float = 0.25) -> bool:
    return all(abs(u - v) <= tol for u, v in zip(a, b))


def _redo_trimmed(page, trimmed) -> None:
    again = []
    remaining = _GlyphIndex(_page_glyphs(page))
    for box, hit_glyphs in trimmed:
        if any(_same_box(glyph, mine) for mine in hit_glyphs for glyph, _line in remaining.near(mine)):
            again.append(box)
    for box in again:
        page.add_redact_annot(box, fill=(1, 1, 1), cross_out=False)
    if again:
        page.apply_redactions(images=0, graphics=0, text=0)


def _write_choice(page, rect, full: str, short: str) -> str:
    """Full label, then short code, then blank. Never a clipped string."""
    for kind, text in (("full", full), ("short", short)):
        if not text:
            continue
        size = fitted_fontsize(rect, text)
        if size is None:
            continue
        if _commit_textbox(page, rect, text, size, _font_for(text)):
            return kind
    return "blank"


def fill_placeholder(page, rect, text: str, *, min_size: float = MIN_PLACEHOLDER_SIZE) -> bool:
    """Write ``text`` on one line inside ``rect``, or write nothing.

    Original page text must already have been deleted (apply_redactions).
    The size starts near the box height and stops at ``min_size``. A box that
    cannot hold the whole string is left empty — never a shortened label.
    """
    box = pymupdf.Rect(rect)
    size = fitted_fontsize(box, text, min_size=min_size)
    if size is None:
        return False
    return _commit_textbox(page, box, text, size, _font_for(text))


def _commit_textbox(page, rect, text: str, fontsize: float, fontname: str) -> bool:
    shape = page.new_shape()
    leftover = shape.insert_textbox(
        rect,
        text,
        fontsize=fontsize,
        fontname=fontname,
        color=(0, 0, 0),
        align=pymupdf.TEXT_ALIGN_LEFT,
    )
    if leftover < 0:
        return False
    shape.commit()
    return True


def _font_runs(text: str):
    """Split into helv and china-s runs so ASCII keeps a proportional advance.

    A CJK font draws every character one em wide. pdftotext then treats the
    gaps inside ``[SP]`` as columns and reads the line out of order.
    """
    if not text:
        return
    font = _font_for(text[0])
    chunk = [text[0]]
    for ch in text[1:]:
        nxt = _font_for(ch)
        if nxt == font:
            chunk.append(ch)
            continue
        yield font, "".join(chunk)
        font = nxt
        chunk = [ch]
    yield font, "".join(chunk)


def _insert_mixed(page, x: float, y: float, text: str, fontsize: float) -> None:
    cursor = float(x)
    for font, chunk in _font_runs(text):
        page.insert_text((cursor, y), chunk, fontname=font, fontsize=fontsize, color=(0, 0, 0))
        if font == "helv":
            cursor += pymupdf.get_text_length(chunk, fontname="helv", fontsize=fontsize)
        else:
            cursor += len(chunk) * fontsize


def append_legend(document, lines: list[str]) -> None:
    """Add one unrotated page that explains the short codes used in this file."""
    if not lines:
        return
    template = document[document.page_count - 1]
    width = float(template.rect.width)
    height = float(template.rect.height)
    page = document.new_page(width=width, height=height)
    _insert_mixed(page, 72, 72, LEGEND_TITLE, 16)
    y = 108.0
    limit = height - 80
    for line in lines:
        if y > limit:
            break
        _insert_mixed(page, 72, y, line, 12)
        y += 22
    footer_y = min(max(y + 28, height - 56), height - 28)
    _insert_mixed(page, 72, footer_y, LEGEND_FOOTER, 11)


def find_hit_rects(page, hit: Hit, textpage) -> list[tuple[float, float, float, float]]:
    """Rectangles for this hit only, one per text line it covers.

    Offsets into the textpage's plain text select this hit. A line that matches
    character for character uses those glyphs. A line that does not is placed
    only from that line's own rawdict glyphs, and only when the plain text and
    the glyph string contain the fragment the same number of times. A cross-line
    hit returns one rectangle per line, including a one-character fragment.

    Those boxes are already in the space ``add_redact_annot`` and
    ``insert_textbox`` use. Extraction and redaction annotations temporarily
    clear ``/Rotate``, and ``insert_textbox`` adds the CropBox origin itself.
    Multiplying by ``page.derotation_matrix`` would move the placeholder off
    the deleted glyphs.
    """
    located = locate_text_hits(page, [hit], textpage)
    return located[0] if located else []


@dataclass
class _Aligned:
    """One plain-text line and, when known, the rawdict line it belongs to."""

    start: int
    end: int
    text: str
    entries: list | None
    bbox: tuple | None
    pieces: list | None = None


def locate_text_hits(page, hits, textpage=None) -> list[list[tuple[float, float, float, float]]]:
    """One rectangle list per hit. Empty means that hit is unlocated.

    Hits on this page whose rectangles are the same, or overlap by more than
    half the smaller box, are all returned empty so each one is counted.
    """
    if textpage is None:
        textpage = page_textpage(page)
    text = page.get_text("text", textpage=textpage)
    raw = page.get_text("rawdict", textpage=textpage)
    aligned = _align_lines(text, raw)
    groups: list[list[tuple[float, float, float, float]]] = []
    for hit in hits:
        rects = _rects_for_hit(hit, aligned)
        groups.append(
            [
                (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                for rect in rects
            ]
        )
    return _without_shared_rects(groups)


def _plain_lines(text: str) -> list[tuple[int, int, str]]:
    """Lines of ``get_text("text")``. Offsets exclude the newline itself.

    A trailing newline does not add an empty line. A blank line stays, with
    equal start and end, so later lines keep the offsets ``find_hits`` used.
    """
    lines: list[tuple[int, int, str]] = []
    start = 0
    for index, ch in enumerate(text):
        if ch != "\n":
            continue
        lines.append((start, index, text[start:index]))
        start = index + 1
    if start < len(text):
        lines.append((start, len(text), text[start:]))
    elif not lines:
        lines.append((0, 0, ""))
    return lines


def _raw_lines(raw: dict):
    """Yield ``(pieces, line_bbox, text)`` for text lines that have glyphs.

    ``line_bbox`` is the rawdict line box, not the union of whatever glyphs
    remain. A line whose characters were dropped still keeps that box, so a
    later search can clip to the original line.
    """
    for block in raw.get("blocks") or []:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines") or []:
            pieces = []
            for span in line.get("spans") or []:
                for ch in span.get("chars") or []:
                    value = ch.get("c") or ""
                    bbox = ch.get("bbox")
                    if not value or not bbox or len(bbox) < 4:
                        continue
                    pieces.append(
                        (
                            value,
                            (
                                float(bbox[0]),
                                float(bbox[1]),
                                float(bbox[2]),
                                float(bbox[3]),
                            ),
                        )
                    )
            if not pieces:
                continue
            text = "".join(value for value, _bbox in pieces)
            raw_box = line.get("bbox")
            if raw_box and len(raw_box) >= 4:
                bbox = tuple(float(value) for value in raw_box[:4])
            else:
                bbox = (
                    min(item[1][0] for item in pieces),
                    min(item[1][1] for item in pieces),
                    max(item[1][2] for item in pieces),
                    max(item[1][3] for item in pieces),
                )
            yield pieces, bbox, text


def _is_subsequence(needle: str, hay: str) -> bool:
    pos = 0
    for ch in hay:
        if ch == needle[pos]:
            pos += 1
            if pos == len(needle):
                return True
    return False


def _line_similarity(plain: str, raw: str) -> int:
    """How safely ``raw`` can stand in for ``plain``. Zero means do not pair.

    The whole shorter string must be a prefix, a substring, or (when it is at
    least two characters) a subsequence. A shared one-character prefix is not
    enough on its own unless one line actually starts with the other.
    """
    if not plain or not raw:
        return 0
    if plain == raw:
        return 1000 + len(plain)
    if plain.startswith(raw) or raw.startswith(plain):
        return 800 + min(len(plain), len(raw))
    if plain in raw or raw in plain:
        return 600 + min(len(plain), len(raw))
    shorter, longer = (plain, raw) if len(plain) <= len(raw) else (raw, plain)
    if len(shorter) >= 2 and _is_subsequence(shorter, longer):
        return 300 + len(shorter)
    return 0


def _best_pairs(plain_lines: list[tuple[int, int, str]], raw_lines: list) -> dict[int, int]:
    """Pair plain lines with raw lines without crossing, highest total score.

    Pairs keep line order: if plain line i goes to raw line j, a later plain
    line can only go to a later raw line. A greedy best-score-first pick could
    swap two neighbouring lines and give each hit the other line's box.
    """
    n, m = len(plain_lines), len(raw_lines)
    score = [[_line_similarity(plain_lines[i][2], raw_lines[j][2]) for j in range(m)] for i in range(n)]
    # best[i][j]: highest total for plain[i:] and raw[j:].
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = score[i][j] + best[i + 1][j + 1] if score[i][j] > 0 else 0
            best[i][j] = max(take, best[i + 1][j], best[i][j + 1])
    pairing: dict[int, int] = {}
    i = j = 0
    while i < n and j < m:
        if score[i][j] > 0 and best[i][j] == score[i][j] + best[i + 1][j + 1]:
            pairing[i] = j
            i += 1
            j += 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return pairing


def _entries_for_line(line_text: str, pieces, line_id: int, base: int):
    entries = []
    pos = 0
    for value, bbox in pieces:
        if not line_text.startswith(value, pos):
            return None
        entries.append((base + pos, base + pos + len(value), bbox, line_id))
        pos += len(value)
    if pos != len(line_text):
        return None
    return entries


def _align_lines(text: str, raw: dict) -> list[_Aligned]:
    """Pair plain lines with rawdict lines. A bad line does not move the others.

    Equal lines map character by character. A line that does not match keeps
    its rawdict box when one can be paired, and has no box otherwise. Nothing
    is searched across the whole page.
    """
    plain = _plain_lines(text)
    raw_lines = list(_raw_lines(raw))
    matcher = difflib.SequenceMatcher(
        a=[item[2] for item in plain],
        b=[item[2] for item in raw_lines],
        autojunk=False,
    )
    aligned: list[_Aligned] = []
    line_id = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            continue
        if tag == "equal":
            for offset, (start, end, line_text) in enumerate(plain[i1:i2]):
                pieces, bbox, _raw_text = raw_lines[j1 + offset]
                entries = _entries_for_line(line_text, pieces, line_id, start)
                aligned.append(_Aligned(start, end, line_text, entries, bbox, pieces))
                line_id += 1
            continue
        if tag == "delete":
            for start, end, line_text in plain[i1:i2]:
                aligned.append(_Aligned(start, end, line_text, None, None))
                line_id += 1
            continue
        plain_slice = plain[i1:i2]
        raw_slice = raw_lines[j1:j2]
        pairing = _best_pairs(plain_slice, raw_slice)
        for offset, (start, end, line_text) in enumerate(plain_slice):
            raw_index = pairing.get(offset)
            if raw_index is None:
                aligned.append(_Aligned(start, end, line_text, None, None))
            else:
                pieces, bbox, _raw_text = raw_slice[raw_index]
                aligned.append(_Aligned(start, end, line_text, None, bbox, pieces))
            line_id += 1
    return aligned


def _find_spans(haystack: str, needle: str) -> list[int]:
    """Start offsets of exact, case-sensitive occurrences. Overlaps count."""
    if not needle:
        return []
    spans = []
    pos = 0
    while True:
        found = haystack.find(needle, pos)
        if found < 0:
            break
        spans.append(found)
        pos = found + 1
    return spans


def _glyph_chars(pieces) -> list[tuple[str, tuple]]:
    chars = []
    for value, bbox in pieces or []:
        for ch in value:
            chars.append((ch, bbox))
    return chars


def _center_in_line(rect, bbox) -> bool:
    y0 = min(float(bbox[1]), float(bbox[3]))
    y1 = max(float(bbox[1]), float(bbox[3]))
    cy = (float(rect.y0) + float(rect.y1)) / 2.0
    return y0 <= cy <= y1


def _rects_from_glyphs(line: _Aligned, fragment: str, local_s: int) -> list:
    """Box of the k-th exact fragment in this line's glyphs, or nothing.

    ``k`` and the total are counted with ``_find_spans`` on the plain line and
    on the glyph string. The hit text is already the original casing, so the
    comparison stays exact. A different count, a missing k-th occurrence, or a
    box whose vertical center sits outside this line is not a location.
    """
    if line.bbox is None:
        return []
    chars = _glyph_chars(line.pieces)
    if not chars:
        return []
    glyph_text = "".join(ch for ch, _bbox in chars)
    plain_spans = _find_spans(line.text, fragment)
    glyph_spans = _find_spans(glyph_text, fragment)
    if len(plain_spans) != len(glyph_spans) or not plain_spans:
        return []
    k = sum(1 for start in plain_spans if start < local_s)
    if k >= len(plain_spans) or k >= len(glyph_spans) or plain_spans[k] != local_s:
        return []
    start = glyph_spans[k]
    end = start + len(fragment)
    if end > len(chars):
        return []
    rect = None
    for _ch, bbox in chars[start:end]:
        piece = pymupdf.Rect(bbox)
        rect = piece if rect is None else rect | piece
    if rect is None or rect.is_empty or rect.width <= 0 or rect.height <= 0:
        return []
    if not _center_in_line(rect, line.bbox):
        return []
    return [rect]


def _box_tuple(rect) -> tuple[float, float, float, float]:
    if isinstance(rect, (tuple, list)):
        return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _box_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _overlap_over_half(left, right) -> bool:
    """True when the boxes are the same or the overlap exceeds half the smaller one."""
    a = _box_tuple(left)
    b = _box_tuple(right)
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    smaller = min(_box_area(a), _box_area(b))
    return smaller > 0 and inter > smaller * 0.5


def _without_shared_rects(groups: list[list[tuple[float, float, float, float]]]):
    bad: set[int] = set()
    for i in range(len(groups)):
        if not groups[i]:
            continue
        for j in range(i + 1, len(groups)):
            if not groups[j]:
                continue
            if any(_overlap_over_half(a, b) for a in groups[i] for b in groups[j]):
                bad.add(i)
                bad.add(j)
    return [[] if index in bad else group for index, group in enumerate(groups)]


def _rects_for_hit(hit: Hit, aligned: list[_Aligned]) -> list:
    """Rectangles for this hit. Any fragment that cannot be placed drops the hit."""
    start, end = hit.start, hit.end
    if end <= start:
        return []
    collected = []
    for line in aligned:
        if line.end <= start or line.start >= end:
            continue
        if line.entries is not None:
            rects = _rects_covering(line.entries, start, end)
            if rects:
                collected.extend(rects)
                continue
        local_s = max(0, start - line.start)
        local_e = min(len(line.text), end - line.start)
        if local_e <= local_s:
            continue
        fragment = line.text[local_s:local_e]
        if not fragment.strip():
            continue
        found = _rects_from_glyphs(line, fragment, local_s)
        if not found:
            return []
        collected.extend(found)
    return collected


def _rects_covering(entries, start: int, end: int) -> list:
    if end <= start:
        return []
    grouped = []
    current_line = None
    current = None
    for cstart, cend, bbox, line_id in entries:
        if cend <= start or cstart >= end:
            continue
        rect = pymupdf.Rect(bbox)
        rect.normalize()
        if current is None or current_line != line_id:
            if current is not None and not current.is_empty:
                grouped.append(current)
            current = rect
            current_line = line_id
        else:
            current |= rect
    if current is not None and not current.is_empty:
        grouped.append(current)
    return [rect for rect in grouped if rect.width > 0 and rect.height > 0]


def _redact_images(document, engine: RedactionEngine) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen: set[int] = set()
    for page in document:
        images = page.get_images(full=True) or []
        masks = {item[1] for item in images if len(item) > 1 and item[1]}
        for item in images:
            xref = item[0]
            if not xref or xref in seen or xref in masks:
                continue
            seen.add(xref)
            try:
                image = _pixmap_image(document, xref)
            except (ValueError, RuntimeError):
                continue
            redacted, image_counts = redact_image(image, engine)
            stream = _png_bytes(redacted)
            page.replace_image(xref, stream=stream)
            merge_counts(counts, image_counts)
    return counts


def _pixmap_image(document, xref: int) -> Image.Image:
    pixmap = pymupdf.Pixmap(document, xref)
    if pixmap.colorspace is not None and pixmap.n - pixmap.alpha not in (1, 3):
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    data = pixmap.tobytes("png")
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
