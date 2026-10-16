"""Presidio analyzer wired only to regex and deny-list recognizers.

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


@dataclass(frozen=True)
class WordlistRule:
    category: str
    terms: list[str]
    case_sensitive: bool


def redaction_label(category: str) -> str:
    return f"[REDACTED:{category}]"


def build_engine(regex_rules: list[RegexRule], wordlists: list[WordlistRule]) -> "RedactionEngine":
    recognizers: list[PatternRecognizer] = []
    for rule in regex_rules:
        try:
            re.compile(rule.pattern, flags=rule.flags)
        except re.error as exc:
            raise ValueError(f"匹配写法 {rule.name} 无法编译: {exc}") from exc
        recognizers.append(
            PatternRecognizer(
                supported_entity=rule.name,
                name=rule.name,
                patterns=[Pattern(name=rule.name, regex=rule.pattern, score=1.0)],
                global_regex_flags=rule.flags,
            )
        )
    for rule in wordlists:
        if not rule.terms:
            continue
        flags = re.DOTALL | re.MULTILINE
        if not rule.case_sensitive:
            flags |= re.IGNORECASE
        recognizers.append(
            PatternRecognizer(
                supported_entity=rule.category,
                name=rule.category,
                deny_list=list(rule.terms),
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
    return RedactionEngine(analyzer)


class RedactionEngine:
    def __init__(self, analyzer: AnalyzerEngine) -> None:
        self.analyzer = analyzer

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
