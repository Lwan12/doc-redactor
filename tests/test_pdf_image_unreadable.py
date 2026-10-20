"""「没法检查」要写出第几页第几张图，并分清是读图失败还是识别文字出错。

全部用编造数据：ABC-2026-001、Acme Pharma。
"""

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from doc_redactor import scan as scan_module
from doc_redactor.engine import RegexRule, build_engine
from doc_redactor.scan import scan_path



def _engine():
    return build_engine([RegexRule(name="PROJECT_ID", pattern=r"ABC-\d{4}-\d{3}", flags=0)], [])


def _png(text: str) -> bytes:
    image = Image.new("RGB", (360, 60), "white")
    ImageDraw.Draw(image).text((10, 20), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_with_broken_second_image(path: Path) -> None:
    """Page 1: one good image. Page 2: one good image, then one whose data is junk.

    图号沿用扫描结果里的全文件序号（与「第 N 页图 M · 图内」一致）。
    """
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 60), "Acme Pharma")
    page.insert_image(pymupdf.Rect(72, 100, 432, 160), stream=_png("Acme Pharma"))
    page = document.new_page()
    page.insert_image(pymupdf.Rect(72, 100, 432, 160), stream=_png("Acme Pharma 1"))
    page.insert_image(pymupdf.Rect(72, 200, 432, 260), stream=_png("Acme Pharma 2"))
    xref = page.get_images(full=True)[-1][0]
    # Keep the dictionary, swap the stream for bytes no decoder can read.
    document.update_stream(xref, b"\x00not-an-image\xff" * 8, compress=False)
    document.xref_set_key(xref, "Filter", "/JPXDecode")
    document.save(path)


def test_undecodable_image_names_page_and_index(tmp_path: Path):
    pdf = tmp_path / "broken.pdf"
    _pdf_with_broken_second_image(pdf)
    scan = scan_path(pdf, _engine())
    assert scan.blocked_reason == "第 2 页图 3 打不开，没法检查，未写出"


def test_ocr_error_is_not_reported_as_unopenable(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "ocr.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(72, 100, 432, 160), stream=_png("ABC-2026-001"))
    document.save(pdf)

    import pytesseract

    def boom(*_args, **_kwargs):
        raise pytesseract.TesseractError(1, "fake")

    monkeypatch.setattr(scan_module, "analyze_image_entities", boom)
    scan = scan_path(pdf, _engine())
    assert scan.blocked_reason == "第 1 页图 1 识别文字时出错，没法检查，未写出"
    assert not [hit for hit in scan.hits if hit.unit.startswith("pdfimg:")]


def test_good_images_still_scan(tmp_path: Path):
    pdf = tmp_path / "good.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(72, 100, 432, 160), stream=_png("Acme Pharma"))
    document.save(pdf)
    scan = scan_path(pdf, _engine())
    assert scan.blocked_reason is None


def _inline_image_ops(x: int, y: int) -> bytes:
    # 2×2 gray inline image, upright, 100×50 pt.
    return f"q 100 0 0 50 {x} {y} cm BI /W 2 /H 2 /CS /G /BPC 8 ID ".encode() + b"\x00\xff\xff\x00" + b" EI Q\n"


def test_second_inline_image_failing_names_its_own_index(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "inline.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 60), "Acme Pharma")
    xref = page.get_contents()[0]
    document.update_stream(xref, document.xref_stream(xref) + b"\n" + _inline_image_ops(72, 600) + _inline_image_ops(72, 400))
    document.save(pdf)

    from doc_redactor import pdf_redactor

    real_open = pdf_redactor.Image.open
    calls = {"n": 0}

    def flaky_open(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("fake broken inline image")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pdf_redactor.Image, "open", flaky_open)
    monkeypatch.setattr(scan_module, "analyze_image_entities", lambda *_a, **_k: [])
    scan = scan_path(pdf, _engine())
    assert scan.blocked_reason == "第 1 页图 2 打不开，没法检查，未写出"


def test_missing_language_data_keeps_install_hint(tmp_path: Path, monkeypatch):
    from doc_redactor.image_redactor import MSG_NO_OCR

    pdf = tmp_path / "lang.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(72, 100, 432, 160), stream=_png("ABC-2026-001"))
    document.save(pdf)

    import pytesseract

    def boom(*_args, **_kwargs):
        raise pytesseract.TesseractError(1, "Tesseract couldn't load any languages!")

    monkeypatch.setattr(scan_module, "analyze_image_entities", boom)
    scan = scan_path(pdf, _engine())
    assert scan.blocked_reason == MSG_NO_OCR.format(n=1)
