"""Pager order, preview scrolling, and the rules menu vs table headings."""

from __future__ import annotations

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
    x, y = place_more_menu(
        button=(860, 90, 80, 32),
        menu_size=(128, 30),
        window=(0, 0, 960, 640),
        headings=headings,
    )
    box = (x, y, x + 128, y + 30)
    for heading in headings:
        assert not rects_overlap(box, heading)
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
