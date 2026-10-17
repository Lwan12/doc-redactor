from pathlib import Path

from doc_redactor.pattern_guess import TEST_HIT_WARN
from doc_redactor.pattern_guess import wrap_pattern
from doc_redactor.ruleset import (
    DUP_SKIP,
    HINT_LETTER_DIGIT,
    KIND_PATTERN,
    KIND_WORDLIST,
    LETTER_DIGIT_INNER,
    LETTER_DIGIT_NAME,
    MSG_IMPORT_FAIL,
    MSG_PLAN_EMPTY,
    PROTOCOL_LIST_NAME,
    UNDO_TTL_SECONDS,
    Rule,
    RuleSet,
    add_words,
    apply_library_toggle,
    common_library,
    find_word_hits,
    library_main_items,
    load_rules_file,
    load_ruleset,
    word_dup_message,
)
from helpers import RULES


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_add_rule_and_engine_hits():
    rules = RuleSet()
    rules.add(
        Rule(
            name="项目号",
            kind=KIND_PATTERN,
            examples=["ABC-2026-001"],
            pattern=r"(?<![A-Za-z0-9])(?:[A-Za-z]{3}-\d{4}-\d{3})(?![A-Za-z0-9])",
            case_sensitive=False,
        )
    )
    hits = rules.to_engine().find_hits("见 ABC-2026-001 与 abc-2026-001")
    assert len(hits) == 2
    assert {hit.category for hit in hits} == {"项目号"}


def test_import_skips_duplicate_names_by_default(tmp_path: Path):
    rules = RuleSet(
        [
            Rule(name="项目号", kind=KIND_PATTERN, pattern=r"ABC-\d+", examples=["ABC-1"]),
        ]
    )
    incoming = tmp_path / "in.yaml"
    incoming.write_text(
        "regex:\n"
        "  - name: 项目号\n"
        "    pattern: 'XYZ-\\d+'\n"
        "  - name: 邮箱\n"
        "    pattern: 'a@b.c'\n"
        "wordlists: []\n",
        encoding="utf-8",
    )
    preview = rules.preview_import(incoming)
    assert preview.new_count == 1
    assert preview.dup_count == 1
    assert preview.bad_count == 0
    dup = next(item for item in preview.items if item.status == "dup")
    assert dup.action == DUP_SKIP
    added = rules.apply_import(preview)
    assert added == 1
    assert [rule.name for rule in rules.rules] == ["项目号", "邮箱"]
    assert rules.rules[0].pattern == r"ABC-\d+"


def test_import_old_yaml_keeps_wordlist_case(tmp_path: Path):
    loaded = load_ruleset(RULES)
    names = {rule.name: rule for rule in loaded.rules}
    assert names["SPONSOR"].case_sensitive is False
    assert names["DRUG"].case_sensitive is True
    engine = loaded.to_engine()
    text = "Seen Acme Pharma and acme pharma and Fictizumab and fictizumab and ABC-2026-001."
    hits = engine.find_hits(text)
    categories = [hit.category for hit in hits]
    assert categories.count("PROJECT_ID") == 1
    assert categories.count("SPONSOR") == 2
    assert categories.count("DRUG") == 1
    assert "fictizumab" not in {hit.text for hit in hits}


def test_undo_expires_after_ten_minutes():
    clock = FakeClock()
    rules = RuleSet(
        [Rule(name="NCT号", kind=KIND_PATTERN, pattern=r"NCT\d{8}")],
        clock=clock,
    )
    rules.delete(0)
    assert rules.undo_label() == "已删除「NCT号」。10 分钟内可撤销。"
    assert not rules.rules
    assert rules.undo()
    assert rules.rules[0].name == "NCT号"

    rules.delete(0)
    clock.value = UNDO_TTL_SECONDS + 1
    assert rules.undo_label() is None
    assert not rules.undo()
    assert not rules.rules


def test_common_library_seven_items_five_checked():
    items = common_library()
    mains = library_main_items(items)
    assert len(mains) == 7
    assert [item.name for item in mains] == [
        "项目号",
        PROTOCOL_LIST_NAME,
        "申办方",
        "药名",
        "NCT 试验登记号",
        "邮箱",
        "手机号（中国大陆）",
    ]
    checked = [item.name for item in mains if item.default_checked]
    assert checked == ["项目号", PROTOCOL_LIST_NAME, "申办方", "药名", "NCT 试验登记号"]
    sub = [item for item in items if item.is_subitem]
    assert len(sub) == 1
    assert sub[0].name == LETTER_DIGIT_NAME
    assert sub[0].default_checked is False
    assert sub[0].parent_name == PROTOCOL_LIST_NAME
    assert sub[0].hint == HINT_LETTER_DIGIT
    kinds = {item.name: item.kind for item in items}
    assert kinds[PROTOCOL_LIST_NAME] == KIND_WORDLIST
    assert kinds["申办方"] == KIND_WORDLIST
    assert kinds["药名"] == KIND_WORDLIST
    rules = RuleSet()
    flags = [item.default_checked for item in items]
    added = rules.add_from_library(items, flags)
    assert added == 5
    names = [rule.name for rule in rules.rules]
    assert PROTOCOL_LIST_NAME in names
    assert LETTER_DIGIT_NAME not in names
    plan = next(rule for rule in rules.rules if rule.name == PROTOCOL_LIST_NAME)
    assert plan.terms == []
    assert plan.enabled is True
    assert plan.display_example() == MSG_PLAN_EMPTY
    labels = {rule.name: rule.replacement_text() for rule in rules.rules}
    assert labels["项目号"] == "[PROTOCOL]"
    assert labels[PROTOCOL_LIST_NAME] == "[PROTOCOL NO]"
    assert labels["申办方"] == "[SPONSOR]"
    assert labels["药名"] == "[STUDY DRUG]"
    assert labels["NCT 试验登记号"] == "[NCT ID]"
    sub_item = next(item for item in items if item.is_subitem)
    assert sub_item.replacement == "[PROTOCOL NO]"
    added_again = rules.add_from_library(items, flags)
    assert added_again == 0


def test_letter_digit_gated_by_protocol_list():
    items = common_library()
    checked = [item.default_checked for item in items]
    sub_i = next(i for i, item in enumerate(items) if item.is_subitem)
    parent_i = next(i for i, item in enumerate(items) if item.name == PROTOCOL_LIST_NAME)
    checked[parent_i] = False
    blocked = apply_library_toggle(items, checked, sub_i)
    assert blocked[sub_i] is False
    checked[parent_i] = True
    on = apply_library_toggle(items, checked, sub_i)
    assert on[sub_i] is True
    off_parent = apply_library_toggle(items, on, parent_i)
    assert off_parent[parent_i] is False
    assert off_parent[sub_i] is False
    rules = RuleSet()
    flags = [False] * len(items)
    flags[sub_i] = True
    assert rules.add_from_library(items, flags) == 0


def test_letter_digit_matches_fictional_protocol_ids():
    pattern = wrap_pattern(LETTER_DIGIT_INNER)
    rules = RuleSet(
        [
            Rule(
                name=LETTER_DIGIT_NAME,
                kind=KIND_PATTERN,
                pattern=pattern,
                case_sensitive=False,
            )
        ]
    )
    engine = rules.to_engine()
    text = "方案 AAA0000 与 aa-k0000 已归档，ABC-1 太短不算，BBB1111 在列。"
    hits = engine.find_hits(text)
    found = {hit.text.lower() for hit in hits}
    assert "aaa0000" in found
    assert "aa-k0000" in found
    assert "bbb1111" in found
    assert not any(hit.text.upper().startswith("ABC-1") for hit in hits)


def test_protocol_list_import_enables_and_is_case_insensitive():
    rules = RuleSet(
        [
            Rule(
                name=PROTOCOL_LIST_NAME,
                kind=KIND_WORDLIST,
                enabled=False,
                terms=[],
                case_sensitive=False,
            )
        ]
    )
    merged, added, skipped = rules.import_terms(0, ["AAA0000", "AA-K0000", "aaa0000"])
    assert rules.rules[0].enabled is True
    assert merged == ["AAA0000", "AA-K0000"]
    assert added == ["AAA0000", "AA-K0000"]
    assert skipped == ["aaa0000"]
    engine = rules.to_engine()
    hits = engine.find_hits("见 aaa0000 与 AA-K0000")
    assert len(hits) == 2


def test_wordlist_hits_case_insensitive():
    hits = find_word_hits(["AAA0000", "AA-K0000"], "编号 aaa0000、AA-K0000。", case_sensitive=False)
    assert [item[2].lower() for item in hits] == ["aaa0000", "aa-k0000"]


def test_missing_case_sensitive_defaults_insensitive(tmp_path: Path):
    path = tmp_path / "old.yaml"
    path.write_text(
        "regex: []\n"
        "wordlists:\n"
        "  - category: 药名\n"
        "    terms: [Fictizumab]\n",
        encoding="utf-8",
    )
    loaded = load_ruleset(path)
    assert loaded.rules[0].case_sensitive is False
    hits = loaded.to_engine().find_hits("fictizumab and Fictizumab")
    assert len(hits) == 2


def test_wordlist_duplicate_words_not_added():
    existing = ["华曜医药", "北辰制药"]
    merged, added, skipped = add_words(existing, ["华曜医药", "北辰制药", "新词药业"], case_sensitive=False)
    assert added == ["新词药业"]
    assert skipped == ["华曜医药", "北辰制药"]
    assert merged == ["华曜医药", "北辰制药", "新词药业"]
    message = word_dup_message(skipped, added)
    assert "「华曜医药」「北辰制药」已经在词表里了，没有重复添加。" in message
    assert "另外添加了 1 个新词。" in message
    _, _, skipped_case = add_words(["Acme"], ["acme"], case_sensitive=False)
    assert skipped_case == ["acme"]
    _, added_cs, skipped_cs = add_words(["Acme"], ["acme"], case_sensitive=True)
    assert added_cs == ["acme"]
    assert skipped_cs == []


def test_import_failure_message(tmp_path: Path):
    bad = tmp_path / "not-rules.txt"
    bad.write_text("this is not yaml mapping: [", encoding="utf-8")
    preview = RuleSet().preview_import(bad)
    assert preview.failed
    assert preview.message == MSG_IMPORT_FAIL


def test_import_bad_flag_uses_match_wording(tmp_path: Path):
    rules = tmp_path / "flags.yaml"
    rules.write_text(
        "regex:\n  - name: BAD\n    pattern: 'ABC'\n    flags: NOT_A_FLAG\nwordlists: []\n",
        encoding="utf-8",
    )
    loaded = load_rules_file(rules)
    message = loaded[0][1]
    assert message
    assert "正则" not in message
    assert "匹配写法" in message


def test_threshold_constant_not_in_library_copy():
    assert TEST_HIT_WARN == 20
