"""Images, comments, revision authors and PDF metadata leave nothing readable.

Fictional data only: ABC-2026-001, Acme Pharma, Fictizumab, 星辰海.
Every check reads the written file back with a parser (zipfile/lxml for
Word, PyMuPDF for PDF) instead of trusting the writer's return value.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw, ImageFont

from doc_redactor.cli import redact_path
from doc_redactor.config import load_rules
from doc_redactor.scan import scan_path, write_scans
from doc_redactor.tesseract_cmd import TESSERACT_HINT, configure_tesseract, find_tesseract
from helpers import RULES, load_make_samples, needs_tesseract

NAME = "星辰海"
SECRETS = ("ABC-2026-001", "Acme Pharma", "Fictizumab", NAME)
needs_ocr = needs_tesseract(reason=TESSERACT_HINT)


# ---------------------------------------------------------------- helpers


def _engine():
    return load_rules(RULES)


def _gui_write(src: Path, outdir: Path):
    engine = _engine()
    scan = scan_path(src, engine)
    result = write_scans([scan], outdir, engine=engine)[0]
    return scan, result


def _cli_write(src: Path, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    redact_path(src, outdir, _engine())
    return outdir / f"{src.stem}.redacted{src.suffix}"


def _patch_zip(src: Path, dest: Path, edits=None, add=None) -> None:
    edits = edits or {}
    add = add or {}
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename in edits:
                data = edits[info.filename](data)
            zout.writestr(info, data)
        for name, data in add.items():
            zout.writestr(name, data)


def _sub(old: str, new: str, count: int = 1):
    def edit(data: bytes) -> bytes:
        text = data.decode("utf-8")
        assert old in text, old
        return text.replace(old, new, count).encode("utf-8")

    return edit


def _parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _folded(text: str) -> str:
    return "".join(text.split()).lower()


def _ocr(image: Image.Image) -> str:
    configure_tesseract()
    import pytesseract

    return pytesseract.image_to_string(image.convert("RGB"), lang="chi_sim+eng")


def _ocr_secrets(image: Image.Image) -> list[str]:
    text = _folded(_ocr(image))
    return [s for s in SECRETS if _folded(s) in text]


def _font(size: int = 40):
    return ImageFont.truetype(str(load_make_samples().font_path()), size)


def _card(mode: str = "RGB") -> Image.Image:
    """Four fictional secrets, one per line. RGBA puts the glyphs in alpha only."""
    if mode == "RGBA":
        image = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
        fill = (0, 0, 0, 255)
    else:
        image = Image.new("RGB", (900, 300), "white")
        fill = "black"
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(["Project ABC-2026-001", "Sponsor Acme Pharma", "Drug Fictizumab"]):
        draw.text((30, 15 + index * 90), line, font=_font(48), fill=fill)
    return image


# ---------------------------------------------------------------- Word: comments, authors, rsid

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# Revision authors become "Author" on every write (#33), hit or not; this
# neutral name is not a rule hit and still must not stay.
REVISOR = "Reviewer A"
REVISION = (
    '<w:p w:rsidR="00A1B2C3" w:rsidRDefault="00A1B2C3" w:rsidP="00A1B2C3">'
    '<w:r w:rsidR="00A1B2C3"><w:t xml:space="preserve">Status </w:t></w:r>'
    f'<w:ins w:id="901" w:author="{REVISOR}" w:date="2026-10-01T10:00:00Z"><w:r><w:t>Acme Pharma</w:t></w:r></w:ins>'
    f'<w:del w:id="902" w:author="{REVISOR}" w:date="2026-10-01T10:00:00Z"><w:r><w:delText>Fictizumab</w:delText></w:r></w:del>'
    "</w:p>"
)
CX_CT = (
    '<Override PartName="/word/commentsExtended.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"/>'
    '<Override PartName="/word/commentsIds.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml"/>'
    '<Override PartName="/word/commentsExtensible.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtensible+xml"/>'
    '<Override PartName="/word/people.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml"/>'
)
CX_RELS = (
    '<Relationship Id="rIdCx9" Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended" '
    'Target="commentsExtended.xml"/>'
    '<Relationship Id="rIdCi9" Type="http://schemas.microsoft.com/office/2016/09/relationships/commentsIds" '
    'Target="commentsIds.xml"/>'
    '<Relationship Id="rIdCe9" Type="http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible" '
    'Target="commentsExtensible.xml"/>'
    '<Relationship Id="rIdPe9" Type="http://schemas.microsoft.com/office/2011/relationships/people" '
    'Target="people.xml"/>'
)
W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'
CX_PARTS = {
    "word/commentsExtended.xml": f'<?xml version="1.0" encoding="UTF-8"?><w15:commentsEx {W15}>'
    '<w15:commentEx w15:paraId="1A2B3C4D" w15:done="0"/></w15:commentsEx>',
    "word/commentsIds.xml": '<?xml version="1.0" encoding="UTF-8"?>'
    '<w16cid:commentsIds xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid">'
    '<w16cid:commentId w16cid:paraId="1A2B3C4D" w16cid:durableId="11223344"/></w16cid:commentsIds>',
    "word/commentsExtensible.xml": '<?xml version="1.0" encoding="UTF-8"?>'
    '<w16cex:commentsExtensible xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex">'
    '<w16cex:commentExtensible w16cex:durableId="11223344" w16cex:dateUtc="2026-10-01T10:00:00Z"/>'
    "</w16cex:commentsExtensible>",
    "word/people.xml": f'<?xml version="1.0" encoding="UTF-8"?><w15:people {W15}>'
    f'<w15:person w15:author="{NAME}"><w15:presenceInfo w15:providerId="None" w15:userId="{NAME}"/>'
    "</w15:person></w15:people>",
}


def _comment_docx(tmp_path: Path) -> Path:
    document = Document()
    document.add_paragraph("Body mentions Acme Pharma here.")
    run = document.add_paragraph("Reviewed text").runs[0]
    document.add_comment(run, text="Acme Pharma 审核意见 Fictizumab", author=NAME, initials="星")
    run2 = document.add_paragraph("Second note").runs[0]
    document.add_comment(run2, text="ABC-2026-001", author=NAME, initials="星")
    document.add_paragraph("REVISION-SLOT")
    base = tmp_path / "base.docx"
    document.save(base)
    with zipfile.ZipFile(base) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    slot = re.search(r"<w:p>(?:(?!</w:p>).)*REVISION-SLOT(?:(?!</w:p>).)*</w:p>", body, re.S).group(0)
    src = tmp_path / "comments.docx"
    _patch_zip(
        base,
        src,
        edits={
            "word/document.xml": _sub(slot, REVISION),
            "[Content_Types].xml": _sub("</Types>", CX_CT + "</Types>"),
            "word/_rels/document.xml.rels": _sub("</Relationships>", CX_RELS + "</Relationships>"),
            "word/settings.xml": _sub(
                "</w:settings>", '<w:rsids><w:rsidRoot w:val="00A1B2C3"/><w:rsid w:val="00A1B2C3"/></w:rsids></w:settings>'
            ),
        },
        add=CX_PARTS,
    )
    return src


def _assert_comments_gone(path: Path) -> None:
    parts = _parts(path)
    names = set(parts)
    for gone in ("word/comments.xml", *CX_PARTS):
        assert gone not in names, gone
    for name, data in parts.items():
        if not name.endswith((".xml", ".rels")):
            continue
        text = data.decode("utf-8")
        assert NAME not in text, name
        assert "comments" not in text.lower() or name == "docProps/app.xml", name
        assert "people.xml" not in text, name
        assert not re.search(r"w:rsid\w*=", text), name
        assert "<w:rsids" not in text, name
    body = parts["word/document.xml"].decode("utf-8")
    for tag in ("commentRangeStart", "commentRangeEnd", "commentReference"):
        assert tag not in body
    # The revision marks stay; their text is replaced and the author is "Author".
    assert REVISOR not in body
    assert body.count('w:author="Author"') == 2
    for secret in SECRETS:
        assert secret not in body
    reopened = Document(str(path))
    assert any("Status" in p.text for p in reopened.paragraphs)


def test_docx_comments_and_authors_removed_gui(tmp_path: Path):
    src = _comment_docx(tmp_path)
    scan, result = _gui_write(src, tmp_path / "gui")
    assert result.error is None and result.dest is not None
    assert getattr(scan, "comment_count", None) == 2
    _assert_comments_gone(result.dest)


def test_docx_comments_and_authors_removed_cli(tmp_path: Path):
    src = _comment_docx(tmp_path)
    _assert_comments_gone(_cli_write(src, tmp_path / "cli"))


# ---------------------------------------------------------------- Word: GIF, SVG, vector, bad image

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="80"><text x="10" y="50">Acme Pharma ABC-2026-001</text></svg>'
SVG_BLIP = (
    '<a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
    '<asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" r:embed="rIdSvg9"/>'
    "</a:ext></a:extLst></a:blip>"
)


def _picture_docx(tmp_path: Path, image: Image.Image, fmt: str, name: str) -> Path:
    raw = tmp_path / f"card.{fmt.lower()}"
    image.save(raw, format=fmt)
    document = Document()
    document.add_paragraph("Sponsor Acme Pharma")
    document.add_picture(str(raw), width=Inches(4))
    src = tmp_path / name
    document.save(src)
    return src


def _media(path: Path) -> dict[str, bytes]:
    return {k: v for k, v in _parts(path).items() if k.startswith("word/media/")}


@needs_ocr
@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_docx_other_raster_images_are_painted(tmp_path: Path, fmt: str):
    src = _picture_docx(tmp_path, _card(), fmt, f"pic-{fmt}.docx")
    (original,) = _media(src).values()
    assert _ocr_secrets(Image.open(io.BytesIO(original))) == ["ABC-2026-001", "Acme Pharma", "Fictizumab"]
    scan, result = _gui_write(src, tmp_path / "gui")
    assert any(h.unit.startswith("media:") for h in scan.hits)
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        (payload,) = _media(dest).values()
        image = Image.open(io.BytesIO(payload))
        assert image.format == fmt
        assert _ocr_secrets(image) == []





@needs_ocr
def test_docx_svg_is_dropped_and_raster_fallback_kept(tmp_path: Path):
    base = _picture_docx(tmp_path, Image.new("RGB", (300, 60), "white"), "PNG", "svg-base.docx")
    body = zipfile.ZipFile(base).read("word/document.xml").decode("utf-8")
    blip = re.search(r"<a:blip [^>]*/>", body).group(0)
    src = tmp_path / "svg.docx"
    _patch_zip(
        base,
        src,
        edits={
            "word/document.xml": _sub(blip, blip[:-2] + ">" + SVG_BLIP),
            "word/_rels/document.xml.rels": _sub(
                "</Relationships>",
                '<Relationship Id="rIdSvg9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="media/image9.svg"/></Relationships>',
            ),
            "[Content_Types].xml": _sub("</Types>", '<Default Extension="svg" ContentType="image/svg+xml"/></Types>'),
        },
        add={"word/media/image9.svg": SVG},
    )
    scan, result = _gui_write(src, tmp_path / "gui")
    assert result.error is None, result.error
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        parts = _parts(dest)
        assert not [n for n in parts if n.lower().endswith(".svg")]
        for name, data in parts.items():
            if name.endswith((".xml", ".rels")):
                text = data.decode("utf-8")
                assert "svgBlip" not in text and "image9.svg" not in text, name
                assert "Acme Pharma" not in text and "ABC-2026-001" not in text, name
        # The PNG the picture falls back to is still there and referenced.
        assert [n for n in parts if n.endswith(".png")]
        assert re.search(r'<a:blip [^>]*r:embed="rId\d+"', parts["word/document.xml"].decode("utf-8"))
        Document(str(dest))


def _emf_docx(tmp_path: Path) -> Path:
    base = _picture_docx(tmp_path, Image.new("RGB", (300, 60), "white"), "PNG", "emf-base.docx")
    (png_name,) = _media(base)
    src = tmp_path / "emf.docx"
    # A picture whose only blip is an EMF (what Word stores for pasted charts).
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename
            if name == png_name:
                name = name[: -len(".png")] + ".emf"
                data = b"\x01\x00\x00\x00" + b"Acme Pharma".ljust(80, b"\x00")
            elif name == "word/_rels/document.xml.rels":
                data = data.replace(png_name.split("/")[-1].encode(), Path(png_name).with_suffix(".emf").name.encode())
            elif name == "[Content_Types].xml":
                data = data.replace(b"</Types>", b'<Default Extension="emf" ContentType="image/x-emf"/></Types>')
            zout.writestr(name, data)
    return src


def _bad_png_docx(tmp_path: Path) -> Path:
    base = _picture_docx(tmp_path, Image.new("RGB", (300, 60), "white"), "PNG", "bad-base.docx")
    (png_name,) = _media(base)
    src = tmp_path / "bad.docx"
    _patch_zip(base, src, edits={png_name: lambda _d: b"\x89PNG\r\n\x1a\nbroken"})
    return src


def _assert_blocked(src: Path, tmp_path: Path, words: tuple[str, ...]) -> None:
    scan, result = _gui_write(src, tmp_path / "gui")
    assert scan.status != "nohit"
    reason = getattr(scan, "blocked_reason", None)
    assert reason and all(w in reason for w in words), reason
    assert result.dest is None and result.error == reason
    assert not list((tmp_path / "gui").glob("*.redacted.*"))
    from doc_redactor.image_redactor import WriteBlocked

    with pytest.raises(WriteBlocked) as caught:
        _cli_write(src, tmp_path / "cli")
    assert all(w in str(caught.value) for w in words)
    assert not list((tmp_path / "cli").glob("*.redacted.*"))


def test_docx_vector_image_blocks_the_write(tmp_path: Path):
    _assert_blocked(_emf_docx(tmp_path), tmp_path, ("矢量图", "未写出"))


def test_docx_undecodable_image_blocks_the_write(tmp_path: Path):
    _assert_blocked(_bad_png_docx(tmp_path), tmp_path, ("打不开", "未写出"))


def _no_tesseract(monkeypatch) -> None:
    import doc_redactor.tesseract_cmd as tc

    monkeypatch.setattr(tc, "find_tesseract", lambda: None)


def test_missing_tesseract_blocks_docx_with_images(tmp_path: Path, monkeypatch):
    _no_tesseract(monkeypatch)
    src = _picture_docx(tmp_path, _card(), "PNG", "ocr-missing.docx")
    _assert_blocked(src, tmp_path, ("Tesseract", "未写出"))


def test_missing_tesseract_does_not_block_docx_without_images(tmp_path: Path, monkeypatch):
    _no_tesseract(monkeypatch)
    document = Document()
    document.add_paragraph("Sponsor Acme Pharma")
    src = tmp_path / "plain.docx"
    document.save(src)
    scan, result = _gui_write(src, tmp_path / "gui")
    assert getattr(scan, "blocked_reason", None) is None and result.dest is not None


# ---------------------------------------------------------------- PDF


def _pdf_raw_text(path: Path) -> str:
    """Every object dictionary and every decoded stream, as text."""
    document = pymupdf.open(path)
    chunks = []
    for xref in range(1, document.xref_length()):
        try:
            chunks.append(document.xref_object(xref, compressed=False))
            if document.xref_is_stream(xref):
                chunks.append((document.xref_stream(xref) or b"").decode("latin-1"))
        except Exception:
            continue
    chunks.append(document.xref_object(-1))
    document.close()
    return "\n".join(chunks)


def _thumb_pdf(tmp_path: Path) -> tuple[Path, bytes]:
    buf = io.BytesIO()
    _card().resize((450, 150)).save(buf, format="JPEG", quality=90)
    thumb = buf.getvalue()
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Sponsor Acme Pharma", fontsize=12)
    xref = document.get_new_xref()
    document.update_object(
        xref, "<< /Type /XObject /Subtype /Image /Width 450 /Height 150 /ColorSpace /DeviceRGB /BitsPerComponent 8 >>"
    )
    document.update_stream(xref, thumb, compress=False)
    document.xref_set_key(xref, "Filter", "/DCTDecode")
    document.xref_set_key(page.xref, "Thumb", f"{xref} 0 R")
    src = tmp_path / "thumb.pdf"
    document.save(src)
    document.close()
    return src, thumb


def test_pdf_page_thumbnail_is_removed(tmp_path: Path):
    src, thumb = _thumb_pdf(tmp_path)
    _scan, result = _gui_write(src, tmp_path / "gui")
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        document = pymupdf.open(dest)
        for page in document:
            assert document.xref_get_key(page.xref, "Thumb")[0] == "null"
        document.close()
        assert thumb[200:1200] not in dest.read_bytes()


def _meta_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Sponsor Acme Pharma", fontsize=12)
    document.set_metadata({"title": "Acme Pharma", "author": NAME})
    info = int(document.xref_get_key(-1, "Info")[1].split()[0])
    document.xref_set_key(info, "Company", pymupdf.get_pdf_str("Acme Pharma"))
    document.xref_set_key(info, "SourceProject", pymupdf.get_pdf_str("ABC-2026-001"))
    document.xref_set_key(page.xref, "PieceInfo", "<< /AcmeApp << /Private (Acme Pharma Fictizumab) >> >>")
    document.xref_set_key(document.pdf_catalog(), "PieceInfo", "<< /AcmeApp << /Private (ABC-2026-001) >> >>")
    src = tmp_path / "meta.pdf"
    document.save(src)
    document.close()
    return src


def test_pdf_info_dictionary_and_pieceinfo_are_emptied(tmp_path: Path):
    src = _meta_pdf(tmp_path)
    _scan, result = _gui_write(src, tmp_path / "gui")
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        document = pymupdf.open(dest)
        kind, value = document.xref_get_key(-1, "Info")
        if kind == "xref":
            info = int(value.split()[0])
            keys = [k for k in document.xref_get_keys(info) if document.xref_get_key(info, k)[0] != "null"]
            assert keys == [], keys
        assert document.xref_get_key(document.pdf_catalog(), "PieceInfo")[0] == "null"
        for page in document:
            assert document.xref_get_key(page.xref, "PieceInfo")[0] == "null"
        document.close()
        raw = _pdf_raw_text(dest)
        for secret in ("Acme Pharma", "ABC-2026-001", "Fictizumab"):
            assert secret not in raw
        assert pymupdf.get_pdf_str(NAME) not in raw


def _smask_pdf(tmp_path: Path) -> Path:
    raw = tmp_path / "rgba.png"
    _card("RGBA").save(raw)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 760), "Sponsor Acme Pharma", fontsize=12)
    page.insert_image(pymupdf.Rect(72, 100, 522, 250), filename=str(raw))
    src = tmp_path / "smask.pdf"
    document.save(src)
    document.close()
    return src


def _rendered_secrets(path: Path, clip) -> list[str]:
    document = pymupdf.open(path)
    pix = document[0].get_pixmap(dpi=200, clip=clip)
    document.close()
    return _ocr_secrets(Image.open(io.BytesIO(pix.tobytes("png"))))


@needs_ocr
def test_pdf_transparent_image_is_composited_and_painted(tmp_path: Path):
    src = _smask_pdf(tmp_path)
    clip = pymupdf.Rect(60, 90, 540, 260)
    assert _rendered_secrets(src, clip) == ["ABC-2026-001", "Acme Pharma", "Fictizumab"]
    scan, result = _gui_write(src, tmp_path / "gui")
    assert sum(1 for h in scan.hits if h.unit.startswith("pdfimg:")) == 3
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        assert _rendered_secrets(dest, clip) == []
        document = pymupdf.open(dest)
        for xref, smask, *_ in document[0].get_images(full=True):
            pix = pymupdf.Pixmap(document, xref)
            if smask:
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(document, smask))
            assert _ocr_secrets(Image.open(io.BytesIO(pix.tobytes("png")))) == []
            if smask:
                mask = Image.open(io.BytesIO(pymupdf.Pixmap(document, smask).tobytes("png")))
                assert _ocr_secrets(mask.point(lambda v: 255 - v)) == []
        document.close()


def _inline_pdf(tmp_path: Path) -> Path:
    gray = _card().convert("L").resize((450, 150))
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Status normal", fontsize=12)
    xref = page.get_contents()[0]
    inline = b"q 450 0 0 150 72 542 cm BI /W 450 /H 150 /CS /G /BPC 8 ID " + gray.tobytes() + b" EI Q\n"
    document.update_stream(xref, document.xref_stream(xref) + b"\n" + inline)
    src = tmp_path / "inline.pdf"
    document.save(src)
    document.close()
    return src


def _inline_images(path: Path) -> list[Image.Image]:
    document = pymupdf.open(path)
    images = []
    for page in document:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 1 and block.get("image"):
                images.append(Image.open(io.BytesIO(block["image"])))
    document.close()
    return images


@needs_ocr
def test_pdf_inline_image_is_painted(tmp_path: Path):
    src = _inline_pdf(tmp_path)
    assert _ocr_secrets(_inline_images(src)[0]) == ["ABC-2026-001", "Acme Pharma", "Fictizumab"]
    scan, result = _gui_write(src, tmp_path / "gui")
    assert result.error is None, result.error
    assert sum(1 for h in scan.hits if h.unit.startswith("pdfinline:")) == 3
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        for image in _inline_images(dest):
            assert _ocr_secrets(image) == []
        assert _rendered_secrets(dest, pymupdf.Rect(60, 140, 540, 310)) == []


def test_missing_tesseract_blocks_pdf_with_images(tmp_path: Path, monkeypatch):
    _no_tesseract(monkeypatch)
    _assert_blocked(_smask_pdf(tmp_path), tmp_path, ("Tesseract", "未写出"))


def test_missing_tesseract_blocks_pdf_with_inline_image(tmp_path: Path, monkeypatch):
    _no_tesseract(monkeypatch)
    _assert_blocked(_inline_pdf(tmp_path), tmp_path, ("Tesseract", "未写出"))


# 没装 Tesseract 不写出时，要说清为什么不写出、怎么办（命令行和界面都看）。文案待前端定。
NO_OCR_WHY = "图里的字"
NO_OCR_FIX = ("装好 Tesseract", "TESSERACT_CMD")


def test_missing_tesseract_cli_says_why_and_what_to_do(tmp_path: Path, monkeypatch, capsys):
    from doc_redactor.cli import main

    _no_tesseract(monkeypatch)
    src = _picture_docx(tmp_path, _card(), "PNG", "ocr-missing.docx")
    code = main(["-r", str(RULES), "-o", str(tmp_path / "out"), str(src)])
    err = capsys.readouterr().err
    assert code != 0
    assert "ocr-missing.docx" in err and "未写出" in err
    assert NO_OCR_WHY in err, err
    assert all(word in err for word in NO_OCR_FIX), err
    assert not list((tmp_path / "out").glob("*.redacted.*"))


# ---------------------------------------------------------------- PDF: small indexed image under a larger soft mask

INDEXED_2X2 = "<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace [/Indexed /DeviceRGB 1 <ffffff000000>] /SMask {smask} 0 R >>"


def _indexed_mask_pdf(tmp_path: Path, colour: bytes = b"\x01\x01\x01\x01") -> Path:
    """A 2×2 indexed fill whose 900×300 soft mask carries the glyphs (all fictional)."""
    alpha = _card("RGBA").getchannel("A")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 760), "Status normal", fontsize=12)
    stub = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stub, "PNG")
    image = page.insert_image(pymupdf.Rect(72, 100, 522, 250), stream=stub.getvalue())
    smask = document.get_new_xref()
    document.update_object(
        smask,
        f"<< /Type /XObject /Subtype /Image /Width {alpha.width} /Height {alpha.height} "
        "/BitsPerComponent 8 /ColorSpace /DeviceGray >>",
    )
    document.update_stream(smask, alpha.tobytes())
    document.update_object(image, INDEXED_2X2.format(smask=smask))
    document.update_stream(image, colour)
    src = tmp_path / "indexed-mask.pdf"
    document.save(src)
    document.close()
    return src


def test_pdf_small_indexed_image_with_larger_mask_is_read(tmp_path: Path):
    from doc_redactor.pdf_redactor import pdf_xref_image

    document = pymupdf.open(_indexed_mask_pdf(tmp_path))
    ((xref, smask, width, height, *_),) = document[0].get_images(full=True)
    assert (width, height) == (2, 2) and smask
    image = pdf_xref_image(document, xref, smask)
    assert image.size == (900, 300)
    document.close()


def test_pdf_undecodable_masked_image_still_blocks(tmp_path: Path):
    from doc_redactor.image_redactor import WriteBlocked
    from doc_redactor.pdf_redactor import pdf_xref_image

    document = pymupdf.open(_indexed_mask_pdf(tmp_path))
    ((xref, smask, *_),) = document[0].get_images(full=True)
    broken = INDEXED_2X2.format(smask=smask).replace("[/Indexed /DeviceRGB 1 <ffffff000000>]", "/NoSuchSpace")
    document.update_object(xref, broken)
    with pytest.raises(WriteBlocked) as caught:
        pdf_xref_image(document, xref, smask)
    assert "打不开" in str(caught.value)
    document.close()


@needs_ocr
def test_pdf_small_indexed_image_with_larger_mask_is_written_and_painted(tmp_path: Path):
    src = _indexed_mask_pdf(tmp_path)
    clip = pymupdf.Rect(60, 90, 540, 260)
    assert _rendered_secrets(src, clip) == ["ABC-2026-001", "Acme Pharma", "Fictizumab"]
    scan, result = _gui_write(src, tmp_path / "gui")
    assert result.error is None, result.error
    assert sum(1 for h in scan.hits if h.unit.startswith("pdfimg:")) == 3
    for dest in (result.dest, _cli_write(src, tmp_path / "cli")):
        assert _rendered_secrets(dest, clip) == []
