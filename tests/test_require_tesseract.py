"""REQUIRE_TESSERACT=1（CI 设）时找不到 tesseract，要 OCR 的测试直接失败，不许跳过；
没设（本机）时照旧跳过。出品审要求。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import REQUIRE_TESSERACT_ENV, check_tesseract, needs_tesseract

TESTS = Path(__file__).resolve().parent
SRC = TESTS.parent / "src"


@pytest.fixture
def no_tesseract(monkeypatch):
    monkeypatch.setattr("doc_redactor.tesseract_cmd.find_tesseract", lambda: None)


def test_required_and_missing_fails(no_tesseract, monkeypatch):
    monkeypatch.setenv(REQUIRE_TESSERACT_ENV, "1")
    with pytest.raises(pytest.fail.Exception, match="REQUIRE_TESSERACT=1"):
        check_tesseract()


def test_not_required_and_missing_skips(no_tesseract, monkeypatch):
    monkeypatch.delenv(REQUIRE_TESSERACT_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        check_tesseract()


def test_decorated_test_fails_when_required_and_missing(no_tesseract, monkeypatch):
    monkeypatch.setenv(REQUIRE_TESSERACT_ENV, "1")
    ran = []
    wrapped = needs_tesseract(lambda: ran.append(1))
    with pytest.raises(pytest.fail.Exception):
        wrapped()
    assert ran == []


def test_found_runs(monkeypatch):
    monkeypatch.setenv(REQUIRE_TESSERACT_ENV, "1")
    monkeypatch.setattr("doc_redactor.tesseract_cmd.find_tesseract", lambda: "/opt/tesseract")
    ran = []
    needs_tesseract(reason="x")(lambda: ran.append(1))()
    assert ran == [1]


_PROBE = """
from helpers import needs_tesseract

@needs_tesseract
def test_probe():
    pass
"""


def _run_probe(tmp_path: Path, required: bool) -> str:
    (tmp_path / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in (REQUIRE_TESSERACT_ENV, "TESSERACT_CMD")}
    env["PATH"] = str(tmp_path)  # 子进程里 PATH 上没有 tesseract
    env["PYTHONPATH"] = os.pathsep.join([str(TESTS), str(SRC)])
    if required:
        env[REQUIRE_TESSERACT_ENV] = "1"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path), str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    return done.stdout + done.stderr


@pytest.mark.skipif(os.name == "nt", reason="子进程清 PATH 的写法只在 POSIX 上验证")
def test_real_pytest_run_reports_failed_not_skipped(tmp_path: Path):
    """真跑一次 pytest：设了变量、PATH 上没有 tesseract，结果是 failed；没设是 skipped。"""
    required, optional = tmp_path / "required", tmp_path / "optional"
    required.mkdir()
    optional.mkdir()
    out = _run_probe(required, required=True)
    assert "1 failed" in out and "skipped" not in out, out
    out = _run_probe(optional, required=False)
    assert "1 skipped" in out and "failed" not in out, out
