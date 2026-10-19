"""出厂门禁（CI 必过）：编造样本经命令行和界面两条路写出后，用常用解析器读输出，
任何一种读法读出原词，这条测试就失败，构建也就失败。

读法照审阅的「AI 读取复核」流程：PDF 用 PyMuPDF、pymupdf4llm（含 RapidOCR 看图）、pdftotext；
Word 用 python-docx 和 pandoc。CI 的 ai-read-gate 作业装齐这五种并设 AI_GATE_STRICT=1，
缺一种就失败；本地和 Windows 测试步骤里缺的读法跳过（PyMuPDF、python-docx、pandoc 总在）。
扫描件样本 P4_scan.pdf 需要中文字体和 tesseract，严格模式下缺了也算失败。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from doc_redactor.cli import redact_paths
from doc_redactor.config import load_rules
from doc_redactor.scan import scan_path, write_scans
from helpers import needs_tesseract

# Pictures need Tesseract now: without it the write is refused, not done with the images skipped.
# 本机没装跳过；CI 设了 REQUIRE_TESSERACT=1 时找不到就失败（tests/helpers.py）。
needs_ocr = needs_tesseract

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "ai_gate"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"ai_gate_{name}", GATE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


samples = _load("make_samples")
reader = _load("read_outputs")
WORDS = reader.load_words()


def _tesseract_ready() -> bool:
    from doc_redactor.tesseract_cmd import configure_tesseract

    return configure_tesseract() is not None


@pytest.fixture(scope="module")
def inputs(tmp_path_factory) -> list[Path]:
    return samples.make_all(tmp_path_factory.mktemp("ai_gate_in"), scan=_tesseract_ready())


def test_strict_gate_has_every_reader_and_the_scan_sample(inputs):
    if not reader.strict_mode():
        pytest.skip("只在 AI_GATE_STRICT=1 时要求五种读法齐全")
    assert reader.missing_readers() == []
    assert "P4_scan.pdf" in [path.name for path in inputs]


def test_gate_is_not_vacuous(inputs):
    """原件用同样的读法读，每份都能读出原词；否则门禁形同虚设。"""
    can_ocr = "pymupdf4llm" in reader.available_readers()
    for path in inputs:
        if path.name == "P4_scan.pdf" and not can_ocr:
            continue  # 整页图片只有带 OCR 的 pymupdf4llm 读得出
        assert reader.findings(path, WORDS), path.name


def test_pandoc_reader_is_present():
    """pandoc 随 requirements.txt 里的 pypandoc-binary 装上，门禁不能悄悄少一种读法。"""
    assert "pandoc" in reader.available_readers()


def test_parser_error_counts_as_a_finding(tmp_path: Path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 not really")
    assert ("PyMuPDF", reader.FAILED) in reader.findings(broken, WORDS)


def _assert_clean(outputs: list[Path]) -> None:
    problems = []
    for path in outputs:
        for method, number in reader.findings(path, WORDS):
            problems.append(reader.describe(path, method, number))
    assert problems == []


@needs_ocr
def test_cli_outputs_pass_the_gate(inputs, tmp_path: Path):
    summaries, blocked = redact_paths(inputs, GATE / "rules.yaml", tmp_path)
    assert blocked == []
    outputs = sorted(tmp_path.glob("*.redacted.*"))
    assert len(outputs) == len(inputs)
    _assert_clean(outputs)


@needs_ocr
def test_gui_outputs_pass_the_gate(inputs, tmp_path: Path):
    engine = load_rules(GATE / "rules.yaml")
    scans = [scan_path(path, engine) for path in inputs]
    results = write_scans(scans, tmp_path, engine=engine)
    assert [r.error for r in results if r.dest is None] == []
    _assert_clean([r.dest for r in results])
