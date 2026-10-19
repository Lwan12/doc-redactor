"""「去掉页眉页脚」 is saved with the rules, defaults to on, and counts as a rule change."""

from __future__ import annotations

import json
from pathlib import Path

from doc_redactor.backpath import session_signature
from doc_redactor.persist import default_rules, load_session, save_session


def test_default_is_on_for_a_first_run(tmp_path):
    session = load_session(path=tmp_path / "s.json", default_output=str(tmp_path))
    assert session.remove_hf is True
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["remove_header_footer"] is True


def test_choice_is_remembered(tmp_path):
    path = tmp_path / "s.json"
    assert save_session(default_rules(), [], str(tmp_path), path=path, remove_hf=False)
    assert load_session(path=path).remove_hf is False


def test_old_settings_without_the_key_load_as_on(tmp_path):
    path = tmp_path / "s.json"
    save_session(default_rules(), [], str(tmp_path), path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("remove_header_footer", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    session = load_session(path=path)
    assert session.remove_hf is True and session.corrupt_name is None


def test_a_non_boolean_value_is_treated_as_a_damaged_file(tmp_path):
    path = tmp_path / "s.json"
    save_session(default_rules(), [], str(tmp_path), path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["remove_header_footer"] = "yes"
    path.write_text(json.dumps(data), encoding="utf-8")
    session = load_session(path=path)
    assert session.corrupt_name and session.remove_hf is True


def test_flipping_the_box_changes_the_signature():
    files = [Path("a.docx")]
    rules = default_rules()
    assert session_signature(files, rules, True) != session_signature(files, rules, False)
    assert session_signature(files, rules, True) == session_signature(files, rules, True)
