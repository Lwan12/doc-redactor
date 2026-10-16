"""Load rules.yaml: regex rules and one-term-per-line wordlists."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import regex as re
import yaml

from doc_redactor.engine import RedactionEngine, RegexRule, WordlistRule, build_engine

_FLAG_NAMES = {
    "I": "IGNORECASE",
    "IGNORECASE": "IGNORECASE",
    "M": "MULTILINE",
    "MULTILINE": "MULTILINE",
    "S": "DOTALL",
    "DOTALL": "DOTALL",
    "X": "VERBOSE",
    "VERBOSE": "VERBOSE",
    "A": "ASCII",
    "ASCII": "ASCII",
}


def load_rules(path: str | Path) -> RedactionEngine:
    rules_path = Path(path)
    if not rules_path.is_file():
        raise FileNotFoundError(f"规则文件不存在: {rules_path}")
    raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("规则文件的顶层必须是映射")

    regex_nodes = raw.get("regex", raw.get("regex_rules", []))
    wordlist_nodes = raw.get("wordlists", [])
    if regex_nodes is None:
        regex_nodes = []
    if wordlist_nodes is None:
        wordlist_nodes = []
    if not isinstance(regex_nodes, list) or not isinstance(wordlist_nodes, list):
        raise ValueError("regex 与 wordlists 必须是列表")

    regex_rules = [_parse_regex(node, index) for index, node in enumerate(regex_nodes, start=1)]
    wordlists = [
        _parse_wordlist(node, index, rules_path.parent)
        for index, node in enumerate(wordlist_nodes, start=1)
    ]
    return build_engine(regex_rules, wordlists)


def parse_flags(value: object) -> int:
    if value is None or value == "" or value == []:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("|", ",").split(",") if part.strip()]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        raise ValueError(f"无法识别的 flags: {value!r}")
    flags = 0
    for part in parts:
        key = part.upper().removeprefix("RE.")
        canonical = _FLAG_NAMES.get(key)
        if canonical is None:
            raise ValueError(f"未知的匹配写法标记: {part}")
        flags |= getattr(re, canonical)
    return flags


def _parse_regex(node: object, index: int) -> RegexRule:
    if not isinstance(node, dict):
        raise ValueError(f"第 {index} 条匹配写法必须是映射")
    name = node.get("name")
    pattern = node.get("pattern")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"第 {index} 条匹配写法缺少名称")
    if not isinstance(pattern, str) or pattern == "":
        raise ValueError(f"匹配写法 {name} 缺少内容")
    if "flags" not in node:
        flags = 0
    else:
        flags = parse_flags(node.get("flags"))
    return RegexRule(name=name.strip(), pattern=pattern, flags=flags)


def _parse_wordlist(node: object, index: int, base_dir: Path) -> WordlistRule:
    if not isinstance(node, dict):
        raise ValueError(f"第 {index} 个词表必须是映射")
    raw_path = node.get("path")
    category = node.get("category")
    raw_terms = node.get("terms")
    if not isinstance(category, str) or not category.strip():
        label = raw_path if isinstance(raw_path, str) else str(index)
        raise ValueError(f"词表 {label} 缺少 category")
    case_sensitive = bool(node.get("case_sensitive", False))
    if raw_terms is not None:
        if not isinstance(raw_terms, list):
            raise ValueError(f"词表 {category} 的 terms 必须是列表")
        terms = [str(item).strip() for item in raw_terms if str(item).strip()]
        return WordlistRule(category=category.strip(), terms=terms, case_sensitive=case_sensitive)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"第 {index} 个词表缺少 path")
    list_path = Path(raw_path)
    if not list_path.is_absolute():
        list_path = (base_dir / list_path).resolve()
    if not list_path.is_file():
        raise FileNotFoundError(f"词表不存在: {list_path}")
    terms = _read_terms(list_path)
    return WordlistRule(category=category.strip(), terms=terms, case_sensitive=case_sensitive)


def _read_terms(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    terms: list[str] = []
    if path.suffix.lower() == ".csv":
        for row in csv.reader(io.StringIO(text)):
            if not row:
                continue
            term = row[0].strip()
            if term:
                terms.append(term)
        return terms
    for line in text.splitlines():
        term = line.strip()
        if term:
            terms.append(term)
    return terms
