from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XLImage
from PIL import Image as PILImage

from doc_redactor.cli import main
from doc_redactor.config import load_rules
from doc_redactor.scan import CellPlace, CommentPlace, scan_path, write_scans
from doc_redactor.sheet import MSG_ENCRYPTED_SHEET, MSG_PICTURE_SHEET, extract_formulas, extract_in_scope
from tests.helpers import RULES

TOKENS = ("ABC-2026-001", "Acme Pharma", "Fictizumab")
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def _engine():
    return load_rules(RULES)


def _xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Fictizumab"
    ws["A1"] = "ABC-2026-001"
    ws["B1"] = "Acme Pharma"
    ws["C1"] = "Fictizumab"
    ws["A2"] = '="ABC-2026-001"'
    ws.row_dimensions[3].hidden = True
    ws["A3"] = "ABC-2026-001"
    ws.column_dimensions["G"].hidden = True
    ws["G1"] = "Fictizumab"
    ws["D4"] = "hello"
    ws["D4"].comment = Comment("batch Fictizumab", "Reviewer")
    ws.oddHeader.left.text = "ABC-2026-001"
    ws.oddFooter.center.text = "Fictizumab"
    ws["A10"] = 1
    ws["A11"] = 2
    chart = BarChart()
    chart.title = "Fictizumab"
    chart.add_data(Reference(ws, min_col=1, min_row=10, max_row=11))
    ws.add_chart(chart, "E10")
    wb.save(path)


def _xls(path: Path) -> None:
    import xlwt
    from xlwt import Formula

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Fictizumab")
    ws.write(0, 0, "ABC-2026-001")
    ws.write(1, 1, "Acme Pharma")
    ws.write(0, 2, "Fictizumab")
    ws.write(2, 2, Formula('"ABC-2026-001"'))
    ws.header_str = b"&LABC-2026-001"
    ws.footer_str = b"&CFictizumab"
    ws.row(3).hidden = 1
    ws.write(3, 0, "ABC-2026-001")
    ws.col(6).hidden = 1
    ws.write(0, 6, "Fictizumab")
    wb.save(str(path))


def _csv(path: Path) -> None:
    path.write_text(
        "ABC-2026-001,Acme Pharma,Fictizumab\n=\"ABC-2026-001\",ok,ok\n",
        encoding="utf-8",
        newline="",
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, outdir: Path, *, disable_text: str | None = None):
    engine = _engine()
    before = _digest(path)
    scan = scan_path(path, engine)
    if disable_text is not None:
        for hit in scan.hits:
            if hit.text == disable_text and getattr(hit.place, "row", None) == 0 and getattr(hit.place, "column", None) == 1:
                hit.enabled = False
    results = write_scans([scan], outdir, replacements=engine.replacements, overwrite=True, engine=engine)
    assert _digest(path) == before
    return scan, results[0]


def _assert_clean(dest: Path) -> None:
    blob = extract_in_scope(dest).blob()
    for token in TOKENS:
        assert blob.count(token) == 0
    formulas = "\n".join(extract_formulas(dest))
    assert "ABC-2026-001" in formulas


def test_xlsx_redacts_in_scope_and_keeps_formula_and_chart(tmp_path: Path):
    src = tmp_path / "demo.xlsx"
    _xlsx(src)
    _scan, result = _write(src, tmp_path / "out")
    assert result.dest is not None and result.dest.is_file()
    _assert_clean(result.dest)
    with zipfile.ZipFile(result.dest) as package:
        chart = package.read("xl/charts/chart1.xml")
    assert b"Fictizumab" in chart


def test_xlsx_comment_part_drops_a_replaced_author(tmp_path: Path, capsys):
    src = tmp_path / "demo.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "hello"
    ws["A1"].comment = Comment("batch Fictizumab", "Acme Pharma")
    wb.save(src)
    out = tmp_path / "out"
    code = main([str(src), "-r", str(RULES), "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    dest = out / "demo.redacted.xlsx"
    with zipfile.ZipFile(dest) as package:
        xml = package.read("xl/comments/comment1.xml").decode("utf-8")
    assert "Acme Pharma" not in xml
    assert "[SPONSOR]" in xml
    assert "Fictizumab" not in xml


def test_shared_comment_author_stays_when_one_comment_keeps_it(tmp_path: Path):
    src = tmp_path / "demo.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"].comment = Comment("seen", "Acme Pharma")
    ws["B1"].comment = Comment("kept", "Acme Pharma")
    wb.save(src)
    engine = _engine()
    scan = scan_path(src, engine)
    for hit in scan.hits:
        place = hit.place
        if isinstance(place, CommentPlace) and place.part == "author" and place.column == 1:
            hit.enabled = False
    result = write_scans(
        [scan],
        tmp_path / "out",
        replacements=engine.replacements,
        overwrite=True,
        engine=engine,
    )[0]
    assert result.dest is not None
    with zipfile.ZipFile(result.dest) as package:
        xml = package.read("xl/comments/comment1.xml").decode("utf-8")
    assert xml.count("Acme Pharma") == 1
    assert 'ref="B1" authorId="0"' in xml
    assert "[SPONSOR]" in xml


def test_xlsx_unchecked_cell_keeps_its_token(tmp_path: Path):
    src = tmp_path / "demo.xlsx"
    _xlsx(src)
    _scan, result = _write(src, tmp_path / "out", disable_text="Acme Pharma")
    assert result.dest is not None
    blob = extract_in_scope(result.dest).blob()
    assert blob.count("Acme Pharma") == 1
    assert blob.count("ABC-2026-001") == 0
    assert blob.count("Fictizumab") == 0
    assert "ABC-2026-001" in "\n".join(extract_formulas(result.dest))


def test_xls_redacts_in_scope_and_keeps_formula(tmp_path: Path):
    src = tmp_path / "demo.xls"
    _xls(src)
    _scan, result = _write(src, tmp_path / "out")
    assert result.dest is not None and result.dest.is_file()
    assert result.dest.suffix == ".xls"
    _assert_clean(result.dest)


def test_csv_redacts_values_and_keeps_formula_field(tmp_path: Path):
    src = tmp_path / "demo.csv"
    _csv(src)
    _scan, result = _write(src, tmp_path / "out")
    assert result.dest is not None and result.dest.is_file()
    _assert_clean(result.dest)
    text = result.dest.read_text(encoding="utf-8")
    assert '="ABC-2026-001"' in text


def test_encrypted_xlsx_fails_with_its_sentence_and_writes_nothing(tmp_path: Path):
    src = tmp_path / "locked.xlsx"
    src.write_bytes(OLE_MAGIC + b"\x00" * 512)
    out = tmp_path / "out"
    scan, result = _write(src, out)
    assert scan.status == "fail"
    assert scan.error == MSG_ENCRYPTED_SHEET
    assert result.dest is None
    assert list(out.glob("*")) == []


def test_picture_xlsx_fails_with_its_sentence_and_writes_nothing(tmp_path: Path):
    src = tmp_path / "pict.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "ABC-2026-001"
    png = tmp_path / "dot.png"
    PILImage.new("RGB", (8, 8), "white").save(png)
    ws.add_image(XLImage(str(png)), "C3")
    wb.save(src)
    out = tmp_path / "out"
    scan, result = _write(src, out)
    assert scan.status == "fail"
    assert scan.error == MSG_PICTURE_SHEET
    assert result.dest is None
    assert list(out.glob("*")) == []


def test_xlsm_is_rejected_and_not_written(tmp_path: Path):
    src = tmp_path / "macro.xlsm"
    good = tmp_path / "demo.xlsx"
    _xlsx(good)
    src.write_bytes(good.read_bytes())
    out = tmp_path / "out"
    scan, result = _write(src, out)
    assert scan.status == "fail"
    assert ".xlsm" not in (scan.error or "")
    assert "表格" in (scan.error or "")
    assert result.dest is None
    assert list(out.glob("*")) == []


def test_cli_csv_keeps_the_formula_and_writes_the_file(tmp_path: Path, capsys):
    src = tmp_path / "demo.csv"
    _csv(src)
    out = tmp_path / "out"
    code = main([str(src), "-r", str(RULES), "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    dest = out / "demo.redacted.csv"
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    assert '="ABC-2026-001"' in text
    assert "ABC-2026-001" not in text.splitlines()[0]


def test_cli_failures_keep_their_sentences_and_write_nothing(tmp_path: Path, capsys):
    locked = tmp_path / "locked.xlsx"
    locked.write_bytes(OLE_MAGIC + b"\x00" * 512)
    locked_out = tmp_path / "locked-out"
    code = main([str(locked), "-r", str(RULES), "-o", str(locked_out)])
    captured = capsys.readouterr()
    assert code == 1
    assert MSG_ENCRYPTED_SHEET in captured.err
    assert list(locked_out.glob("*")) == []

    pictured = tmp_path / "pict.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "ABC-2026-001"
    png = tmp_path / "dot.png"
    PILImage.new("RGB", (8, 8), "white").save(png)
    ws.add_image(XLImage(str(png)), "C3")
    wb.save(pictured)
    pictured_out = tmp_path / "pict-out"
    code = main([str(pictured), "-r", str(RULES), "-o", str(pictured_out)])
    captured = capsys.readouterr()
    assert code == 1
    assert MSG_PICTURE_SHEET in captured.err
    assert list(pictured_out.glob("*")) == []


# openpyxl 3.1 writes inline strings. Excel writes one shared-string table.
_SST_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
_SST_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
_QA_TOKEN = "QA_REDACT_7F3A91"
_QA_PLACEHOLDER = "[REDACTED]"


def _qa_rules(path: Path) -> Path:
    path.write_text(
        "regex:\n"
        "  - name: QA\n"
        f"    pattern: '{_QA_TOKEN}'\n"
        f'    replacement: "{_QA_PLACEHOLDER}"\n',
        encoding="utf-8",
    )
    return path


def _as_shared_strings(path: Path) -> None:
    with zipfile.ZipFile(path) as package:
        files = {name: package.read(name) for name in package.namelist()}
    strings: list[str] = []
    uses = 0
    for name, data in list(files.items()):
        if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
            continue
        xml = data.decode("utf-8")

        def cell(match: re.Match[str]) -> str:
            nonlocal uses
            attrs, inner = match.group(1), match.group(2)
            if inner is None or 't="inlineStr"' not in attrs:
                return match.group(0)
            text = "".join(re.findall(r"<t\b[^>]*>(.*?)</t>", inner, re.DOTALL))
            if text not in strings:
                strings.append(text)
            index = strings.index(text)
            uses += 1
            attrs = attrs.replace(' t="inlineStr"', ' t="s"')
            return f"<c{attrs}><v>{index}</v></c>"

        files[name] = re.sub(r"<c\b([^>/]*?)(?:/>|>(.*?)</c>)", cell, xml, flags=re.DOTALL).encode("utf-8")
    items = "".join(f"<si><t>{text}</t></si>" for text in strings)
    files["xl/sharedStrings.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{uses}" uniqueCount="{len(strings)}">{items}</sst>'
    ).encode("utf-8")
    types = files["[Content_Types].xml"].decode("utf-8")
    if "sharedStrings" not in types:
        files["[Content_Types].xml"] = types.replace(
            "</Types>",
            f'<Override PartName="/xl/sharedStrings.xml" ContentType="{_SST_TYPE}"/></Types>',
        ).encode("utf-8")
    rels_name = "xl/_rels/workbook.xml.rels"
    rels = files[rels_name].decode("utf-8")
    if "sharedStrings" not in rels:
        next_id = max((int(n) for n in re.findall(r'Id="rId(\d+)"', rels)), default=0) + 1
        files[rels_name] = rels.replace(
            "</Relationships>",
            f'<Relationship Id="rId{next_id}" Type="{_SST_REL}" Target="sharedStrings.xml"/></Relationships>',
        ).encode("utf-8")
    with zipfile.ZipFile(path, "w") as package:
        for name, data in files.items():
            package.writestr(name, data)


def _package_contains(path: Path, token: str) -> bool:
    needle = token.encode("utf-8")
    with zipfile.ZipFile(path) as package:
        return any(needle in package.read(name) for name in package.namelist())


def _cell(path: Path, sheet: int, coord: str):
    workbook = load_workbook(path, data_only=False)
    try:
        return workbook.worksheets[sheet][coord].value
    finally:
        workbook.close()


def test_written_xlsx_drops_an_unused_shared_string(tmp_path: Path, capsys):
    src = tmp_path / "one.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = _QA_TOKEN
    workbook.active["B1"] = "keep-me"
    workbook.save(src)
    _as_shared_strings(src)
    out = tmp_path / "out"
    code = main([str(src), "-r", str(_qa_rules(tmp_path / "rules.yaml")), "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    dest = out / "one.redacted.xlsx"
    assert _cell(dest, 0, "A1") == _QA_PLACEHOLDER
    assert _cell(dest, 0, "B1") == "keep-me"
    assert not _package_contains(dest, _QA_TOKEN)


def _two_sheet_source(path: Path) -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "One"
    second = workbook.create_sheet("Two")
    first["A1"] = _QA_TOKEN
    second["A1"] = _QA_TOKEN
    workbook.save(path)
    _as_shared_strings(path)
    with zipfile.ZipFile(path) as package:
        table = package.read("xl/sharedStrings.xml").decode("utf-8")
        sheet_one = package.read("xl/worksheets/sheet1.xml").decode("utf-8")
        sheet_two = package.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert table.count(f"<t>{_QA_TOKEN}</t>") == 1
    assert sheet_one.count("<v>0</v>") == 1
    assert sheet_two.count("<v>0</v>") == 1


def _write_two_sheets(src: Path, outdir: Path, *, second_sheet: bool):
    rules = _qa_rules(src.parent / "rules.yaml")
    engine = load_rules(rules)
    scan = scan_path(src, engine)
    if not second_sheet:
        for hit in scan.hits:
            if isinstance(hit.place, CellPlace) and hit.place.sheet == 1:
                hit.enabled = False
    result = write_scans(
        [scan],
        outdir,
        replacements=engine.replacements,
        overwrite=True,
        engine=engine,
    )[0]
    assert result.error is None
    assert result.dest is not None
    return result.dest


def test_shared_string_stays_when_only_the_first_sheet_is_checked(tmp_path: Path):
    src = tmp_path / "two.xlsx"
    _two_sheet_source(src)
    dest = _write_two_sheets(src, tmp_path / "out", second_sheet=False)
    assert _cell(dest, 0, "A1") == _QA_PLACEHOLDER
    assert _cell(dest, 1, "A1") == _QA_TOKEN
    assert _package_contains(dest, _QA_TOKEN)


def test_formula_cache_keeps_a_shared_string_the_formula_still_names(tmp_path: Path, capsys):
    src = tmp_path / "formula.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = _QA_TOKEN
    workbook.save(src)
    _as_shared_strings(src)
    with zipfile.ZipFile(src) as package:
        files = {name: package.read(name) for name in package.namelist()}
    sheet = "xl/worksheets/sheet1.xml"
    xml = files[sheet].decode("utf-8")
    cached = '<c r="C1" t="s"><f>A1</f><v>0</v></c>'
    xml = xml.replace("</row>", cached + "</row>", 1)
    files[sheet] = xml.encode("utf-8")
    with zipfile.ZipFile(src, "w") as package:
        for name, data in files.items():
            package.writestr(name, data)
    before = files[sheet]
    out = tmp_path / "out"
    code = main([str(src), "-r", str(_qa_rules(tmp_path / "rules.yaml")), "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    dest = out / "formula.redacted.xlsx"
    assert _cell(dest, 0, "A1") == _QA_PLACEHOLDER
    with zipfile.ZipFile(dest) as package:
        after = package.read(sheet)
    assert b"<f>A1</f>" in after
    assert b"<f>A1</f>" in before
    formula = after.decode("utf-8")
    assert '<c r="C1" t="s"><f>A1</f><v>0</v></c>' in formula
    assert _package_contains(dest, _QA_TOKEN)


def test_shared_string_is_gone_when_both_sheets_are_checked(tmp_path: Path):
    src = tmp_path / "two.xlsx"
    _two_sheet_source(src)
    dest = _write_two_sheets(src, tmp_path / "out", second_sheet=True)
    assert _cell(dest, 0, "A1") == _QA_PLACEHOLDER
    assert _cell(dest, 1, "A1") == _QA_PLACEHOLDER
    assert not _package_contains(dest, _QA_TOKEN)
