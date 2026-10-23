"""Result screen for a spreadsheet. Column rows, a yellow cell, and sheet tabs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from doc_redactor.scan import (
    KIND_SHEET,
    KIND_WORD,
    CellPlace,
    CommentPlace,
    FileScan,
    HeaderFooterPlace,
    LocatedHit,
    SheetCellView,
    SheetNamePlace,
    SheetView,
)


def _hit(hit_id, category, text, location, place, unit):
    return LocatedHit(
        hit_id=hit_id,
        category=category,
        text=text,
        location=location,
        unit=unit,
        start=0,
        end=len(text),
        page=0,
        place=place,
    )


def _sheet_scan() -> FileScan:
    hits = [
        _hit(1, "PROJECT_ID", "ABC-2026-001", "A1", CellPlace(0, 0, 0), "s:0:c:0:0"),
        _hit(2, "PROJECT_ID", "ABC-2026-001", "A3，隐藏行", CellPlace(0, 0, 2, hidden_row=True), "s:0:c:0:2"),
        _hit(3, "SPONSOR", "Acme Pharma", "B1", CellPlace(0, 1, 0), "s:0:c:1:0"),
        _hit(4, "DRUG", "Fictizumab", "D4 批注", CommentPlace(0, 3, 3, "text"), "s:0:note:3:3:text"),
        _hit(5, "PROJECT_ID", "ABC-2026-001", "页眉", HeaderFooterPlace(0, "h:odd", 0), "s:0:h:odd:0"),
        _hit(6, "DRUG", "Fictizumab", "工作表名", SheetNamePlace(0), "s:0:name"),
        _hit(7, "DRUG", "Fictizumab", "A1", CellPlace(1, 0, 0), "s:1:c:0:0"),
    ]
    sheets = (
        SheetView(
            title="Fictizumab",
            index=0,
            cells=(
                SheetCellView(0, 0, "ABC-2026-001", False, False, False, (1,)),
                SheetCellView(0, 1, '="ABC-2026-001"', True, False, False, ()),
                SheetCellView(0, 2, "ABC-2026-001", False, True, False, (2,)),
                SheetCellView(1, 0, "Acme Pharma", False, False, False, (3,)),
            ),
        ),
        SheetView(
            title="Notes",
            index=1,
            cells=(SheetCellView(0, 0, "Fictizumab", False, False, False, (7,)),),
        ),
    )
    return FileScan(path=Path("demo.xlsx"), kind=KIND_SHEET, status="ok", hits=hits, sheets=sheets)


def _word_scan() -> FileScan:
    hit = LocatedHit(
        hit_id=11,
        category="PROJECT_ID",
        text="ABC-2026-001",
        location="第 1 段",
        unit="p:0",
        start=0,
        end=12,
    )
    return FileScan(path=Path("note.docx"), kind=KIND_WORD, status="ok", hits=[hit], paragraphs=["ABC-2026-001"])


@pytest.fixture
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


def _show(app, scans, selected=0) -> None:
    app.scans = scans
    app.selected = selected
    app.written = False
    app.preview_mode = "orig"
    app.hide_text = False
    app.sheet_tab = 0
    app.show("result")
    app.root.update()


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _labels(app, text: str):
    return [w for w in _walk(app.result_view.frame) if w.winfo_class() == "Label" and text in str(w.cget("text"))]


def _notebooks(app):
    from tkinter import ttk

    return [w for w in _walk(app.result_view.frame) if isinstance(w, ttk.Notebook)]


def _click_tree(app, iid: str) -> None:
    tree = app.result_view.loc_tree
    tree.see(iid)
    app.root.update()
    box = tree.bbox(iid, "#1")
    assert box is not None, iid
    tree.event_generate("<Button-1>", x=box[0] + 4, y=box[1] + 4)
    app.root.update()


def _click_yellow(app) -> None:
    canvas = app.result_view._sheet_canvas
    rects = [item for item in canvas.find_withtag("yellow") if canvas.type(item) == "rectangle"]
    assert rects
    x1, y1, x2, y2 = canvas.bbox(rects[0])
    canvas.event_generate("<Button-1>", x=(x1 + x2) // 2, y=(y1 + y2) // 2)
    app.root.update()


def _enabled(scan: FileScan, hit_id: int) -> bool:
    return next(hit.enabled for hit in scan.hits if hit.hit_id == hit_id)


def test_yellow_cell_unchecks_and_shows_under_the_column(gui):
    app = gui
    scan = _sheet_scan()
    _show(app, [scan, _word_scan()])
    books = _notebooks(app)
    assert len(books) == 1
    assert books[0].tab(0, "text") == "Fictizumab"
    assert books[0].tab(1, "text") == "Notes"
    assert _labels(app, "公式未改")
    assert _labels(app, "表格")
    tree = app.result_view.loc_tree
    assert "col:0:0" in tree.get_children("")
    assert "cell:0:0:0" not in tree.get_children("col:0:0")
    assert "整列" in tree.item("col:0:0", "values")[1]
    assert "hit:4" in tree.get_children("")
    assert "批注" in tree.item("hit:4", "values")[1]
    assert "页眉" in tree.item("hit:5", "values")[1]
    assert "工作表名" in tree.item("hit:6", "values")[1]
    _click_yellow(app)
    tree = app.result_view.loc_tree
    assert tree.item("col:0:0", "values")[0] == "▣"
    child = tree.get_children("col:0:0")
    assert child == ("cell:0:0:0",)
    assert tree.item(child[0], "values")[0] == ""
    assert "A1" in tree.item(child[0], "values")[1]
    assert "ABC-2026-001" in tree.item(child[0], "values")[3]
    assert _enabled(scan, 1) is False
    assert _enabled(scan, 2) is True
    _click_tree(app, "col:0:0")
    tree = app.result_view.loc_tree
    assert tree.item("col:0:0", "values")[0] == "☑"
    assert tree.get_children("col:0:0") == ()
    assert _enabled(scan, 1) is True
    assert _enabled(scan, 2) is True
    _click_tree(app, "col:0:0")
    tree = app.result_view.loc_tree
    assert tree.item("col:0:0", "values")[0] == "☐"
    assert _enabled(scan, 1) is False
    assert _enabled(scan, 2) is False
    canvas = app.result_view._sheet_canvas
    for item in canvas.find_withtag("c-0-0") + canvas.find_withtag("c-0-2"):
        assert "yellow" not in canvas.gettags(item)
    x1, y1, x2, y2 = canvas.bbox("c-0-0")
    canvas.event_generate("<Button-1>", x=(x1 + x2) // 2, y=(y1 + y2) // 2)
    app.root.update()
    assert _enabled(scan, 1) is False
    book = app.result_view._sheet_book
    book.select(1)
    app.root.update()
    tree = app.result_view.loc_tree
    assert "col:1:0" in tree.get_children("")
    assert tree.get_children("col:1:0") == ()
    assert "整列" in tree.item("col:1:0", "values")[1]
    assert "hit:4" not in tree.get_children("")
    assert "hit:5" not in tree.get_children("")
    assert "hit:6" not in tree.get_children("")


def test_formula_stays_visible_after_replacement_preview(gui):
    app = gui
    scan = _sheet_scan()
    _show(app, [scan])
    app.preview_mode = "after"
    app.result_view.refresh()
    app.root.update()
    assert _labels(app, "公式未改")
    canvas = app.result_view._sheet_canvas
    texts = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
    assert any(text.startswith("=") and "ABC-2026-001" in text for text in texts)
    app.written = True
    app.result_view.refresh()
    app.root.update()
    assert _labels(app, "公式未改")


def test_all_files_list_stays_flat_and_a_cell_click_does_not_turn_back_on(gui):
    app = gui
    scan = _sheet_scan()
    word = _word_scan()
    _show(app, [scan, word], selected="all")
    assert _notebooks(app) == []
    tree = app.result_view.loc_tree
    assert tree.get_children("") == tuple(str(hit.hit_id) for hit in scan.hits + word.hits)
    _click_tree(app, "1")
    assert _enabled(scan, 1) is False
    assert _enabled(scan, 2) is True
    _click_tree(app, "1")
    assert _enabled(scan, 1) is False
    _show(app, [scan, word], selected=1)
    assert _notebooks(app) == []
    assert app.result_view.loc_tree.exists("11")
