"""Guess a match from examples / placeholders / templates. No tkinter."""

from __future__ import annotations

from dataclasses import dataclass, field

import regex as re

TEST_HIT_WARN = 20

MSG_EMPTY = "请粘贴 1～3 个例子，比如「AAA-0000-000」，或照样写 AAA-####-###。"
MSG_GUESSING = "正在根据例子找规律…"
MSG_GUESSED = "找到了：{desc}。请在右边试一段文字。"
MSG_GUESSED_HITS = "找到了：{desc}。命中 {n} 处。"
MSG_UNSURE = "例子之间差别太大，没找到统一的写法。请多贴几个同类例子，或改用模板。"
MSG_NO_HIT = "这段文字里没有命中。请检查例子是否一致，或换一段文字再试。"
MSG_TOO_MANY = (
    "命中了 {n} 处，比平时多很多，可能把不该遮的也遮了。请看高亮；不影响保存，可直接点「确定」。"
)
MSG_INVALID = "这条规则暂时用不了：{reason}。请改例子，或改用模板。"
MSG_DUP = "已经有一条叫「{name}」的规则了。请换个名字，或选择「覆盖旧的」。"

TEMPLATE_LETTER_DIGIT = "字母+数字编号"
TEMPLATE_DIGITS = "纯数字编号"
TEMPLATE_DATE = "日期"
TEMPLATES = (TEMPLATE_LETTER_DIGIT, TEMPLATE_DIGITS, TEMPLATE_DATE)

_SEP_NAME = {"-": "横线", "/": "斜杠", "_": "下划线", ".": "点", " ": "空格"}
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TOKEN = re.compile(r"[A-Za-z]+|\d+|.")
_PLACEHOLDER = re.compile(r"#+|@+|[A-Za-z0-9]+|.")


@dataclass(frozen=True)
class ShapeToken:
    kind: str  # L letters, D digits, T literal, S symbol
    n: int
    text: str = ""


@dataclass
class GuessResult:
    ok: bool = False
    empty: bool = False
    unsure: bool = False
    description: str = ""
    inner_pattern: str = ""
    tokens: tuple[ShapeToken, ...] = ()
    from_placeholder: bool = False


@dataclass
class TestHit:
    start: int
    end: int
    text: str


@dataclass
class FormEval:
    status: str
    message: str
    icon: str
    tone: str  # n, ok, warn, err
    guess: GuessResult
    hits: list[TestHit] = field(default_factory=list)
    can_save: bool = False
    duplicate: bool = False
    invalid: bool = False
    pattern: str = ""


def too_many_test_hits(count: int) -> bool:
    return count > TEST_HIT_WARN


def examples_from_text(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def guess_from_examples(examples: list[str]) -> GuessResult:
    cleaned = [item.strip() for item in examples if item and item.strip()]
    if not cleaned:
        return GuessResult(empty=True)
    if any(_CJK.search(item) for item in cleaned):
        return GuessResult(unsure=True)
    shapes = [_shape(item) for item in cleaned]
    key0 = _shape_key(shapes[0])
    if any(_shape_key(item) != key0 for item in shapes[1:]):
        return GuessResult(unsure=True)
    tokens = tuple(shapes[0])
    inner = tokens_to_pattern(tokens)
    return GuessResult(
        ok=True,
        description=describe_tokens(tokens),
        inner_pattern=inner,
        tokens=tokens,
    )


def guess_from_placeholder(placeholder: str) -> GuessResult:
    text = placeholder.strip()
    if not text:
        return GuessResult(empty=True)
    tokens = tuple(_placeholder_tokens(text))
    inner = tokens_to_pattern(tokens)
    return GuessResult(
        ok=True,
        description=describe_tokens(tokens),
        inner_pattern=inner,
        tokens=tokens,
        from_placeholder=True,
    )


def guess_from_template(
    kind: str,
    *,
    letters: int = 3,
    sep: str = "-",
    digits: int = 4,
    digits2: int = 3,
) -> GuessResult:
    tokens: list[ShapeToken]
    if kind == TEMPLATE_DIGITS:
        n = max(1, int(digits or 4))
        tokens = [ShapeToken("D", n)]
    elif kind == TEMPLATE_DATE:
        mark = sep if sep else "-"
        tokens = [
            ShapeToken("D", 4),
            ShapeToken("S", 1, mark),
            ShapeToken("D", 2),
            ShapeToken("S", 1, mark),
            ShapeToken("D", 2),
        ]
    else:
        n_letters = max(1, int(letters or 3))
        n1 = max(1, int(digits or 4))
        tokens = [ShapeToken("L", n_letters)]
        if sep:
            tokens.append(ShapeToken("S", 1, sep))
        tokens.append(ShapeToken("D", n1))
        n2 = int(digits2 or 0)
        if n2:
            if sep:
                tokens.append(ShapeToken("S", 1, sep))
            tokens.append(ShapeToken("D", n2))
    frozen = tuple(tokens)
    return GuessResult(
        ok=True,
        description=describe_tokens(frozen),
        inner_pattern=tokens_to_pattern(frozen),
        tokens=frozen,
    )


def describe_tokens(tokens: tuple[ShapeToken, ...] | list[ShapeToken]) -> str:
    parts: list[str] = []
    for token in tokens:
        if token.kind == "T":
            parts.append(f"固定文字「{token.text}」")
        elif token.kind == "L":
            parts.append(f"字母 {token.n} 位")
        elif token.kind == "D":
            parts.append(f"{token.n} 位数字")
        else:
            parts.append(_SEP_NAME.get(token.text, f"符号「{token.text}」"))
    return " + ".join(parts)


def tokens_to_pattern(tokens: tuple[ShapeToken, ...] | list[ShapeToken]) -> str:
    parts: list[str] = []
    for token in tokens:
        if token.kind == "T":
            parts.append(re.escape(token.text))
        elif token.kind == "L":
            parts.append(rf"[A-Za-z]{{{token.n}}}")
        elif token.kind == "D":
            parts.append(rf"\d{{{token.n}}}")
        else:
            parts.append(re.escape(token.text))
    return "".join(parts)


def wrap_pattern(inner: str) -> str:
    return rf"(?<![A-Za-z0-9])(?:{inner})(?![A-Za-z0-9])"


def compile_pattern(inner: str, *, case_sensitive: bool) -> re.Pattern:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(wrap_pattern(inner), flags)


def find_test_hits(inner: str, text: str, *, case_sensitive: bool) -> list[TestHit]:
    if not text:
        return []
    compiled = compile_pattern(inner, case_sensitive=case_sensitive)
    hits: list[TestHit] = []
    for match in compiled.finditer(text):
        if match.start() == match.end():
            continue
        hits.append(TestHit(start=match.start(), end=match.end(), text=match.group(0)))
    return hits


def invalid_reason(exc: BaseException) -> str:
    message = str(exc).lower()
    if any(word in message for word in ("missing )", "unbalanced", "parenthesis", "group")):
        return "括号没有配对"
    if any(word in message for word in ("unterminated", "bracket", "character class")):
        return "方括号没有闭合"
    if any(word in message for word in ("nothing to repeat", "multiple repeat", "quantif", "repeat")):
        return "重复次数的写法不完整"
    return "写法不完整"


def evaluate_rule_form(
    *,
    name: str,
    examples_text: str,
    placeholder: str,
    test_text: str,
    existing_names: list[str],
    overwrite: bool = False,
    case_sensitive: bool = False,
    template: str | None = None,
    template_letters: int = 3,
    template_sep: str = "-",
    template_digits: int = 4,
    template_digits2: int = 3,
    advanced: str | None = None,
    guessing: bool = False,
    editing_name: str | None = None,
) -> FormEval:
    guess = GuessResult(empty=True)
    if guessing:
        return FormEval(
            status="guessing",
            message=MSG_GUESSING,
            icon="◔",
            tone="n",
            guess=guess,
        )

    if template:
        guess = guess_from_template(
            template,
            letters=template_letters,
            sep=template_sep,
            digits=template_digits,
            digits2=template_digits2,
        )
    elif placeholder.strip():
        guess = guess_from_placeholder(placeholder)
    else:
        guess = guess_from_examples(examples_from_text(examples_text))

    inner = advanced if advanced is not None else (guess.inner_pattern if guess.ok else "")
    pattern = wrap_pattern(inner) if inner else ""

    if inner:
        try:
            compile_pattern(inner, case_sensitive=case_sensitive)
        except re.error as exc:
            reason = invalid_reason(exc)
            return FormEval(
                status="invalid",
                message=MSG_INVALID.format(reason=reason),
                icon="✕",
                tone="err",
                guess=guess,
                invalid=True,
                pattern=pattern,
            )

    if guess.empty and not inner:
        return FormEval(
            status="empty",
            message=MSG_EMPTY,
            icon="ⓘ",
            tone="n",
            guess=guess,
        )
    if guess.unsure and advanced is None:
        return FormEval(
            status="unsure",
            message=MSG_UNSURE,
            icon="⚠",
            tone="warn",
            guess=guess,
        )

    trimmed = name.strip()
    duplicate = bool(trimmed) and _is_duplicate(trimmed, existing_names, editing_name)
    if duplicate and not overwrite:
        return FormEval(
            status="dup",
            message=MSG_DUP.format(name=trimmed),
            icon="✕",
            tone="err",
            guess=guess,
            duplicate=True,
            pattern=pattern,
        )

    hits: list[TestHit] = []
    if inner and test_text:
        try:
            hits = find_test_hits(inner, test_text, case_sensitive=case_sensitive)
        except re.error as exc:
            reason = invalid_reason(exc)
            return FormEval(
                status="invalid",
                message=MSG_INVALID.format(reason=reason),
                icon="✕",
                tone="err",
                guess=guess,
                invalid=True,
                pattern=pattern,
            )

    can_save = bool(trimmed) and bool(inner) and guess.ok or (bool(trimmed) and bool(inner) and advanced is not None)
    if not guess.ok and advanced is None:
        can_save = False
    if duplicate and not overwrite:
        can_save = False
    if trimmed and inner and (guess.ok or advanced is not None):
        can_save = True
        if duplicate and not overwrite:
            can_save = False

    if not test_text:
        desc = guess.description or "—"
        return FormEval(
            status="guessed",
            message=MSG_GUESSED.format(desc=desc),
            icon="✓",
            tone="ok",
            guess=guess,
            can_save=can_save,
            duplicate=duplicate,
            pattern=pattern,
        )
    if not hits:
        return FormEval(
            status="nohit",
            message=MSG_NO_HIT,
            icon="⚠",
            tone="warn",
            guess=guess,
            can_save=can_save,
            duplicate=duplicate,
            pattern=pattern,
        )
    if too_many_test_hits(len(hits)):
        return FormEval(
            status="toomany",
            message=MSG_TOO_MANY.format(n=len(hits)),
            icon="⚠",
            tone="warn",
            guess=guess,
            hits=hits,
            can_save=can_save,
            duplicate=duplicate,
            pattern=pattern,
        )
    return FormEval(
        status="guessed",
        message=MSG_GUESSED_HITS.format(desc=guess.description, n=len(hits)),
        icon="✓",
        tone="ok",
        guess=guess,
        hits=hits,
        can_save=can_save,
        duplicate=duplicate,
        pattern=pattern,
    )


def _is_duplicate(name: str, existing: list[str], editing_name: str | None) -> bool:
    for item in existing:
        if item == name and item != editing_name:
            return True
    return False


def _shape(text: str) -> list[ShapeToken]:
    tokens: list[ShapeToken] = []
    for match in _TOKEN.finditer(text):
        piece = match.group(0)
        if piece.isalpha():
            tokens.append(ShapeToken("L", len(piece), piece))
        elif piece.isdigit():
            tokens.append(ShapeToken("D", len(piece), piece))
        else:
            tokens.append(ShapeToken("S", 1, piece))
    return tokens


def _shape_key(tokens: list[ShapeToken]) -> tuple:
    return tuple((token.kind, token.n if token.kind != "S" else token.text) for token in tokens)


def _placeholder_tokens(text: str) -> list[ShapeToken]:
    tokens: list[ShapeToken] = []
    for match in _PLACEHOLDER.finditer(text):
        run = match.group(0)
        if run[0] == "#":
            tokens.append(ShapeToken("D", len(run), run))
        elif run[0] == "@":
            tokens.append(ShapeToken("L", len(run), run))
        elif run[0].isalnum():
            tokens.append(ShapeToken("T", len(run), run))
        else:
            tokens.append(ShapeToken("S", 1, run))
    return tokens
