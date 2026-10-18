"""In-memory rules: add/edit/delete/undo/import/export/common library. No tkinter."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import regex as re
import yaml

from doc_redactor.config import parse_flags
from doc_redactor.engine import RedactionEngine, RegexRule, WordlistRule, build_engine, redaction_label
from doc_redactor.pattern_guess import (
    compile_pattern,
    guess_from_examples,
    guess_from_placeholder,
    wrap_pattern,
)

KIND_PATTERN = "pattern"
KIND_WORDLIST = "wordlist"
UNDO_TTL_SECONDS = 10 * 60
DUP_SKIP = "跳过"
DUP_RENAME = "改名后导入"
DUP_OVERWRITE = "覆盖"

PROTOCOL_LIST_NAME = "方案号清单"
LETTER_DIGIT_NAME = "字母加数字"
MSG_PLAN_EMPTY = "还没有词，去导入"
HINT_LETTER_DIGIT = "这条抓不全，也可能误伤"
# ≥2 letters + 3–4 digits, optional hyphen and suffix (fictional examples: AAA0000, AA-K0000)
LETTER_DIGIT_INNER = r"[A-Za-z]{2,}(?:-[A-Za-z]+)?-?\d{3,4}(?:[-/][A-Za-z0-9]+)?"

MSG_IMPORT_FAIL = "这个文件打不开，或不是本工具导出的规则文件。现有规则没有改动。"
MSG_WORD_DUP = "「{names}」已经在词表里了，没有重复添加。"
MSG_WORD_DUP_EXTRA = "另外添加了 {n} 个新词。"
MSG_WORD_ADDED = "已添加 {n} 个词。"
MSG_WORD_EMPTY_ADD = "请先写要加的词，每行一个。"
MSG_WORD_DELETED = "已删除 {n} 个词。10 分钟内可撤销。"


class DupAction(str, Enum):
    SKIP = DUP_SKIP
    RENAME = DUP_RENAME
    OVERWRITE = DUP_OVERWRITE


@dataclass
class Rule:
    name: str
    kind: str
    enabled: bool = True
    examples: list[str] = field(default_factory=list)
    placeholder: str = ""
    pattern: str = ""
    case_sensitive: bool = False
    extra_flags: int = 0
    replacement: str = ""
    terms: list[str] = field(default_factory=list)
    source_path: str | None = None

    def display_kind(self) -> str:
        return "词表" if self.kind == KIND_WORDLIST else "例子"

    def display_example(self) -> str:
        if self.kind == KIND_WORDLIST:
            if not self.terms:
                return MSG_PLAN_EMPTY
            if len(self.terms) == 1:
                return self.terms[0]
            return f"{self.terms[0]} 等 {len(self.terms)} 个词"
        if self.examples:
            return self.examples[0]
        return self.placeholder

    def replacement_text(self) -> str:
        if self.replacement.strip():
            return self.replacement
        return redaction_label(self.name)

    def compile_flags(self) -> int:
        flags = self.extra_flags
        if self.case_sensitive:
            return flags & ~int(re.IGNORECASE)
        return flags | int(re.IGNORECASE)


@dataclass
class UndoRecord:
    label: str
    snapshot: list[Rule]
    created_at: float


@dataclass(frozen=True)
class LibraryItem:
    name: str
    kind: str
    example: str
    default_checked: bool
    placeholder: str = ""
    pattern: str = ""
    terms: tuple[str, ...] = ()
    is_subitem: bool = False
    parent_name: str = ""
    hint: str = ""
    replacement: str = ""


@dataclass
class ImportItem:
    name: str
    example: str
    status: str  # new, dup, bad
    action: str = DUP_SKIP
    rule: Rule | None = None
    error: str = ""
    rename_to: str = ""


@dataclass
class ImportPreview:
    path: Path | None
    failed: bool = False
    message: str = ""
    items: list[ImportItem] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return sum(1 for item in self.items if item.status == "new")

    @property
    def dup_count(self) -> int:
        return sum(1 for item in self.items if item.status == "dup")

    @property
    def bad_count(self) -> int:
        return sum(1 for item in self.items if item.status == "bad")

    def will_import_count(self) -> int:
        n = 0
        for item in self.items:
            if item.status == "new":
                n += 1
            elif item.status == "dup" and item.action != DUP_SKIP:
                n += 1
        return n


def common_library() -> list[LibraryItem]:
    project = guess_from_placeholder("AAA-####-###")
    nct = guess_from_placeholder("NCT########")
    email_inner = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    phone_inner = r"1[3-9]\d{1}[ \-]?[0-9]{4}[ \-]?[0-9]{4}"
    return [
        LibraryItem(
            name="项目号",
            kind=KIND_PATTERN,
            example="AAA-0000-000",
            default_checked=True,
            placeholder="AAA-####-###",
            pattern=wrap_pattern(project.inner_pattern),
            replacement=redaction_label("项目号"),
        ),
        LibraryItem(
            name=PROTOCOL_LIST_NAME,
            kind=KIND_WORDLIST,
            example="AAA0000、AA-K0000（粘贴或导入一列）",
            default_checked=True,
            terms=(),
            replacement=redaction_label(PROTOCOL_LIST_NAME),
        ),
        LibraryItem(
            name=LETTER_DIGIT_NAME,
            kind=KIND_PATTERN,
            example="AA-K0000",
            default_checked=False,
            pattern=wrap_pattern(LETTER_DIGIT_INNER),
            is_subitem=True,
            parent_name=PROTOCOL_LIST_NAME,
            hint=HINT_LETTER_DIGIT,
            replacement=redaction_label(LETTER_DIGIT_NAME),
        ),
        LibraryItem(
            name="申办方",
            kind=KIND_WORDLIST,
            example="Acme Pharma",
            default_checked=True,
            terms=("Acme Pharma",),
            replacement=redaction_label("申办方"),
        ),
        LibraryItem(
            name="药名",
            kind=KIND_WORDLIST,
            example="Fictizumab",
            default_checked=True,
            terms=("Fictizumab",),
            replacement=redaction_label("药名"),
        ),
        LibraryItem(
            name="NCT 试验登记号",
            kind=KIND_PATTERN,
            example="NCT00000000",
            default_checked=True,
            placeholder="NCT########",
            pattern=wrap_pattern(nct.inner_pattern),
            replacement=redaction_label("NCT 试验登记号"),
        ),
        LibraryItem(
            name="邮箱",
            kind=KIND_PATTERN,
            example="name@example.com",
            default_checked=False,
            pattern=wrap_pattern(email_inner),
            replacement=redaction_label("邮箱"),
        ),
        LibraryItem(
            name="手机号（中国大陆）",
            kind=KIND_PATTERN,
            example="138 0000 0000",
            default_checked=False,
            pattern=wrap_pattern(phone_inner),
            replacement=redaction_label("手机号（中国大陆）"),
        ),
    ]


def library_main_items(items: list[LibraryItem] | None = None) -> list[LibraryItem]:
    items = items if items is not None else common_library()
    return [item for item in items if not item.is_subitem]


def apply_library_toggle(items: list[LibraryItem], checked: list[bool], index: int) -> list[bool]:
    """Toggle a library row. Sub-item is gated by its parent; unchecking parent unchecks children."""
    checked = list(checked)
    if index < 0 or index >= len(items):
        return checked
    item = items[index]
    if item.is_subitem:
        parent_on = False
        for i, other in enumerate(items):
            if other.name == item.parent_name and not other.is_subitem:
                parent_on = bool(checked[i])
                break
        if not parent_on:
            return checked
    checked[index] = not checked[index]
    if not item.is_subitem and not checked[index]:
        for i, other in enumerate(items):
            if other.is_subitem and other.parent_name == item.name:
                checked[i] = False
    return checked


def find_word_hits(terms: list[str], text: str, *, case_sensitive: bool) -> list[tuple[int, int, str]]:
    if not terms or not text:
        return []
    flags = 0 if case_sensitive else re.IGNORECASE
    found: list[tuple[int, int, str]] = []
    for term in sorted({item for item in terms if item}, key=len, reverse=True):
        for match in re.finditer(re.escape(term), text, flags):
            found.append((match.start(), match.end(), match.group(0)))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    out: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, token in found:
        if start >= last_end:
            out.append((start, end, token))
            last_end = end
    return out


def default_rules() -> list[Rule]:
    """Factory defaults: library items with default_checked (not the 7-item library)."""
    return [
        library_item_to_rule(item, enabled=True)
        for item in common_library()
        if item.default_checked and not item.is_subitem
    ]


def default_library_checked() -> list[bool]:
    return [item.default_checked for item in common_library()]


def library_item_to_rule(item: LibraryItem, *, enabled: bool = True) -> Rule:
    replacement = item.replacement.strip() or redaction_label(item.name)
    if item.kind == KIND_WORDLIST:
        return Rule(
            name=item.name,
            kind=KIND_WORDLIST,
            enabled=enabled,
            terms=list(item.terms),
            examples=[item.example] if item.example else [],
            case_sensitive=False,
            replacement=replacement,
        )
    examples = [item.example] if item.example else []
    return Rule(
        name=item.name,
        kind=KIND_PATTERN,
        enabled=enabled,
        examples=examples,
        placeholder=item.placeholder,
        pattern=item.pattern,
        case_sensitive=False,
        replacement=replacement,
    )


def add_words(
    existing: list[str],
    incoming: list[str],
    *,
    case_sensitive: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Return (new_list, added, skipped)."""
    added: list[str] = []
    skipped: list[str] = []
    current = list(existing)

    def seen(word: str) -> bool:
        if case_sensitive:
            return word in current or word in added
        lowered = word.lower()
        return any(item.lower() == lowered for item in current) or any(
            item.lower() == lowered for item in added
        )

    for raw in incoming:
        word = raw.strip()
        if not word:
            continue
        if seen(word):
            skipped.append(word)
        else:
            added.append(word)
    current.extend(added)
    return current, added, skipped


def word_dup_message(skipped: list[str], added: list[str]) -> str:
    if not skipped:
        return MSG_WORD_ADDED.format(n=len(added))
    names = "」「".join(skipped)
    text = MSG_WORD_DUP.format(names=names)
    if added:
        text += MSG_WORD_DUP_EXTRA.format(n=len(added))
    return text


class RuleSet:
    def __init__(self, rules: list[Rule] | None = None, *, clock=time.monotonic) -> None:
        self.rules: list[Rule] = list(rules or [])
        self._clock = clock
        self._undo: UndoRecord | None = None

    def names(self) -> list[str]:
        return [rule.name for rule in self.rules]

    def enabled_rules(self) -> list[Rule]:
        return [rule for rule in self.rules if rule.enabled]

    def find_index(self, name: str) -> int | None:
        for index, rule in enumerate(self.rules):
            if rule.name == name:
                return index
        return None

    def undo_label(self) -> str | None:
        if self._undo is None:
            return None
        if self._clock() - self._undo.created_at >= UNDO_TTL_SECONDS:
            self._undo = None
            return None
        return self._undo.label

    def undo(self) -> bool:
        if self.undo_label() is None:
            return False
        assert self._undo is not None
        self.rules = self._undo.snapshot
        self._undo = None
        return True

    def add(self, rule: Rule, *, overwrite: bool = False) -> None:
        existing = self.find_index(rule.name)
        if existing is not None and not overwrite:
            raise ValueError(MSG_DUP_NAME(rule.name))
        self._push(f"已新增「{rule.name}」。")
        if existing is not None and overwrite:
            self.rules[existing] = copy.deepcopy(rule)
        else:
            self.rules.append(copy.deepcopy(rule))

    def replace(self, index: int, rule: Rule) -> None:
        self._push(f"已修改「{rule.name}」。")
        self.rules[index] = copy.deepcopy(rule)

    def delete(self, index: int) -> Rule:
        rule = self.rules[index]
        self._push(f"已删除「{rule.name}」。10 分钟内可撤销。")
        return self.rules.pop(index)

    def set_enabled(self, index: int, enabled: bool) -> None:
        self.rules[index].enabled = enabled

    def add_from_library(self, items: list[LibraryItem], checked: list[bool]) -> int:
        parent_on = {
            item.name: bool(on)
            for item, on in zip(items, checked)
            if not item.is_subitem
        }
        incoming: list[Rule] = []
        for item, on in zip(items, checked):
            if not on:
                continue
            if item.is_subitem and not parent_on.get(item.parent_name, False):
                continue
            if self.find_index(item.name) is not None:
                continue
            incoming.append(library_item_to_rule(item, enabled=True))
        if not incoming:
            return 0
        self._push("已从常用规则库添加。")
        self.rules.extend(incoming)
        return len(incoming)

    def import_terms(self, index: int, incoming: list[str]) -> tuple[list[str], list[str], list[str]]:
        """Merge words into a wordlist and enable it. Used after 导入清单."""
        rule = self.rules[index]
        merged, added, skipped = add_words(
            list(rule.terms), incoming, case_sensitive=rule.case_sensitive
        )
        self._push(f"已更新「{rule.name}」。")
        rule.terms = merged
        rule.enabled = True
        if merged:
            rule.examples = [merged[0]]
        return merged, added, skipped

    def seed_defaults(self) -> None:
        if self.rules:
            return
        self.rules = default_rules()

    def restore_defaults(self, *, label: str) -> None:
        self._push(label)
        self.rules = default_rules()

    def to_engine(self) -> RedactionEngine:
        regex_rules: list[RegexRule] = []
        wordlists: list[WordlistRule] = []
        for rule in self.enabled_rules():
            if rule.kind == KIND_WORDLIST:
                wordlists.append(
                    WordlistRule(
                        category=rule.name,
                        terms=list(rule.terms),
                        case_sensitive=rule.case_sensitive,
                        replacement=rule.replacement,
                    )
                )
                continue
            pattern = rule.pattern
            if not pattern:
                if rule.placeholder:
                    guessed = guess_from_placeholder(rule.placeholder)
                    pattern = wrap_pattern(guessed.inner_pattern) if guessed.ok else ""
                elif rule.examples:
                    guessed = guess_from_examples(rule.examples)
                    pattern = wrap_pattern(guessed.inner_pattern) if guessed.ok else ""
            if not pattern:
                continue
            regex_rules.append(
                RegexRule(
                    name=rule.name,
                    pattern=pattern,
                    flags=rule.compile_flags(),
                    replacement=rule.replacement,
                )
            )
        return build_engine(regex_rules, wordlists)

    def replacements(self) -> dict[str, str]:
        return {rule.name: rule.replacement_text() for rule in self.rules}

    def export_yaml(self, *, only_enabled: bool = False) -> str:
        chosen = [rule for rule in self.rules if (rule.enabled if only_enabled else True)]
        payload = rules_to_mapping(chosen)
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)

    def save(self, path: str | Path, *, only_enabled: bool = False) -> None:
        Path(path).write_text(self.export_yaml(only_enabled=only_enabled), encoding="utf-8")

    def preview_import(self, path: str | Path) -> ImportPreview:
        target = Path(path)
        try:
            rules = load_rules_file(target)
        except (OSError, ValueError, yaml.YAMLError):
            return ImportPreview(path=target, failed=True, message=MSG_IMPORT_FAIL)
        items: list[ImportItem] = []
        for rule, error in rules:
            if error or rule is None:
                items.append(
                    ImportItem(
                        name=(rule.name if rule else "（没有名字）"),
                        example=(rule.display_example() if rule else "读不出匹配写法"),
                        status="bad",
                        error=error or "无效",
                    )
                )
                continue
            status = "dup" if self.find_index(rule.name) is not None else "new"
            action = DUP_SKIP if status == "dup" else "直接导入"
            items.append(
                ImportItem(
                    name=rule.name,
                    example=rule.display_example(),
                    status=status,
                    action=action,
                    rule=rule,
                    rename_to=f"{rule.name} (2)",
                )
            )
        return ImportPreview(path=target, items=items)

    def apply_import(self, preview: ImportPreview) -> int:
        if preview.failed:
            return 0
        incoming: list[tuple[str, Rule]] = []
        for item in preview.items:
            if item.rule is None or item.status == "bad":
                continue
            if item.status == "new":
                incoming.append(("add", copy.deepcopy(item.rule)))
            elif item.action == DUP_SKIP:
                continue
            elif item.action == DUP_OVERWRITE:
                incoming.append(("overwrite", copy.deepcopy(item.rule)))
            elif item.action == DUP_RENAME:
                renamed = copy.deepcopy(item.rule)
                renamed.name = _unique_name(item.rename_to or f"{item.name} (2)", self.names())
                incoming.append(("add", renamed))
        if not incoming:
            return 0
        self._push("已导入规则。")
        count = 0
        for action, rule in incoming:
            if action == "overwrite":
                index = self.find_index(rule.name)
                if index is not None:
                    self.rules[index] = rule
                else:
                    self.rules.append(rule)
            else:
                self.rules.append(rule)
            count += 1
        return count

    def _push(self, label: str) -> None:
        self._undo = UndoRecord(
            label=label,
            snapshot=copy.deepcopy(self.rules),
            created_at=self._clock(),
        )


def MSG_DUP_NAME(name: str) -> str:
    return f"已经有一条叫「{name}」的规则了。请换个名字，或选择「覆盖旧的」。"


def _unique_name(base: str, existing: list[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base} ({n})" in existing or f"{base}({n})" in existing:
        n += 1
    candidate = f"{base} ({n})"
    if base.endswith(" (2)") and base not in existing:
        return base
    while candidate in existing:
        n += 1
        candidate = f"{base} ({n})"
    return candidate


def rules_to_mapping(rules: list[Rule]) -> dict:
    regex_nodes: list[dict] = []
    wordlist_nodes: list[dict] = []
    for rule in rules:
        if rule.kind == KIND_WORDLIST:
            node = {
                "category": rule.name,
                "case_sensitive": bool(rule.case_sensitive),
                "terms": list(rule.terms),
                "enabled": bool(rule.enabled),
            }
            if rule.source_path:
                node["path"] = rule.source_path
            if rule.replacement:
                node["replacement"] = rule.replacement
            wordlist_nodes.append(node)
            continue
        flags = []
        compiled = rule.compile_flags()
        for name in ("IGNORECASE", "MULTILINE", "DOTALL", "VERBOSE", "ASCII"):
            if compiled & getattr(re, name):
                flags.append(name)
        node = {
            "name": rule.name,
            "pattern": rule.pattern,
            "flags": "|".join(flags),
            "enabled": bool(rule.enabled),
        }
        if rule.examples:
            node["example"] = rule.examples[0]
            if len(rule.examples) > 1:
                node["examples"] = list(rule.examples)
        if rule.placeholder:
            node["placeholder"] = rule.placeholder
        if rule.replacement:
            node["replacement"] = rule.replacement
        regex_nodes.append(node)
    return {"regex": regex_nodes, "wordlists": wordlist_nodes}


def load_rules_file(path: str | Path) -> list[tuple[Rule | None, str]]:
    rules_path = Path(path)
    raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("规则文件的顶层必须是映射")
    regex_nodes = raw.get("regex", raw.get("regex_rules", [])) or []
    wordlist_nodes = raw.get("wordlists", []) or []
    if not isinstance(regex_nodes, list) or not isinstance(wordlist_nodes, list):
        raise ValueError("regex 与 wordlists 必须是列表")
    result: list[tuple[Rule | None, str]] = []
    for index, node in enumerate(regex_nodes, start=1):
        try:
            result.append((_parse_regex_rule(node, index), ""))
        except ValueError as exc:
            name = ""
            if isinstance(node, dict) and isinstance(node.get("name"), str):
                name = node.get("name") or ""
            result.append((Rule(name=name or "（没有名字）", kind=KIND_PATTERN), str(exc)))
    for index, node in enumerate(wordlist_nodes, start=1):
        try:
            result.append((_parse_wordlist_rule(node, index, rules_path.parent), ""))
        except (ValueError, FileNotFoundError, OSError) as exc:
            name = ""
            if isinstance(node, dict) and isinstance(node.get("category"), str):
                name = node.get("category") or ""
            result.append((Rule(name=name or "（没有名字）", kind=KIND_WORDLIST), str(exc)))
    if not result:
        # empty file is valid
        return []
    return result


def load_ruleset(path: str | Path, *, clock=time.monotonic) -> RuleSet:
    loaded = load_rules_file(path)
    rules = []
    for rule, error in loaded:
        if error or rule is None:
            continue
        rules.append(rule)
    return RuleSet(rules, clock=clock)


def _parse_regex_rule(node: object, index: int) -> Rule:
    if not isinstance(node, dict):
        raise ValueError(f"第 {index} 条规则必须是映射")
    name = node.get("name")
    pattern = node.get("pattern")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"第 {index} 条规则缺少名称")
    if not isinstance(pattern, str) or pattern == "":
        raise ValueError(f"规则 {name} 缺少匹配写法")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"规则 {name} 暂时用不了") from exc
    if "flags" not in node:
        flags = 0
    else:
        flags = parse_flags(node.get("flags"))
    case_sensitive = not bool(flags & int(re.IGNORECASE))
    extra = flags & ~int(re.IGNORECASE)
    examples: list[str] = []
    if isinstance(node.get("examples"), list):
        examples = [str(item).strip() for item in node["examples"] if str(item).strip()]
    elif isinstance(node.get("example"), str) and node["example"].strip():
        examples = [node["example"].strip()]
    placeholder = node.get("placeholder") if isinstance(node.get("placeholder"), str) else ""
    replacement = node.get("replacement") if isinstance(node.get("replacement"), str) else ""
    enabled = bool(node.get("enabled", True))
    return Rule(
        name=name.strip(),
        kind=KIND_PATTERN,
        enabled=enabled,
        examples=examples,
        placeholder=placeholder,
        pattern=pattern,
        case_sensitive=case_sensitive,
        extra_flags=extra,
        replacement=replacement,
    )


def _parse_wordlist_rule(node: object, index: int, base_dir: Path) -> Rule:
    if not isinstance(node, dict):
        raise ValueError(f"第 {index} 个词表必须是映射")
    category = node.get("category")
    if not isinstance(category, str) or not category.strip():
        raise ValueError(f"第 {index} 个词表缺少名称")
    case_sensitive = bool(node.get("case_sensitive", False))
    terms: list[str] = []
    source_path = None
    if node.get("terms") is not None:
        raw_terms = node.get("terms")
        if not isinstance(raw_terms, list):
            raise ValueError(f"词表 {category} 的 terms 必须是列表")
        terms = [str(item).strip() for item in raw_terms if str(item).strip()]
    elif isinstance(node.get("path"), str) and node.get("path").strip():
        source_path = node["path"].strip()
        list_path = Path(source_path)
        if not list_path.is_absolute():
            list_path = (base_dir / list_path).resolve()
        from doc_redactor.config import _read_terms

        terms = _read_terms(list_path)
    else:
        terms = []
    enabled = bool(node.get("enabled", True))
    replacement = node.get("replacement") if isinstance(node.get("replacement"), str) else ""
    return Rule(
        name=category.strip(),
        kind=KIND_WORDLIST,
        enabled=enabled,
        terms=terms,
        case_sensitive=case_sensitive,
        source_path=source_path,
        examples=[terms[0]] if terms else [],
        replacement=replacement,
    )


# silence unused import if compile_pattern is useful to callers
__all__ = [
    "KIND_PATTERN",
    "KIND_WORDLIST",
    "UNDO_TTL_SECONDS",
    "DUP_SKIP",
    "DUP_RENAME",
    "DUP_OVERWRITE",
    "MSG_IMPORT_FAIL",
    "Rule",
    "RuleSet",
    "LibraryItem",
    "ImportPreview",
    "ImportItem",
    "common_library",
    "default_rules",
    "default_library_checked",
    "library_item_to_rule",
    "library_main_items",
    "apply_library_toggle",
    "find_word_hits",
    "add_words",
    "word_dup_message",
    "load_ruleset",
    "compile_pattern",
    "PROTOCOL_LIST_NAME",
    "LETTER_DIGIT_NAME",
    "LETTER_DIGIT_INNER",
    "MSG_PLAN_EMPTY",
    "HINT_LETTER_DIGIT",
]
