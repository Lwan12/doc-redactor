"""「重复认出」收紧（PR #28 第 2 轮，出品审 M1/S1/S2）。

Every PDF here is built in memory from made-up text (ABC-2026-001,
Acme Pharma, Fictizumab). Each case is something that repeats on every page
but is body content, so it must survive removal.
"""

from __future__ import annotations

import pymupdf

from doc_redactor import header_footer as hf

HEADER = "ABC-2026-001 Version 1.0"
WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf")


def _pdf(pages: int, draw) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        draw(page, i)
    return pymupdf.open("pdf", doc.tobytes())


def _body(page, i):
    page.insert_text((72, 400), f"Fictizumab body paragraph {WORDS[i]}", fontsize=10)
    page.insert_text((72, 420), f"Another body line {WORDS[-1 - i]}", fontsize=10)


def _remove(doc):
    plan = hf.confirm(hf.identify_pdf(doc))
    result = hf.apply_pdf(doc, plan)
    out = pymupdf.open("pdf", doc.tobytes(garbage=3))
    return plan, result, out


def _text(doc) -> str:
    return "\n".join(page.get_text() for page in doc)


# ---------------------------------------------------------------- M1


def test_continued_title_column_header_and_footnote_stay():
    def draw(page, i):
        page.insert_text((72, 60), HEADER, fontsize=9)
        page.insert_text((72, 130), "Table 14.1 Demographics (continued)", fontsize=10)
        page.insert_text((72, 150), "Variable   Placebo   Fictizumab   Total", fontsize=10)
        _body(page, i)
        page.insert_text((72, 720), "Note: percentages based on N in each arm.", fontsize=8)
        page.insert_text((72, 800), f"Page {i + 1} of 5", fontsize=8)

    _plan, result, out = _remove(_pdf(5, draw))
    text = _text(out)
    for kept in ("Table 14.1 Demographics (continued)", "Variable", "Note: percentages"):
        assert text.count(kept) == 5, kept
    assert HEADER not in text and "Page 1 of 5" not in text
    assert result.body_lost == 0


def test_data_row_that_differs_only_in_digits_stays():
    def draw(page, i):
        page.insert_text((72, 150), f"10{i}-00{i}   {40 + i} yrs   Visit {i + 2}   {i}.{i}", fontsize=10)
        _body(page, i)

    doc = _pdf(5, draw)
    plan = hf.identify_pdf(doc)
    assert plan.route == hf.ROUTE_NONE and plan.candidates == ()


def test_listing_header_with_changing_subject_number_stays():
    def draw(page, i):
        page.insert_text((72, 140), f"Subject: 10{i}-00{i}  Listing 16.2.1", fontsize=10)
        _body(page, i)

    assert hf.identify_pdf(_pdf(5, draw)).candidates == ()


def test_page_number_digits_still_ignored_in_a_long_footer():
    def draw(page, i):
        page.insert_text((72, 800), f"Acme Pharma Confidential   Page {i + 1} of 12", fontsize=8)
        _body(page, i % 7)

    plan = hf.identify_pdf(_pdf(12, draw))
    assert plan.route == hf.ROUTE_REPEAT
    assert {c.role for c in plan.candidates} == {hf.ROLE_FOOTER}
    assert hf.pages_affected(plan) == 12


def test_table_border_at_header_rule_height_stays():
    def draw(page, i):
        page.insert_text((72, 60), HEADER, fontsize=9)
        page.draw_line((72, 82.7), (523, 82.7), width=0.5)
        page.draw_line((150, 82.7), (400, 82.7), width=0.3)  # table top border on the rule
        page.draw_rect(pymupdf.Rect(72, 84, 523, 300), color=(0, 0, 0), width=0.5)
        page.insert_text((80, 92), f"{WORDS[i]} tight row", fontsize=8)
        _body(page, i)

    doc = _pdf(5, draw)
    before = [len(p.get_drawings()) for p in doc]
    _plan, result, out = _remove(doc)
    assert [len(p.get_drawings()) for p in out] == before
    assert HEADER not in _text(out)
    assert all(f"{w} tight row" in _text(out) for w in WORDS[:5])
    assert result.body_lost == 0


def test_table_border_touching_a_repeated_rule_keeps_the_rule():
    def draw(page, i):
        page.insert_text((72, 60), HEADER, fontsize=9)
        page.draw_line((72, 82.7), (523, 82.7), width=0.5)
        page.draw_line((72, 83.5), (72, 300), width=0.5)  # table side starts right under it
        page.draw_line((523, 83.5), (523, 300), width=0.5)
        _body(page, i)

    doc = _pdf(5, draw)
    plan = hf.identify_pdf(doc)
    assert not [e for c in plan.candidates for e in c.elements if e.kind == hf.KIND_LINE]


# ---------------------------------------------------------------- S1


def test_body_hugging_the_header_is_never_deleted():
    for gap in (3.0, 6.0, 8.0):

        def draw(page, i, gap=gap):
            page.insert_text((72, 60), HEADER, fontsize=9)
            page.insert_text((72, 60 + gap), f"{WORDS[i]} body gamma", fontsize=9)
            page.insert_text((72, 400), f"mid {WORDS[i]}", fontsize=9)

        doc = _pdf(5, draw)
        plan, result, out = _remove(doc)
        text = _text(out)
        assert all(f"{w} body gamma" in text for w in WORDS[:5]), gap
        assert result.body_lost == 0


def test_check_text_counts_a_foreign_glyph_inside_the_box_as_lost():
    removed = (("A", 72.0, 50.0),)
    before = [("A", 72.0, 50.0), ("K", 74.0, 51.0)]  # K is body, centre inside the header box
    assert hf.check_text(before, [], removed) == (0, 1)
    assert hf.check_text(before, [("K", 74.0, 51.0)], removed) == (0, 0)
    assert hf.check_text(before, before, removed) == (1, 0)


def test_apply_refuses_when_a_mark_would_cut_a_body_glyph():
    def draw(page, i):
        page.insert_text((72, 60), HEADER, fontsize=9)
        _body(page, i)

    doc = _pdf(5, draw)
    plan = hf.confirm(hf.identify_pdf(doc))
    assert plan.candidates
    # Body text added after identifying, right on the header line.
    for page in doc:
        page.insert_text((90, 61), "Fictizumab overlay", fontsize=9)
    doc = pymupdf.open("pdf", doc.tobytes())
    try:
        hf.apply_pdf(doc, plan)
    except (hf.PlanMismatch, hf.HeaderFooterLeft):
        pass
    else:  # pragma: no cover
        raise AssertionError("apply must refuse")


# ---------------------------------------------------------------- S2


def _odd_even(pages):
    def draw(page, i):
        if i % 2 == 0:
            page.insert_text((72, 60), "Acme Pharma odd header", fontsize=9)
        else:
            page.insert_text((400, 60), "Even header ABC-2026-001", fontsize=9)
        _body(page, i)

    return _pdf(pages, draw)


def test_odd_and_even_headers_are_counted_separately():
    for pages in (6, 7):
        _plan, _result, out = _remove(_odd_even(pages))
        text = _text(out)
        assert "odd header" not in text and "Even header" not in text, pages
        assert text.count("Fictizumab body paragraph") == pages


def test_each_parity_still_needs_three_pages():
    plan = hf.identify_pdf(_odd_even(5))  # even pages: only 2
    texts = [e.text for c in plan.candidates for e in c.elements]
    assert not [t for t in texts if "Even header" in t]
    assert len([t for t in texts if "odd header" in t]) == 3


def test_digits_are_ignored_only_in_page_numbers():
    key = hf.repeat_text_key
    assert key("Page 3 of 12") == key("Page 10 of 12")
    assert key("Acme Pharma Confidential  Page 3/76") == key("Acme Pharma Confidential  Page 4/76")
    assert key("第 3 页，共 76 页") == key("第 4 页，共 76 页")
    assert key("- 3 -") == key("- 14 -") and key("7") == key("8")
    assert key("Subject 001") != key("Subject 003")
    assert key("Overlap body 0 gamma") != key("Overlap body 1 gamma")
    assert key("101-001   41 yrs   Visit 3") != key("102-002   42 yrs   Visit 4")


def test_short_line_differing_in_a_number_on_alternate_pages_stays():
    def draw(page, i):
        page.insert_text((72, 60), f"第 {i + 1} 页" if i % 2 else f"Subject 00{i + 1}", fontsize=9, fontname="china-s")
        _body(page, i)

    plan = hf.identify_pdf(_pdf(6, draw))
    assert not [e for c in plan.candidates for e in c.elements if "Subject" in e.text]


def test_signing_line_above_the_header_does_not_hide_the_header():
    # The envelope line sits at the very top, well above the header. It is
    # removed by its own rule, so it must not count as the header's first row.
    def draw(page, i):
        page.insert_text((40, 16), "DocuSign Envelope ID: 0A1B2C3D-0000-4E5F-9A8B-ABCDEF012345", fontsize=7)
        page.insert_text((72, 50), "Acme Pharma", fontsize=9)
        page.insert_text((72, 62), HEADER, fontsize=9)
        _body(page, i)

    plan = hf.identify_pdf(_pdf(5, draw))
    sources = {c.source for c in plan.candidates}
    assert hf.SOURCE_SIGN in sources and hf.SOURCE_REPEAT in sources
    repeat_pages = {e.page for c in plan.candidates if c.source == hf.SOURCE_REPEAT for e in c.elements}
    assert repeat_pages == set(range(5))
