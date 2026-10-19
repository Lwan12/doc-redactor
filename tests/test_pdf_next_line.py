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


def _is_poppler() -> bool:
    """Windows CI ships Git's xpdf 4.00 pdftotext. It joins two lines a few
    points apart into one and interleaves their letters, untouched text
    included (CI: "body gamma" mixed into "Header line"), so only Poppler
    is used as the second parser (same as test_pdf_align / test_pdf_fit)."""
    try:
        probe = subprocess.run(["pdftotext", "-v"], capture_output=True)
    except OSError:
        return False
    return b"Poppler" in (probe.stdout + probe.stderr)


def test_second_parser_is_poppler():
    """Says in ``-rs`` when the pdftotext pass below is left out (#40 review):
    the tests then check with PyMuPDF only."""
    if not _is_poppler():
        # English: the Windows CI log escapes non-ASCII skip reasons into \\uXXXX.
        pytest.skip("pdftotext is not Poppler (e.g. Git's xpdf on Windows): this file re-reads with PyMuPDF only")


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
    if not _is_poppler():
        return lines, texts
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


# --- 第 2 轮（出品审 PR #40 M1/S2）---------------------------------------

SCAN_HIT = "Fictizumab-gyp"


def _scan_like(tmp_path: Path, with_hit: bool, gap: float) -> Path:
    """A scanned page: the picture is an image, an invisible text layer on top."""

    def lines(page, render_mode):
        hit = SCAN_HIT if with_hit else " " * len(SCAN_HIT)
        page.insert_text((72, 100 - gap), "Upper neighbour gggyyyppp", fontsize=9, render_mode=render_mode)
        page.insert_text((72, 100), f"ID {hit} tail", fontsize=9, render_mode=render_mode)
        page.insert_text((72, 100 + gap), "Lower neighbour bdfhkl", fontsize=9, render_mode=render_mode)

    visible = pymupdf.open()
    lines(visible.new_page(width=595, height=842), 0)
    png = visible[0].get_pixmap(dpi=200).tobytes("png")
    visible.close()
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png)
    lines(page, 3)
    path = tmp_path / f"scan-{int(with_hit)}.pdf"
    document.save(path)
    document.close()
    return path


def _render_gray(path: Path, clip) -> bytes:
    document = pymupdf.open(path)
    try:
        pix = document[0].get_pixmap(dpi=300, clip=clip, colorspace=pymupdf.csGRAY)
        return pix.samples
    finally:
        document.close()


@pytest.mark.parametrize("gap", [8.0, 10.0])
def test_scanned_page_keeps_no_stroke_of_the_hit_inside_its_box(tmp_path, gap, monkeypatch):
    # M1: a trimmed white box left the top and bottom of the hit's strokes in
    # the image visible. Output vs. the same page drawn without the hit,
    # 300dpi, inside the hit's whole box: no extra dark pixel.
    from doc_redactor import pdf_redactor as pr

    monkeypatch.setattr(pr, "_write_choice", lambda page, rect, full, short: "blank")
    source = _scan_like(tmp_path, True, gap)
    reference = _scan_like(tmp_path, False, gap)
    document = pymupdf.open(source)
    rects = document[0].search_for(SCAN_HIT)
    assert rects
    box = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        box |= rect
    pr.redact_text_placeholders(document, [(0, rects, "项目号", SCAN_HIT)], lambda category: f"[{category}]")
    out = tmp_path / "out.pdf"
    document.save(out)
    document.close()
    got, want = _render_gray(out, box), _render_gray(reference, box)
    assert len(got) == len(want)
    residue = sum(1 for g, w in zip(got, want) if g < 128 and w >= 128)
    assert residue == 0, (gap, residue)


def test_redaction_box_falls_back_to_the_whole_box_when_the_core_is_lost():
    # S2: a neighbour reaching past the hit's middle leaves too little of it.
    from doc_redactor import pdf_redactor as pr

    box = pymupdf.Rect(72, 90, 140, 102)
    glyphs = [
        (pymupdf.Rect(72, 90, 80, 102), 1),  # the hit's own glyph
        (pymupdf.Rect(72, 80, 140, 101), 2),  # neighbour line covering most of it
    ]
    assert pr._redaction_box(box, glyphs) == box
    # Control: a small overlap is trimmed, not the whole box.
    glyphs[1] = (pymupdf.Rect(72, 80, 140, 91), 2)
    assert pr._redaction_box(box, glyphs) != box


def test_trimmed_box_that_missed_hit_glyphs_is_redone_with_the_whole_box(tmp_path, monkeypatch):
    # S2: if the first, trimmed deletion leaves a hit glyph behind, the whole
    # box is deleted again. The trim is forced to miss the hit entirely.
    from doc_redactor import pdf_redactor as pr

    def miss(box, glyphs):
        return pymupdf.Rect(box.x0, box.y0 - 40, box.x1, box.y0 - 39)

    monkeypatch.setattr(pr, "_redaction_box", miss)
    monkeypatch.setattr(pr, "_write_choice", lambda page, rect, full, short: "blank")
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 200), f"{HIT} alone", fontsize=9)
    rects = page.search_for(HIT)
    pr.redact_text_placeholders(document, [(0, rects, "项目号", HIT)], lambda category: f"[{category}]")
    assert HIT not in document[0].get_text()
