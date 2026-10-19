"""Raster redaction via Presidio's image engines and pytesseract.

ImageAnalyzerEngine / ImageRedactorEngine receive this project's AnalyzerEngine,
so OCR does not construct the default engine and does not download a spaCy model.
Language is chi_sim+eng. Hits are painted black.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from doc_redactor.engine import RedactionEngine
from doc_redactor.tesseract_cmd import require_tesseract

_OCR_LANG = "chi_sim+eng"
_PAD = 4


def redact_image_file(src: Path, dest: Path, engine: RedactionEngine) -> dict[str, int]:
    _refuse_same_path(src, dest)
    with Image.open(src) as opened:
        opened.load()
        redacted, counts = redact_image(opened, engine)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _save_image(redacted, dest)
    return counts


def analyze_image_entities(image: Image.Image, engine: RedactionEngine) -> list[dict]:
    """Return unique entities with OCR snippet and bounding boxes.

    Each item: category, text, start, end, boxes (list of left, top, width, height).
    """
    require_tesseract()
    prepared = _to_rgb(image)
    analyzer = _build_image_analyzer(engine)
    boxes = analyzer.analyze(
        prepared,
        ocr_kwargs={"lang": _OCR_LANG},
        language="en",
    )
    grouped: dict[tuple[str, int, int], dict] = {}
    for box in boxes:
        key = (box.entity_type, int(box.start), int(box.end))
        item = grouped.get(key)
        rect = (int(box.left), int(box.top), int(box.width), int(box.height))
        if item is None:
            grouped[key] = {
                "category": box.entity_type,
                "start": int(box.start),
                "end": int(box.end),
                "text": "",
                "boxes": [rect],
            }
        else:
            item["boxes"].append(rect)
    ocr_text = getattr(analyzer, "last_ocr_text", "") or ""
    for item in grouped.values():
        # What OCR read for this hit. Kept in memory for the screen only: the
        # hit list and reports carry category counts, never this text.
        read = ocr_text[item["start"] : item["end"]].strip()
        item["text"] = read or _ocr_crop(prepared, item["boxes"][0]) or item["category"]
        item["approx"] = engine.is_approximate_ocr_match(read) if read else False
    return list(grouped.values())


def paint_image_boxes(image: Image.Image, boxes: list[tuple[int, int, int, int]], pad: int = _PAD) -> Image.Image:
    prepared = _to_rgb(image)
    class _Box:
        def __init__(self, left, top, width, height):
            self.left = left
            self.top = top
            self.width = width
            self.height = height

    _paint_padding(prepared, [_Box(*box) for box in boxes], pad)
    return prepared


def _build_image_analyzer(engine: RedactionEngine):
    import os

    os.environ.setdefault("MPLBACKEND", "Agg")
    from presidio_image_redactor import ImageAnalyzerEngine, TesseractOCR

    analyzer = ImageAnalyzerEngine(
        # OCR text forgives one misread character in long terms (see engine).
        analyzer_engine=engine.for_ocr().analyzer,
        ocr=TesseractOCR(),
    )
    # Keep the text the rules ran on, so a hit can show what OCR read.
    joined = analyzer.ocr.get_text_from_ocr_dict

    def remember(ocr_result, *args, **kwargs):
        text = joined(ocr_result, *args, **kwargs)
        analyzer.last_ocr_text = text
        return text

    analyzer.ocr.get_text_from_ocr_dict = remember
    return analyzer


def _ocr_crop(image: Image.Image, box: tuple[int, int, int, int]) -> str:
    import pytesseract

    left, top, width, height = box
    region = image.crop((left, top, left + max(width, 1), top + max(height, 1)))
    return (pytesseract.image_to_string(region, lang=_OCR_LANG) or "").strip()


def redact_image(image: Image.Image, engine: RedactionEngine) -> tuple[Image.Image, dict[str, int]]:
    require_tesseract()
    prepared = _to_rgb(image)
    redactor = _build_redactor(engine)
    redacted, boxes = redactor.redact_and_return_bbox(
        prepared,
        fill=(0, 0, 0),
        ocr_kwargs={"lang": _OCR_LANG},
        language="en",
    )
    _paint_padding(redacted, boxes, _PAD)
    return redacted, _counts_from_boxes(boxes)


def redact_image_bytes(data: bytes, suffix: str, engine: RedactionEngine) -> tuple[bytes, dict[str, int]]:
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        redacted, counts = redact_image(opened, engine)
    buf = io.BytesIO()
    fmt = _format_for_suffix(suffix)
    if fmt == "JPEG":
        redacted.save(buf, format=fmt, quality=95)
    else:
        redacted.save(buf, format=fmt)
    return buf.getvalue(), counts


def _build_redactor(engine: RedactionEngine):
    from presidio_image_redactor import ImageRedactorEngine

    return ImageRedactorEngine(image_analyzer_engine=_build_image_analyzer(engine))


def _counts_from_boxes(boxes) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen: set[tuple[str, int, int]] = set()
    for box in boxes:
        # One entity can cover several OCR words. Count the entity once.
        key = (box.entity_type, int(box.start), int(box.end))
        if key in seen:
            continue
        seen.add(key)
        counts[box.entity_type] = counts.get(box.entity_type, 0) + 1
    return counts


def _paint_padding(image: Image.Image, boxes, pad: int) -> None:
    if pad <= 0 or not boxes:
        return
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for box in boxes:
        x0 = max(0, int(box.left) - pad)
        y0 = max(0, int(box.top) - pad)
        x1 = min(width, int(box.left) + int(box.width) + pad)
        y1 = min(height, int(box.top) + int(box.height) + pad)
        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image.copy()
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _save_image(image: Image.Image, dest: Path) -> None:
    fmt = _format_for_suffix(dest.suffix)
    if fmt == "JPEG":
        image.save(dest, format=fmt, quality=95)
    else:
        image.save(dest, format=fmt)


def _format_for_suffix(suffix: str) -> str:
    lowered = suffix.lower()
    if lowered in {".jpg", ".jpeg"}:
        return "JPEG"
    if lowered == ".png":
        return "PNG"
    raise ValueError(f"不支持的图片类型: {suffix}")


def _refuse_same_path(src: Path, dest: Path) -> None:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")


def is_raster_suffix(suffix: str) -> bool:
    return suffix.lower() in {".png", ".jpg", ".jpeg"}
