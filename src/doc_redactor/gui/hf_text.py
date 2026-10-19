"""Wording and states for 「去掉页眉页脚」 on the result page (方案.md 2.11,
brief.md 23). All user-facing strings of this feature live here.

Pure functions over a FileScan and the app's flags, so they can be tested
without a window.
"""

from __future__ import annotations

from doc_redactor import header_footer as hf
from doc_redactor.scan import KIND_IMAGE, KIND_PDF, KIND_WORD

BOX = "去掉页眉页脚"
BOX_HINT = hf.UI_TEXT["option_hint"]

# 认不出（产品 10-19 定稿，两行）
NOT_FOUND = hf.UI_TEXT["not_found"]
NOT_FOUND_NEXT = hf.UI_TEXT["not_found_next"]

# 待确认 / 已确认（按每页重复认出）
PENDING = hf.UI_TEXT["pending"]
CONFIRMED = "✓ 已确认删除"
CONFIRM_BUTTON = "确认删除"
UNDO_BUTTON = "撤销确认"
PENDING_ROW_AUTO = "⚠ 待确认，不删（页眉页脚）"
PENDING_ROW_EXTRA = "⚠ 待确认，不删（每页重复的一行）"
CONFIRMED_ROW = "✓ 已确认删除（重复认出的部分）"
BAR_PENDING_AUTO = (
    "这个 PDF 没有页眉页脚标记，虚线框里的页眉、页脚是按每页重复认出的，"
    "可能混进续表标题、表头或脚注，现在不删。"
)
BAR_PENDING_EXTRA = (
    "页眉、页脚按文件标记删除，签署信息按固定文字删除，都不用确认。"
    "虚线框里那一行没有标记，是按每页重复认出的，现在不删。"
)
BAR_CONFIRMED_AUTO = "虚线框里按每页重复认出的页眉、页脚，写出时会删除。"
BAR_CONFIRMED_EXTRA = "虚线框里那一行写出时会删除。页眉、页脚按文件标记删除，签署信息按固定文字删除。"
BAR_SIGN_TAIL = "签署信息按固定文字识别，照常删除。"

KEPT = hf.UI_TEXT["file_kept"]
WATERMARK = hf.UI_TEXT["watermark"]
# 签署行：前端说标签文案还在改，换文案只改这里。
SIGN_SOURCE = "按固定文字识别"
SIGN_NOTE = "电子签署时加上的信封号；只删含固定文字「DocuSign Envelope ID」的这一行"
OUTPUT_HINT_HF = "原文件不会被修改，页眉页脚只在另存的新文件里删除。每次写出都放进新的文件夹，不会盖掉上一次。"

NAMES = {hf.ROLE_HEADER: "页眉", hf.ROLE_FOOTER: "页脚", hf.ROLE_SIGNATURE: "签署信息"}
SOURCES = {
    hf.SOURCE_TAG: "按文件标记删除",
    hf.SOURCE_WORD: "Word 页眉页脚整块",
    hf.SOURCE_SIGN: SIGN_SOURCE,
    hf.SOURCE_REPEAT: "已确认（按每页重复认出）",
}
REPEAT_SOURCE = "按每页重复认出"
EXTRA_LINE = "每页重复的一行"

# Right pane values
WORD_REMOVE = "整份删除"
WORD_REMOVED = "整份已删除"
WORD_KEEP = "整份保留"
NOT_FOUND_SHORT = "— 没认出，不删"
NOT_FOUND_WITH_SIGN = "— 页眉页脚没认出，不删"
SIGN_ONLY = "签署信息按固定文字删除 {n} 页"
PENDING_LABEL = "⚠ 待确认"
PENDING_VALUE = "{n} 页 · 不删"

TONE_INFO, TONE_WARN, TONE_OK, TONE_MUTED = "info", "warn", "ok", "muted"

STYLE_DELETE, STYLE_PENDING, STYLE_KEEP = "delete", "pending", "keep"


def shows_box(scan, option_on: bool) -> bool:
    """Word/PDF files where something was found, or a PDF where nothing was
    found (the box is then greyed out)."""
    if not option_on or scan.status == "fail" or scan.kind not in (KIND_WORD, KIND_PDF):
        return False
    return scan.hf_found or (scan.kind == KIND_PDF and scan.hf_route == hf.ROUTE_NONE)


def box_disabled(scan, written: bool) -> bool:
    return written or not scan.hf_found


def box_label(scan, written: bool) -> str:
    mark = "☐" if scan.hf_keep or not scan.hf_found else "☑"
    return f"   {mark} {BOX}"


def _extra_only(scan) -> bool:
    """Repeats only add to tags: one more repeated line next to them."""
    return scan.hf_route == hf.ROUTE_TAG


def file_note(scan, option_on: bool) -> str:
    """Note under the file row; '' when there is nothing to say."""
    return file_note_tone(scan, option_on)[0]


def file_note_tone(scan, option_on: bool) -> tuple[str, str]:
    if not shows_box(scan, option_on):
        return "", TONE_INFO
    if scan.hf_keep:
        return "ⓘ " + KEPT, TONE_INFO
    if scan.kind == KIND_PDF and scan.hf_route == hf.ROUTE_NONE:
        return NOT_FOUND, TONE_INFO
    if scan.hf_pending:
        return (PENDING_ROW_EXTRA if _extra_only(scan) else PENDING_ROW_AUTO), TONE_WARN
    if scan.hf_has_repeat and scan.hf_confirmed:
        return CONFIRMED_ROW, TONE_OK
    if scan.hf_watermark:
        return "ⓘ " + WATERMARK, TONE_INFO
    return "", TONE_INFO


def preview_notes(scan, option_on: bool) -> list[str]:
    """Plain lines above the preview (认不出)."""
    if not option_on or scan.status == "fail" or scan.kind != KIND_PDF:
        return []
    if scan.hf_route == hf.ROUTE_NONE and not scan.hf_keep:
        return [NOT_FOUND, NOT_FOUND_NEXT]
    return []


def confirm_bar(scan, option_on: bool, written: bool) -> dict | None:
    """The strip above the preview with 「确认删除 / 撤销确认」, or None.

    Only for files with blocks found by repetition; a tagged PDF whose only
    extra is the signing line has no strip.
    """
    if not option_on or scan.status == "fail" or not scan.hf_has_repeat or scan.hf_keep:
        return None
    extra = _extra_only(scan)
    sign = scan.hf_has_sign and not extra
    if scan.hf_confirmed:
        text = BAR_CONFIRMED_EXTRA if extra else BAR_CONFIRMED_AUTO + (BAR_SIGN_TAIL if sign else "")
        return {"status": CONFIRMED, "tone": TONE_OK, "text": text, "button": UNDO_BUTTON, "enabled": not written}
    text = BAR_PENDING_EXTRA if extra else BAR_PENDING_AUTO + (BAR_SIGN_TAIL if sign else "")
    return {"status": PENDING, "tone": TONE_WARN, "text": text, "button": CONFIRM_BUTTON, "enabled": not written}


def block_style(scan, source: str) -> str:
    if scan.hf_source_active(source):
        return STYLE_DELETE
    if source == hf.SOURCE_REPEAT and scan.hf_pending:
        return STYLE_PENDING
    return STYLE_KEEP


def block_label(role: str, scan, source: str = "") -> str:
    """Label at the start of a block, or of a kept header line."""
    source = source or (hf.SOURCE_WORD if scan.kind == KIND_WORD else hf.SOURCE_TAG)
    name = NAMES.get(role, "页眉")
    if source == hf.SOURCE_REPEAT and _extra_only(scan) and role != hf.ROLE_SIGNATURE:
        name = EXTRA_LINE
    style = block_style(scan, source)
    if style == STYLE_DELETE:
        return f"将删除：{name} · {SOURCES[source]}"
    if style == STYLE_PENDING:
        return f"{PENDING}：{name} · {REPEAT_SOURCE}"
    if role == hf.ROLE_SIGNATURE:
        return "签署信息（保留）"
    if scan.kind == KIND_WORD:
        return f"{name}（保留，按规则替换，部分文字可能替换不到）"
    return f"{name}（保留，按规则替换）"


def delete_pages(scans, option_on: bool) -> int:
    """PDF pages where something goes. Word is counted in files, not pages."""
    if not option_on:
        return 0
    return sum(scan.hf_delete_pages for scan in scans if scan.status != "fail")


def pending_pages(scans, option_on: bool) -> int:
    if not option_on:
        return 0
    return sum(scan.hf_pending_pages for scan in scans if scan.status != "fail")


def word_files(scans, option_on: bool) -> int:
    if not option_on:
        return 0
    return sum(1 for s in scans if s.kind == KIND_WORD and s.hf_active)


def page_total(scans, option_on: bool) -> int:
    """Kept for callers: pages that go (PDF)."""
    return delete_pages(scans, option_on)


def files_removed(scans) -> int:
    return sum(1 for scan in scans if scan.hf_active)


def top_parts(scans, option_on: bool, written: bool) -> list[tuple[str, str]]:
    """Pieces for the totals line. 将删除 and 待确认 are shown apart and
    never added up: one page can be in both."""
    if not option_on:
        return []
    verb = "已删除" if written else "将删除"
    parts = []
    n, words = delete_pages(scans, option_on), word_files(scans, option_on)
    if n or words:
        bits = []
        if n:
            bits.append(f"{n} 页")
        if words:
            bits.append(f"Word {words} 份整份")
        parts.append((f"▭ 页眉页脚{verb} " + "，".join(bits), TONE_INFO))
    pending = pending_pages(scans, option_on)
    if pending:
        parts.append((f"⚠ 待确认 {pending} 页（不删）", TONE_WARN))
    return parts


def summary_rows(scan, option_on: bool, written: bool, scans=None) -> list[tuple[str, str, str]]:
    """Rows after 合计 in the right pane: (label, value, tone). [] hides them.

    ``scan`` None means 全部文件 (then ``scans`` is the list).
    """
    if not option_on:
        return []
    verb = "已删除" if written else "将删除"
    label = "▭ 页眉页脚"
    if scan is None:
        scans = scans or []
        if not any(s.hf_route for s in scans):
            return []  # scanned with the option off: nothing was looked for
        n, words = delete_pages(scans, True), word_files(scans, True)
        values = []
        if n:
            values.append(f"{n} 页 · {verb}")
        if words:
            values.append(f"Word {words} 份 · {WORD_REMOVED if written else WORD_REMOVE}")
        rows = [(label, "\n".join(values) if values else "— 不删", TONE_INFO if values else TONE_MUTED)]
        pending = pending_pages(scans, True)
        if pending:
            rows.append((PENDING_LABEL, PENDING_VALUE.format(n=pending), TONE_WARN))
        return rows
    if scan.status == "fail":
        return []
    if scan.kind == KIND_IMAGE:
        return [(label, "— 图片不处理", TONE_MUTED)]
    if scan.kind == KIND_WORD:
        if not scan.hf_route:
            return []
        if not scan.hf_found:
            return [(label, "— 没有页眉页脚", TONE_MUTED)]
        if scan.hf_keep:
            return [(label, WORD_KEEP, TONE_INFO)]
        return [(label, WORD_REMOVED if written else WORD_REMOVE, TONE_INFO)]
    if scan.kind != KIND_PDF or not scan.hf_route:
        return []
    if scan.hf_keep:
        return [(label, "— 这个文件保留", TONE_MUTED)]
    if scan.hf_route == hf.ROUTE_NONE:
        signs = scan.hf_sign_pages
        if signs:
            return [(label, NOT_FOUND_WITH_SIGN + "\n" + SIGN_ONLY.format(n=signs), TONE_INFO)]
        return [(label, NOT_FOUND_SHORT, TONE_MUTED)]
    n = scan.hf_delete_pages
    rows = [(label, f"{n} 页 · {verb}" if n else "— 不删", TONE_INFO if n else TONE_MUTED)]
    if scan.hf_pending_pages:
        rows.append((PENDING_LABEL, PENDING_VALUE.format(n=scan.hf_pending_pages), TONE_WARN))
    return rows


def summary_value(scan, option_on: bool, written: bool, scans=None) -> str | None:
    """First row's value only (older callers and tests)."""
    rows = summary_rows(scan, option_on, written, scans)
    return rows[0][1] if rows else None


def output_hint(scans, option_on: bool, base: str) -> str:
    if option_on and files_removed(scans):
        return OUTPUT_HINT_HF
    return base
