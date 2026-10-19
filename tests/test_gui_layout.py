"""Pager order, preview scrolling, and the rules menu vs table headings."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from doc_redactor.gui.result_view import PDF_PAGER, clamp_page, line_fully_inside, line_visible
from doc_redactor.gui.rules_view import place_more_menu, rects_overlap


def test_clamp_page_matches_prev_and_next():
    assert PDF_PAGER == (("‹", -1), ("›", 1))
    assert PDF_PAGER[0][1] < 0 < PDF_PAGER[-1][1]
    assert clamp_page(1, PDF_PAGER[0][1], 2) == 0
    assert clamp_page(0, PDF_PAGER[0][1], 2) == 0
    assert clamp_page(0, PDF_PAGER[-1][1], 2) == 1
    assert clamp_page(1, PDF_PAGER[-1][1], 2) == 1
    assert clamp_page(3, 1, 0) == 0


def test_place_more_menu_misses_headings_and_first_row():
    headings = [
        (20, 134, 100, 163),
        (100, 134, 230, 163),
        (230, 134, 280, 163),
        (280, 134, 710, 163),
        (710, 134, 840, 163),
        (840, 134, 950, 163),
    ]
    first_row = [
        (20, 163, 100, 191),
        (100, 163, 230, 191),
        (230, 163, 280, 191),
        (280, 163, 710, 191),
        (710, 163, 840, 191),
        (840, 163, 950, 191),
    ]
    action = (840, 163, 950, 400)
    x, y = place_more_menu(
        button=(860, 90, 80, 32),
        menu_size=(128, 30),
        window=(0, 0, 960, 640),
        headings=headings,
        avoid=[action, *first_row],
    )
    box = (x, y, x + 128, y + 30)
    for heading in headings:
        assert not rects_overlap(box, heading)
    for cell in first_row:
        assert not rects_overlap(box, cell)
    assert not rects_overlap(box, action)


def test_place_more_menu_stays_under_the_button_when_table_is_lower():
    headings = [(20, 170, 950, 199)]
    first = (20, 199, 950, 227)
    x, y = place_more_menu(
        button=(860, 90, 80, 32),
        menu_size=(128, 30),
        window=(0, 0, 960, 640),
        headings=headings,
        avoid=[first, (840, 199, 950, 400)],
    )
    assert (x, y) == (812, 122)
    box = (x, y, x + 128, y + 30)
    assert not rects_overlap(box, headings[0])
    assert not rects_overlap(box, first)


def test_place_more_menu_stays_under_the_button_when_nothing_is_below():
    x, y = place_more_menu(
        button=(100, 40, 70, 30),
        menu_size=(80, 24),
        window=(0, 0, 960, 640),
        headings=[],
    )
    assert (x, y) == (90, 70)


@pytest.fixture(scope="module")
def gui():
    pytest.importorskip("tkinter")
    if sys.platform == "win32" and not os.environ.get("DOC_REDACTOR_GUI_TESTS"):
        pytest.skip("window geometry tests run under xvfb (set DOC_REDACTOR_GUI_TESTS=1 to force)")
    from doc_redactor.gui.app import App, create_root

    try:
        root, dnd = create_root()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")
    root.geometry("960x640+0+0")
    app = App(root, dnd, shoot=True)
    root.update_idletasks()
    root.deiconify()
    root.update()
    yield app
    try:
        root.destroy()
    except Exception:
        pass


def _settle(app) -> None:
    app.root.update_idletasks()
    app.root.update()


def _pager(app):
    pager = app.result_view._pdf_pager
    assert pager is not None and pager.winfo_exists()
    parts = pager.pack_slaves()
    assert len(parts) >= 3
    return parts


def test_pdf_pager_reads_prev_page_next_and_clicks_match(gui):
    app = gui
    app.apply_shoot_state("res-pdf")
    _settle(app)
    assert app.pdf_page == 1
    parts = _pager(app)
    texts = [part.cget("text") for part in parts]
    assert texts[0] == "‹"
    assert texts[-1] == "›"
    assert "第 2 / 2 页" in texts[1]
    xs = [part.winfo_rootx() for part in parts]
    assert xs[0] < xs[1] < xs[-1]

    parts[0].invoke()
    _settle(app)
    assert app.pdf_page == 0
    parts = _pager(app)
    assert "第 1 / 2 页" in parts[1].cget("text")
    parts[0].invoke()
    _settle(app)
    assert app.pdf_page == 0

    parts = _pager(app)
    assert parts[-1].cget("text") == "›"
    assert parts[-1].winfo_rootx() > parts[0].winfo_rootx()
    parts[-1].invoke()
    _settle(app)
    assert app.pdf_page == 1
    parts = _pager(app)
    parts[-1].invoke()
    _settle(app)
    assert app.pdf_page == 1
    parts = _pager(app)
    assert [part.cget("text") for part in parts][0] == "‹"
    assert parts[0].winfo_rootx() < parts[-1].winfo_rootx()


def test_pdf_preview_keeps_the_body_reachable(gui):
    app = gui
    app.apply_shoot_state("res-pdf")
    _settle(app)
    text = app.result_view._preview_text
    scroll = app.result_view._preview_scroll
    assert text is not None and text.winfo_exists()
    assert scroll is not None and scroll.winfo_ismapped()
    assert text.cget("yscrollcommand")
    assert scroll.cget("command")
    body = text.get("1.0", "end")
    assert "第 1 页" in body
    assert "第 2 页" in body
    assert "入组速度最快" in body
    root = app.root
    assert text.winfo_rooty() >= root.winfo_rooty()
    assert text.winfo_rooty() + text.winfo_height() <= root.winfo_rooty() + root.winfo_height() + 2
    # The text widget itself is not clipped by the preview footer.
    assert text.winfo_height() > 80
    first = text.search("第 1 页", "1.0")
    opened = text.search("第 2 页", "1.0")
    last = text.search("入组速度最快", "1.0")
    assert first and opened and last
    # Default view keeps the last line whole. The line at the top of the pane
    # is a full line, not a sliced glyph.
    assert line_fully_inside(text, last)
    top = text.dlineinfo(text.index("@0,0"))
    assert top is not None and top[1] >= 0
    assert line_visible(text, opened)
    text.yview_moveto(0)
    text.update_idletasks()
    assert line_visible(text, first)
    text.yview_moveto(1)
    text.update_idletasks()
    assert line_fully_inside(text, last)


def _assert_page_marker_at_top(app, page_number: int) -> None:
    text = app.result_view._preview_text
    assert text is not None and text.winfo_exists()
    label = f"第 {page_number} 页"
    idx = text.search(label, "1.0")
    assert idx
    assert line_visible(text, idx)
    assert line_fully_inside(text, idx)
    info = text.dlineinfo(idx)
    assert info is not None and info[1] <= 12
    top = text.get("@0,0 linestart", "@0,0 lineend")
    assert label in top
    if page_number == 1:
        assert text.yview()[0] == 0


def test_pdf_page_turn_scrolls_marker_to_the_top(gui):
    app = gui
    app.apply_shoot_state("res-pdf")
    _settle(app)
    assert app.pdf_page == 1
    parts = _pager(app)
    parts[0].invoke()
    _settle(app)
    assert app.pdf_page == 0
    _assert_page_marker_at_top(app, 1)

    parts = _pager(app)
    parts[-1].invoke()
    _settle(app)
    assert app.pdf_page == 1
    _assert_page_marker_at_top(app, 2)


def test_more_menu_does_not_cover_rule_headings(gui):
    app = gui
    app.apply_shoot_state("rules-more")
    _settle(app)
    menu = app.rules_view._more_menu
    tree = app.rules_view._rules_tree
    btn = app.rules_view._more_btn
    assert menu is not None and tree is not None and btn is not None
    width = menu.winfo_width()
    height = menu.winfo_height()
    assert width > 20 and height > 8
    box = (menu.winfo_rootx(), menu.winfo_rooty(), menu.winfo_rootx() + width, menu.winfo_rooty() + height)
    names = [tree.heading(col, "text") for col in tree.cget("columns")]
    rects = app.rules_view.heading_rects()
    assert names == ["启用", "规则名", "类型", "例子", "替换成", "操作"]
    assert len(rects) == len(names)
    for name, rect in zip(names, rects):
        assert not rects_overlap(box, rect), f"{name} {rect} overlaps menu {box}"
    kids = tree.get_children()
    assert kids
    first_rects = app.rules_view.first_row_rects()
    assert len(first_rects) == len(names)
    for name, rect in zip(names, first_rects):
        assert not rects_overlap(box, rect), f"first-row {name} {rect} overlaps menu {box}"
    values = tree.item(kids[0], "values")
    assert "修改" in values[-1]
    assert "删除" in values[-1]
    assert values[names.index("替换成")]
    for rect in app.rules_view.action_rects():
        assert not rects_overlap(box, rect), f"操作 {rect} overlaps menu {box}"
    assert box[1] >= btn.winfo_rooty() + btn.winfo_height()


def test_write_files_keeps_existing_output_and_source(gui, tmp_path: Path):
    """A same-named output already on disk is left alone. The write makes a new folder."""
    from docx import Document

    from doc_redactor.scan import KIND_WORD, FileScan, LocatedHit

    source = tmp_path / "notes.docx"
    document = Document()
    document.add_paragraph("ABC-2026-001")
    document.save(source)
    original = source.read_bytes()
    out = tmp_path / "out"
    out.mkdir()
    prior_dir = out / "20200101-0000"
    prior_dir.mkdir()
    prior = prior_dir / "notes.redacted.docx"
    prior.write_bytes(b"prior-output-do-not-touch")

    app = gui
    app.show("main")
    _settle(app)
    app.output_var.set(str(out))
    app.scans = [
        FileScan(
            path=source,
            kind=KIND_WORD,
            status="ok",
            hits=[
                LocatedHit(
                    hit_id=1,
                    category="项目号",
                    text="ABC-2026-001",
                    location="第 1 段",
                    unit="p:0",
                    start=0,
                    end=12,
                )
            ],
            paragraphs=["ABC-2026-001"],
        )
    ]
    app.running = False
    app.written = False
    app.write_errors = {}
    app.batch_dir = None
    app.kept_outputs = []

    app.write_files()
    _settle(app)
    first_dir = app.batch_dir
    assert first_dir is not None
    assert first_dir.parent == out
    assert first_dir != prior_dir
    assert re.fullmatch(r"\d{8}-\d{4}(-\d+)?", first_dir.name)
    first = first_dir / "notes.redacted.docx"
    assert first.is_file()
    assert first.resolve() != source.resolve()
    assert first.resolve() != prior.resolve()
    assert prior.read_bytes() == b"prior-output-do-not-touch"
    assert source.read_bytes() == original
    first_bytes = first.read_bytes()

    app.write_files()
    _settle(app)
    second_dir = app.batch_dir
    assert second_dir is not None
    assert second_dir != first_dir
    assert second_dir.parent == out
    assert re.fullmatch(r"\d{8}-\d{4}(-\d+)?", second_dir.name)
    second = second_dir / "notes.redacted.docx"
    assert second.is_file()
    assert second.resolve() != first.resolve()
    assert first.read_bytes() == first_bytes
    assert prior.read_bytes() == b"prior-output-do-not-touch"
    assert source.read_bytes() == original


def test_res_skipped_shoot_is_written_not_conflict(gui):
    from doc_redactor.gui.result_view import _scan_status

    app = gui
    app.apply_shoot_state("res-skipped")
    _settle(app)
    assert app.written is True
    assert app.write_failure_rows() == []
    assert getattr(app, "_overlay_frame", None) is None
    notes = list(app.write_notes.values())
    assert notes
    assert all(note == "这个文件之前已写出，已跳过" for note in notes)
    kind, text, tag = _scan_status(app.scans[0], app)
    assert kind == "skipped"
    assert tag == "ok"
    assert "✕" not in text


def test_retry_existing_file_is_skipped_as_written(gui, tmp_path: Path):
    """Retry that meets an already-written dest skips it. Not an error, not red."""
    from docx import Document

    from doc_redactor.gui.result_view import _scan_status
    from doc_redactor.gui.theme import OK, status_style
    from doc_redactor.scan import KIND_WORD, FileScan, LocatedHit, MSG_ALREADY_WRITTEN

    source = tmp_path / "notes.docx"
    document = Document()
    document.add_paragraph("ABC-2026-001")
    document.save(source)
    out = tmp_path / "out"
    out.mkdir()

    app = gui
    app.show("main")
    _settle(app)
    app.output_var.set(str(out))
    app.scans = [
        FileScan(
            path=source,
            kind=KIND_WORD,
            status="ok",
            hits=[
                LocatedHit(
                    hit_id=1,
                    category="项目号",
                    text="ABC-2026-001",
                    location="第 1 段",
                    unit="p:0",
                    start=0,
                    end=12,
                )
            ],
            paragraphs=["ABC-2026-001"],
        )
    ]
    app.running = False
    app.written = False
    app.write_errors = {}
    app.write_notes = {}
    app.batch_dir = None
    app.kept_outputs = []

    app.write_files()
    _settle(app)
    dest = app.batch_dir / "notes.redacted.docx"
    assert dest.is_file()
    kept = dest.read_bytes()
    assert app.written is True
    assert str(source) not in app.write_errors

    app.write_errors[str(source)] = "输出文件夹里这个文件正被别的程序占用"
    app.written = False
    app.write_files(retry=True)
    _settle(app)
    assert dest.read_bytes() == kept
    assert str(source) not in app.write_errors
    assert app.write_notes[str(source)] == MSG_ALREADY_WRITTEN
    assert app.written is True
    kind, text, tag = _scan_status(app.scans[0], app)
    assert kind == "skipped"
    assert text == MSG_ALREADY_WRITTEN
    assert tag == "ok"
    icon, label, color = status_style(kind)
    assert icon == "✓"
    assert label == MSG_ALREADY_WRITTEN
    assert color == OK
    assert tag != "bad"
    assert app.write_failure_rows() == []


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def test_skipped_row_puts_the_file_name_before_the_note(gui):
    from tkinter import ttk

    from doc_redactor.scan import MSG_ALREADY_WRITTEN

    app = gui
    app.apply_shoot_state("res-skipped")
    _settle(app)
    labels = []
    for w in _walk(app.root):
        if isinstance(w, ttk.Treeview) and tuple(w.cget("columns")) == ("name", "n"):
            labels += [w.item(i, "values")[0] for i in w.get_children()]
    row = next(x for x in labels if MSG_ALREADY_WRITTEN in x)
    assert row.index(app.scans[0].path.name) < row.index(MSG_ALREADY_WRITTEN)


def _table_top(app) -> int:
    app.root.update_idletasks()
    app.root.update()
    return app.rules_view._rules_tree.winfo_rooty()


def _open_menu(app):
    rv = app.rules_view
    rv.post_more_menu()
    app.root.update()
    return rv


@pytest.mark.parametrize("how", ["unpost", "escape", "item", "close_gap"])
def test_more_menu_open_then_close_restores_table_top(gui, how):
    app = gui
    app.show_rules_page("list")
    _settle(app)
    base = _table_top(app)
    rv = _open_menu(app)
    assert _table_top(app) > base
    menu = rv._more_menu
    if how == "unpost":
        menu.unpost()
        menu.event_generate("<Unmap>")
    elif how == "escape":
        menu.event_generate("<Escape>")
        menu.unpost()
        menu.event_generate("<Unmap>")
    elif how == "item":
        called = []
        app.ask_restore_defaults = lambda: called.append(1)
        menu.unpost()
        menu.event_generate("<Unmap>")
    else:
        rv._close_menu_gap()
    app.root.update()
    assert _table_top(app) == base
    assert not rv._menu_gap.winfo_manager()
    # and it can open again after closing
    _open_menu(app)
    assert _table_top(app) > base
    rv._close_menu_gap()
    assert _table_top(app) == base
