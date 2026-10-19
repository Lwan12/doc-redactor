"""Wording and states for 「去掉页眉页脚」 on the result page (方案.md 2.11).

Pure functions over a FileScan and the app's flags, so they can be tested
without a window. Strings are copied from the design brief and prototype.
"""

from __future__ import annotations

from doc_redactor import header_footer as hf
from doc_redactor.scan import KIND_IMAGE, KIND_PDF, KIND_WORD

BOX = "去掉页眉页脚"
BOX_HINT = hf.UI_TEXT["option_hint"]
NOT_FOUND = hf.UI_TEXT["not_found"]
NOT_FOUND_WHY = (
    "原因：文件里没有页眉页脚标记，各页顶部和底部的内容位置也不一致，工具不敢猜，所以没删。"
    "下一步：如果手上有原 Word 文件，从 Word 重新导出 PDF（导出时会带标记）再放进来；"
    "或者先这样写出，页眉页脚里命中的词照样会被替换。"
)
AUTO_FOUND = hf.UI_TEXT["auto_found"]
AUTO_FOUND_TOP = (
    "ⓘ 这个 PDF 没有页眉页脚标记，页眉页脚是按每页同一位置重复的内容自动认出的。"
    "请翻看灰底部分，认错了就在左边取消这个文件的「去掉页眉页脚」。"
)
KEPT = hf.UI_TEXT["file_kept"]
WATERMARK = hf.UI_TEXT["watermark"]
SIGN_NOTE = "电子签署时加上的信封号，不算页眉，每页都有，一起删除"
OUTPUT_HINT_HF = "原文件不会被修改，页眉页脚只在另存的新文件里删除。每次写出都放进新的文件夹，不会盖掉上一次。"

NAMES = {hf.ROLE_HEADER: "页眉", hf.ROLE_FOOTER: "页脚", hf.ROLE_SIGNATURE: "签署信息"}


def shows_box(scan, option_on: bool) -> bool:
    """Word/PDF files whose scan looked for headers/footers and found some,
    or a PDF where nothing was found (the box is then greyed out)."""
    if not option_on or scan.status == "fail" or scan.kind not in (KIND_WORD, KIND_PDF):
        return False
    return scan.hf_found or (scan.kind == KIND_PDF and scan.hf_route == hf.ROUTE_NONE)


def box_disabled(scan, written: bool) -> bool:
    return written or not scan.hf_found


def box_label(scan, written: bool) -> str:
    mark = "☑" if scan.hf_active else "☐"
    return f"   {mark} {BOX}"


def file_note(scan, option_on: bool) -> str:
    """Note under the file row; '' when there is nothing to say."""
    if not shows_box(scan, option_on):
        return ""
    if scan.kind == KIND_PDF and scan.hf_route == hf.ROUTE_NONE:
        return NOT_FOUND
    if scan.hf_keep:
        return "ⓘ " + KEPT
    if scan.hf_watermark:
        return "ⓘ " + WATERMARK
    if scan.hf_route == hf.ROUTE_REPEAT:
        return AUTO_FOUND
    return ""


def page_total(scans, option_on: bool) -> int:
    """Pages whose headers/footers go, over files that keep the box ticked."""
    if not option_on:
        return 0
    return sum(int(scan.hf_pages or 0) for scan in scans if scan.hf_active)


def files_removed(scans) -> int:
    return sum(1 for scan in scans if scan.hf_active)


def summary_value(scan, option_on: bool, written: bool, scans=None) -> str | None:
    """Right pane line after 合计. None hides the row.

    ``scan`` None means 全部文件 (then ``scans`` is the list).
    """
    if not option_on:
        return None
    verb = "已删除" if written else "将删除"
    if scan is None:
        n = page_total(scans or [], option_on)
        auto = any(s.hf_active and s.hf_route == hf.ROUTE_REPEAT for s in scans or [])
        if not n:
            return "— 不删"
        return f"{n} 页 · {verb}" + (f"\n{AUTO_FOUND}" if auto else "")
    if scan.status == "fail":
        return None
    if scan.kind == KIND_IMAGE:
        return "— 图片不处理"
    if scan.kind == KIND_PDF and scan.hf_route == hf.ROUTE_NONE:
        return "— 没认出，不删"
    if not scan.hf_found:
        return "— 没有页眉页脚"
    if scan.hf_keep:
        return "— 这个文件保留"
    head = f"{scan.hf_pages} 页 · {verb}" if scan.hf_pages else verb
    if scan.hf_route == hf.ROUTE_REPEAT:
        head += f"\n{AUTO_FOUND}"
    return head


def output_hint(scans, option_on: bool, base: str) -> str:
    if option_on and files_removed(scans):
        return OUTPUT_HINT_HF
    return base


def block_label(role: str, scan) -> str:
    """Label at the start of a grey block, or of a kept header line."""
    name = NAMES.get(role, "页眉")
    if scan.hf_active:
        return f"将删除：{name}"
    if role == hf.ROLE_SIGNATURE:
        return "签署信息（保留）"
    if scan.kind == KIND_WORD:
        return f"{name}（保留，按规则替换，部分文字可能替换不到）"
    return f"{name}（保留，按规则替换）"
