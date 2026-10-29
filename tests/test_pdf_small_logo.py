"""A one-word footer logo whose default OCR pass reads nothing.

The PDF writer still has to black it out. The word is fictional. The picture
is drawn here, not taken from a customer file.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from doc_redactor.cli import main
from doc_redactor.image_redactor import _to_rgb
from doc_redactor.pdf_redactor import pdf_xref_image
from doc_redactor.tesseract_cmd import configure_tesseract
from helpers import check_tesseract

WORD = "Quorin"
FONT = Path("/System/Library/Fonts/Supplemental/SnellRoundhand.ttc")
# Size measured on this font: default page segmentation reads nothing,
# and a 2x single-block pass reads the word.
FONT_SIZE = 82
CANVAS = (226, 150)


def _words(image: Image.Image, *, scale: int, psm: int | None) -> str:
    configure_tesseract()
    import pytesseract

    prepared = _to_rgb(image)
    if scale != 1:
        prepared = prepared.resize(
            (prepared.width * scale, prepared.height * scale),
            Image.Resampling.LANCZOS,
        )
    kwargs = {"lang": "chi_sim+eng", "output_type": pytesseract.Output.DICT}
    if psm is not None:
        kwargs["config"] = f"--psm {psm}"
    data = pytesseract.image_to_data(prepared, **kwargs)
    return " ".join(str(item) for item in data["text"] if str(item).strip())


def _logo() -> Image.Image:
    image = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), FONT_SIZE)
    box = draw.textbbox((0, 0), WORD, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    x = (CANVAS[0] - width) // 2 - box[0]
    y = (CANVAS[1] - height) // 2 - box[1]
    draw.text((x, y), WORD, font=font, fill=(190, 30, 40, 255))
    background = Image.new("RGB", CANVAS, (255, 255, 255))
    background.paste(image, mask=image.getchannel("A"))
    return background


def _embedded(path: Path) -> Image.Image:
    document = pymupdf.open(path)
    try:
        images = document[0].get_images(full=True)
        assert images, "pdf has no image"
        xref, smask = images[0][0], images[0][1] if len(images[0]) > 1 else 0
        return pdf_xref_image(document, xref, smask)
    finally:
        document.close()


def test_pdf_footer_logo_is_blacked_out_when_default_ocr_reads_nothing(tmp_path: Path) -> None:
    check_tesseract()
    if not FONT.is_file():
        pytest.skip("script font for this logo is not installed")
    logo = _logo()
    logo_path = tmp_path / "logo.png"
    logo.save(logo_path)

    source = tmp_path / "plan.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 400), f"Sponsor {WORD} remains in the body")
    page.insert_image(pymupdf.Rect(500, 730, 590, 780), filename=str(logo_path))
    document.save(source)
    document.close()

    embedded = _embedded(source)
    default = _words(embedded, scale=1, psm=None).lower()
    block = _words(embedded, scale=2, psm=6).lower()
    if WORD.lower() in default:
        pytest.skip("default OCR already reads this logo, so it is not the miss")
    if WORD.lower() not in block:
        pytest.skip("2x single-block OCR does not read this logo on this machine")

    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "regex:\n"
        "  - name: MARK\n"
        "    pattern: 'Quorin'\n"
        "    flags: IGNORECASE\n"
        "    replacement: \"[MARK]\"\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    code = main([str(source), "-r", str(rules), "-o", str(out)])
    assert code == 0
    written = out / "plan.redacted.pdf"
    opened = pymupdf.open(written)
    text = "\n".join(item.get_text() for item in opened)
    opened.close()
    assert WORD not in text
    assert "[MARK]" in text

    painted = _embedded(written)
    assert WORD.lower() not in _words(painted, scale=2, psm=6).lower()
