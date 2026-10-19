"""Redact PDF text by deletion, and raster images by OCR plus black boxes.

PDF placeholders stay on one line. The full label shrinks down to 6pt.
If it still does not fit, the category's short code is used. If that does
not fit either, the box stays blank. A half-written label is never emitted.
"""

from __future__ import annotations

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
MSG_FIT_SHORT = "ⓘ 这个 PDF 有 {n} 处位置太窄，已换成短码，最后一页有对照说明。"
MSG_FIT_BLANK = "⚠ 另有 {m} 处太窄，连短码也放不下，只留了空白，原文已删除。"
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


def fit_notice(short_count: int, blank_count: int) -> str:
    if short_count <= 0:
        return ""
    text = MSG_FIT_SHORT.format(n=short_count)
    if blank_count > 0:
        text += "\n" + MSG_FIT_BLANK.format(m=blank_count)
    return text


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
        kind, _shown = placeholder_choice(rects[0], full, code)
        if kind == "short":
            short_count += 1
        elif kind == "blank":
            blank_count += 1
    return short_count, blank_count


def redact_pdf(src: Path, dest: Path, engine: RedactionEngine) -> RedactionCounts:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")
    document = pymupdf.open(src)
    try:
        if document.is_encrypted:
            raise ValueError(f"不支持加密的 PDF: {src.name}")
        counts: dict[str, int] = {}
        items: list[tuple[int, list, str]] = []
        page_total = document.page_count
        for index in range(page_total):
            page = document[index]
            flags = pymupdf.TEXTFLAGS_TEXT
            textpage = page.get_textpage(flags=flags)
            text = page.get_text("text", textpage=textpage)
            for hit in engine.find_hits(text):
                usable = _hit_rects(page, hit, textpage)
                if not usable:
                    continue
                items.append((index, usable, hit.category))
                counts[hit.category] = counts.get(hit.category, 0) + 1
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
    for name in list(document.embfile_names() or []):
        document.embfile_del(name)
    document.set_toc([])
    document.set_metadata({key: "" for key in _INFO_FIELDS})
    document.set_xml_metadata("")
    document.del_xml_metadata()
    _drop_catalog_attachments(document)


def _drop_catalog_attachments(document) -> None:
    catalog = document.pdf_catalog()
    for key in ("AcroForm", "PageMode"):
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

    ``items`` are ``(page_index, rects, category)``. Every rectangle is
    deleted. The label sits on the first one. A cross-line tail stays blank.
    """
    plan = ShortPlan(category_order)
    stats = PlaceholderStats()
    by_page: dict[int, list[tuple[list, str]]] = {}
    page_total = document.page_count
    for page_index, rects, category in items:
        if page_index < 0 or page_index >= page_total or not rects:
            continue
        by_page.setdefault(page_index, []).append((rects, category))
    for page_index, jobs in by_page.items():
        page = document[page_index]
        fills: list[tuple[object, str]] = []
        placed = False
        for rects, category in jobs:
            usable = []
            for rect in rects:
                box = pymupdf.Rect(rect)
                if box.is_empty or box.width <= 0 or box.height <= 0:
                    continue
                page.add_redact_annot(box, fill=(1, 1, 1), cross_out=False)
                usable.append(box)
                placed = True
            if usable:
                fills.append((usable[0], category))
        if not placed:
            continue
        # images=0 keeps image xobjects intact so a later OCR pass can see them.
        page.apply_redactions(images=0, graphics=0, text=0)
        for box, category in fills:
            full = label(category)
            code, title = plan.resolve(category)
            kind = _write_choice(page, box, full, code)
            if kind == "short":
                stats.short_count += 1
                stats.used.append((code, title, full))
            elif kind == "blank":
                stats.blank_count += 1
    return stats


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

    ``page.search_for`` returns every occurrence of the string on the page, so
    each hit of a repeated word used to carry every box. Offsets into the same
    textpage's plain text select this hit's glyphs. A cross-line hit returns
    one rectangle per line, including a one-character fragment.

    Those boxes are already in the space ``add_redact_annot`` and
    ``insert_textbox`` use. Extraction and redaction annotations temporarily
    clear ``/Rotate``, and ``insert_textbox`` adds the CropBox origin itself.
    Multiplying by ``page.derotation_matrix`` would move the placeholder off
    the deleted glyphs.
    """
    return [
        (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        for rect in _hit_rects(page, hit, textpage)
    ]


def _hit_rects(page, hit: Hit, textpage) -> list:
    if textpage is None:
        textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
    text = page.get_text("text", textpage=textpage)
    raw = page.get_text("rawdict", textpage=textpage)
    entries = _align_chars(text, raw)
    return _rects_covering(entries, hit.start, hit.end)


def _raw_lines(raw: dict):
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
            yield pieces


def _align_chars(text: str, raw: dict) -> list[tuple[int, int, tuple[float, float, float, float], int]]:
    """Map plain-text offsets to glyph boxes from the same textpage.

    ``get_text("text")`` inserts a newline after each line. Those newlines are
    not glyphs. Returns an empty list when the two views cannot be aligned,
    so a hit is skipped instead of painted on the wrong occurrence.
    """
    entries: list[tuple[int, int, tuple[float, float, float, float], int]] = []
    pos = 0
    line_id = 0
    for pieces in _raw_lines(raw):
        for value, bbox in pieces:
            if not text.startswith(value, pos):
                return []
            entries.append((pos, pos + len(value), bbox, line_id))
            pos += len(value)
        if pieces and not str(pieces[-1][0]).endswith("\n") and pos < len(text) and text[pos] == "\n":
            pos += 1
        line_id += 1
    if pos != len(text):
        return []
    return entries


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
