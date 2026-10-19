"""Wording/state rules for 「去掉页眉页脚」 on the result page (fictional demo data)."""

from __future__ import annotations

import pytest

from doc_redactor import header_footer as hf
from doc_redactor.gui import hf_text
from doc_redactor.gui.demo import demo_hf_scans


def _by_kind(scans, kind):
    return next(s for s in scans if s.kind == kind)


def test_no_regex_word_in_any_string():
    strings = [v for v in vars(hf_text).values() if isinstance(v, str)] + list(hf.UI_TEXT.values())
    assert strings
    assert not any("正则" in s for s in strings)


def test_tagged_pdf_and_word_will_delete():
    scans = demo_hf_scans("tag")
    word, pdf = _by_kind(scans, "word"), _by_kind(scans, "pdf")
    assert hf_text.shows_box(pdf, True) and hf_text.shows_box(word, True)
    assert not hf_text.shows_box(pdf, False)
    assert hf_text.page_total(scans, True) == word.hf_pages + pdf.hf_pages
    assert hf_text.page_total(scans, False) == 0
    assert hf_text.summary_value(pdf, True, False) == f"{pdf.hf_pages} 页 · 将删除"
    assert hf_text.summary_value(pdf, True, True) == f"{pdf.hf_pages} 页 · 已删除"
    assert hf_text.summary_value(pdf, False, False) is None
    assert hf_text.block_label(hf.ROLE_SIGNATURE, pdf) == "将删除：签署信息"
    assert hf_text.file_note(word, True) == "ⓘ " + hf_text.WATERMARK
    assert hf_text.output_hint(scans, True, "base") == hf_text.OUTPUT_HINT_HF
    assert hf_text.output_hint(scans, False, "base") == "base"


def test_not_found_pdf_is_greyed_and_deletes_nothing():
    scans = demo_hf_scans("none")
    pdf = _by_kind(scans, "pdf")
    assert pdf.hf_route == hf.ROUTE_NONE
    assert hf_text.shows_box(pdf, True)
    assert hf_text.box_disabled(pdf, False)
    assert hf_text.file_note(pdf, True) == hf_text.NOT_FOUND
    assert hf_text.summary_value(pdf, True, False) == "— 没认出，不删"
    assert hf_text.box_label(pdf, False).endswith("☐ 去掉页眉页脚")


def test_auto_found_needs_review_note():
    pdf = _by_kind(demo_hf_scans("auto"), "pdf")
    assert pdf.hf_route == hf.ROUTE_REPEAT
    assert hf_text.file_note(pdf, True) == hf_text.AUTO_FOUND
    assert hf_text.summary_value(pdf, True, False).endswith("\n" + hf_text.AUTO_FOUND)


@pytest.mark.parametrize("kind,label", [
    ("word", "页眉（保留，按规则替换，部分文字可能替换不到）"),
    ("pdf", "页眉（保留，按规则替换）"),
])
def test_kept_file_labels(kind, label):
    scan = _by_kind(demo_hf_scans("tag"), kind)
    scan.hf_keep = True
    assert not scan.hf_active
    assert hf_text.block_label(hf.ROLE_HEADER, scan) == label
    assert hf_text.block_label(hf.ROLE_SIGNATURE, scan) == "签署信息（保留）"
    assert hf_text.summary_value(scan, True, False) == "— 这个文件保留"
    assert hf_text.file_note(scan, True) == "ⓘ " + hf_text.KEPT


def test_image_row_says_not_processed():
    img = _by_kind(demo_hf_scans("tag"), "image")
    assert not hf_text.shows_box(img, True)
    assert hf_text.summary_value(img, True, False) == "— 图片不处理"
