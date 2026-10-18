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


def test_place_more_menu_drops_below_every_heading():
    headings = [
        (20, 134, 100, 163),
        (100, 134, 230, 163),
        (230, 134, 280, 163),
        (280, 134, 710, 163),
        (710, 134, 840, 163),
        (840, 134, 950, 163),
    ]
    # First data row's 操作 cell: 修改 / 删除. The column continues downward.
    action = (840, 163, 950, 400)
    x, y = place_more_menu(
        button=(860, 90, 80, 32),
        menu_size=(128, 30),
        window=(0, 0, 960, 640),
        headings=headings,
        avoid=[action],
    )
    box = (x, y, x + 128, y + 30)
    for heading in headings:
        assert not rects_overlap(box, heading)
    assert not rects_overlap(box, action)
    assert y >= 163


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
    assert menu is not None and tree is not None
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
    header_bottom = max(rect[3] for rect in rects)
    assert box[1] >= header_bottom
    kids = tree.get_children()
    cell = tree.bbox(kids[0], "act")
    assert cell and cell[2] > 0 and cell[3] > 0
    rx, ry = tree.winfo_rootx(), tree.winfo_rooty()
    action = (rx + cell[0], ry + cell[1], rx + cell[0] + cell[2], ry + cell[1] + cell[3])
    assert not rects_overlap(box, action), f"操作 {action} overlaps menu {box}"
    assert "修改" in tree.item(kids[0], "values")[-1]
    assert "删除" in tree.item(kids[0], "values")[-1]


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
