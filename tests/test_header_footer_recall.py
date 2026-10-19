"""「重复认出」不能漏认（PR #28 第 3 轮，出品审 M2）。

Every PDF here is built in memory from made-up text (ABC-2026-001,
Acme Pharma, Fictizumab, 星辰海). Each case is a real header/footer that the
round-2 rules left on the page: two footer rows 16pt apart, a three-row
header, and landscape pages whose header sits elsewhere than on the
portrait pages. After confirming, none of it may be readable.
"""

from __future__ import annotations

import pymupdf

from doc_redactor import header_footer as hf

WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
         "kilo", "lima", "mike", "november")


def _pdf(sizes, draw) -> pymupdf.Document:
    doc = pymupdf.open()
    for i, (w, h) in enumerate(sizes):
        page = doc.new_page(width=w, height=h)
        draw(page, i)
    return pymupdf.open("pdf", doc.tobytes())


def _body(page, i, y=400):
    page.insert_text((72, y), f"Fictizumab body paragraph {WORDS[i % len(WORDS)]}", fontsize=10)
    page.insert_text((72, y + 20), f"Another body line {WORDS[-1 - i % len(WORDS)]}", fontsize=10)


def _remove(doc):
    plan = hf.confirm(hf.identify_pdf(doc))
    result = hf.apply_pdf(doc, plan)
    out = pymupdf.open("pdf", doc.tobytes(garbage=3))
    return plan, result, out


def _text(doc) -> str:
    return "\n".join(page.get_text() for page in doc)


PORTRAIT = (595, 842)
LANDSCAPE = (842, 595)


def test_two_footer_rows_16pt_apart_are_both_removed():
    # Fixed footer, then the page number one blank line below (baselines 27pt
    # apart at 9pt, about 15pt of white space between the boxes).
    def draw(page, i):
        _body(page, i)
        page.insert_text((214, 756), "Acme Pharma Confidential Footer Text", fontsize=9)
        page.insert_text((274, 783), f"Page {i + 1} of 5", fontsize=9)

    _plan, result, out = _remove(_pdf([PORTRAIT] * 5, draw))
    text = _text(out)
    assert "Confidential Footer" not in text
    assert "Page 1 of 5" not in text
    assert text.count("Fictizumab body paragraph") == 5
    assert result.body_lost == 0
    assert hf.leftover_edge_rows(out) == (0, 0)


def test_three_header_rows_are_all_removed():
    def draw(page, i):
        page.insert_text((72, 30), "Acme Pharma", fontsize=9)
        page.insert_text((72, 42), "ABC-2026-001 Protocol", fontsize=9)
        page.insert_text((72, 54), "Version 2.0 Final", fontsize=9)
        _body(page, i)

    plan, result, out = _remove(_pdf([PORTRAIT] * 5, draw))
    assert plan.route == hf.ROUTE_REPEAT
    text = _text(out)
    for gone in ("Acme Pharma", "ABC-2026-001 Protocol", "Version 2.0"):
        assert gone not in text, gone
    assert text.count("Fictizumab body paragraph") == 5
    assert result.body_lost == 0
    assert hf.leftover_edge_rows(out) == (0, 0)


def _mixed(landscape_pages: int):
    portrait = 10
    sizes = [PORTRAIT] * portrait + [LANDSCAPE] * landscape_pages

    def draw(page, i):
        h = page.rect.height
        if page.rect.width > h:
            # Landscape section: header and footer further from the edges
            # than on the portrait pages, as in a real CSR with listings.
            page.insert_text((72, 50), "ABC-2026-001 Listing header", fontsize=9)
            page.insert_text((72, h - 70), "星辰海 Acme Pharma Confidential", fontsize=9, fontname="china-s")
            page.insert_text((400, h - 52), f"Page {i + 1} of {len(sizes)}", fontsize=9)
            _body(page, i, y=250)
        else:
            page.insert_text((72, 36), "ABC-2026-001 Listing header", fontsize=9)
            page.insert_text((72, h - 50), "星辰海 Acme Pharma Confidential", fontsize=9, fontname="china-s")
            page.insert_text((280, h - 30), f"Page {i + 1} of {len(sizes)}", fontsize=9)
            _body(page, i)

    return sizes, draw


def test_landscape_pages_are_judged_as_their_own_group():
    sizes, draw = _mixed(4)
    plan, result, out = _remove(_pdf(sizes, draw))
    assert hf.pages_affected(plan) == len(sizes)
    for index in range(len(sizes)):
        text = out[index].get_text()
        assert "Listing header" not in text, index
        assert "Confidential" not in text, index
        assert "Page " not in text, index
        assert "Fictizumab body paragraph" in text, index
    assert result.body_lost == 0


def test_two_landscape_pages_are_too_few_to_count_as_repeating():
    sizes, draw = _mixed(2)
    plan = hf.identify_pdf(_pdf(sizes, draw))
    assert {e.page for c in plan.candidates for e in c.elements} == set(range(10))


def test_body_title_two_lines_below_the_header_stays():
    # A repeated table title with about two blank lines between it and the
    # header is body text: the band only spans what is about one line apart.
    def draw(page, i):
        page.insert_text((72, 36), "ABC-2026-001 Protocol", fontsize=9)
        page.insert_text((72, 70), "Table 14.1 Demographics (continued)", fontsize=10)
        _body(page, i)

    _plan, result, out = _remove(_pdf([PORTRAIT] * 5, draw))
    text = _text(out)
    assert "ABC-2026-001 Protocol" not in text
    assert text.count("Table 14.1 Demographics (continued)") == 5
    assert result.body_lost == 0


def test_leftover_edge_rows_finds_a_footer_nobody_removed():
    # The round-2 「残留 0」 only counted glyphs of recognised candidates. This
    # count looks at the output itself: fixed rows still repeating at the
    # page edge, whether or not anything recognised them.
    def draw(page, i):
        _body(page, i)
        page.insert_text((214, 790), "Acme Pharma Confidential Footer Text", fontsize=9)

    doc = _pdf([PORTRAIT] * 5, draw)
    assert hf.leftover_edge_rows(doc) == (5, 5)
    _plan, _result, out = _remove(doc)
    assert hf.leftover_edge_rows(out) == (0, 0)


def test_leftover_edge_rows_ignores_body_text():
    def draw(page, i):
        _body(page, i)
        page.insert_text((72, 300), "Table 14.1 Demographics (continued)", fontsize=10)

    assert hf.leftover_edge_rows(_pdf([PORTRAIT] * 5, draw)) == (0, 0)
