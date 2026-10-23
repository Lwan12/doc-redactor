from pathlib import Path

from doc_redactor.scan import (
    CellPlace,
    CommentPlace,
    FileScan,
    LocatedHit,
    column_state,
    cycle_column,
    disable_cell,
    sheet_rows,
)


def _hit(hit_id: int, text: str, column: int, row: int, category: str = "PROJECT_ID") -> LocatedHit:
    return LocatedHit(
        hit_id=hit_id,
        category=category,
        text=text,
        location=f"A{row + 1}",
        place=CellPlace(0, column, row, hidden_row=row == 2, hidden_col=False),
    )


def _scan() -> FileScan:
    scan = FileScan(path=Path("demo.xlsx"), kind="sheet")
    scan.hits = [
        _hit(1, "ABC-2026-001", 0, 0),
        _hit(2, "ABC-2026-001", 0, 2),
        _hit(3, "Acme Pharma", 1, 0, "SPONSOR"),
        LocatedHit(
            hit_id=4,
            category="DRUG",
            text="Fictizumab",
            location="工作表名",
            place=CommentPlace(0, 3, 3, "text"),
        ),
    ]
    return scan


def test_column_cycles_on_off_on():
    scan = _scan()
    assert column_state(scan, 0, 0) == "on"
    cycle_column(scan, 0, 0)
    assert column_state(scan, 0, 0) == "off"
    assert all(not hit.enabled for hit in scan.hits if getattr(hit.place, "column", None) == 0 and type(hit.place).__name__ == "CellPlace")
    cycle_column(scan, 0, 0)
    assert column_state(scan, 0, 0) == "on"


def test_disable_cell_is_noop_when_the_column_is_off():
    scan = _scan()
    cycle_column(scan, 0, 0)
    assert column_state(scan, 0, 0) == "off"
    assert disable_cell(scan, 0, 0, 0) is False
    assert column_state(scan, 0, 0) == "off"


def test_mixed_column_lists_the_disabled_cell_under_it():
    scan = _scan()
    assert disable_cell(scan, 0, 0, 2) is True
    assert column_state(scan, 0, 0) == "mixed"
    rows = sheet_rows(scan, 0, None)
    column = next(row for row in rows if row.action == "column" and row.iid == "col:0:0")
    assert column.mark == "▣"
    assert "含隐藏行" in column.location
    children = [row for row in rows if row.action == "child"]
    assert len(children) == 1
    assert children[0].parent == column.iid
    assert children[0].mark == ""
    assert "3" in children[0].location
    assert "ABC-2026-001" in children[0].text


def test_category_filter_hides_rows_but_the_click_still_flips_every_cell():
    scan = _scan()
    scan.hits.append(_hit(5, "Fictizumab", 0, 1, "DRUG"))
    visible = sheet_rows(scan, 0, "DRUG")
    assert any(row.iid == "col:0:0" for row in visible)
    assert all(row.category in {"", "DRUG"} for row in visible)
    cycle_column(scan, 0, 0)
    assert column_state(scan, 0, 0) == "off"
    assert scan.hits[0].enabled is False
    assert scan.hits[1].enabled is False
