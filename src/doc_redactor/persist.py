"""Save and restore rules/settings. No tkinter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from doc_redactor.ruleset import (
    KIND_PATTERN,
    KIND_WORDLIST,
    Rule,
    common_library,
    default_library_checked,
    default_rules,
)

SETTINGS_VERSION = 1
SETTINGS_NAME = "settings.json"
APP_DIR_NAME = "doc-redactor"

MSG_FIRST = "已载入默认规则，之后修改自动保存。"
MSG_PERSIST_FOOT = "规则保存在你这台电脑上，改动会自动保存。导出只用来备份或发给同事。"
MSG_OPEN_DIR = "打开保存位置"
MSG_OPENED = "✓ 已在文件夹里打开"
MSG_COPIED = "✓ 路径已复制"
MSG_RESET_MENU = "恢复默认规则…"
MSG_RESET_TITLE = "恢复默认规则？"
MSG_RESET_BODY = (
    "会清掉你自己添加或改过的 {n} 条规则，换回默认的 {x} 条。"
    "确认之前不会改任何东西。恢复后 10 分钟内可以撤销。"
)
MSG_RESET_SAME = "现在的规则和默认规则一样，不需要恢复。"
MSG_RESET_CONFIRM = "恢复默认规则"
MSG_RESET_DONE = "已恢复默认规则。10 分钟内可点「撤销」找回你原来的规则。"
MSG_BAD_TITLE = "规则文件有问题"
MSG_BAD_BODY = (
    "上次保存的规则文件打不开，已改名留存，没有删除。"
    "现在用的是默认规则。需要找回旧规则，点「打开保存位置」。"
)
MSG_BAD_FILE = "旧文件：{name}"
MSG_SAVE_FAIL = "这次的修改没能保存，关闭程序后会丢失。可以点「导出」先备份一份。"
MSG_EXPORT_BODY = (
    "得到一个规则文件，用来备份或换电脑。里面只有规则，没有测试用的文字，也没有任何被处理文件的内容。"
)
MSG_IMPORT_FOOT = "将导入 {n} 条。重名的默认跳过，现有规则不变。确认后会自动保存。"
MSG_OUTPUT_REMEMBER = "下次打开还会记住这个文件夹。"
MSG_OUTPUT_MISSING = (
    "✕ 这个文件夹找不到了（可能被移动或删除）。请点「更改…」重新选一个，工具不会自己换路径。"
)
MSG_OUTPUT_UNWRITABLE = "✕ 这个文件夹不能写入（没有权限，或磁盘已满）。请点「更改…」换一个，例如桌面。"

ALLOWED_TOP = frozenset({"version", "output_dir", "library_checked", "rules"})
ALLOWED_RULE = frozenset(
    {
        "name",
        "kind",
        "enabled",
        "replacement",
        "case_sensitive",
        "examples",
        "placeholder",
        "pattern",
        "terms",
        "extra_flags",
    }
)


@dataclass
class Session:
    rules: list[Rule]
    library_checked: list[bool]
    output_dir: str
    first_run: bool = False
    corrupt_name: str | None = None
    save_failed: bool = False


@dataclass
class RevealResult:
    opened: bool
    copied: bool
    path: str
    message: str


@dataclass
class OutputCheck:
    path: str
    error: str | None = None


def settings_dir(
    *,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("DOC_REDACTOR_HOME", "").strip()
    if override:
        return Path(override)
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        local = env.get("LOCALAPPDATA", "").strip()
        if local:
            return Path(local) / APP_DIR_NAME
        base = Path.home() if home is None else home
        return base / "AppData" / "Local" / APP_DIR_NAME
    base = Path.home() if home is None else home
    return base / ".local" / "share" / APP_DIR_NAME


def settings_path(**kwargs) -> Path:
    return settings_dir(**kwargs) / SETTINGS_NAME


def default_output_dir(*, home: Path | None = None) -> Path:
    base = Path.home() if home is None else home
    return base / "脱敏输出"


def backup_filename(when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"settings.损坏.{stamp}.json"


def unique_backup_path(directory: Path, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    candidate = directory / backup_filename(when)
    if not candidate.exists():
        return candidate
    stamp = when.strftime("%Y%m%d-%H%M%S")
    candidate = directory / f"settings.损坏.{stamp}.json"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        extra = directory / f"settings.损坏.{stamp}-{n}.json"
        if not extra.exists():
            return extra
        n += 1


def library_checked_names(flags: list[bool]) -> list[str]:
    items = common_library()
    names = []
    for item, on in zip(items, flags):
        if on:
            names.append(item.name)
    return names


def flags_from_names(names: list[str] | None) -> list[bool]:
    chosen = set(names or [])
    items = common_library()
    if not names:
        return [item.default_checked for item in items]
    return [item.name in chosen for item in items]


def rules_equivalent(left: Rule, right: Rule) -> bool:
    return (
        left.name == right.name
        and left.kind == right.kind
        and bool(left.enabled) == bool(right.enabled)
        and bool(left.case_sensitive) == bool(right.case_sensitive)
        and list(left.terms) == list(right.terms)
        and list(left.examples) == list(right.examples)
        and (left.pattern or "") == (right.pattern or "")
        and (left.placeholder or "") == (right.placeholder or "")
        and left.replacement_text() == right.replacement_text()
        and int(left.extra_flags or 0) == int(right.extra_flags or 0)
    )


def changed_from_defaults(rules: list[Rule]) -> tuple[int, int]:
    """N = extras + modified + missing defaults. X = factory default count."""
    defaults = default_rules()
    x = len(defaults)
    by_name = {rule.name: rule for rule in defaults}
    seen: set[str] = set()
    n = 0
    for rule in rules:
        seen.add(rule.name)
        factory = by_name.get(rule.name)
        if factory is None or not rules_equivalent(rule, factory):
            n += 1
    for rule in defaults:
        if rule.name not in seen:
            n += 1
    return n, x


def reset_body(n: int, x: int) -> str:
    return MSG_RESET_BODY.format(n=n, x=x)


def rule_to_dict(rule: Rule) -> dict:
    node: dict = {
        "name": rule.name,
        "kind": rule.kind,
        "enabled": bool(rule.enabled),
        "replacement": rule.replacement,
        "case_sensitive": bool(rule.case_sensitive),
        "examples": list(rule.examples),
        "placeholder": rule.placeholder or "",
        "pattern": rule.pattern or "",
        "terms": list(rule.terms),
    }
    if rule.extra_flags:
        node["extra_flags"] = int(rule.extra_flags)
    return node


def rule_from_dict(node: object) -> Rule:
    if not isinstance(node, dict):
        raise ValueError("rule is not an object")
    name = node.get("name")
    kind = node.get("kind")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("rule missing name")
    if kind not in {KIND_PATTERN, KIND_WORDLIST}:
        raise ValueError("rule missing kind")
    examples = node.get("examples") or []
    if not isinstance(examples, list):
        raise ValueError("examples")
    terms = node.get("terms") or []
    if not isinstance(terms, list):
        raise ValueError("terms")
    extra = node.get("extra_flags") or 0
    if not isinstance(extra, int) or isinstance(extra, bool):
        extra = 0
    return Rule(
        name=name.strip(),
        kind=kind,
        enabled=bool(node.get("enabled", True)),
        replacement=node.get("replacement") if isinstance(node.get("replacement"), str) else "",
        case_sensitive=bool(node.get("case_sensitive", False)),
        examples=[str(item).strip() for item in examples if str(item).strip()],
        placeholder=node.get("placeholder") if isinstance(node.get("placeholder"), str) else "",
        pattern=node.get("pattern") if isinstance(node.get("pattern"), str) else "",
        terms=[str(item).strip() for item in terms if str(item).strip()],
        extra_flags=int(extra),
    )


def session_payload(rules: list[Rule], library_checked: list[bool], output_dir: str) -> dict:
    return {
        "version": SETTINGS_VERSION,
        "output_dir": output_dir,
        "library_checked": library_checked_names(library_checked),
        "rules": [rule_to_dict(rule) for rule in rules],
    }


def payload_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def parse_payload(raw: str) -> tuple[list[Rule], list[bool], str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("settings root")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("version")
    rules_node = data.get("rules")
    if not isinstance(rules_node, list):
        raise ValueError("rules")
    rules = [rule_from_dict(node) for node in rules_node]
    names = data.get("library_checked")
    if names is not None and not isinstance(names, list):
        raise ValueError("library_checked")
    checked = flags_from_names([str(item) for item in names] if isinstance(names, list) else None)
    output = data.get("output_dir")
    if output is not None and not isinstance(output, str):
        raise ValueError("output_dir")
    output_dir = output.strip() if isinstance(output, str) and output.strip() else str(default_output_dir())
    return rules, checked, output_dir


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def save_session(
    rules: list[Rule],
    library_checked: list[bool],
    output_dir: str,
    *,
    path: Path | None = None,
) -> bool:
    target = path if path is not None else settings_path()
    payload = session_payload(rules, library_checked, output_dir)
    try:
        atomic_write(target, payload_text(payload))
        return True
    except OSError:
        return False


def _factory_session(output_dir: str | None = None) -> Session:
    return Session(
        rules=default_rules(),
        library_checked=default_library_checked(),
        output_dir=output_dir if output_dir is not None else str(default_output_dir()),
    )


def load_session(
    *,
    path: Path | None = None,
    now: datetime | None = None,
    default_output: str | None = None,
) -> Session:
    target = path if path is not None else settings_path()
    output_fallback = default_output if default_output is not None else str(default_output_dir())
    if not target.exists():
        session = _factory_session(output_fallback)
        session.first_run = True
        session.save_failed = not save_session(
            session.rules, session.library_checked, session.output_dir, path=target
        )
        return session
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        session = _factory_session(output_fallback)
        session.save_failed = True
        return session
    try:
        rules, checked, output_dir = parse_payload(raw)
        return Session(rules=rules, library_checked=checked, output_dir=output_dir)
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        backup = unique_backup_path(target.parent, now)
        renamed = None
        try:
            os.replace(target, backup)
            renamed = backup.name
        except OSError:
            renamed = target.name
        session = _factory_session(output_fallback)
        session.corrupt_name = renamed
        session.save_failed = not save_session(
            session.rules, session.library_checked, session.output_dir, path=target
        )
        return session


def check_output_dir(raw: str) -> OutputCheck:
    """Existence and writability. Does not create the folder or change the path."""
    text = (raw or "").strip() or "."
    path = Path(text)
    if not path.exists() or not path.is_dir():
        return OutputCheck(path=text, error=MSG_OUTPUT_MISSING)
    try:
        probe = path / ".doc_redactor_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return OutputCheck(path=text, error=MSG_OUTPUT_UNWRITABLE)
    return OutputCheck(path=text, error=None)


def reveal_directory(
    directory: Path,
    *,
    platform: str | None = None,
    startfile=None,
) -> RevealResult:
    directory.mkdir(parents=True, exist_ok=True)
    plat = sys.platform if platform is None else platform
    shown = str(directory)
    if plat.startswith("win"):
        opener = startfile if startfile is not None else getattr(os, "startfile", None)
        if opener is None:
            return RevealResult(opened=False, copied=True, path=shown, message=MSG_COPIED)
        opener(shown)
        return RevealResult(opened=True, copied=False, path=shown, message=MSG_OPENED)
    return RevealResult(opened=False, copied=True, path=shown, message=MSG_COPIED)


def payload_leaks_source(text: str, forbidden: list[str]) -> list[str]:
    return [item for item in forbidden if item and item in text]
