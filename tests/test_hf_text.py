"""Wording/state rules for 「去掉页眉页脚」 on the result page (fictional demo data).

方案.md 2.11 / brief.md 23 (Lee 10-19): blocks found by repetition wait for a
per-file confirmation; tags, Word parts and the signing line go without one.
"""

from __future__ import annotations

import pytest

from doc_redactor import header_footer as hf
from doc_redactor.gui import hf_text
from doc_redactor.gui.demo import demo_hf_scans
from doc_redactor.scan import set_file_hf_confirmed


def _by_kind(scans, kind):
    return next(s for s in scans if s.kind == kind)


def test_no_regex_word_in_any_string():
    strings = [v for v in vars(hf_text).values() if isinstance(v, str)] + list(hf.UI_TEXT.values())
    strings += list(hf_text.SOURCES.values())
    assert strings
    assert not any("正则" in s for s in strings)


def test_word_shows_whole_file_not_pages():
    scans = demo_hf_scans("tag")
    word = _by_kind(scans, "word")
    assert word.hf_delete_pages == 0
    assert hf_text.summary_value(word, True, False) == "整份删除"
    assert not any("页" in v for _l, v, _t in hf_text.summary_rows(word, True, False))
    word.hf_keep = True
    assert hf_text.summary_value(word, True, False) == "整份保留"
    word.hf_keep = False
    assert hf_text.block_label(hf.ROLE_HEADER, word) == "将删除：页眉 · Word 页眉页脚整块"


def test_tagged_pdf_and_word_will_delete():
    scans = demo_hf_scans("tag")
    word, pdf = _by_kind(scans, "word"), _by_kind(scans, "pdf")
    assert hf_text.shows_box(pdf, True) and hf_text.shows_box(word, True)
    assert not hf_text.shows_box(pdf, False)
    assert hf_text.delete_pages(scans, True) == 2  # PDF pages only; Word has none
    assert hf_text.pending_pages(scans, True) == 0
    assert hf_text.summary_value(pdf, True, False) == "2 页 · 将删除"
    assert hf_text.summary_value(pdf, True, True) == "2 页 · 已删除"
    assert hf_text.summary_value(pdf, False, False) is None
    assert hf_text.block_label(hf.ROLE_HEADER, pdf, hf.SOURCE_TAG) == "将删除：页眉 · 按文件标记删除"
    assert hf_text.block_label(hf.ROLE_SIGNATURE, pdf, hf.SOURCE_SIGN) == "将删除：签署信息 · 按固定文字识别"
    assert hf_text.confirm_bar(pdf, True, False) is None  # tags + signing line: no strip
    assert hf_text.file_note(word, True) == "ⓘ " + hf_text.WATERMARK
    assert hf_text.output_hint(scans, True, "base") == hf_text.OUTPUT_HINT_HF
    assert hf_text.output_hint(scans, False, "base") == "base"
    parts = [p for p, _t in hf_text.top_parts(scans, True, False)]
    assert parts == ["▭ 页眉页脚将删除 2 页，Word 1 份整份"]


def test_not_found_two_lines_and_signing_line_still_goes():
    pdf = _by_kind(demo_hf_scans("none"), "pdf")
    assert pdf.hf_route == hf.ROUTE_NONE and pdf.hf_has_sign
    assert hf_text.NOT_FOUND == "ⓘ 没认出页眉页脚，这份不删"
    assert hf_text.NOT_FOUND_NEXT == "可从原 Word 重新导出 PDF 再放进来，或先这样写出"
    assert hf_text.preview_notes(pdf, True) == [hf_text.NOT_FOUND, hf_text.NOT_FOUND_NEXT]
    assert hf_text.file_note(pdf, True) == hf_text.NOT_FOUND
    # The box can still be ticked: it decides about the signing line.
    assert hf_text.shows_box(pdf, True) and not hf_text.box_disabled(pdf, False)
    assert hf_text.box_label(pdf, False).endswith("☑ 去掉页眉页脚")
    assert hf_text.summary_value(pdf, True, False) == "— 页眉页脚没认出，不删\n签署信息按固定文字删除 2 页"
    assert pdf.hf_active and pdf.hf_delete_pages == 2


def test_not_found_without_signing_line_is_greyed():
    pdf = _by_kind(demo_hf_scans("none-plain"), "pdf")
    assert hf_text.box_disabled(pdf, False)
    assert hf_text.box_label(pdf, False).endswith("☐ 去掉页眉页脚")
    assert hf_text.summary_value(pdf, True, False) == "— 没认出，不删"
    assert not pdf.hf_active


def test_auto_found_waits_then_confirm_then_undo():
    scans = demo_hf_scans("auto")
    pdf = _by_kind(scans, "pdf")
    assert pdf.hf_route == hf.ROUTE_REPEAT and pdf.hf_pending
    # Pending: file row and the strip above the preview say so, in warn colour.
    assert hf_text.file_note_tone(pdf, True) == ("⚠ 待确认，不删（页眉页脚）", hf_text.TONE_WARN)
    bar = hf_text.confirm_bar(pdf, True, False)
    assert bar["status"] == "⚠ 待确认，不删" and bar["button"] == "确认删除" and bar["tone"] == hf_text.TONE_WARN
    assert hf_text.block_style(pdf, hf.SOURCE_REPEAT) == hf_text.STYLE_PENDING
    assert hf_text.block_label(hf.ROLE_HEADER, pdf, hf.SOURCE_REPEAT) == "⚠ 待确认，不删：页眉 · 按每页重复认出"
    # The signing line is not part of it and goes anyway.
    assert hf_text.block_style(pdf, hf.SOURCE_SIGN) == hf_text.STYLE_DELETE
    rows = hf_text.summary_rows(pdf, True, False)
    assert rows == [("▭ 页眉页脚", "2 页 · 将删除", "info"), ("⚠ 待确认", "2 页 · 不删", "warn")]
    # 产品 ②: a page with the signing line and pending blocks is in both counts;
    # nothing shows their sum.
    texts = " ".join(p for p, _t in hf_text.top_parts(scans, True, False))
    assert "待确认 2 页" in texts and "将删除 2 页" in texts and "4 页" not in texts
    hits_pending = len(pdf.visible_hits)

    assert set_file_hf_confirmed(pdf, True)
    assert hf_text.file_note_tone(pdf, True) == ("✓ 已确认删除（重复认出的部分）", hf_text.TONE_OK)
    bar = hf_text.confirm_bar(pdf, True, False)
    assert bar["status"] == "✓ 已确认删除" and bar["button"] == "撤销确认"
    assert hf_text.block_label(hf.ROLE_HEADER, pdf, hf.SOURCE_REPEAT) == "将删除：页眉 · 已确认（按每页重复认出）"
    assert [r[0] for r in hf_text.summary_rows(pdf, True, False)] == ["▭ 页眉页脚"]
    assert len(pdf.visible_hits) < hits_pending  # block hits leave the count

    assert set_file_hf_confirmed(pdf, False)
    assert pdf.hf_pending and len(pdf.visible_hits) == hits_pending
    assert hf_text.confirm_bar(pdf, True, True)["enabled"] is False  # greyed once written


def test_mixed_file_labels_tags_and_the_extra_line_apart():
    pdf = _by_kind(demo_hf_scans("mixed"), "pdf")
    assert pdf.hf_route == hf.ROUTE_TAG and pdf.hf_pending
    assert hf_text.file_note(pdf, True) == "⚠ 待确认，不删（每页重复的一行）"
    assert hf_text.block_label(hf.ROLE_HEADER, pdf, hf.SOURCE_TAG) == "将删除：页眉 · 按文件标记删除"
    assert hf_text.block_label(hf.ROLE_HEADER, pdf, hf.SOURCE_REPEAT) == "⚠ 待确认，不删：每页重复的一行 · 按每页重复认出"
    assert hf_text.confirm_bar(pdf, True, False)["text"] == hf_text.BAR_PENDING_EXTRA


def test_confirm_is_per_file_and_ignored_when_it_does_not_apply():
    scans = demo_hf_scans("auto")
    word, pdf = _by_kind(scans, "word"), _by_kind(scans, "pdf")
    assert not set_file_hf_confirmed(word, True)  # nothing found by repetition
    assert not set_file_hf_confirmed(pdf, True, written=True)
    pdf.hf_keep = True
    assert not set_file_hf_confirmed(pdf, True)
    assert not pdf.hf_pending and hf_text.confirm_bar(pdf, True, False) is None


def test_default_comes_from_one_place():
    assert hf.REPEAT_CONFIRMED_BY_DEFAULT is False
    pdf = _by_kind(demo_hf_scans("auto"), "pdf")
    assert pdf.hf_confirmed is hf.REPEAT_CONFIRMED_BY_DEFAULT


@pytest.mark.parametrize("kind,label", [
    ("word", "页眉（保留，按规则替换，部分文字可能替换不到）"),
    ("pdf", "页眉（保留，按规则替换）"),
])
def test_kept_file_labels(kind, label):
    scan = _by_kind(demo_hf_scans("tag"), kind)
    scan.hf_keep = True
    assert not scan.hf_active
    assert hf_text.block_label(hf.ROLE_HEADER, scan) == label
    assert hf_text.block_label(hf.ROLE_FOOTER, scan) == label.replace("页眉", "页脚")
    assert hf_text.block_label(hf.ROLE_SIGNATURE, scan, hf.SOURCE_SIGN) == "签署信息（保留）"
    assert hf_text.file_note(scan, True) == "ⓘ " + hf_text.KEPT


def test_image_row_says_not_processed():
    img = _by_kind(demo_hf_scans("tag"), "image")
    assert not hf_text.shows_box(img, True)
    assert hf_text.summary_value(img, True, False) == "— 图片不处理"


def test_no_header_footer_rows_for_files_scanned_with_the_option_off():
    # The option was off when these files were scanned: nothing was looked
    # for, so the right pane has no 「页眉页脚」 line to show (#30 layout).
    scans = demo_hf_scans("tag")
    for scan in scans:
        scan.hf_route = ""
    assert hf_text.summary_rows(None, True, False, scans) == []
    assert all(hf_text.summary_rows(s, True, False) == [] for s in scans if s.kind != "image")
