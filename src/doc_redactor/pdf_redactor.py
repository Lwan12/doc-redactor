"""Redact PDF text by deletion, and raster images by OCR plus black boxes."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

from doc_redactor.engine import Hit, RedactionEngine, merge_counts
from doc_redactor.image_redactor import redact_image

MIN_PLACEHOLDER_SIZE = 4.0


def redact_pdf(src: Path, dest: Path, engine: RedactionEngine) -> dict[str, int]:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")
    document = pymupdf.open(src)
    try:
        if document.is_encrypted:
            raise ValueError(f"不支持加密的 PDF: {src.name}")
        counts: dict[str, int] = {}
        for page in document:
            merge_counts(counts, _redact_page_text(page, engine))
        merge_counts(counts, _redact_images(document, engine))
        clear_pdf_hidden(document)
        dest.parent.mkdir(parents=True, exist_ok=True)
        document.save(dest, garbage=4, clean=True)
    finally:
        document.close()
    return counts


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


def _redact_page_text(page, engine: RedactionEngine) -> dict[str, int]:
    flags = pymupdf.TEXTFLAGS_TEXT
    textpage = page.get_textpage(flags=flags)
    text = page.get_text("text", textpage=textpage)
    hits = engine.find_hits(text)
    if not hits:
        return {}
    matched: list[Hit] = []
    fills: list[tuple[object, str]] = []
    placed = False
    for hit in hits:
        usable = _hit_rects(page, hit, textpage)
        if not usable:
            continue
        for rect in usable:
            page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
        # One placeholder per hit, on the first line of a cross-line match.
        fills.append((usable[0], engine.label(hit.category)))
        matched.append(hit)
        placed = True
    if placed:
        # images=0 keeps image xobjects intact so the later OCR pass can see them.
        page.apply_redactions(images=0, graphics=0, text=0)
        for rect, label in fills:
            fill_placeholder(page, rect, label)
    return _category_counts(matched)


def fill_placeholder(page, rect, text: str, *, min_size: float = MIN_PLACEHOLDER_SIZE) -> None:
    """Write ``text`` into ``rect``, shrinking then truncating so it stays inside.

    Original page text must already have been deleted (apply_redactions).
    A box too small for even one glyph is left empty — never the source word.
    """
    box = pymupdf.Rect(rect)
    if not text or box.is_empty or box.width <= 0 or box.height <= 0:
        return
    fontsize = min(11.0, max(min_size, float(box.height) * 0.9))
    while fontsize >= min_size - 1e-6:
        if _commit_textbox(page, box, text, fontsize):
            return
        fontsize -= 0.5
    shown = text
    while shown:
        if _commit_textbox(page, box, shown, min_size):
            return
        shown = shown[:-1]


def _commit_textbox(page, rect, text: str, fontsize: float) -> bool:
    shape = page.new_shape()
    leftover = shape.insert_textbox(
        rect,
        text,
        fontsize=fontsize,
        fontname="helv",
        color=(0, 0, 0),
        align=pymupdf.TEXT_ALIGN_LEFT,
    )
    if leftover < 0:
        return False
    shape.commit()
    return True


def _category_counts(hits: list[Hit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.category] = counts.get(hit.category, 0) + 1
    return counts


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
