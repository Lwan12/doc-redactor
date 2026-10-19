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
from doc_redactor.scan import scan_path, write_scans


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
    """One line is missing glyphs. The other lines still redact, and that line
    falls back to a search inside its own box, in occurrence order.
    """
    source = tmp_path / "gap.pdf"
    _build_gap_pdf(source)
    _patch_rawdict(monkeypatch, drop_buried=True)
    engine = _engine(["星辰海"])

    scan = scan_path(source, engine)
    assert len(scan.hits) == 4
    assert getattr(scan, "unlocated_count", 0) == 0
    tops = sorted(hit.rects[0][1] for hit in scan.hits)
    assert tops[1] - tops[0] > 20
    assert abs(tops[2] - tops[1]) < 8
    assert tops[3] - tops[2] > 20

    cli_out = tmp_path / "cli.pdf"
    assert redact_pdf(source, cli_out, engine)["申办方"] == 4
    written = write_scans([scan], tmp_path / "batch", engine=engine)
    assert written[0].error is None
    assert written[0].dest is not None and written[0].dest.is_file()

    for output in (cli_out, written[0].dest):
        body = _page_text(output)
        assert "星辰海" not in body
        assert "星" not in body and "辰" not in body and "海" not in body
        assert "前文保留" in body and "公司" in body
        assert "甲" in body and "乙" in body and "丙" in body
        assert "后文保留" in body and "结束" in body


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
