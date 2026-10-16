"""Redact PDF text by deletion, and raster images by OCR plus black boxes."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

from doc_redactor.engine import Hit, RedactionEngine, merge_counts
from doc_redactor.image_redactor import redact_image


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
    placed = False
    for hit in hits:
        rects = _search_hit(page, hit, textpage)
        usable = []
        for rect in rects:
            if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                continue
            # search_for is case-insensitive. Keep the rect only when the
            # visible text still contains this hit's exact characters.
            extracted = page.get_textbox(rect) or ""
            if hit.text not in extracted:
                continue
            usable.append(rect)
        if not usable:
            continue
        for rect in usable:
            # No replacement string: apply_redactions deletes the text.
            page.add_redact_annot(rect)
        matched.append(hit)
        placed = True
    if placed:
        # images=0 keeps image xobjects intact so the later OCR pass can see them.
        page.apply_redactions(images=0, graphics=0, text=0)
    return _category_counts(matched)


def _category_counts(hits: list[Hit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.category] = counts.get(hit.category, 0) + 1
    return counts


def find_hit_rects(page, hit: Hit, textpage) -> list[tuple[float, float, float, float]]:
    rects = []
    for rect in _search_hit(page, hit, textpage):
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            continue
        extracted = page.get_textbox(rect) or ""
        if hit.text not in extracted:
            continue
        rects.append((float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)))
    return rects


def _search_hit(page, hit: Hit, textpage) -> list:
    pieces = [hit.text]
    if "\n" in hit.text:
        pieces = [part.strip() for part in hit.text.splitlines() if part.strip()]
    rects = []
    for piece in pieces:
        if len(piece) < 2:
            continue
        found = page.search_for(piece, textpage=textpage)
        if not found:
            found = page.search_for(piece)
        rects.extend(found)
    return rects


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
