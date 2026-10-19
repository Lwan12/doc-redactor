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


def _visible_copy(widget) -> str:
    chunks = []
    for node in _walk(widget):
        try:
            value = node.cget("text")
        except Exception:
            continue
        # Hand-wrapped labels keep the unwrapped sentence here.
        value = getattr(node, "logical_text", value)
        if isinstance(value, str) and value:
            chunks.append(value)
    return "\n".join(chunks)


def test_blank_only_result_notice_and_column_wrap(gui):
    import tkinter as tk

    from doc_redactor.pdf_redactor import MSG_FIT_BLANK
    from doc_redactor.scan import KIND_PDF, FileScan, LocatedHit

    app = gui
    blank = FileScan(
        path=Path("blank_only.pdf"),
        kind=KIND_PDF,
        status="ok",
        hits=[
            LocatedHit(
                hit_id=1,
                category="申办方",
                text="I",
                location="第 1 页 · 第 1 行",
                unit="page:0",
                start=0,
                end=1,
                rects=[(72.0, 80.0, 82.0, 88.0)],
                page=0,
            )
        ],
        pages=[["I"]],
    )
    lost = FileScan(
        path=Path("lost.pdf"),
        kind=KIND_PDF,
        status="ok",
        hits=[
            LocatedHit(
                hit_id=2,
                category="申办方",
                text="Acme",
                location="第 1 页 · 第 1 行",
                unit="page:0",
                start=0,
                end=4,
                rects=[(72.0, 80.0, 280.0, 110.0)],
                page=0,
            )
        ],
        pages=[["Acme"]],
        unlocated_count=2,
    )
    app.scans = [blank, lost]
    app.selected = 0
    app.written = False
    app.write_errors = {}
    app.write_notes = {}
    app.preview_mode = "orig"
    app.hide_text = False
    app.show("result")
    _settle(app)
    app.root.update_idletasks()
    app.root.update()

    notices = []
    for node in _walk(app.root):
        if not isinstance(node, tk.Label):
            continue
        try:
            text = node.cget("text")
        except tk.TclError:
            continue
        if "只留了空白" in text or "无法定位" in text:
            notices.append(node)
    assert notices
    assert MSG_FIT_BLANK.format(m=1) in _visible_copy(app.root)
    assert "2\u00a0处无法定位，未写出" in _visible_copy(app.root)
    assert "换成短码" not in _visible_copy(app.root)
    import tkinter.font as tkfont

    from doc_redactor.gui.result_view import NO_LINE_START

    for node in notices:
        # The label is laid out by hand so a comma never starts a line.
        lines = node.cget("text").split("\n")
        parent_w = node.master.winfo_width()
        assert parent_w > 80
        assert int(node.cget("wraplength")) == 0
        measure = tkfont.Font(font=node.cget("font")).measure
        for line in lines:
            assert line and line[0] not in NO_LINE_START
            assert measure(line) <= parent_w - 8
        assert "".join(lines).replace(" ", "") == node.logical_text.replace(" ", "")


def test_mixed_unlocated_row_warns_and_write_button_skips_it(gui):
    """A file with both placed and unplaced hits is warned before writing.

    The write button counts only files that will actually be written.
    """
    from tkinter import ttk

    from doc_redactor.gui.result_view import _scan_status
    from doc_redactor.gui.theme import status_style
    from doc_redactor.scan import KIND_PDF, FileScan, LocatedHit

    def _hit(hit_id: int, text: str) -> LocatedHit:
        return LocatedHit(
            hit_id=hit_id,
            category="申办方",
            text=text,
            location="第 1 页 · 第 1 行",
            unit="page:0",
            start=0,
            end=len(text),
            rects=[(72.0, 80.0, 140.0, 94.0)],
            page=0,
        )

    kept = FileScan(
        path=Path("kept.pdf"),
        kind=KIND_PDF,
        status="ok",
        hits=[_hit(1, "Acme")],
        pages=[["Acme"]],
    )
    mixed = FileScan(
        path=Path("mixed.pdf"),
        kind=KIND_PDF,
        status="ok",
        hits=[_hit(2, "星辰海")],
        pages=[["星辰海"]],
        unlocated_count=1,
    )
    app = gui
    app.scans = [kept, mixed]
    app.selected = 0
    app.written = False
    app.write_errors = {}
    app.write_notes = {}
    app.preview_mode = "orig"
    app.hide_text = False
    app.show("result")
    _settle(app)

    kind, text, tag = _scan_status(mixed, app)
    assert kind == "unlocated"
    assert tag == "warn"
    assert text == "1 处无法定位"
    icon, _label, _color = status_style(kind)
    assert icon == "⚠"

    labels = []
    for node in _walk(app.root):
        if isinstance(node, ttk.Treeview) and tuple(node.cget("columns")) == ("name", "n"):
            labels += [node.item(item, "values")[0] for item in node.get_children()]
    mixed_row = next(row for row in labels if "mixed.pdf" in row)
    kept_row = next(row for row in labels if "kept.pdf" in row)
    assert mixed_row.startswith("⚠")
    assert "mixed.pdf" in mixed_row
    assert "1 处无法定位" in mixed_row
    assert mixed_row.index("mixed.pdf") < mixed_row.index("无法定位")
    assert kept_row.startswith("✓")
    assert "无法定位" not in kept_row

    buttons = [node.cget("text") for node in _walk(app.root) if isinstance(node, ttk.Button)]
    assert any(label.startswith("写出 1 个脱敏文件") for label in buttons)
    assert not any("写出 2 个" in label for label in buttons)
    write_label = next(label for label in buttons if label.startswith("写出 "))
    assert "替换 1 处" in write_label
    assert "替换 2 处" not in write_label


def test_pdf_fit_notices_use_the_product_copy(gui):
    from doc_redactor.pdf_redactor import MSG_FIT_BLANK, MSG_FIT_SHORT, MSG_PDF_WIDTH, MSG_REPL_HINT

    app = gui
    app.apply_shoot_state("res-pdf")
    _settle(app)
    copy = _visible_copy(app.root)
    assert MSG_FIT_SHORT.format(n=2) in copy
    assert MSG_FIT_BLANK.format(m=1) in copy
    assert "有\u00a02\u00a0处" in copy
    assert "另有\u00a01\u00a0处" in copy
    assert MSG_PDF_WIDTH in copy
    assert "正则" not in copy

    app.apply_shoot_state("rule-placeholder")
    _settle(app)
    rules = _visible_copy(app.root)
    assert MSG_REPL_HINT in rules
    assert "正则" not in rules

    app.apply_shoot_state("words-list")
    _settle(app)
    words = _visible_copy(app.root)
    assert MSG_REPL_HINT in words
    assert "正则" not in words

    app.apply_shoot_state("plan-list")
    _settle(app)
    plan = _visible_copy(app.root)
    assert MSG_REPL_HINT in plan
    assert "正则" not in plan

    app.apply_shoot_state("main-rules")
    _settle(app)
    main = _visible_copy(app.root)
    assert "截断" not in main
    assert "换成短码" in main
    assert "正则" not in main


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


def _wait_scan(app, limit: float = 20.0) -> None:
    import time

    deadline = time.time() + limit
    app.root.update()
    while app.running and time.time() < deadline:
        app.root.update()
        time.sleep(0.02)
    for _ in range(5):
        app.root.update()
    assert not app.running


def test_relabel_only_keeps_unchecked_and_redraws_preview(gui, tmp_path, monkeypatch):
    from docx import Document

    import doc_redactor.gui.app as app_mod

    class _Inline:
        """Run the scan worker on the Tk thread; there is no mainloop in tests."""

        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(app_mod, "Thread", _Inline)

    from doc_redactor.ruleset import KIND_WORDLIST, Rule

    app = gui
    source = tmp_path / "memo.docx"
    document = Document()
    document.add_paragraph("申办方星辰海医药，合作方星辰海研究所。")
    document.save(source)
    app.ruleset.rules = [Rule(name="申办方", kind=KIND_WORDLIST, terms=["星辰海"], replacement="[甲方]")]
    app.files = [source]
    (tmp_path / "out").mkdir()
    app.output_var.set(str(tmp_path / "out"))
    app.output_error = None
    app.start_scan()
    _wait_scan(app)
    hits = app.scans[0].hits
    assert len(hits) == 2
    hits[1].enabled = False
    app._clear_preview()
    app.ruleset.rules[0].replacement = "[SPONSOR-X]"
    app.start_scan()
    _wait_scan(app)
    assert app.note_cleared is False
    assert [h.enabled for h in app.scans[0].hits] == [True, False]
    app.preview_mode = "after"
    app.show("result")
    _settle(app)
    body = app.result_view._preview_text.get("1.0", "end")
    assert "[SPONSOR-X]" in body
    assert "[甲方]" not in body
    shots = os.environ.get("DOC_REDACTOR_RELABEL_SHOT")
    if shots:
        from PIL import ImageGrab

        root = app.root
        menu = getattr(app.rules_view, "_more_menu", None)
        if menu is not None:
            menu.unpost()  # left open by an earlier test in this module
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(shots)

    app.scans[0].hits[1].enabled = False
    app._clear_preview()
    app.ruleset.rules[0].terms = ["星辰海", "星海"]
    app.start_scan()
    _wait_scan(app)
    assert app.note_cleared is True
    assert all(h.enabled for h in app.scans[0].hits)


def _s2_scans():
    from doc_redactor.scan import KIND_PDF, FileScan, LocatedHit

    def _hit(hit_id: int, text: str) -> LocatedHit:
        return LocatedHit(
            hit_id=hit_id,
            category="申办方",
            text=text,
            location="第 1 页 · 第 1 行",
            unit="page:0",
            start=0,
            end=len(text),
            rects=[(72.0, 80.0, 140.0, 94.0)],
            page=0,
        )

    kept = FileScan(
        path=Path("kept.pdf"),
        kind=KIND_PDF,
        status="ok",
        hits=[_hit(1, "Acme"), _hit(2, "Acme"), _hit(3, "Acme")],
        pages=[["Acme Acme Acme"]],
    )
    lost = FileScan(
        path=Path("lost.pdf"),
        kind=KIND_PDF,
        status="ok",
        hits=[_hit(4, "星辰海"), _hit(5, "星辰海"), _hit(6, "星辰海")],
        pages=[["星辰海 星辰海 星辰海"]],
        unlocated_count=2,
    )
    return kept, lost


def test_writable_total_skips_blocked_and_failed_files():
    from doc_redactor.gui.result_view import writable_hit_total

    kept, lost = _s2_scans()
    assert writable_hit_total([kept, lost], written=False, write_errors={}) == 3
    kept.hits[0].enabled = False
    assert writable_hit_total([kept, lost], written=False, write_errors={}) == 2
    # After a write, a file the write refused no longer counts.
    errors = {str(kept.path): "占用"}
    assert writable_hit_total([kept, lost], written=True, write_errors=errors) == 0
    # Before writing, stale errors are ignored.
    assert writable_hit_total([kept, lost], written=False, write_errors=errors) == 2


def _summary_number(app, verb: str) -> str:
    import tkinter as tk

    for node in _walk(app.root):
        if isinstance(node, tk.Label) and node.cget("text").strip() == verb:
            siblings = node.master.pack_slaves()
            return siblings[siblings.index(node) + 1].cget("text")
    raise AssertionError(f"no {verb} label")


def test_summary_count_matches_the_write_button(gui):
    """S2: the top 将替换 N 处 used to include hits of a blocked file."""
    from tkinter import ttk

    kept, lost = _s2_scans()
    app = gui
    app.scans = [kept, lost]
    app.selected = 0
    app.written = False
    app.write_errors = {}
    app.write_notes = {}
    app.preview_mode = "orig"
    app.hide_text = False
    app.show("result")
    _settle(app)

    buttons = [node.cget("text") for node in _walk(app.root) if isinstance(node, ttk.Button)]
    write_label = next(label for label in buttons if label.startswith("写出 "))
    assert "替换 3 处" in write_label
    assert _summary_number(app, "将替换") == "3"
    rows = []
    for node in _walk(app.root):
        if isinstance(node, ttk.Treeview) and tuple(node.cget("columns")) == ("name", "n"):
            rows += [node.item(item, "values") for item in node.get_children()]
    all_row = next(row for row in rows if "全部文件" in str(row[0]))
    assert str(all_row[1]) == "3"
    lost_row = next(row for row in rows if "lost.pdf" in str(row[0]))
    assert str(lost_row[1]) == "—"
    # Blocked hits are not hits the user cancelled.
    assert "你取消了" not in _visible_copy(app.root)

    # After a write the blocked file is a 没写出 row; it still adds nothing.
    app.written = True
    app.write_errors = {str(lost.path): "2 处无法定位，未写出"}
    app.show("result")
    _settle(app)
    rows = []
    for node in _walk(app.root):
        if isinstance(node, ttk.Treeview) and tuple(node.cget("columns")) == ("name", "n"):
            rows += [node.item(item, "values") for item in node.get_children()]
    assert str(next(row for row in rows if "全部文件" in str(row[0]))[1]) == "3"
    assert str(next(row for row in rows if "lost.pdf" in str(row[0]))[1]) == "—"
    assert _summary_number(app, "已替换") == "3"


_S3_TEXT = "⚠ 另有\u00a02\u00a0处太窄，连短码也放不下，只留了空白，原文已删除。 2\u00a0处无法定位，未写出"


@pytest.mark.parametrize("width", range(4, 60))
def test_wrapped_lines_never_start_with_a_comma(width):
    """S3: Tk breaks a long CJK run anywhere, so 「，」 could start a line."""
    from doc_redactor.gui.result_view import NO_LINE_START, wrap_lines

    lines = wrap_lines(_S3_TEXT, width, len)
    for line in lines:
        assert line, lines
        assert line[0] not in NO_LINE_START, lines
        assert not line.startswith(" ") and not line.endswith(" ")
    # Only normal spaces at a break are dropped; nothing else is lost.
    assert "".join(lines).replace(" ", "") == _S3_TEXT.replace(" ", "")
    for line in lines:
        assert not line.endswith("\u00a0") and not line.startswith("\u00a0")
    # The count and 处 stay together.
    assert all("2\u00a0处" in line or "2" not in line for line in lines)


def test_wrap_lines_fits_the_width_when_it_can():
    from doc_redactor.gui.result_view import wrap_lines

    for width in range(8, 60):
        for line in wrap_lines(_S3_TEXT, width, len):
            # A glued unit (count + 处, or a char plus its comma) may hang by 1.
            assert len(line) <= width + 1


@pytest.mark.parametrize("padx", [6, 8])
@pytest.mark.parametrize("width", [180, 233, 260, 297, 320, 341])
def test_wrap_label_fits_inside_its_pack_padding(gui, padx, width):
    """The middle column packs the notice with padx; no glyph may be clipped."""
    import tkinter as tk

    from doc_redactor.gui.result_view import _wrap_label
    from doc_redactor.pdf_redactor import MSG_FIT_BLANK, MSG_FIT_SHORT

    app = gui
    host = tk.Toplevel(app.root)
    try:
        frame = tk.Frame(host, width=width, height=200)
        frame.pack_propagate(False)
        frame.pack()
        text = MSG_FIT_SHORT.format(n=3) + " " + MSG_FIT_BLANK.format(m=2)
        label = _wrap_label(frame, text, bg="white", fg="black", font=app.fonts["small"])
        label.pack(fill="x", padx=padx)
        for _ in range(3):
            host.update_idletasks()
            host.update()
        assert frame.winfo_width() == width
        # Everything Tk wants to draw fits in the space the pack padding leaves.
        assert label.winfo_reqwidth() <= width - 2 * padx, (label.cget("text"), label.winfo_reqwidth())
    finally:
        host.destroy()


def test_unwritten_file_is_not_ticked_after_write(gui):
    """After a write, hits of a file that was not written say 未写出, not ✓."""
    import tkinter as tk
    from tkinter import ttk

    kept, lost = _s2_scans()
    app = gui
    app.scans = [kept, lost]
    app.selected = "all"
    app.selected_cat = None
    app.written = True
    app.write_errors = {str(lost.path): "2 处无法定位，未写出"}
    app.write_notes = {}
    app.preview_mode = "orig"
    app.hide_text = False
    app.show("result")
    _settle(app)

    marks = {}
    cat_rows = []
    for node in _walk(app.root):
        if isinstance(node, ttk.Treeview) and tuple(node.cget("columns")) == ("on", "loc", "cat", "text"):
            for item in node.get_children():
                marks[int(item)] = str(node.item(item, "values")[0])
        if isinstance(node, ttk.Treeview) and tuple(node.cget("columns")) == ("cat", "n"):
            cat_rows += [tuple(str(v) for v in node.item(item, "values")) for item in node.get_children()]
    lost_ids = {hit.hit_id for hit in lost.hits}
    assert marks and all(marks[i] == "未写出" for i in lost_ids), marks
    assert all(marks[h.hit_id] == "✓" for h in kept.hits), marks
    # The category total matches 已替换; the unwritten hits are listed apart.
    assert ("合计", "3") in cat_rows, cat_rows
    assert ("未写出", "3") in cat_rows, cat_rows
    # The middle matrix names the file that was not written and leaves it out.
    copy = [getattr(n, "logical_text", n.cget("text")) for n in _walk(app.root) if isinstance(n, tk.Label)]
    assert "lost.pdf 未写出，不计入合计" in copy



def _m34_hit(hit_id: int, text: str, unit: str):
    from doc_redactor.scan import LocatedHit

    return LocatedHit(
        hit_id=hit_id, category="申办方", text=text, location="第 1 页", unit=unit,
        start=0, end=len(text), rects=[(72.0, 80.0, 140.0, 94.0)], page=0,
    )


def test_no_hit_file_after_write_is_not_shown_as_written(gui):
    """M34-1：0 命中的文件写出后，列表里不能是绿色「✓ 已写出，已跳过」（文案待前端定）。"""
    from tkinter import ttk

    from doc_redactor.gui.result_view import _scan_status
    from doc_redactor.leak_check import MSG_NO_HITS
    from doc_redactor.scan import KIND_PDF, FileScan

    line = "没有命中，没有写出脱敏件。不要把原件当成脱敏件使用"
    written = FileScan(
        path=Path("kept.pdf"), kind=KIND_PDF, status="ok", hits=[_m34_hit(1, "Acme", "page:0")], pages=[["Acme"]]
    )
    clean = FileScan(path=Path("appendix.pdf"), kind=KIND_PDF, status="nohit", hits=[], pages=[["nothing"]])
    app = gui
    app.scans = [written, clean]
    app.selected = 1
    app.written = True
    app.write_errors = {}
    app.write_notes = {str(clean.path): MSG_NO_HITS}
    app.preview_mode = "orig"
    app.hide_text = False
    app.show("result")
    _settle(app)

    kind, _text, tag = _scan_status(clean, app)
    assert kind != "skipped" and tag != "ok"
    rows = []
    for node in _walk(app.root):
        if isinstance(node, ttk.Treeview) and tuple(node.cget("columns")) == ("name", "n"):
            rows += [(node.item(item, "values"), node.item(item, "tags")) for item in node.get_children()]
    values, tags = next((v, t) for v, t in rows if "appendix.pdf" in v[0])
    assert not values[0].startswith("✓"), values
    assert "已写出" not in values[0] and "已跳过" not in values[0]
    assert "ok" not in tags
    copy = _visible_copy(app.root)
    assert line in copy
    assert "之前已写出" not in copy
