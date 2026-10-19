"""#28 跟进（出品审 #28 第 3 轮次项 m7、m9）。

Made-up text only (ABC-2026-001, Acme Pharma, Fictizumab, 星辰海).

m9: guards the other half of the removed
``test_rows_beyond_the_two_outermost_are_not_taken``: a third row that is
plain text and differs from page to page stays.
m7: a fixed row taken past the outer two is counted (``fixed_rows``) and
shown on the confirm strip; recognition and the default (不删) do not change.
"""

from __future__ import annotations

from types import SimpleNamespace

import pymupdf

from doc_redactor import header_footer as hf
from doc_redactor.gui import hf_text

WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
PORTRAIT = (595, 842)


def _pdf(draw, pages: int = 5) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=PORTRAIT[0], height=PORTRAIT[1])
        draw(page, i)
    return pymupdf.open("pdf", doc.tobytes())


def _body(page, i):
    page.insert_text((72, 400), f"Fictizumab body paragraph {WORDS[i % len(WORDS)]}", fontsize=10)


def _two_rows(page):
    page.insert_text((72, 30), "Acme Pharma", fontsize=9)
    page.insert_text((72, 42), "ABC-2026-001 Protocol", fontsize=9)


def _remove(doc):
    plan = hf.confirm(hf.identify_pdf(doc))
    hf.apply_pdf(doc, plan)
    out = pymupdf.open("pdf", doc.tobytes(garbage=3))
    return plan, "\n".join(p.get_text() for p in out)


def _fixed(plan) -> int:
    return sum(c.fixed_rows for c in plan.candidates if c.source == hf.SOURCE_REPEAT)


# ------------------------------------------------------------------ m9

def test_plain_third_row_that_differs_per_page_stays():
    def draw(page, i):
        _two_rows(page)
        page.insert_text((72, 54), f"Section {WORDS[i].title()} overview", fontsize=9)
        _body(page, i)

    plan, text = _remove(_pdf(draw))
    assert plan.route == hf.ROUTE_REPEAT
    assert "ABC-2026-001 Protocol" not in text
    for word in WORDS[:5]:
        assert f"Section {word.title()} overview" in text, word
    assert _fixed(plan) == 0


def test_third_row_that_differs_only_in_digits_stays():
    # Same place every page, same text but for a number: matches as a repeat,
    # but is not fixed text, so the band stops before it.
    def draw(page, i):
        _two_rows(page)
        page.insert_text((72, 54), f"Visit {i + 3} summary for 星辰海", fontsize=9, fontname="china-s")
        _body(page, i)

    plan, text = _remove(_pdf(draw))
    assert "ABC-2026-001 Protocol" not in text
    for i in range(5):
        assert f"Visit {i + 3} summary" in text, i
    assert _fixed(plan) == 0


# ------------------------------------------------------------------ m7

def test_fixed_third_row_is_counted():
    def draw(page, i):
        _two_rows(page)
        page.insert_text((72, 54), "Version 2.0 Final", fontsize=9)
        _body(page, i)

    plan = hf.identify_pdf(_pdf(draw))
    assert plan.route == hf.ROUTE_REPEAT
    assert _fixed(plan) == 1
    # Still waiting for confirmation: the hint does not change the default.
    assert hf.unconfirmed(plan) > 0


def test_page_number_third_row_is_not_counted():
    def draw(page, i):
        _two_rows(page)
        page.insert_text((72, 54), f"Page {i + 1} of 5", fontsize=9)
        _body(page, i)

    plan = hf.identify_pdf(_pdf(draw))
    assert plan.route == hf.ROUTE_REPEAT
    assert _fixed(plan) == 0


def test_two_rows_only_are_not_counted():
    def draw(page, i):
        _two_rows(page)
        _body(page, i)

    assert _fixed(hf.identify_pdf(_pdf(draw))) == 0


def test_fixed_rows_hold_no_text():
    def draw(page, i):
        _two_rows(page)
        page.insert_text((72, 54), "Version 2.0 Final", fontsize=9)
        _body(page, i)

    plan = hf.identify_pdf(_pdf(draw))
    for candidate in plan.candidates:
        assert "Version" not in repr(candidate)
    assert "Version" not in repr(hf.summary(plan))


def _scan(fixed: int, confirmed: bool):
    cand = hf.Candidate(
        id="repeat-header", source=hf.SOURCE_REPEAT, role=hf.ROLE_HEADER, pages=(0, 1, 2),
        elements=(), needs_confirmation=True, confirmed=confirmed, fixed_rows=fixed,
    )
    plan = hf.Plan(kind=hf.KIND_PDF, route=hf.ROUTE_REPEAT, page_count=3, candidates=(cand,))
    return SimpleNamespace(
        status="ok", hf_plan=plan, hf_keep=False, hf_confirmed=confirmed, hf_route=hf.ROUTE_REPEAT,
        hf_has_repeat=True, hf_has_sign=False,
    )


def test_confirm_bar_names_the_fixed_rows():
    bar = hf_text.confirm_bar(_scan(2, False), True, False)
    assert bar["status"] == hf_text.PENDING
    assert bar["text"] == hf_text.BAR_PENDING_AUTO_FIXED.format(n=2)
    assert "含 2 行固定文字" in bar["text"] and bar["text"].endswith("现在不删。")
    assert bar["fixed"] == 2 and bar["button"] == hf_text.CONFIRM_BUTTON
    done = hf_text.confirm_bar(_scan(2, True), True, False)
    assert done["status"] == hf_text.CONFIRMED and done["text"].endswith("虚线框含 2 行固定文字。")


def test_confirm_bar_without_fixed_rows_is_unchanged():
    bar = hf_text.confirm_bar(_scan(0, False), True, False)
    assert bar["status"] == hf_text.PENDING
    assert bar["text"] == hf_text.BAR_PENDING_AUTO


def test_fixed_hint_keeps_the_continued_title_and_footnote_warning():
    """#48 n1：有 N 时也保留「续表标题、脚注」这层提醒。"""
    bar = hf_text.confirm_bar(_scan(2, False), True, False)
    assert "含 2 行固定文字" in bar["text"]
    assert "续表标题" in bar["text"] and "脚注" in bar["text"]
    assert bar["text"].endswith("现在不删。")


def test_scan_path_to_confirm_bar_counts_fixed_rows(tmp_path):
    """#48 n3：识别 → 提示整条链走真实 scan_path，不手塞 fixed_rows。编造数据。"""
    from doc_redactor.engine import WordlistRule, build_engine
    from doc_redactor.scan import scan_path, set_file_hf_confirmed

    def draw(page, i):
        _two_rows(page)
        page.insert_text((72, 54), "Version 2.0 Final", fontsize=9)
        _body(page, i)

    src = tmp_path / "fixed.pdf"
    _pdf(draw).save(src)
    scan = scan_path(src, build_engine([], [WordlistRule("药名", ["Nothingmab"], True)]), remove_hf=True)
    bar = hf_text.confirm_bar(scan, True, False)
    assert bar is not None and bar["status"] == hf_text.PENDING
    assert "含 1 行固定文字" in bar["text"]
    assert "Version" not in bar["text"]
    set_file_hf_confirmed(scan, True)
    done = hf_text.confirm_bar(scan, True, False)
    assert done["status"] == hf_text.CONFIRMED and done["text"].endswith("虚线框含 1 行固定文字。")
