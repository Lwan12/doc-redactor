"""A mismatched PDF line must not drop the rest of the page.

Fabricated terms only: 星辰海, 玄冥港, Acme. One line can be missing glyphs in
rawdict while plain text still contains it. Other lines stay located. A line
that still has no rectangle is counted and blocks the write.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pymupdf
import pytest

from doc_redactor.cli import main
from doc_redactor.engine import WordlistRule, build_engine
from doc_redactor.pdf_redactor import redact_pdf
from doc_redactor.backpath import hit_list_payload
from doc_redactor.scan import scan_path, write_scan, write_scans


def _is_poppler() -> bool:
    try:
        probe = subprocess.run(["pdftotext", "-v"], capture_output=True)
    except OSError:
        return False
    return b"Poppler" in (probe.stdout + probe.stderr)


def _cjk(page, point, text: str, size: float = 14) -> None:
    page.insert_text(point, text, fontname="china-s", fontsize=size)


def _engine(terms: list[str]):
    return build_engine(
        [],
        [WordlistRule(category="申办方", terms=terms, case_sensitive=True)],
    )


def _page_text(path: Path) -> str:
    document = pymupdf.open(path)
    try:
        mupdf_text = "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()
    if not _is_poppler():
        return mupdf_text
    proc = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(path), "-"],
        check=True,
        capture_output=True,
    )
    poppler = proc.stdout.decode("utf-8", errors="replace")
    return mupdf_text + "\n" + poppler


def _option(args, kwargs):
    if args:
        return args[0]
    return kwargs.get("option", "text")


def _line_text(line: dict) -> str:
    parts = []
    for span in line.get("spans") or []:
        for ch in span.get("chars") or []:
            parts.append(ch.get("c") or "")
    return "".join(parts)


def _mutate_rawdict(raw: dict, *, drop_buried: bool, drop_lost_line: bool) -> dict:
    """Drop glyphs from a copy of rawdict. Plain text is left unchanged."""
    cloned = copy.deepcopy(raw)
    for block in cloned.get("blocks") or []:
        if block.get("type", 0) != 0:
            continue
        kept = []
        for line in block.get("lines") or []:
            text = _line_text(line)
            if drop_lost_line and "玄冥港" in text:
                continue
            if drop_buried and "甲" in text and "星辰海" in text:
                for span in line.get("spans") or []:
                    span["chars"] = [
                        ch
                        for ch in span.get("chars") or []
                        if (ch.get("c") or "") not in set("星辰海")
                    ]
            kept.append(line)
        block["lines"] = kept
    return cloned


def _patch_rawdict(monkeypatch, *, drop_buried: bool = False, drop_lost_line: bool = False) -> None:
    original = pymupdf.Page.get_text

    def wrapped(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if _option(args, kwargs) == "rawdict" and isinstance(result, dict):
            return _mutate_rawdict(result, drop_buried=drop_buried, drop_lost_line=drop_lost_line)
        return result

    monkeypatch.setattr(pymupdf.Page, "get_text", wrapped)


def _build_gap_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    _cjk(page, (72, 80), "前文保留星辰海公司")
    _cjk(page, (72, 150), "甲星辰海乙星辰海丙")
    _cjk(page, (72, 220), "后文保留星辰海结束")
    document.save(path)
    document.close()


def _build_lost_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    _cjk(page, (72, 80), "前文星辰海保留")
    _cjk(page, (72, 160), "此处玄冥港单独")
    document.save(path)
    document.close()


def _build_plain_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 80), "See Acme now", fontname="helv", fontsize=14)
    document.save(path)
    document.close()


def test_buried_glyphs_do_not_drop_the_other_lines(tmp_path: Path, monkeypatch) -> None:
    """Glyphs of the term itself are gone on one line. That line is unlocated.

    The other lines still get their own rectangles. The file is not written,
    because a partial PDF would keep the buried line.
    """
    source = tmp_path / "gap.pdf"
    _build_gap_pdf(source)
    _patch_rawdict(monkeypatch, drop_buried=True)
    engine = _engine(["星辰海"])

    scan = scan_path(source, engine)
    assert len(scan.hits) == 2
    assert getattr(scan, "unlocated_count", 0) == 2
    assert all(hit.text == "星辰海" for hit in scan.hits)
    tops = sorted(hit.rects[0][1] for hit in scan.hits)
    assert tops[1] - tops[0] > 20
    assert not _hits_share_rect(scan.hits)

    from doc_redactor.pdf_redactor import UnlocatedText

    with pytest.raises(UnlocatedText) as raised:
        redact_pdf(source, tmp_path / "cli.pdf", engine)
    assert "2 处无法定位，未写出" in str(raised.value)
    assert not (tmp_path / "cli.pdf").exists()
    written = write_scans([scan], tmp_path / "batch", engine=engine)
    assert written[0].dest is None
    assert written[0].error is not None and "2 处无法定位，未写出" in written[0].error
    assert not (tmp_path / "batch" / "gap.redacted.pdf").exists()


def test_unlocatable_hit_blocks_the_file_and_keeps_the_other_line(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "lost.pdf"
    other = tmp_path / "other.pdf"
    _build_lost_pdf(source)
    _build_plain_pdf(other)
    _patch_rawdict(monkeypatch, drop_lost_line=True)
    engine = _engine(["星辰海", "玄冥港", "Acme"])

    lost = scan_path(source, engine)
    plain = scan_path(other, engine)
    assert any(hit.text == "星辰海" and hit.rects for hit in lost.hits)
    assert all("玄冥港" not in hit.text for hit in lost.hits)
    assert getattr(lost, "unlocated_count", 0) == 1
    assert len(plain.hits) == 1

    batch = tmp_path / "out"
    results = write_scans([lost, plain], batch, engine=engine)
    by_name = {item.path.name: item for item in results}
    blocked = by_name["lost.pdf"]
    assert blocked.dest is None
    assert blocked.error is not None and "1 处无法定位，未写出" in blocked.error
    assert "玄冥港" not in blocked.error
    assert not (batch / "lost.redacted.pdf").exists()
    assert not (batch / "lost.report.json").exists()
    assert by_name["other.pdf"].dest is not None and by_name["other.pdf"].dest.is_file()
    assert "Acme" not in _page_text(by_name["other.pdf"].dest)

    payload = hit_list_payload([lost, plain], category_order=["申办方"])
    raw = __import__("json").dumps(payload, ensure_ascii=False)
    assert "玄冥港" not in raw
    assert "星辰海" not in raw
    assert payload["lost.pdf"]["无法定位"] == 1

    with pytest.raises(Exception) as raised:
        redact_pdf(source, tmp_path / "direct.pdf", engine)
    assert "1 处无法定位，未写出" in str(raised.value)
    assert not (tmp_path / "direct.pdf").exists()


def _mupdf_text(path: Path) -> str:
    document = pymupdf.open(path)
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()


def _hits_share_rect(hits) -> bool:
    """True when two hits carry the same box or boxes that overlap by more than half."""
    boxes = []
    for index, hit in enumerate(hits):
        for rect in hit.rects:
            box = pymupdf.Rect(rect)
            boxes.append((index, box))
    for left in range(len(boxes)):
        i, a = boxes[left]
        for right in range(left + 1, len(boxes)):
            j, b = boxes[right]
            if i == j:
                continue
            inter = a & b
            if inter.is_empty:
                continue
            smaller = min(a.get_area(), b.get_area())
            if smaller > 0 and inter.get_area() > smaller * 0.5:
                return True
    return False


def _patch_drop_marker_char(monkeypatch, marker: str, drop: str) -> None:
    """Drop one non-hit glyph from rawdict lines that contain ``marker``."""
    original = pymupdf.Page.get_text

    def wrapped(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if _option(args, kwargs) != "rawdict" or not isinstance(result, dict):
            return result
        cloned = copy.deepcopy(result)
        for block in cloned.get("blocks") or []:
            for line in block.get("lines") or []:
                if marker not in _line_text(line):
                    continue
                for span in line.get("spans") or []:
                    span["chars"] = [
                        ch for ch in span.get("chars") or [] if (ch.get("c") or "") != drop
                    ]
        return cloned

    monkeypatch.setattr(pymupdf.Page, "get_text", wrapped)


def _two_line_pdf(path: Path, font: str, first: str, second: str, lead: float, size: float = 12) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), first, fontname=font, fontsize=size)
    page.insert_text((72, 100 + lead), second, fontname=font, fontsize=size)
    document.save(path)
    document.close()


@pytest.mark.parametrize(
    ("font", "term", "first", "second", "lead"),
    [
        ("china-s", "星辰海", "星辰海在上一行", "MARK#甲乙丙丁戊己星辰海", lead)
        for lead in (11, 12, 13, 14, 14.4, 15, 15.5)
    ]
    + [
        ("helv", "Acme", "Acme first line", "MARK# zz zz zz Acme", lead)
        for lead in (11, 12, 13, 14)
    ],
)
def test_tight_leading_does_not_reuse_the_previous_line(
    tmp_path: Path, monkeypatch, font: str, term: str, first: str, second: str, lead: float
) -> None:
    """A missing glyph on a tight line must not steal the same word from the line above.

    Either both copies are gone from the written PDF, or the line that cannot
    be placed is counted and the file is not written. The upper line's box is
    never shared by both hits.
    """
    source = tmp_path / "tight.pdf"
    _two_line_pdf(source, font, first, second, lead)
    _patch_drop_marker_char(monkeypatch, "MARK", "#")
    engine = build_engine(
        [],
        [WordlistRule(category="申办方", terms=[term], case_sensitive=False)],
    )

    scan = scan_path(source, engine)
    assert scan.unlocated_count + len(scan.hits) == 2
    assert not _hits_share_rect(scan.hits)
    if len(scan.hits) == 2:
        tops = sorted(hit.rects[0][1] for hit in scan.hits)
        assert tops[1] - tops[0] > 4

    out = tmp_path / "out.pdf"
    if scan.unlocated_count == 0:
        counts = redact_pdf(source, out, engine)
        assert _mupdf_text(out).count(term) == 0
        assert counts["申办方"] == 2
        gui = tmp_path / "gui.pdf"
        assert write_scan(scan, gui, engine=engine)["申办方"] == 2
        assert _mupdf_text(gui).count(term) == 0
        return

    from doc_redactor.pdf_redactor import UnlocatedText

    with pytest.raises(UnlocatedText) as raised:
        redact_pdf(source, out, engine)
    assert f"{scan.unlocated_count} 处无法定位，未写出" in str(raised.value)
    assert not out.exists()
    results = write_scans([scan], tmp_path / "batch", engine=engine)
    assert results[0].dest is None
    assert results[0].error is not None
    assert f"{scan.unlocated_count} 处无法定位，未写出" in results[0].error
    assert not (tmp_path / "batch").exists() or not any((tmp_path / "batch").rglob("*.pdf"))


@pytest.mark.parametrize(
    ("line", "terms", "case_sensitive", "keep", "gone"),
    [
        ("MARK# acrf zz aCRF", ["aCRF"], True, ["acrf"], ["aCRF"]),
        ("MARK# ACRF zz aCRF", ["acrf"], False, [], ["ACRF", "aCRF"]),
        ("MARK# aCRF zz ACRF", ["ACRF"], False, [], ["aCRF", "ACRF"]),
    ],
)
def test_case_variants_on_one_line_are_not_stacked(
    tmp_path: Path, monkeypatch, line: str, terms: list[str], case_sensitive: bool, keep: list[str], gone: list[str]
) -> None:
    """Different cases on one mismatched line keep their own boxes.

    A word that should stay (lowercase acrf under a case-sensitive rule) stays.
    Words that should go are gone, or the file is blocked and counted. Two hits
    never share one rectangle. A written count matches how many words disappeared.
    """
    source = tmp_path / "case.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), line, fontname="helv", fontsize=14)
    document.save(source)
    document.close()
    _patch_drop_marker_char(monkeypatch, "MARK", "#")
    engine = build_engine(
        [],
        [WordlistRule(category="申办方", terms=terms, case_sensitive=case_sensitive)],
    )
    before = _mupdf_text(source)
    expected = len(engine.find_hits(before))

    scan = scan_path(source, engine)
    assert scan.unlocated_count + len(scan.hits) == expected
    assert not _hits_share_rect(scan.hits)

    out = tmp_path / "out.pdf"
    if scan.unlocated_count == 0:
        counts = redact_pdf(source, out, engine)
        body = _mupdf_text(out)
        for term in gone:
            assert term not in body
        for term in keep:
            assert term in body
        removed = sum(before.count(term) - body.count(term) for term in gone)
        assert counts["申办方"] == removed
        assert removed == len(scan.hits)
        gui = tmp_path / "gui.pdf"
        gui_counts = write_scan(scan, gui, engine=engine)
        gui_body = _mupdf_text(gui)
        assert gui_counts["申办方"] == removed
        for term in gone:
            assert term not in gui_body
        for term in keep:
            assert term in gui_body
        return

    from doc_redactor.pdf_redactor import UnlocatedText

    for term in keep:
        assert term in before
    with pytest.raises(UnlocatedText) as raised:
        redact_pdf(source, out, engine)
    assert f"{scan.unlocated_count} 处无法定位，未写出" in str(raised.value)
    assert not out.exists()
    results = write_scans([scan], tmp_path / "batch", engine=engine)
    assert results[0].dest is None
    assert not any((tmp_path / "batch").glob("*.pdf"))


@pytest.mark.parametrize("kind", ["same", "half", "apart"])
def test_shared_page_rects_are_unlocated(tmp_path: Path, monkeypatch, kind: str) -> None:
    """Identical boxes, and boxes that overlap by more than half, unlocate both hits."""
    source = tmp_path / "two.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 80), "Acme one", fontname="helv", fontsize=12)
    page.insert_text((72, 200), "Acme two", fontname="helv", fontsize=12)
    document.save(source)
    document.close()
    same = pymupdf.Rect(72, 70, 110, 82)
    pairs = {
        "same": (same, pymupdf.Rect(same)),
        "half": (pymupdf.Rect(72, 70, 140, 90), pymupdf.Rect(72, 70, 140, 82)),
        "apart": (pymupdf.Rect(72, 68, 110, 82), pymupdf.Rect(72, 188, 110, 202)),
    }
    chosen = pairs[kind]
    state = {"n": 0}

    def fake(*_args, **_kwargs):
        rect = chosen[state["n"] % 2]
        state["n"] += 1
        return [rect]

    monkeypatch.setattr("doc_redactor.pdf_redactor._rects_for_hit", fake)
    engine = _engine(["Acme"])
    scan = scan_path(source, engine)
    if kind == "apart":
        assert scan.unlocated_count == 0
        assert len(scan.hits) == 2
        assert not _hits_share_rect(scan.hits)
        return
    assert scan.unlocated_count == 2
    assert scan.hits == []
    from doc_redactor.pdf_redactor import UnlocatedText

    with pytest.raises(UnlocatedText) as raised:
        redact_pdf(source, tmp_path / "out.pdf", engine)
    assert "2 处无法定位，未写出" in str(raised.value)
    assert not (tmp_path / "out.pdf").exists()


def test_cli_prints_unlocated_and_skips_the_file(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "lost.pdf"
    other = tmp_path / "other.pdf"
    _build_lost_pdf(source)
    _build_plain_pdf(other)
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "wordlists:\n"
        "  - category: 申办方\n"
        "    case_sensitive: true\n"
        "    terms: [星辰海, 玄冥港, Acme]\n",
        encoding="utf-8",
    )
    _patch_rawdict(monkeypatch, drop_lost_line=True)
    outdir = tmp_path / "cli-out"
    code = main([str(source), str(other), "-r", str(rules), "-o", str(outdir)])
    captured = capsys.readouterr()
    assert code != 0
    assert "1 处无法定位，未写出" in captured.err
    assert "玄冥港" not in captured.err
    assert "星辰海" not in captured.err
    assert not (outdir / "lost.redacted.pdf").exists()
    assert (outdir / "other.redacted.pdf").is_file()
    assert "Acme" not in _page_text(outdir / "other.redacted.pdf")
