"""Raster redaction via Presidio's image engines and pytesseract.

ImageAnalyzerEngine / ImageRedactorEngine receive this project's AnalyzerEngine,
so OCR does not construct the default engine and does not download a spaCy model.
Language is chi_sim+eng. Hits are painted black.
"""

from __future__ import annotations

import io
import json
import re
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


_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


def _format_for_suffix(suffix: str) -> str:
    fmt = _FORMATS.get(suffix.lower())
    if fmt is None:
        raise ValueError(f"不支持的图片类型: {suffix}")
    return fmt


def _refuse_same_path(src: Path, dest: Path) -> None:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")


def is_raster_suffix(suffix: str) -> bool:
    return suffix.lower() in {".png", ".jpg", ".jpeg"}


# Pictures inside a Word file that are read with OCR and painted in place.
# Animated GIF and multi-page TIFF keep only the first frame.
EMBEDDED_RASTER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"})


def is_embedded_raster_suffix(suffix: str) -> bool:
    return suffix.lower() in EMBEDDED_RASTER_SUFFIXES


class WriteBlocked(ValueError):
    """The file holds something that cannot be checked, so it is not written.

    The message names the reason and counts only; it never carries source text.
    """


class PdfImageOcrFailed(WriteBlocked):
    """A decoded PDF picture could not be OCR-checked; it may be kept with a warning."""


def checked_pdf_image_entities(image, engine, page: int, index: int, *, analyze=None) -> list[dict]:
    """Only OCR-stage failures are eligible for a partial PDF export.

    Decoding, painting and write verification remain outside this boundary.
    Missing Tesseract or language data retains the existing blocking behavior.
    """
    try:
        return (analyze or analyze_image_entities)(image, engine)
    except Exception as exc:
        if (isinstance(exc, RuntimeError) and "Tesseract" in str(exc)) or type(exc).__name__ == "TesseractNotFoundError":
            raise
        raise PdfImageOcrFailed(MSG_OCR_SKIPPED_AT.format(page=page, index=index)) from exc


def skipped_pdf_image(page: int, index: int, unit: str) -> dict:
    """Safe location metadata only. Never store the exception or recognized text."""
    return {"page": page, "index": index, "unit": unit, "reason": "识别文字时出错，图片保留原样"}


def partial_image_notice(skipped_images, written: bool = False) -> str:
    if not skipped_images:
        return ""
    places = "、".join(f"第 {item['page']} 页图 {item['index']}" for item in skipped_images)
    prefix = "已写出部分脱敏文件，图片需人工检查" if written else "部分脱敏，图片需人工检查"
    return f"{prefix}：{places}。这些图片保留原样，可能仍含敏感信息"


def write_partial_warning(dest: Path, skipped_images) -> Path | None:
    """Persist the partial status beside an exported PDF, without source text."""
    if not skipped_images:
        return None
    path = dest.with_suffix(".warnings.json")
    payload = {
        "status": "partial",
        "message": partial_image_notice(skipped_images, written=True),
        "skipped_images": list(skipped_images),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# 没装 Tesseract：说清为什么不写出（图里的字查不了），和怎么办（MSG_NO_OCR_FIX）。
MSG_NO_OCR = "有 {n} 张图片没法检查：没找到 Tesseract（识别图片文字的程序），查不了图里的字有没有敏感词，所以未写出"
MSG_NO_OCR_FIX = "装好 Tesseract（带中文 chi_sim 和英文 eng 语言数据）后重新扫描；已经装了的，把环境变量 TESSERACT_CMD 设成 tesseract 程序的路径"
MSG_IMAGE_FIX = "可以先把这张图删掉或换成普通图片，再拖进来"
MSG_BAD_IMAGE = "有 {n} 张图片打不开，没法检查，未写出"
MSG_OCR_FAILED = "有 {n} 张图片识别文字时出错，没法检查，未写出"
# PDF 里的图按页和图号写明是哪一张；读图失败和识别文字出错分开说。
MSG_BAD_IMAGE_AT = "第 {page} 页图 {index} 打不开，没法检查，未写出"
MSG_OCR_FAILED_AT = "第 {page} 页图 {index} 识别文字时出错，没法检查，未写出"
MSG_OCR_SKIPPED_AT = "第 {page} 页图 {index} 识别文字时出错，图片保留原样，需人工检查"
MSG_VECTOR = "有 {n} 张矢量图（EMF、WMF 等），没法检查其中文字，未写出"
MSG_INLINE_FAILED = "有 {n} 张内嵌图没能涂黑，未写出"

_NO_OCR_PATTERN = re.compile(re.escape(MSG_NO_OCR).replace(re.escape("{n}"), r"\d+"))


def blocked_message(exc: Exception, *, ocr: bool = False) -> str:
    """Sentence for a picture that stopped the file from being written."""
    if isinstance(exc, WriteBlocked):
        return str(exc)
    if type(exc).__name__ == "TesseractNotFoundError" or (
        isinstance(exc, RuntimeError) and "Tesseract" in str(exc)
    ):
        return MSG_NO_OCR.format(n=1)
    if ocr:
        return MSG_OCR_FAILED.format(n=1)
    return MSG_BAD_IMAGE.format(n=1)


def blocked_advice(reason: str) -> str:
    """What the user can do about a file that was not written for ``reason``."""
    return MSG_NO_OCR_FIX if _NO_OCR_PATTERN.fullmatch(reason or "") else MSG_IMAGE_FIX
