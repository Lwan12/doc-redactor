"""leak_check must not match the random bytes of a PDF file identifier (/ID, issue #45)."""

from pathlib import Path

import pymupdf

from doc_redactor.leak_check import count_leaks
from doc_redactor.engine import WordlistRule, build_engine

# Bytes that decode (latin1) to "\x01I\x02": the single-letter rule "I" hits it.
_ID = "<0149020304050607> <0149020304050607>"


def _engine():
    return build_engine([], [WordlistRule("手机号", ["I"], True)])


def _pdf(path: Path, *, compressed_xref: bool = False, title: str = "") -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Sponsor [SP] visit")
    if title:
        document.set_metadata({"title": title})
    document.xref_set_key(-1, "ID", f"[{_ID}]")
    document.save(path, no_new_id=True, use_objstms=1 if compressed_xref else 0)
    document.close()


def _has_id(path: Path) -> bool:
    data = path.read_bytes()
    return b"0149020304050607" in data.lower() or b"0149020304050607".upper() in data


def test_trailer_id_bytes_are_not_a_leak(tmp_path: Path):
    path = tmp_path / "id.pdf"
    _pdf(path)
    assert _has_id(path)
    assert count_leaks(path, _engine()) == 0


def test_xref_stream_id_bytes_are_not_a_leak(tmp_path: Path):
    path = tmp_path / "id-objstm.pdf"
    _pdf(path, compressed_xref=True)
    assert count_leaks(path, _engine()) == 0


def test_same_word_in_metadata_is_still_a_leak(tmp_path: Path):
    path = tmp_path / "meta.pdf"
    _pdf(path, title="Owner I")
    assert count_leaks(path, _engine()) >= 1


def test_id_that_is_plain_text_is_still_checked(tmp_path: Path):
    # The writer copies the first /ID string of the original, so text there must still count.
    path = tmp_path / "text-id.pdf"
    document = pymupdf.open()
    document.new_page()
    document.xref_set_key(-1, "ID", "[(Acme) <0149020304050607>]")
    document.save(path, no_new_id=True)
    document.close()
    engine = build_engine([], [WordlistRule("申办方", ["Acme"], True), WordlistRule("手机号", ["I"], True)])
    assert count_leaks(path, engine) == 1
