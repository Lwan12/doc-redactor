"""Back-path decisions: what a return keeps, and where a write goes. No tkinter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STEP_FILES = 0
STEP_RULES = 1
STEP_SCAN = 2
STEP_RESULT = 3
STEP_NAMES = ("选文件", "选规则", "开始扫描", "查看结果")

MSG_BACK_TITLE = "返回修改？"
MSG_BACK_BODY = "返回后，这次的扫描结果会清除，要重新扫描。"
MSG_BACK_KEEP = "你取消勾选的 {n} 处，规则不变时重新扫描后仍保留。"
MSG_BACK_CONFIRM = "返回修改"
MSG_BACK_STAY = "留在这里"
MSG_BACK_FILES = "返回不会丢失已添加的文件。"
MSG_STEP_TIP_LOSE = "回到这一步。这次的扫描结果会清除，要重新扫描。"
MSG_STEP_TIP_KEEP = "回到这一步，不会丢失已添加的文件。"
MSG_WRITTEN_TOP = "已写出 {n} 个文件。想改规则，点「返回修改」，改完再出一份新的，这一份会保留。"
MSG_STALE = "规则改过了，请重新扫描"
MSG_STALE_DROP = "规则改过了，请重新扫描。原来取消勾选的位置会清除"
MSG_KEPT = "文件和规则没变，重新扫描后你取消勾选的 {n} 处仍保留。"
MSG_CLEARED_TOP = "文件或规则改过了，原来取消勾选的位置已清除，现在全部按命中处理。"
MSG_RESTORE = "恢复全部（已取消 {n} 处）"
MSG_CLEAR_FILES = "清空会移除列表里的 {n} 个文件，不会删除电脑上的文件。清空只清文件，规则和输出文件夹保留。"
MSG_CANCEL = "⊘ 已取消，没有写出任何文件"
MSG_CANCEL_EXTRA = "已扫完的结果也已清除，没有写出任何文件。"
MSG_PARTIAL_BOTTOM = "已写出 {n} 个，失败 {m} 个"
MSG_PARTIAL_KEEP = "已写出的不会删除。"
MSG_OUTPUT_HINT = "原文件不会被修改。每次写出都放进新的文件夹，不会盖掉上一次。"
MSG_OUTPUT_PARENT = "原文件不会被修改。每次写出都放进带日期和时间的新文件夹，不会盖掉上一次。"
MSG_REMOVE_SELECTED = "移除所选（{n}）"
MSG_SOURCE_REFUSED = "输出路径与原文件相同，已拒绝覆盖原文件"


@dataclass(frozen=True)
class UncheckMemory:
    signature: str
    keys: tuple


@dataclass(frozen=True)
class ScanTicket:
    """Generation captured when a scan starts. Callbacks carry this back."""

    generation: int


@dataclass(frozen=True)
class LeaveDecision:
    """Shared by the step bar and the back buttons."""

    prompt: bool
    clear_preview: bool
    title: str = ""
    body: str = ""
    confirm: str = MSG_BACK_CONFIRM
    stay: str = MSG_BACK_STAY


def _rule_token(rule) -> tuple:
    return (
        rule.name,
        rule.kind,
        rule.pattern or "",
        getattr(rule, "placeholder", "") or "",
        getattr(rule, "replacement", "") or "",
        bool(getattr(rule, "case_sensitive", False)),
        tuple(getattr(rule, "terms", []) or []),
        tuple(getattr(rule, "examples", []) or []),
    )


def files_signature(paths) -> str:
    return repr([str(Path(path)) for path in paths])


def rules_signature(rules) -> str:
    """Content of rules that are checked. Disabled rules are not part of the scan."""
    checked = [_rule_token(rule) for rule in rules if getattr(rule, "enabled", True)]
    return repr(checked)


def session_signature(paths, rules) -> str:
    return files_signature(paths) + "||" + rules_signature(rules)


def hit_identity(path, hit) -> tuple:
    return (
        str(path),
        hit.category,
        hit.unit,
        int(hit.start),
        int(hit.end),
        int(getattr(hit, "page", 0) or 0),
        hit.location,
    )


def capture_unchecks(scans, signature: str) -> UncheckMemory:
    keys = []
    for scan in scans:
        if getattr(scan, "status", "") == "fail":
            continue
        for hit in scan.hits:
            if not hit.enabled:
                keys.append(hit_identity(scan.path, hit))
    return UncheckMemory(signature=signature, keys=tuple(keys))


def apply_unchecks(scans, memory: UncheckMemory | None, signature: str) -> str:
    """Return 'none', 'kept', or 'cleared'.

    'kept' disables the same hits again. 'cleared' leaves every hit enabled
    because the file list or the checked rules changed.
    """
    if memory is None or not memory.keys:
        return "none"
    if signature != memory.signature:
        return "cleared"
    pending = list(memory.keys)
    for scan in scans:
        for hit in scan.hits:
            key = hit_identity(scan.path, hit)
            if key in pending:
                hit.enabled = False
                pending.remove(key)
    return "kept"


def unchecked_count(scans) -> int:
    total = 0
    for scan in scans:
        if getattr(scan, "status", "") == "fail":
            continue
        total += sum(1 for hit in scan.hits if not hit.enabled)
    return total


def back_prompt_body(unchecked: int) -> str:
    if unchecked <= 0:
        return MSG_BACK_BODY
    return MSG_BACK_BODY + MSG_BACK_KEEP.format(n=unchecked)


def decide_leave(*, on_result: bool, written: bool, has_preview: bool, unchecked: int) -> LeaveDecision:
    """Whether leaving the current step must ask first, and whether the preview goes away.

    A prompt exists only on the result page before anything is written.
    Going back from the rules page, or after a write, does not ask.
    """
    if not (on_result and has_preview):
        return LeaveDecision(prompt=False, clear_preview=False)
    if written:
        return LeaveDecision(prompt=False, clear_preview=True)
    return LeaveDecision(
        prompt=True,
        clear_preview=True,
        title=MSG_BACK_TITLE,
        body=back_prompt_body(unchecked),
    )


def decide_close(*, on_result: bool, written: bool, has_preview: bool) -> bool:
    """Close asks only while the result page is showing and nothing has been written."""
    return bool(on_result and not written and has_preview)


def step_clickable(*, target: int, current: int, reached: int, running: bool) -> bool:
    if running:
        return False
    if target < STEP_FILES or target > STEP_RESULT:
        return False
    if target >= current or target > reached:
        return False
    return True


def step_tip(*, on_result: bool, written: bool) -> str:
    if on_result and not written:
        return MSG_STEP_TIP_LOSE
    return MSG_STEP_TIP_KEEP


def rescan_hint(*, from_result: bool, cancelled: bool, signature_matches: bool, unchecked: int) -> str | None:
    if not from_result or cancelled:
        return None
    if signature_matches:
        if unchecked <= 0:
            return None
        return MSG_KEPT.format(n=unchecked)
    if unchecked > 0:
        return MSG_STALE_DROP
    return MSG_STALE


def write_allowed(*, has_fresh_scans: bool) -> bool:
    return bool(has_fresh_scans)


def discard_scan_results(scans) -> list:
    """Cancel throws away hits already found. Nothing is written."""
    return []


def bump_generation(generation: int) -> int:
    """Start and cancel both retire whatever ticket the previous scan holds."""
    return generation + 1


def is_current(ticket: ScanTicket, generation: int) -> bool:
    """Progress and scan-done callbacks are live only for the current generation."""
    return ticket.generation == generation


def retain_editor_draft(draft: dict) -> dict:
    """Back and validation errors keep the examples and the test text."""
    return draft


def without_paths(files, sizes, status, drop: set[str]):
    kept_files = []
    kept_sizes = []
    kept_status = []
    for path, size, state in zip(files, sizes, status):
        if str(path) in drop:
            continue
        kept_files.append(path)
        kept_sizes.append(size)
        kept_status.append(state)
    return kept_files, kept_sizes, kept_status


def clear_file_list(rules, output):
    """Clearing the list drops files only. Rules and the output folder stay."""
    return [], rules, output


def protect_written(paths) -> list:
    """Already written files are not deleted or edited when the user goes back."""
    return list(paths)


def allocate_batch_dir(root: Path | str, when: datetime) -> Path:
    """A new folder per write: YYYYMMDD-HHMM, then -2, -3 if that name exists."""
    parent = Path(root)
    stamp = when.strftime("%Y%m%d-%H%M")
    candidate = parent / stamp
    if not candidate.exists():
        return candidate
    number = 2
    while (parent / f"{stamp}-{number}").exists():
        number += 1
    return parent / f"{stamp}-{number}"


def assert_not_source(source: Path, dest: Path) -> None:
    if Path(source).resolve() == Path(dest).resolve():
        raise ValueError(MSG_SOURCE_REFUSED)


def is_write_failure(scan, result) -> bool:
    """A file that failed to open is not a write failure. A write error is."""
    if getattr(scan, "status", "") == "fail":
        return False
    if result.dest is not None and not result.error:
        return False
    return bool(result.error)


def decide_written(written: bool, results) -> bool:
    """Turn written on only when this attempt produced at least one file.

    If every target failed, leave the flag unchanged. A first write stays off,
    so the written state and close-without-prompt do not start. A later retry
    that also fails does not clear an earlier success.
    """
    for item in results:
        if getattr(item, "dest", None) is not None and not getattr(item, "error", None):
            return True
    return written


def unwritten_failure_banner(failures: list[tuple[str, str]]) -> str:
    """Every target failed, so the batch is not marked written. Say the reason once."""
    if not failures:
        return ""
    reasons = {reason for _name, reason in failures}
    if len(reasons) == 1:
        return f"✕ {next(iter(reasons))}"
    parts = "；".join(f"{name}：{reason}" for name, reason in failures)
    return f"✕ 没写出。{parts}"


def partial_banner(failures: list[tuple[str, str]]) -> str:
    if not failures:
        return ""
    if len(failures) == 1:
        name, reason = failures[0]
        return (
            f"✕ {name} 没写出：{reason}。其他文件已正常写出，已写出的不会删除。"
            "点「重试失败的」只重做这一个。"
        )
    names = "、".join(name for name, _reason in failures)
    return f"✕ {names} 没写出。其他文件已正常写出，已写出的不会删除。点「重试失败的」只重做这些。"


def merge_kept(kept: list, new_paths: list) -> list:
    """Successful outputs stay. A retry must not drop them."""
    merged = list(kept)
    for path in new_paths:
        if path not in merged:
            merged.append(path)
    return merged


def hit_list_payload(scans, *, replacements=None, category_order=None) -> dict:
    """File name to category counts. No source text, no locations.

    A PDF may also carry ``短码`` and ``留空``. Those are subsets of the
    category counts, not extra hits, and they are omitted when zero.
    """
    payload = {}
    for scan in scans:
        if getattr(scan, "status", "") == "fail":
            continue
        counts = dict(scan.category_counts(enabled_only=True))
        if not counts:
            continue
        if getattr(scan, "kind", "") == "pdf":
            short_count, blank_count = _pdf_fit_tallies(scan, replacements, category_order)
            if short_count:
                counts["短码"] = short_count
            if blank_count:
                counts["留空"] = blank_count
        payload[scan.path.name] = counts
    return payload


def _pdf_fit_tallies(scan, replacements, category_order) -> tuple[int, int]:
    from doc_redactor.engine import redaction_label
    from doc_redactor.pdf_redactor import pdf_fit_counts

    def label(category: str) -> str:
        if replacements:
            custom = replacements.get(category)
            if isinstance(custom, str) and custom.strip():
                return custom
        return redaction_label(category)

    hits = scan.enabled_hits if hasattr(scan, "enabled_hits") else []
    return pdf_fit_counts(hits, label, category_order)
