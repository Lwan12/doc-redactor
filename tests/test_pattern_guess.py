from doc_redactor.pattern_guess import (
    MSG_EMPTY,
    MSG_GUESSED,
    MSG_NO_HIT,
    MSG_TOO_MANY,
    MSG_UNSURE,
    TEST_HIT_WARN,
    evaluate_rule_form,
    find_test_hits,
    guess_from_examples,
    guess_from_placeholder,
    too_many_test_hits,
    wrap_pattern,
)


def test_guess_project_id_shape():
    guessed = guess_from_examples(["ABC-2026-001", "PRJ-2026-017"])
    assert guessed.ok
    assert guessed.description == "字母 3 位 + 横线 + 4 位数字 + 横线 + 3 位数字"


def test_guess_unsure_when_examples_differ():
    guessed = guess_from_examples(["ABC-2026-001", "华曜医药"])
    assert guessed.unsure


def test_placeholder_proj_hashes():
    guessed = guess_from_placeholder("PROJ-####")
    assert guessed.ok
    assert guessed.description == "固定文字「PROJ」 + 横线 + 4 位数字"
    text = "PROJ-2026 与 PROJ-0042 已归档，PRJ-2026-017 不算。"
    hits = find_test_hits(guessed.inner_pattern, text, case_sensitive=False)
    assert [hit.text for hit in hits] == ["PROJ-2026", "PROJ-0042"]


def test_too_many_threshold_20_and_21():
    assert too_many_test_hits(20) is False
    assert too_many_test_hits(21) is True
    assert TEST_HIT_WARN == 20
    numbers = " ".join(f"N{i:03d}" for i in range(21))
    eval20 = evaluate_rule_form(
        name="编号",
        examples_text="N000",
        placeholder="N###",
        test_text=" ".join(f"N{i:03d}" for i in range(20)),
        existing_names=[],
    )
    assert eval20.status != "toomany"
    assert len(eval20.hits) == 20
    eval21 = evaluate_rule_form(
        name="编号",
        examples_text="N000",
        placeholder="N###",
        test_text=numbers,
        existing_names=[],
    )
    assert eval21.status == "toomany"
    assert len(eval21.hits) == 21
    assert eval21.can_save
    assert str(TEST_HIT_WARN) not in eval21.message
    assert eval21.message == MSG_TOO_MANY.format(n=21)


def test_prompt_strings_match_spec():
    empty = evaluate_rule_form(
        name="",
        examples_text="",
        placeholder="",
        test_text="",
        existing_names=[],
    )
    assert empty.message == MSG_EMPTY
    guessing = evaluate_rule_form(
        name="",
        examples_text="ABC-2026-001",
        placeholder="",
        test_text="",
        existing_names=[],
        guessing=True,
    )
    assert guessing.message == "正在根据例子找规律…"
    guessed = evaluate_rule_form(
        name="访视项目号",
        examples_text="ABC-2026-001",
        placeholder="",
        test_text="",
        existing_names=[],
    )
    assert guessed.message == MSG_GUESSED.format(desc="字母 3 位 + 横线 + 4 位数字 + 横线 + 3 位数字")
    guessed_hits = evaluate_rule_form(
        name="访视项目号",
        examples_text="AAA-0000-000",
        placeholder="",
        test_text="本次访视按 AAA-0000-000 方案执行。",
        existing_names=[],
    )
    assert guessed_hits.status == "guessed"
    assert guessed_hits.message.endswith("命中 1 处。")
    assert "找到了：" in guessed_hits.message
    unsure = evaluate_rule_form(
        name="x",
        examples_text="ABC-2026-001\n华曜医药",
        placeholder="",
        test_text="hello",
        existing_names=[],
    )
    assert unsure.message == MSG_UNSURE
    nohit = evaluate_rule_form(
        name="访视项目号",
        examples_text="ABC-2026-001",
        placeholder="",
        test_text="今天的访视按计划进行，没有新的安全性事件。",
        existing_names=[],
    )
    assert nohit.message == MSG_NO_HIT
    assert nohit.can_save
    invalid = evaluate_rule_form(
        name="访视项目号",
        examples_text="ABC-2026-001",
        placeholder="",
        test_text="ABC-2026-001",
        existing_names=[],
        advanced="PRJ-(\\d{4}",
    )
    assert invalid.status == "invalid"
    assert "括号没有配对" in invalid.message
    dup = evaluate_rule_form(
        name="项目号",
        examples_text="ABC-2026-001",
        placeholder="",
        test_text="ABC-2026-001",
        existing_names=["项目号"],
    )
    assert dup.status == "dup"
    assert dup.message == "已经有一条叫「项目号」的规则了。请换个名字，或选择「覆盖旧的」。"
    assert not dup.can_save


def test_wrap_adds_boundaries():
    assert wrap_pattern(r"ABC") == r"(?<![A-Za-z0-9])(?:ABC)(?![A-Za-z0-9])"
