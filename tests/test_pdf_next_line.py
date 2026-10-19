"""#38: a placeholder must not take the neighbouring line with it.

Fabricated text only (ABC-2026-001, Acme Pharma, Fictizumab). Two 9pt lines
close together: replacing the hit on one line used to remove the whole other
line from the text layer (gap below about 12pt). Output is read back with
PyMuPDF and, when installed, Poppler's pdftotext.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pymupdf
import pytest

from doc_redactor.engine import WordlistRule, build_engine
from doc_redactor.scan import scan_path, write_scan

HIT = "ABC-2026-001"
HIT_LINE = f"{HIT} Header line"
NEIGHBOUR = "Fictizumab body gamma"


def _engine():
    return build_engine([], [WordlistRule(category="项目号", terms=[HIT], case_sensitive=True)])


def _read_back(path: Path) -> tuple[list[str], list[str]]:
    """(PyMuPDF rawdict lines, whole text of each parser) of the output."""
    document = pymupdf.open(path)
    try:
        lines = [
            "".join(ch["c"] for span in line["spans"] for ch in span["chars"])
            for page in document
            for block in page.get_text("rawdict")["blocks"]
            for line in block.get("lines", ())
        ]
        texts = ["\n".join(page.get_text("text") for page in document)]
    finally:
        document.close()
    try:
        proc = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return lines, texts
    texts.append(proc.stdout.decode("utf-8", errors="replace"))
    return lines, texts


def _assert_neighbour_whole(result, gap):
    lines, texts = result
    # The neighbour is still one line, every glyph in place.
    assert NEIGHBOUR in lines, (gap, lines)
    for text in texts:
        assert HIT not in text
        # Overlapping lines may be interleaved by a parser; every word stays.
        for word in NEIGHBOUR.split():
            assert word in text, (gap, word)


def _write(tmp_path: Path, draw) -> list[str]:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    draw(page)
    source = tmp_path / "in.pdf"
    document.save(source)
    scan = scan_path(source, _engine())
    assert scan.unlocated_count == 0 and len(scan.hits) == 1
    dest = tmp_path / "out.pdf"
    write_scan(scan, dest)
    return _read_back(dest)


@pytest.mark.parametrize("gap", [2.5, 3.0, 6.0, 9.0, 11.0])
def test_line_below_the_hit_stays_whole(tmp_path, gap):
    # gap 2.5 = 出品审 t9/t9b; 3 and 6 = adv3.
    def draw(page):
        page.insert_text((72, 60), HIT_LINE, fontsize=9)
        page.insert_text((72, 60 + gap), NEIGHBOUR, fontsize=9)

    _assert_neighbour_whole(_write(tmp_path, draw), gap)


@pytest.mark.parametrize("gap", [3.0, 6.0, 11.0])
def test_line_above_the_hit_stays_whole(tmp_path, gap):
    def draw(page):
        page.insert_text((72, 60), NEIGHBOUR, fontsize=9)
        page.insert_text((72, 60 + gap), HIT_LINE, fontsize=9)

    _assert_neighbour_whole(_write(tmp_path, draw), gap)


def test_rest_of_the_hit_line_and_far_lines_are_unchanged(tmp_path):
    def draw(page):
        page.insert_text((72, 60), HIT_LINE, fontsize=9)
        page.insert_text((72, 66), NEIGHBOUR, fontsize=9)
        page.insert_text((72, 200), "Acme Pharma far paragraph", fontsize=10)

    lines, texts = _write(tmp_path, draw)
    _assert_neighbour_whole((lines, texts), 6.0)
    assert " Header line" in lines or any(line.endswith(" Header line") for line in lines), lines
    assert "Acme Pharma far paragraph" in lines


@pytest.mark.parametrize("font,hit,neighbour", [("helv", HIT, NEIGHBOUR), ("china-s", "星辰海", "正文第二行内容")])
@pytest.mark.parametrize("sizes", [(9, 9), (14, 9), (9, 14), (6, 12)])
@pytest.mark.parametrize("gap", [1.0, 2.5, 6.0, 12.0])
def test_trimming_never_leaves_hit_text(tmp_path, font, hit, neighbour, sizes, gap):
    # Lines that overlap so much that the box cannot be trimmed fall back to
    # the whole box: the neighbour may go, the hit never stays.
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), f"{hit} tail", fontsize=sizes[0], fontname=font)
    page.insert_text((72, 100 + gap), neighbour, fontsize=sizes[1], fontname=font)
    source = tmp_path / "in.pdf"
    document.save(source)
    engine = build_engine([], [WordlistRule(category="申办方", terms=[hit], case_sensitive=True)])
    scan = scan_path(source, engine)
    dest = tmp_path / "out.pdf"
    write_scan(scan, dest)
    out = pymupdf.open(dest)
    assert hit not in out[0].get_text()
    streams = b"".join(out.xref_stream(x) or b"" for x in range(1, out.xref_length()) if out.xref_is_stream(x))
    assert hit.encode() not in streams
