"""Presidio analyzer wired only to regex recognizers.

Wordlists are compiled here. Presidio's deny_list wraps terms in ``\\W``
boundaries, and Python treats CJK characters as word characters, so a Chinese
term stuck to other Chinese never matched.

The NLP engine is Presidio's NoOpNlpEngine. It does not load spaCy language
models and does not download en_core_web_lg or any other model.
"""

from __future__ import annotations

from dataclasses import dataclass

import regex as re
from presidio_analyzer import (
    AnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
)
from presidio_analyzer.context_aware_enhancers import ContextAwareEnhancer
from presidio_analyzer.nlp_engine import NoOpNlpEngine


class _PassthroughEnhancer(ContextAwareEnhancer):
    """Skip lemma context boosting.

    NoOpNlpEngine has no tokens. The default lemma enhancer would only warn.
    """

    def __init__(self) -> None:
        super().__init__(
            context_similarity_factor=0.35,
            min_score_with_context_similarity=0.4,
            context_prefix_count=5,
            context_suffix_count=0,
        )

    def enhance_using_context(
        self,
        text,
        raw_results,
        nlp_artifacts,
        recognizers,
        context=None,
    ):
        return raw_results


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    category: str
    text: str


@dataclass(frozen=True)
class RegexRule:
    name: str
    pattern: str
    flags: int
    replacement: str = ""


@dataclass(frozen=True)
class WordlistRule:
    category: str
    terms: list[str]
    case_sensitive: bool
    replacement: str = ""


# Category names used by the common library and by example/old yaml files.
DEFAULT_REPLACEMENTS = {
    "DRUG": "[STUDY DRUG]",
    "药名": "[STUDY DRUG]",
    "SPONSOR": "[SPONSOR]",
    "申办方": "[SPONSOR]",
    "PROJECT_ID": "[PROTOCOL]",
    "项目号": "[PROTOCOL]",
    "NCT": "[NCT ID]",
    "NCT 试验登记号": "[NCT ID]",
    "方案号清单": "[PROTOCOL NO]",
    "字母加数字": "[PROTOCOL NO]",
    "EMAIL": "[EMAIL]",
    "邮箱": "[EMAIL]",
    "PHONE": "[PHONE]",
    "手机号": "[PHONE]",
    "手机号（中国大陆）": "[PHONE]",
}


def redaction_label(category: str, mapping: dict[str, str] | None = None) -> str:
    if mapping:
        custom = mapping.get(category)
        if isinstance(custom, str) and custom.strip():
            return custom
    default = DEFAULT_REPLACEMENTS.get(category)
    if default:
        return default
    return f"[REDACTED:{category}]"


def _replacements_from_rules(regex_rules: list[RegexRule], wordlists: list[WordlistRule]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for rule in regex_rules:
        if rule.replacement.strip():
            mapping[rule.name] = rule.replacement
    for rule in wordlists:
        if rule.replacement.strip():
            mapping[rule.category] = rule.replacement
    return mapping


def _is_ascii_alnum(ch: str) -> bool:
    return len(ch) == 1 and ch.isascii() and ch.isalnum()


def _is_cjk(ch: str) -> bool:
    """Han, Hiragana, Katakana, Hangul, and nearby CJK blocks."""
    if len(ch) != 1:
        return False
    code = ord(ch)
    return (
        0x1100 <= code <= 0x11FF
        or 0x2E80 <= code <= 0x2FDF
        or 0x3000 <= code <= 0x303F
        or 0x3040 <= code <= 0x30FF
        or 0x3130 <= code <= 0x318F
        or 0x31F0 <= code <= 0x31FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF66 <= code <= 0xFF9D
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0x2CEB0 <= code <= 0x2EBEF
        or 0x30000 <= code <= 0x3134F
    )


# ASCII word edge. Underscore counts; a hyphen does not. Do not use ``\w``:
# Unicode ``\w`` includes Han, so ``ABC`` would stop matching ``ABC星``.
_ASCII_EDGE = "0-9A-Za-z_"
# At most a few spaces and one line break. A blank line (two breaks) does not match.
_CJK_GAP = r"[ \t\u3000]{0,3}(?:\r?\n[ \t\u3000]{0,3})?"


def _term_regex(term: str) -> str:
    """Match ``term`` with ASCII whole-word edges and optional breaks inside CJK.

    A side whose first or last character is an ASCII letter or digit must not
    sit against another ASCII letter, digit, or underscore (``Acme`` does not
    match ``Acmes`` or ``ABC_2026``). A hyphen is not a word character.
    A CJK or other non-ASCII edge has no boundary, so ``星辰海`` matches
    inside ``星辰海医药有限公司``.

    Between adjacent characters that include CJK, at most three spaces and one
    newline are allowed, so ``星\\n辰海`` matches and ``星\\n\\n辰海`` does not.
    The hit text keeps the original break. A space that is part of the term
    (an English phrase) matches any whitespace, including a newline.
    """
    tokens: list[tuple[str, str | None]] = []
    index = 0
    while index < len(term):
        if term[index].isspace():
            while index < len(term) and term[index].isspace():
                index += 1
            tokens.append(("ws", None))
            continue
        tokens.append(("ch", term[index]))
        index += 1
    parts: list[str] = []
    for position, (kind, ch) in enumerate(tokens):
        if kind == "ws":
            parts.append(r"\s+")
            continue
        if position > 0 and tokens[position - 1][0] == "ch":
            previous = tokens[position - 1][1] or ""
            if _is_cjk(previous) or _is_cjk(ch or ""):
                parts.append(_CJK_GAP)
        parts.append(re.escape(ch or ""))
    body = "".join(parts)
    prefix = rf"(?<![{_ASCII_EDGE}])" if term and _is_ascii_alnum(term[0]) else ""
    suffix = rf"(?![{_ASCII_EDGE}])" if term and _is_ascii_alnum(term[-1]) else ""
    return prefix + body + suffix


def _ocr_term_regex(term: str) -> str:
    """``_term_regex`` that also accepts one misread character, for OCR text only.

    Tesseract reads a glyph as a look-alike now and then (a fabricated drug name
    菲替珠单抗 comes back as 菲蔡珠单抗 in a plain 34 px scan). Upscaling,
    binarising, and chi_sim best do not fix it, and another OCR engine would
    still read the real name off the image, so the box must be painted anyway.
    One substitution is allowed when the term has at least four CJK characters
    or six other characters; shorter terms stay exact so common words survive.
    A term containing any digit is never loosened (M34-2 is not done).
    """
    exact = _term_regex(term)
    letters = [ch for ch in term if not ch.isspace()]
    cjk = sum(1 for ch in letters if _is_cjk(ch))
    # Terms with a digit (contract, protocol, subject numbers) stay exact:
    # one digit off is another number, and painting it is a wrong redaction.
    if any(ch.isdigit() for ch in letters):
        return exact
    if cjk >= 4 or (cjk == 0 and len(letters) >= 6):
        return f"(?:{exact}){{s<=1}}"
    return exact


def _wordlist_regex(terms: list[str], ocr: bool = False) -> str | None:
    usable = [term for term in terms if term and not term.isspace()]
    if not usable:
        return None
    # Longer terms first so a prefix alternative does not consume a longer term.
    ordered = sorted(usable, key=len, reverse=True)
    build = _ocr_term_regex if ocr else _term_regex
    return "|".join(f"(?:{build(term)})" for term in ordered)


# ``\b`` in a user pattern, ASCII edition: a boundary unless both sides are
# ASCII word characters. Unicode ``\b`` counts Han as a word character, so
# ``\bABC-2026-001`` never matched right after Chinese (``受试者ABC-2026-001``).
_ASCII_BOUNDARY = rf"(?:(?<![{_ASCII_EDGE}])|(?![{_ASCII_EDGE}]))"


def ascii_word_boundaries(pattern: str) -> str:
    """Replace each ``\b`` outside a character class with ``_ASCII_BOUNDARY``.

    ``\\b`` (a literal backslash and b) and ``[\b]`` (backspace) stay as they are.
    """
    out: list[str] = []
    in_class = False
    index = 0
    while index < len(pattern):
        ch = pattern[index]
        if ch == "\\" and index + 1 < len(pattern):
            pair = pattern[index : index + 2]
            out.append(_ASCII_BOUNDARY if pair == "\\b" and not in_class else pair)
            index += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
        elif ch == "[":
            in_class = True
            out.append(ch)
            index += 1
            # A ] right after [ or [^ is a literal, not the end of the class.
            if pattern[index : index + 1] == "^":
                out.append("^")
                index += 1
            if pattern[index : index + 1] == "]":
                out.append("]")
                index += 1
            continue
        out.append(ch)
        index += 1
    return "".join(out)


def build_engine(
    regex_rules: list[RegexRule], wordlists: list[WordlistRule], ocr: bool = False
) -> "RedactionEngine":
    """``ocr=True`` lets long wordlist terms match with one misread character."""
    recognizers: list[PatternRecognizer] = []
    for rule in regex_rules:
        pattern = ascii_word_boundaries(rule.pattern)
        try:
            re.compile(pattern, flags=rule.flags)
        except re.error as exc:
            raise ValueError(f"匹配写法 {rule.name} 无法编译: {exc}") from exc
        recognizers.append(
            PatternRecognizer(
                supported_entity=rule.name,
                name=rule.name,
                patterns=[Pattern(name=rule.name, regex=pattern, score=1.0)],
                global_regex_flags=rule.flags,
            )
        )
    for rule in wordlists:
        pattern = _wordlist_regex(rule.terms, ocr=ocr)
        if pattern is None:
            continue
        flags = re.DOTALL | re.MULTILINE
        if not rule.case_sensitive:
            flags |= re.IGNORECASE
        recognizers.append(
            PatternRecognizer(
                supported_entity=rule.category,
                name=rule.category,
                patterns=[Pattern(name=rule.category, regex=pattern, score=1.0)],
                global_regex_flags=flags,
            )
        )
    # An empty registry makes AnalyzerEngine load the predefined pack, which
    # pulls in NLP recognizers. A pattern that cannot match keeps the engine offline.
    if not recognizers:
        recognizers.append(
            PatternRecognizer(
                supported_entity="NONE",
                name="none",
                patterns=[Pattern(name="none", regex=r"a^", score=1.0)],
                global_regex_flags=0,
            )
        )

    nlp = NoOpNlpEngine(models=[{"lang_code": "en", "model_name": "no_op"}])
    registry = RecognizerRegistry(recognizers=recognizers, supported_languages=["en"])
    analyzer = AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp,
        supported_languages=["en"],
        context_aware_enhancer=_PassthroughEnhancer(),
    )
    order = [rule.name for rule in regex_rules] + [rule.category for rule in wordlists]
    engine = RedactionEngine(analyzer, _replacements_from_rules(regex_rules, wordlists), order)
    if ocr:
        engine._ocr = engine
    else:
        engine._ocr_rules = (list(regex_rules), list(wordlists))
    return engine


class RedactionEngine:
    def __init__(
        self,
        analyzer: AnalyzerEngine,
        replacements: dict[str, str] | None = None,
        category_order: list[str] | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.replacements = dict(replacements or {})
        self.category_order = list(category_order or [])
        self._ocr: RedactionEngine | None = None
        self._ocr_rules: tuple[list[RegexRule], list[WordlistRule]] | None = None

    def for_ocr(self) -> "RedactionEngine":
        """The engine used on OCR text: same rules, long terms forgive one misread character."""
        if self._ocr is None:
            if self._ocr_rules is None:
                return self
            self._ocr = build_engine(*self._ocr_rules, ocr=True)
        return self._ocr

    def is_approximate_ocr_match(self, text: str) -> bool:
        """True when ``text`` (what OCR read for one hit) is only a near match.

        The exact rules (this engine, not ``for_ocr``) must cover the whole
        text; otherwise the hit came from the one-misread-character allowance
        and the screen marks it 近似 (review M34-2).
        """
        stripped = (text or "").strip()
        if not stripped:
            return False
        lead = len(text) - len(text.lstrip())
        end = lead + len(stripped)
        return not any(hit.start <= lead and hit.end >= end for hit in self.find_hits(text))

    def label(self, category: str) -> str:
        return redaction_label(category, self.replacements)

    def find_hits(self, text: str) -> list[Hit]:
        if not text:
            return []
        results = self.analyzer.analyze(text=text, language="en")
        # Longer spans win so a short term inside a longer term is one hit.
        ordered = sorted(
            results,
            key=lambda item: (-(item.end - item.start), item.start, item.entity_type),
        )
        chosen = []
        for item in ordered:
            if item.entity_type == "NONE":
                continue
            overlaps = any(item.start < kept.end and kept.start < item.end for kept in chosen)
            if overlaps:
                continue
            chosen.append(item)
        chosen.sort(key=lambda item: item.start)
        return [
            Hit(
                start=item.start,
                end=item.end,
                category=item.entity_type,
                text=text[item.start : item.end],
            )
            for item in chosen
        ]


def counts_from_categories(categories: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for category in categories:
        counts[category] = counts.get(category, 0) + 1
    return counts


def merge_counts(dest: dict[str, int], src: dict[str, int]) -> None:
    for key, value in src.items():
        dest[key] = dest.get(key, 0) + value
