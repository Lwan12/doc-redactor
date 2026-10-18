"""Auto-save and restore of rules/settings. No tkinter."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from doc_redactor.persist import (
    ALLOWED_RULE,
    ALLOWED_TOP,
    MSG_EXPORT_BODY,
    MSG_FIRST,
    MSG_IMPORT_FOOT,
    MSG_OPENED,
    MSG_OUTPUT_MISSING,
    MSG_RESET_DONE,
    MSG_RESET_SAME,
    MSG_SAVE_FAIL,
    SETTINGS_NAME,
    backup_filename,
    changed_from_defaults,
    check_output_dir,
    load_session,
    payload_leaks_source,
    payload_text,
    parse_payload,
    reset_body,
    reveal_directory,
    save_session,
    session_payload,
    settings_dir,
    settings_path,
)
from doc_redactor.ruleset import (
    KIND_PATTERN,
    KIND_WORDLIST,
    UNDO_TTL_SECONDS,
    Rule,
    RuleSet,
    common_library,
    default_library_checked,
    default_rules,
)
from helpers import SECRETS


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOC_REDACTOR_HOME", str(home))
    return home


def test_first_launch_writes_defaults_and_sets_banner(tmp_path: Path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    session = load_session(path=home / SETTINGS_NAME, default_output=str(tmp_path / "out"))
    assert session.first_run is True
    assert session.corrupt_name is None
    assert session.save_failed is False
    path = home / SETTINGS_NAME
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    names = [rule["name"] for rule in payload["rules"]]
    assert names == [rule.name for rule in default_rules()]
    assert len(names) == 5
    assert len(common_library()) - 1 == 7  # 7 mains; sub-item is extra
    terms = []
    for rule in payload["rules"]:
        terms.extend(rule.get("terms") or [])
    assert "Acme Pharma" in terms
    assert "Fictizumab" in terms
    assert session.rules[0].name == "项目号"


def test_second_launch_restores_rules_words_replacement_enabled_library_output(
    tmp_path: Path, monkeypatch
):
    home = _home(tmp_path, monkeypatch)
    path = home / SETTINGS_NAME
    first = load_session(path=path, default_output=str(tmp_path / "out"))
    first.rules[0].enabled = False
    first.rules[0].replacement = "[PROTOCOL-X]"
    sponsor = next(rule for rule in first.rules if rule.name == "申办方")
    sponsor.terms = ["Acme Pharma", "Northwind Labs"]
    first.rules.append(
        Rule(name="内部编号", kind=KIND_PATTERN, pattern=r"ZX-\d+", examples=["ZX-001"], enabled=True)
    )
    items = common_library()
    flags = list(first.library_checked)
    letter = next(i for i, item in enumerate(items) if item.name == "字母加数字")
    flags[letter] = True
    out = str(tmp_path / "kept-out")
    out_path = Path(out)
    out_path.mkdir()
    assert save_session(first.rules, flags, out, path=path)

    second = load_session(path=path)
    assert second.first_run is False
    assert second.corrupt_name is None
    assert [rule.name for rule in second.rules] == [rule.name for rule in first.rules]
    assert second.rules[0].enabled is False
    assert second.rules[0].replacement == "[PROTOCOL-X]"
    restored_sponsor = next(rule for rule in second.rules if rule.name == "申办方")
    assert restored_sponsor.terms == ["Acme Pharma", "Northwind Labs"]
    assert second.library_checked[letter] is True
    assert second.output_dir == out


def test_corrupt_file_is_renamed_defaults_loaded_prompt_once(tmp_path: Path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    path = home / SETTINGS_NAME
    path.write_text("{not json", encoding="utf-8")
    when = datetime(2026, 10, 18, 20, 54)
    session = load_session(path=path, now=when, default_output=str(tmp_path / "out"))
    backup = home / backup_filename(when)
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "{not json"
    assert session.corrupt_name == backup.name
    assert session.first_run is False
    assert [rule.name for rule in session.rules] == [rule.name for rule in default_rules()]
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    again = load_session(path=path)
    assert again.corrupt_name is None
    assert again.first_run is False


def test_save_failure_keeps_memory_and_clears_on_success(tmp_path: Path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    path = home / SETTINGS_NAME
    session = load_session(path=path, default_output=str(tmp_path / "out"))
    session.rules.append(Rule(name="临时", kind=KIND_PATTERN, pattern=r"TMP-\d+", examples=["TMP-1"]))
    fail = {"on": True}
    import doc_redactor.persist as persist_mod

    real_write = persist_mod.atomic_write

    def maybe_write(target, text):
        if fail["on"]:
            raise OSError("disk full")
        real_write(target, text)

    monkeypatch.setattr(persist_mod, "atomic_write", maybe_write)
    ok = save_session(session.rules, session.library_checked, session.output_dir, path=path)
    assert ok is False
    assert any(rule.name == "临时" for rule in session.rules)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert all(rule["name"] != "临时" for rule in stored["rules"])
    fail["on"] = False
    ok = save_session(session.rules, session.library_checked, session.output_dir, path=path)
    assert ok is True
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert any(rule["name"] == "临时" for rule in stored["rules"])
    assert MSG_SAVE_FAIL


def test_restore_defaults_counts_and_ten_minute_undo_saves(tmp_path: Path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    path = home / SETTINGS_NAME
    clock = FakeClock()
    rules = RuleSet(default_rules(), clock=clock)
    n, x = changed_from_defaults(rules.rules)
    assert n == 0
    assert x == len(default_rules()) == 5
    assert x != 7
    rules.add(Rule(name="内部编号", kind=KIND_PATTERN, pattern=r"ZX-\d+", examples=["ZX-001"]))
    rules.rules[0].enabled = False
    sponsor = next(rule for rule in rules.rules if rule.name == "申办方")
    sponsor.terms = ["Acme Pharma", "Northwind Labs"]
    n, x = changed_from_defaults(rules.rules)
    assert n == 3
    assert x == 5
    assert "3" in reset_body(n, x) and "5" in reset_body(n, x)
    save_session(rules.rules, default_library_checked(), str(tmp_path / "out"), path=path)

    rules.restore_defaults(label=MSG_RESET_DONE)
    assert [rule.name for rule in rules.rules] == [rule.name for rule in default_rules()]
    assert rules.undo_label() == MSG_RESET_DONE
    save_session(rules.rules, default_library_checked(), str(tmp_path / "out"), path=path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert [rule["name"] for rule in stored["rules"]] == [rule.name for rule in default_rules()]

    assert rules.undo()
    assert any(rule.name == "内部编号" for rule in rules.rules)
    save_session(rules.rules, default_library_checked(), str(tmp_path / "out"), path=path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert any(rule["name"] == "内部编号" for rule in stored["rules"])

    rules.restore_defaults(label=MSG_RESET_DONE)
    clock.value = UNDO_TTL_SECONDS + 1
    assert rules.undo_label() is None
    assert not rules.undo()
    assert [rule.name for rule in rules.rules] == [rule.name for rule in default_rules()]


def test_atomic_write_keeps_old_file_when_replace_fails(tmp_path: Path, monkeypatch):
    path = tmp_path / SETTINGS_NAME
    rules = default_rules()
    assert save_session(rules, default_library_checked(), str(tmp_path / "a"), path=path)
    original = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    extra = list(rules) + [Rule(name="新", kind=KIND_WORDLIST, terms=["x"])]
    assert save_session(extra, default_library_checked(), str(tmp_path / "b"), path=path) is False
    assert path.read_text(encoding="utf-8") == original
    leftover = list(tmp_path.glob(".settings.*.tmp"))
    assert leftover == []


def test_doc_redactor_home_overrides_path(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("DOC_REDACTOR_HOME", str(custom))
    assert settings_dir() == custom
    assert settings_path() == custom / SETTINGS_NAME
    monkeypatch.delenv("DOC_REDACTOR_HOME")
    linux = settings_dir(environ={}, platform="linux", home=tmp_path / "user")
    assert linux == tmp_path / "user" / ".local" / "share" / "doc-redactor"
    win = settings_dir(
        environ={"LOCALAPPDATA": str(tmp_path / "la")},
        platform="win32",
        home=tmp_path / "user",
    )
    assert win == tmp_path / "la" / "doc-redactor"


def test_settings_payload_has_no_source_filenames_or_document_text(tmp_path: Path):
    rules = default_rules()
    payload = session_payload(rules, default_library_checked(), str(tmp_path / "脱敏输出"))
    text = payload_text(payload)
    assert set(payload) <= ALLOWED_TOP
    for node in payload["rules"]:
        assert set(node) <= ALLOWED_RULE
    forbidden = [
        "访视记录_受试者012.docx",
        "入组统计报告_V2.pdf",
        "现场照片_0912.png",
        "ABC-2026-001",
        "samples/sample.docx",
        "/workspace/",
        str(tmp_path / "secret.docx"),
    ]
    assert payload_leaks_source(text, forbidden) == []
    for secret in SECRETS:
        if secret in {"Acme Pharma", "Fictizumab", "acme pharma", "fictizumab"}:
            continue
        assert secret not in text
    assert "files" not in payload
    assert "scans" not in payload
    parsed_rules, _, output = parse_payload(text)
    assert output.endswith("脱敏输出")
    assert [rule.name for rule in parsed_rules] == [rule.name for rule in default_rules()]


def test_missing_output_folder_reports_and_keeps_path(tmp_path: Path):
    missing = tmp_path / "gone" / "out"
    check = check_output_dir(str(missing))
    assert check.error == MSG_OUTPUT_MISSING
    assert check.path == str(missing)
    assert not missing.exists()
    present = tmp_path / "out"
    present.mkdir()
    ok = check_output_dir(str(present))
    assert ok.error is None
    assert ok.path == str(present)


def test_reveal_windows_opens_others_copy(tmp_path: Path):
    folder = tmp_path / "doc-redactor"
    called = []
    win = reveal_directory(folder, platform="win32", startfile=lambda p: called.append(p))
    assert win.opened is True
    assert win.message == MSG_OPENED
    assert called == [str(folder)]
    linux = reveal_directory(folder, platform="linux")
    assert linux.opened is False
    assert linux.copied is True
    assert linux.path == str(folder)


def test_same_as_defaults_copy():
    assert MSG_RESET_SAME
    assert MSG_FIRST
    assert "{n}" in MSG_IMPORT_FOOT
    assert "备份或换电脑" in MSG_EXPORT_BODY
