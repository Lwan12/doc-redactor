"""Shared fixtures for fictional documents."""

from __future__ import annotations

import functools
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules.example.yaml"
SECRETS = ("ABC-2026-001", "Acme Pharma", "acme pharma", "Fictizumab", "fictizumab")


def load_make_samples():
    path = ROOT / "tools" / "make_samples.py"
    spec = importlib.util.spec_from_file_location("make_samples", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_report_has_no_secrets(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in raw
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    for key, value in payload.items():
        assert isinstance(key, str)
        assert isinstance(value, int)
        assert value > 0
    return payload


# --- Tesseract：本机没装就跳过，CI 设了 REQUIRE_TESSERACT=1 就必须有 ------------

REQUIRE_TESSERACT_ENV = "REQUIRE_TESSERACT"
NO_TESSERACT_REASON = "需要 Tesseract（含图片的文件没有它不写出）"


def tesseract_required() -> bool:
    return os.environ.get(REQUIRE_TESSERACT_ENV, "").strip() == "1"


def check_tesseract(reason: str = NO_TESSERACT_REASON) -> None:
    """有 Tesseract 就返回；没有时，设了 REQUIRE_TESSERACT=1 就让测试失败，否则跳过。

    CI 上 Tesseract 没装好，OCR 测试不能悄悄全跳过（出品审要求）。
    """
    from doc_redactor.tesseract_cmd import find_tesseract

    if find_tesseract() is not None:
        return
    if tesseract_required():
        pytest.fail(f"{REQUIRE_TESSERACT_ENV}=1 但找不到 tesseract：{reason}", pytrace=False)
    pytest.skip(reason)


def needs_tesseract(test=None, *, reason: str = NO_TESSERACT_REASON):
    """装饰器：测试开始前先 check_tesseract。可写 @needs_tesseract 或 @needs_tesseract(reason=...)。"""

    def wrap(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            check_tesseract(reason)
            return func(*args, **kwargs)

        return wrapper

    return wrap(test) if test is not None else wrap
