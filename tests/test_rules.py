from pathlib import Path

import pytest

from doc_redactor.config import load_rules, parse_flags
from doc_redactor.engine import RedactionEngine
from presidio_analyzer.nlp_engine import NoOpNlpEngine
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from helpers import RULES


def test_example_rules_load_and_wordlist_case():
    engine = load_rules(RULES)
    assert isinstance(engine, RedactionEngine)
    assert isinstance(engine.analyzer.nlp_engine, NoOpNlpEngine)
    recognizers = engine.analyzer.get_recognizers(language="en")
    assert recognizers
    assert not any(isinstance(item, SpacyRecognizer) for item in recognizers)

    text = "Seen Acme Pharma and acme pharma and Fictizumab and fictizumab and ABC-2026-001."
    hits = engine.find_hits(text)
    categories = [hit.category for hit in hits]
    assert categories.count("PROJECT_ID") == 1
    assert categories.count("SPONSOR") == 2
    assert categories.count("DRUG") == 1
    assert "fictizumab" not in {hit.text for hit in hits}
    assert any(hit.text == "Fictizumab" for hit in hits)
    assert any(hit.text.lower() == "acme pharma" for hit in hits)


def test_rules_can_add_regex_and_drop_wordlists(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "regex:\n"
        "  - name: LOT\n"
        "    pattern: 'lot-\\d+'\n"
        "    flags: IGNORECASE\n"
        "wordlists: []\n",
        encoding="utf-8",
    )
    engine = load_rules(rules)
    hits = engine.find_hits("Batch LOT-15 and lot-9 and Acme Pharma")
    assert [(hit.category, hit.text) for hit in hits] == [("LOT", "LOT-15"), ("LOT", "lot-9")]


def test_regex_without_ignorecase_is_sensitive(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "regex:\n"
        "  - name: PROJECT_ID\n"
        "    pattern: '\\bABC-\\d{4}-\\d{3}\\b'\n"
        "wordlists: []\n",
        encoding="utf-8",
    )
    engine = load_rules(rules)
    assert engine.find_hits("abc-2026-001") == []
    assert engine.find_hits("ABC-2026-001")[0].category == "PROJECT_ID"


def test_parse_flags_and_invalid_pattern(tmp_path: Path):
    assert parse_flags(None) == 0
    assert parse_flags(["IGNORECASE", "MULTILINE"]) != 0
    assert parse_flags("IGNORECASE|DOTALL") != 0
    with pytest.raises(ValueError):
        parse_flags("NOT_A_FLAG")
    rules = tmp_path / "bad.yaml"
    rules.write_text(
        "regex:\n  - name: BAD\n    pattern: '('\nwordlists: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="BAD"):
        load_rules(rules)


def test_wordlist_path_is_relative_to_the_rules_file(tmp_path: Path):
    listed = tmp_path / "names.txt"
    listed.write_text("Northwind Trials\n", encoding="utf-8")
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "regex: []\n"
        "wordlists:\n"
        "  - path: names.txt\n"
        "    case_sensitive: true\n"
        "    category: SPONSOR\n",
        encoding="utf-8",
    )
    engine = load_rules(rules)
    assert engine.find_hits("northwind trials") == []
    assert engine.find_hits("Northwind Trials")[0].category == "SPONSOR"
