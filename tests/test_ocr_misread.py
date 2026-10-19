"""扫描件里的中文药名被 tesseract 认错一个字（编造药名「菲替珠单抗」认成「菲蔡珠单抗」）时，
图片里这处仍要涂黑；否则换一套 OCR 的 AI 照样读得出。全部编造数据。"""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from doc_redactor.config import RegexRule, WordlistRule
from doc_redactor.engine import build_engine

DRUG = "菲替珠单抗"


def _engine():
    return build_engine(
        [RegexRule(name="PROJECT_ID", pattern=r"(?<![A-Za-z0-9])ABC-\d{4}-\d{3}(?![A-Za-z0-9])", flags=0)],
        [
            WordlistRule(category="DRUG", terms=[DRUG, "Fictizumab"], case_sensitive=False),
            WordlistRule(category="SPONSOR", terms=["星辰海"], case_sensitive=False),
        ],
    )


def test_ocr_text_tolerates_one_misread_character():
    ocr = _engine().for_ocr()
    # presidio 把 OCR 的词用空格连起来；中文常常一字一词。
    assert [h.category for h in ocr.find_hits("药物 菲 蔡 珠 单 抗 研究")] == ["DRUG"]
    assert [h.category for h in ocr.find_hits("Drug: Fictlzumab")] == ["DRUG"]


def test_exact_text_and_short_terms_stay_exact():
    engine = _engine()
    assert engine.find_hits("药物菲蔡珠单抗") == []
    ocr = engine.for_ocr()
    # 短词（少于 4 个字）和编号不放宽，免得把普通字词涂掉。
    assert ocr.find_hits("星辰湖") == []
    assert ocr.find_hits("ABC-2026-00l") == []
    assert ocr.find_hits("药物菲蔡珠单片") == []


def _cjk_font() -> str | None:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    return next((path for path in candidates if Path(path).is_file()), None)


def _scan_page(font_path: str) -> bytes:
    # 和审阅样例同样的扫描件画法：白底黑字整行图片，34 像素字。
    image = Image.new("RGB", (1654, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 34)
    for row, line in enumerate(["Protocol ABC-2026-001", f"申办方星辰海制药  药物{DRUG}  研究者王小明"]):
        draw.text((60, 40 + row * 60), line, font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.skipif(shutil.which("tesseract") is None and sys.platform != "win32", reason="需要 tesseract")
def test_misread_drug_in_scanned_pdf_is_painted(tmp_path: Path):
    pytest.importorskip("pytesseract")
    from doc_redactor.pdf_redactor import redact_pdf
    from doc_redactor.tesseract_cmd import configure_tesseract

    if configure_tesseract() is None:
        pytest.skip("需要 tesseract")
    font = _cjk_font()
    if font is None:
        pytest.skip("需要中文字体")
    src = tmp_path / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(20, 40, 575, 161), stream=_scan_page(font))
    document.save(src)
    document.close()

    out = tmp_path / "scan.redacted.pdf"
    engine = _engine()
    counts = redact_pdf(src, out, engine)
    assert counts.get("DRUG") == 1

    import pytesseract

    result = pymupdf.open(out)
    xref = result[0].get_images()[0][0]
    image = Image.open(io.BytesIO(result.extract_image(xref)["image"]))
    text = pytesseract.image_to_string(image, lang="chi_sim+eng")
    result.close()
    flat = "".join(text.split())
    assert DRUG not in flat
    # 认错的近似词也不能留下：放宽后的 OCR 匹配读整张输出图，药名一处都找不到。
    assert [h for h in engine.for_ocr().find_hits(text) if h.category == "DRUG"] == []
    assert "星辰海" not in flat
