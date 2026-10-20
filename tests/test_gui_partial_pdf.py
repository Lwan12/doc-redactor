"""A skipped PDF image stays visibly partial before and after exporting."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from doc_redactor import header_footer as hf
from doc_redactor.backpath import ScanTicket
from doc_redactor.gui.app import App
from doc_redactor.gui.result_view import (
    MSG_PARTIAL_IMAGES,
    _has_findings,
    _hf_only_write,
    _partial_batch_notice,
    _partial_file_notice,
    _scan_status,
    _top_bucket,
    counts_toward_write,
    writable_hit_total,
)
from doc_redactor.image_redactor import partial_image_notice, skipped_pdf_image
from doc_redactor.leak_check import MSG_NO_HITS
from doc_redactor.scan import KIND_PDF, FileScan, LocatedHit, WriteResult


def _scan(*, hits=True, name="partial.pdf"):
    found = [
        LocatedHit(
            hit_id=1, category="项目号", text="ABC-2026-001",
            location="第 1 页 · 第 1 行", unit="page:0",
            start=0, end=12, page=0, rects=[(10, 10, 100, 20)],
        )
    ] if hits else []
    return FileScan(
        path=Path(name), kind=KIND_PDF, hits=found,
        pages=[["ABC-2026-001 sample body"], ["Second page"]],
        skipped_images=[skipped_pdf_image(2, 3, "pdfimg:8")],
    )


def _app(scans, *, written=False, notes=None, errors=None):
    app = App.__new__(App)
    app.scans = scans
    app.written = written
    app.write_notes = notes or {}
    app.write_errors = errors or {}
    app.remove_hf = False
    return app


@pytest.mark.parametrize("written", [False, True])
def test_partial_file_has_warning_status_and_its_own_top_bucket(written):
    scan = _scan()
    app = _app([scan], written=written)
    if written:
        app.write_notes[str(scan.path)] = partial_image_notice(scan.skipped_images, written=True)
    kind, text, tag = _scan_status(scan, app)
    assert kind == "partial"
    assert MSG_PARTIAL_IMAGES in text
    assert tag == "warn"
    assert _top_bucket(scan, app) == "partial"
    assert _has_findings(scan)
    assert counts_toward_write(scan, written=written, write_errors={})
    assert writable_hit_total([scan], written=written, write_errors={}) == 1
    assert app.written_file_count() == 1


@pytest.mark.parametrize("written", [False, True])
def test_partial_notice_keeps_locations_and_unchanged_image_warning(written):
    scan = _scan()
    app = _app([scan], written=written)
    notice = _partial_file_notice(scan, app)
    assert "图片需人工检查" in notice
    assert "第 2 页图 3" in notice
    assert "保留原样" in notice and "可能仍含敏感信息" in notice
    assert ("已写出" in notice) == written
    assert "ABC-2026-001" not in notice


def test_partial_zero_hit_file_remains_unwritten_and_not_clean():
    scan = _scan(hits=False)
    app = _app([scan], notes={str(scan.path): MSG_NO_HITS})
    assert app.written_file_count() == 0
    assert writable_hit_total([scan], written=False, write_errors={}) == 0
    assert _has_findings(scan)
    kind, text, tag = _scan_status(scan, app)
    assert (kind, tag) == ("partial", "warn")
    assert "未写出" in text
    assert "未写出" in _partial_file_notice(scan, app)
    assert "原件不能当成脱敏件交出去" in _partial_file_notice(scan, app)


def test_partial_header_footer_only_output_is_not_green_or_lost_to_write_note():
    scan = _scan(hits=False)
    scan.hf_route = hf.ROUTE_TAG
    scan.hf_plan = SimpleNamespace(candidates=[SimpleNamespace(source=hf.SOURCE_TAG)])
    note = partial_image_notice(scan.skipped_images, written=True)
    app = _app([scan], written=True, notes={str(scan.path): note})
    assert scan.will_write() and app.written_file_count() == 1
    assert _hf_only_write(scan, app)
    assert _scan_status(scan, app)[::2] == ("partial", "warn")
    assert "已写出" in _partial_file_notice(scan, app)
    assert "未写出" not in _partial_file_notice(scan, app)


@pytest.mark.parametrize("block", ["scan", "write", "ocr", "unlocated"])
def test_real_failures_and_blocks_still_take_precedence(block):
    scan = _scan()
    app = _app([scan], written=True)
    if block == "scan":
        scan.status, scan.error = "fail", "文件无法打开"
    elif block == "write":
        app.write_errors[str(scan.path)] = "输出校验失败"
    elif block == "ocr":
        scan.blocked_reason = "没装 Tesseract"
    else:
        scan.unlocated_count = 1
    kind, _text, tag = _scan_status(scan, app)
    assert kind in {"fail", "nowrite", "blocked", "unlocated"}
    assert tag != "ok"
    assert not counts_toward_write(scan, written=True, write_errors=app.write_errors)
    assert app.written_file_count() == 0


def test_batch_warning_counts_partial_exports_separately_from_unwritten():
    exported = _scan()
    unwritten = _scan(hits=False, name="unchecked.pdf")
    clean = _scan(name="clean.pdf")
    clean.skipped_images = []
    app = _app([exported, unwritten, clean], written=True)
    text = _partial_batch_notice(app.scans, app)
    assert text.startswith("⚠ 已写出 2 个文件")
    assert f"其中 1 个{MSG_PARTIAL_IMAGES}" in text
    assert "另有 1 个图片未检查的文件未写出" in text
    assert "✓" not in text
    assert [_top_bucket(s, app) for s in app.scans] == ["partial", "partial", "hit"]


def test_scan_completion_retains_skipped_image_metadata():
    scan = _scan()
    app = _app([])
    app.scan_generation = 7
    app.cancelled = False
    app.files = [scan.path]
    app.ruleset = SimpleNamespace(rules=[])
    app.uncheck_memory = None
    shown = []
    app.show = shown.append
    app._scan_done([scan], ScanTicket(7))
    assert app.scans[0].skipped_images == [skipped_pdf_image(2, 3, "pdfimg:8")]
    assert shown == ["result"]
    assert _scan_status(app.scans[0], app)[0] == "partial"


def test_write_result_adds_warning_to_scan_without_replacing_previous_locations(tmp_path, monkeypatch):
    scan = _scan()
    old = scan.skipped_images[0]
    new = skipped_pdf_image(1, 4, "pdfinline:0:4")
    dest = tmp_path / "partial.partial-redacted.pdf"
    item = WriteResult(
        path=scan.path, dest=dest, report=None, counts={"项目号": 1},
        skipped_images=[old, new], note=partial_image_notice([old, new], written=True),
    )
    app = _app([scan])
    app.output_var = SimpleNamespace(get=lambda: str(tmp_path))
    app.ruleset = SimpleNamespace(replacements=lambda: {}, to_engine=lambda: None)
    app.running = False
    app.kept_outputs = []
    app.refresh = lambda: None
    monkeypatch.setattr("doc_redactor.gui.app.write_scans", lambda *args, **kwargs: [item])
    app.write_files()
    assert scan.skipped_images == [old, new]
    assert app.written and app.kept_outputs == [dest]
    assert _scan_status(scan, app)[::2] == ("partial", "warn")
    assert "第 1 页图 4" in _partial_file_notice(scan, app)


@pytest.fixture(scope="module")
def gui():
    pytest.importorskip("tkinter")
    if sys.platform == "win32" and not os.environ.get("DOC_REDACTOR_GUI_TESTS"):
        pytest.skip("GUI geometry tests require an explicit display")
    from doc_redactor.gui.app import create_root

    try:
        root, dnd = create_root()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")
    app = App(root, dnd, shoot=True)
    root.geometry("960x640+0+0")
    root.deiconify()
    root.update()
    yield app
    root.destroy()


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _copy(root):
    import tkinter as tk

    chunks = []
    for node in _walk(root):
        if isinstance(node, tk.Text):
            chunks.append(node.get("1.0", "end"))
        else:
            try:
                chunks.append(str(getattr(node, "logical_text", node.cget("text"))))
            except tk.TclError:
                pass
    return "\n".join(chunks)


def _show(app, scan, written):
    app.scans = [scan]
    app.selected = 0
    app.written = written
    app.write_errors = {}
    app.write_notes = {}
    app.preview_mode = "orig"
    app.hide_text = False
    app.current_hit = None
    app.selected_cat = None
    app.pdf_page = 0
    app.remove_hf = False
    app.show("result")
    app.root.update_idletasks()
    app.root.update()


@pytest.mark.parametrize("written", [False, True])
def test_gui_partial_row_preview_banner_and_footer_remain_warning(gui, written):
    from tkinter import ttk

    _show(gui, _scan(), written)
    copy = _copy(gui.root)
    assert MSG_PARTIAL_IMAGES in copy
    assert "第 2 页图 3" in copy
    assert "部分脱敏 1" in copy
    assert "✓ 已写出" not in copy
    tree = next(
        n for n in _walk(gui.root)
        if isinstance(n, ttk.Treeview) and tuple(n.cget("columns")) == ("name", "n")
    )
    row = next(i for i in tree.get_children() if "partial.pdf" in tree.item(i, "values")[0])
    assert tree.item(row, "values")[0].startswith("⚠ 部分脱敏")
    assert "warn" in tree.item(row, "tags") and "ok" not in tree.item(row, "tags")
    preview = gui.result_view._preview_text
    assert preview is not None and preview.winfo_ismapped()
    assert preview.winfo_height() > 30
    if not written:
        button = next(n for n in _walk(gui.root) if isinstance(n, ttk.Button) and str(n.cget("text")).startswith("写出 "))
        assert str(button.cget("state")) == "normal"
        assert "含部分脱敏" in button.cget("text")


def test_many_skipped_locations_are_scrollable_without_hiding_preview(gui):
    scan = _scan()
    scan.skipped_images = [skipped_pdf_image(i, i, f"pdfimg:{i}") for i in range(1, 101)]
    _show(gui, scan, False)
    warning = next(n for n in _walk(gui.root) if getattr(n, "partial_image_warning", False))
    assert int(warning.cget("height")) == 3
    assert warning.cget("yscrollcommand")
    assert "第 100 页图 100" in warning.get("1.0", "end")
    warning.yview_moveto(1.0)
    gui.root.update()
    assert warning.yview()[1] >= 0.99
    preview = gui.result_view._preview_text
    assert preview is not None and preview.winfo_ismapped() and preview.winfo_height() > 30


def test_gui_partial_zero_hit_keeps_preview_and_disables_export(gui):
    from tkinter import ttk

    _show(gui, _scan(hits=False), False)
    assert gui.result_view._preview_text is not None
    assert "这个文件未写出" in _copy(gui.root)
    button = next(n for n in _walk(gui.root) if isinstance(n, ttk.Button) and str(n.cget("text")).startswith("写出 "))
    assert str(button.cget("state")) == "disabled"
