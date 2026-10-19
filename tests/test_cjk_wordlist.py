"""Wordlist boundaries for CJK terms, and one PDF rect per hit.

Fabricated names only: 星辰海, 星海, 星辰海医药有限公司, 星辰海妥单抗, Acme.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pymupdf
from docx import Document

from doc_redactor.docx_redactor import redact_docx
from doc_redactor.engine import RegexRule, WordlistRule, build_engine
from doc_redactor.pdf_redactor import find_hit_rects, redact_pdf
from doc_redactor.scan import scan_path, write_scan


def _engine(terms: list[str], *, category: str = "申办方", case_sensitive: bool = False):
    return build_engine(
        [],
        [WordlistRule(category=category, terms=terms, case_sensitive=case_sensitive)],
    )


def _overlap_engine():
    return build_engine(
        [RegexRule(name="项目号", pattern=r"星辰海-\d{2}", flags=0)],
        [
            WordlistRule(category="申办方", terms=["星辰海"], case_sensitive=False),
            WordlistRule(category="药名", terms=["星辰海妥单抗"], case_sensitive=True),
        ],
    )


def test_cjk_term_matches_when_stuck_to_other_chinese():
    engine = _engine(["星辰海"])
    alone = engine.find_hits("星辰海")
    assert [(hit.category, hit.text) for hit in alone] == [("申办方", "星辰海")]

    company = engine.find_hits("星辰海医药有限公司")
    assert [(hit.text, hit.start, hit.end) for hit in company] == [("星辰海", 0, 3)]

    wrapped = engine.find_hits("由星辰海提供")
    assert [hit.text for hit in wrapped] == ["星辰海"]
    assert wrapped[0].start == 1

    assert [hit.text for hit in engine.find_hits("A星辰海B")] == ["星辰海"]


def test_cjk_term_matches_across_whitespace_and_newline():
    engine = _engine(["星辰海"])
    broken = "星\n辰海"
    hits = engine.find_hits(broken)
    assert len(hits) == 1
    assert hits[0].text == "星\n辰海"
    assert hits[0].start == 0 and hits[0].end == len(broken)

    sentence = "由星\n辰海提供"
    hits = engine.find_hits(sentence)
    assert [hit.text for hit in hits] == ["星\n辰海"]
    assert sentence[hits[0].start : hits[0].end] == "星\n辰海"

    spaced = engine.find_hits("星 辰海")
    assert [hit.text for hit in spaced] == ["星 辰海"]


def test_english_wordlist_still_requires_ascii_boundaries():
    engine = _engine(["Acme"], category="SPONSOR", case_sensitive=False)
    assert [hit.text for hit in engine.find_hits("Acme Pharma Ltd")] == ["Acme"]
    assert engine.find_hits("Acmes") == []
    assert engine.find_hits("XAcme") == []
    assert engine.find_hits("Acme1") == []
    assert engine.find_hits("1Acme") == []
    assert [hit.text for hit in engine.find_hits("Acme星")] == ["Acme"]
    assert [hit.text for hit in engine.find_hits("星Acme")] == ["Acme"]
    assert [hit.text for hit in engine.find_hits("acme")] == ["acme"]

    phrase = _engine(["Acme Pharma"], category="SPONSOR", case_sensitive=False)
    assert [hit.text for hit in phrase.find_hits("Acme Pharma Ltd")] == ["Acme Pharma"]
    assert phrase.find_hits("AcmePharma") == []
    assert phrase.find_hits("Acme Pharmas") == []
    crossed = "See Acme\nPharma Ltd"
    hits = phrase.find_hits(crossed)
    assert [hit.text for hit in hits] == ["Acme\nPharma"]

    sensitive = _engine(["Acme"], category="SPONSOR", case_sensitive=True)
    assert [hit.text for hit in sensitive.find_hits("Acme")] == ["Acme"]
    assert sensitive.find_hits("acme") == []


def test_longer_term_wins_when_chinese_sponsor_overlaps_drug_or_protocol():
    engine = _overlap_engine()
    text = "星辰海医药有限公司的星辰海妥单抗见星辰海-01"
    hits = engine.find_hits(text)
    assert [(hit.category, hit.text) for hit in hits] == [
        ("申办方", "星辰海"),
        ("药名", "星辰海妥单抗"),
        ("项目号", "星辰海-01"),
    ]

    drug_only = engine.find_hits("星辰海妥单抗")
    assert [(hit.category, hit.text) for hit in drug_only] == [("药名", "星辰海妥单抗")]

    protocol_only = engine.find_hits("星辰海-01")
    assert [(hit.category, hit.text) for hit in protocol_only] == [("项目号", "星辰海-01")]

    # Same list: the longer term wins over its own prefix.
    both = _engine(["星", "星辰海"])
    assert [hit.text for hit in both.find_hits("星辰海医药")] == ["星辰海"]


def test_docx_redacts_cjk_term_stuck_to_chinese_across_runs(tmp_path: Path):
    engine = _engine(["星辰海"])
    source = tmp_path / "note.docx"
    document = Document()
    document.add_paragraph("星辰海医药有限公司")
    split = document.add_paragraph()
    split.add_run("由星")
    split.add_run("辰海提供")
    soft = document.add_paragraph()
    soft.add_run("星\n辰海结尾")
    document.save(source)

    dest = tmp_path / "note.redacted.docx"
    counts = redact_docx(source, dest, engine)
    paragraphs = [paragraph.text for paragraph in Document(str(dest)).paragraphs]
    assert paragraphs[0] == "[SPONSOR]医药有限公司"
    assert paragraphs[1] == "由[SPONSOR]提供"
    assert paragraphs[2] == "[SPONSOR]结尾"
    combined = "\n".join(paragraphs)
    assert "星辰海" not in combined
    assert counts == {"申办方": 3}


def _cjk(page, point, text: str, size: float = 12) -> None:
    page.insert_text(point, text, fontname="china-s", fontsize=size)


def _page_chars(page):
    """Character boxes in the same order as get_text('text'), skipping inserted newlines."""
    flags = pymupdf.TEXTFLAGS_TEXT
    textpage = page.get_textpage(flags=flags)
    text = page.get_text("text", textpage=textpage)
    raw = page.get_text("rawdict", textpage=textpage)
    chars = []
    pos = 0
    line_id = 0
    for block in raw["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines") or []:
            pieces = []
            for span in line.get("spans") or []:
                for ch in span.get("chars") or []:
                    value = ch.get("c") or ""
                    bbox = ch.get("bbox")
                    if value and bbox:
                        pieces.append((value, pymupdf.Rect(bbox)))
            for value, rect in pieces:
                if not text.startswith(value, pos):
                    raise AssertionError(
                        f"text/rawdict diverged at {pos}: {text[pos : pos + 12]!r} vs {value!r}"
                    )
                chars.append((pos, pos + len(value), rect, line_id))
                pos += len(value)
            if pieces and not pieces[-1][0].endswith("\n") and pos < len(text) and text[pos] == "\n":
                pos += 1
            line_id += 1
    if pos != len(text):
        raise AssertionError(f"unused text tail {text[pos:]!r}")
    return text, chars


def _union_lines(chars, start: int, end: int) -> list[pymupdf.Rect]:
    grouped: list[pymupdf.Rect] = []
    current_line = None
    current = None
    for cstart, cend, rect, line_id in chars:
        if cend <= start or cstart >= end:
            continue
        if current_line != line_id:
            if current is not None:
                grouped.append(current)
            current = pymupdf.Rect(rect)
            current_line = line_id
        else:
            current |= rect
    if current is not None:
        grouped.append(current)
    return grouped


def _extract(path: Path) -> tuple[str, str]:
    document = pymupdf.open(path)
    try:
        mupdf_text = "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()
    proc = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
    )
    poppler = proc.stdout.decode("utf-8", errors="replace")
    return mupdf_text, poppler


def _compact(text: str) -> str:
    return "".join(text.split())


def _overlaps(a, b) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


def _contains(outer, inner, tol: float = 1.5) -> bool:
    return (
        inner.x0 >= outer.x0 - tol
        and inner.y0 >= outer.y0 - tol
        and inner.x1 <= outer.x1 + tol
        and inner.y1 <= outer.y1 + tol
    )


def _build_touching_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    _cjk(page, (72, 80), "星辰海医药有限公司", 14)
    _cjk(page, (72, 120), "由星辰海提供", 14)
    _cjk(page, (72, 170), "见星辰", 14)
    _cjk(page, (72, 200), "海公司", 14)
    document.save(path)
    document.close()


def test_pdf_deletes_cjk_term_stuck_to_chinese_and_across_lines(tmp_path: Path):
    source = tmp_path / "touch.pdf"
    _build_touching_pdf(source)
    engine = _engine(["星辰海"])

    opened = pymupdf.open(source)
    page = opened[0]
    text, _chars = _page_chars(page)
    hits = engine.find_hits(text)
    assert len(hits) == 3
    wrapped = [hit for hit in hits if "\n" in hit.text]
    assert len(wrapped) == 1
    assert wrapped[0].text == "星辰\n海"
    flags = pymupdf.TEXTFLAGS_TEXT
    textpage = page.get_textpage(flags=flags)
    rects = find_hit_rects(page, wrapped[0], textpage)
    assert len(rects) == 2
    assert pymupdf.Rect(rects[0]).y0 < pymupdf.Rect(rects[1]).y0
    widths = sorted(rect[2] - rect[0] for rect in rects)
    assert widths[0] * 1.4 < widths[1]
    plain = [hit for hit in hits if "\n" not in hit.text]
    for hit in plain:
        assert len(find_hit_rects(page, hit, textpage)) == 1
    opened.close()

    scan = scan_path(source, engine)
    assert len(scan.hits) == 3
    scan_wrapped = [hit for hit in scan.hits if "\n" in hit.text]
    assert len(scan_wrapped) == 1
    assert len(scan_wrapped[0].rects) == 2

    cli_out = tmp_path / "cli.pdf"
    counts = redact_pdf(source, cli_out, engine)
    assert counts == {"申办方": 3}
    gui_out = tmp_path / "gui.pdf"
    assert write_scan(scan, gui_out, engine=engine) == {"申办方": 3}

    for output in (cli_out, gui_out):
        mupdf_text, poppler = _extract(output)
        for body in (mupdf_text, poppler):
            assert "星辰海" not in body
            assert "星辰" not in body
            assert "海" not in body
            assert "医药有限公司" in body
            assert "提供" in body
            assert "公司" in body
        assert _compact(mupdf_text).count("[SPONSOR]") == 3
        assert _compact(poppler).count("[SPONSOR]") == 3

    # The two-character first line is narrower than [SPONSOR] even at 6pt,
    # so that box gets the short code. The single-character second line
    # stays blank, and the legend page explains [SP].
    assert counts.short_count == 1
    assert counts.blank_count == 0
    single_rects = [pymupdf.Rect(hit.rects[0]) for hit in scan.hits if "\n" not in hit.text]
    first = pymupdf.Rect(scan_wrapped[0].rects[0])
    second = pymupdf.Rect(scan_wrapped[0].rects[1])
    for output in (cli_out, gui_out):
        written = pymupdf.open(output)
        try:
            assert written.page_count == 2
            assert "[SP] = 申办方（[SPONSOR]）" in written[1].get_text()
            page = written[0]
            words = page.get_text("words")

            def _blob(outer, predicate) -> str:
                return "".join(
                    word[4] for word in words if predicate(outer, pymupdf.Rect(word[:4]))
                )

            assert _compact(_blob(first, _contains)) == "[SP]"
            assert "SPONSOR" not in _blob(second, _overlaps)
            assert "[" not in _blob(second, _overlaps)
            boxes = [pymupdf.Rect(item) for item in page.search_for("[SPONSOR]")]
            assert len(boxes) == 2
            for origin in single_rects:
                assert sum(1 for box in boxes if _contains(origin, box)) == 1
        finally:
            written.close()


def _build_repeat_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    _cjk(page, (72, 80), "星辰海", 32)
    _cjk(page, (72, 200), "正文说明星辰海用于试验", 12)
    _cjk(page, (72, 280), "组别  星辰海  剂量", 12)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(60, 258, 360, 292))
    shape.finish(width=0.6, color=(0, 0, 0))
    shape.commit()
    document.save(path)
    document.close()


def _source_term_rects(path: Path, term: str) -> list[pymupdf.Rect]:
    document = pymupdf.open(path)
    try:
        text, chars = _page_chars(document[0])
    finally:
        document.close()
    rects = []
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            break
        parts = _union_lines(chars, index, index + len(term))
        assert len(parts) == 1
        rects.append(parts[0])
        start = index + len(term)
    return rects


def _assert_placeholders_in_sources(path: Path, sources: list[pymupdf.Rect], needle: str) -> None:
    document = pymupdf.open(path)
    try:
        page = document[0]
        if needle == "[SPONSOR]":
            boxes = [pymupdf.Rect(item) for item in page.search_for(needle)]
        else:
            boxes = []
            words = page.get_text("words")
            for word in words:
                token = word[4]
                if "STUDY" in token or "DRUG" in token:
                    boxes.append(pymupdf.Rect(word[:4]))
        assert boxes
        used: set[int] = set()
        for source in sources:
            matched = [
                index
                for index, box in enumerate(boxes)
                if index not in used and _contains(source, box)
            ]
            assert matched, f"no placeholder inside {source}"
            # One visual hit: every word of this placeholder stays in this source,
            # and at least the whole label is present.
            if needle != "[SPONSOR]":
                blob = "".join(
                    word[4]
                    for word in page.get_text("words")
                    if _contains(source, pymupdf.Rect(word[:4]))
                )
                assert "STUDY" in blob and "DRUG" in blob
            for index in matched:
                used.add(index)
        assert len(used) == len(boxes)
        chosen = [boxes[index] for index in range(len(boxes))]
        for i, left in enumerate(chosen):
            for right in chosen[i + 1 :]:
                assert not _overlaps(left, right)
    finally:
        document.close()


def test_pdf_same_word_placeholders_stay_on_their_own_rects(tmp_path: Path):
    source = tmp_path / "repeat.pdf"
    _build_repeat_pdf(source)
    engine = _engine(["星辰海"])
    sources = _source_term_rects(source, "星辰海")
    assert len(sources) == 3
    tops = sorted(rect.y0 for rect in sources)
    assert tops[1] - tops[0] > 20
    assert tops[2] - tops[1] > 20
    heights = sorted(rect.height for rect in sources)
    assert heights[-1] > heights[0] * 1.5

    scan = scan_path(source, engine)
    assert len(scan.hits) == 3
    assert all(len(hit.rects) == 1 for hit in scan.hits)
    scan_rects = [pymupdf.Rect(hit.rects[0]) for hit in scan.hits]
    for left, right in zip(scan_rects, scan_rects[1:]):
        assert not _overlaps(left, right)
    for origin in sources:
        assert any(_contains(origin, rect, tol=2.0) and _contains(rect, origin, tol=2.0) for rect in scan_rects)

    gui_out = tmp_path / "gui.pdf"
    assert write_scan(scan, gui_out, engine=engine) == {"申办方": 3}
    cli_out = tmp_path / "cli.pdf"
    assert redact_pdf(source, cli_out, engine) == {"申办方": 3}

    for output in (gui_out, cli_out):
        mupdf_text, poppler = _extract(output)
        assert "星辰海" not in mupdf_text
        assert "星辰海" not in poppler
        assert _compact(mupdf_text).count("[SPONSOR]") == 3
        assert _compact(poppler).count("[SPONSOR]") == 3
        _assert_placeholders_in_sources(output, sources, "[SPONSOR]")


def test_pdf_short_cjk_word_shrinks_long_placeholder(tmp_path: Path):
    source = tmp_path / "short.pdf"
    document = pymupdf.open()
    page = document.new_page()
    _cjk(page, (72, 90), "星海", 28)
    _cjk(page, (72, 180), "服用星海每日", 11)
    document.save(source)
    document.close()

    engine = _engine(["星海"], category="药名", case_sensitive=True)
    sources = _source_term_rects(source, "星海")
    assert len(sources) == 2
    assert max(rect.height for rect in sources) > min(rect.height for rect in sources) * 1.5

    cli_out = tmp_path / "cli.pdf"
    cli_counts = redact_pdf(source, cli_out, engine)
    assert cli_counts == {"药名": 2}
    assert cli_counts.short_count == 1
    scan = scan_path(source, engine)
    assert len(scan.hits) == 2
    assert all(len(hit.rects) == 1 for hit in scan.hits)
    gui_out = tmp_path / "gui.pdf"
    gui_counts = write_scan(scan, gui_out, engine=engine)
    assert gui_counts == {"药名": 2}
    assert gui_counts.short_count == 1

    wide_source = max(sources, key=lambda item: item.width)
    narrow_source = min(sources, key=lambda item: item.width)
    for output in (cli_out, gui_out):
        mupdf_text, poppler = _extract(output)
        assert "星海" not in mupdf_text
        assert "星海" not in poppler
        opened = pymupdf.open(output)
        try:
            assert opened.page_count == 2
            legend = opened[1].get_text()
            assert "[SD] = 药名（[STUDY DRUG]）" in legend
            page = opened[0]
            wide_blob = "".join(
                word[4] for word in page.get_text("words") if _contains(wide_source, pymupdf.Rect(word[:4]))
            )
            narrow_blob = "".join(
                word[4] for word in page.get_text("words") if _contains(narrow_source, pymupdf.Rect(word[:4]))
            )
            assert "STUDY" in wide_blob and "DRUG" in wide_blob
            assert narrow_blob.replace(" ", "") == "[SD]"
        finally:
            opened.close()


def _assert_single_placeholder(path: Path, source_rect: pymupdf.Rect) -> None:
    mupdf_text, poppler = _extract(path)
    assert "星辰海" not in mupdf_text
    assert "星辰海" not in poppler
    assert _compact(mupdf_text).count("[SPONSOR]") == 1
    assert _compact(poppler).count("[SPONSOR]") == 1
    document = pymupdf.open(path)
    try:
        boxes = [pymupdf.Rect(item) for item in document[0].search_for("[SPONSOR]")]
        assert len(boxes) == 1
        assert _contains(source_rect, boxes[0])
    finally:
        document.close()


def test_pdf_placeholder_respects_cropbox_offset(tmp_path: Path):
    source = tmp_path / "crop.pdf"
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.set_cropbox(pymupdf.Rect(50, 80, 560, 740))
    assert page.cropbox.x0 > 0 and page.cropbox.y0 > 0
    _cjk(page, (40, 110), "委托星辰海申报", 14)
    document.save(source)
    document.close()

    engine = _engine(["星辰海"])
    sources = _source_term_rects(source, "星辰海")
    assert len(sources) == 1
    cli_out = tmp_path / "cli.pdf"
    assert redact_pdf(source, cli_out, engine) == {"申办方": 1}
    scan = scan_path(source, engine)
    assert len(scan.hits) == 1 and len(scan.hits[0].rects) == 1
    gui_out = tmp_path / "gui.pdf"
    write_scan(scan, gui_out, engine=engine)
    for output in (cli_out, gui_out):
        _assert_single_placeholder(output, sources[0])
        opened = pymupdf.open(output)
        try:
            assert opened[0].cropbox.x0 > 0 and opened[0].cropbox.y0 > 0
        finally:
            opened.close()


def test_pdf_placeholder_respects_page_rotation(tmp_path: Path):
    source = tmp_path / "rot.pdf"
    document = pymupdf.open()
    page = document.new_page()
    _cjk(page, (72, 200), "委托星辰海申报", 14)
    page.set_rotation(90)
    assert page.rotation == 90
    document.save(source)
    document.close()

    engine = _engine(["星辰海"])
    sources = _source_term_rects(source, "星辰海")
    assert len(sources) == 1
    cli_out = tmp_path / "cli.pdf"
    assert redact_pdf(source, cli_out, engine) == {"申办方": 1}
    scan = scan_path(source, engine)
    assert len(scan.hits) == 1 and len(scan.hits[0].rects) == 1
    gui_out = tmp_path / "gui.pdf"
    write_scan(scan, gui_out, engine=engine)
    for output in (cli_out, gui_out):
        _assert_single_placeholder(output, sources[0])
        opened = pymupdf.open(output)
        try:
            assert opened[0].rotation == 90
        finally:
            opened.close()
