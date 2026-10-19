from pathlib import Path

from docx import Document

from doc_redactor.cli import main
import doc_redactor.gui as gui
from helpers import RULES


def test_cli_writes_redacted_docx_and_report(tmp_path: Path, capsys):
    source = tmp_path / "in.docx"
    document = Document()
    document.add_paragraph("Project ABC-2026-001")
    document.save(source)
    outdir = tmp_path / "out"
    code = main([str(source), "-r", str(RULES), "-o", str(outdir)])
    assert code == 0
    captured = capsys.readouterr()
    assert "PROJECT_ID: 1" in captured.out
    assert "ABC-2026-001" not in captured.out
    assert (outdir / "in.redacted.docx").is_file()
    report = (outdir / "in.report.json").read_text(encoding="utf-8")
    assert "ABC-2026-001" not in report
    assert '"PROJECT_ID": 1' in report


def test_cli_rejects_unknown_type(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("ABC-2026-001", encoding="utf-8")
    code = main([str(source), "-r", str(RULES), "-o", str(tmp_path / "out")])
    assert code == 1


def test_gui_import_does_not_require_a_display():
    assert callable(gui.main)


def test_python_m_cli_module_runs_main():
    """`python -m doc_redactor.cli --help` 要真的跑起来，不能静默退出 0。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "doc_redactor.cli", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "没有输出"
    assert "usage" in proc.stdout.lower()
