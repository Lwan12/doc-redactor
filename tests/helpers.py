"""Shared fixtures for fictional documents."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
