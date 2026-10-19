"""Scan and write with 「去掉页眉页脚」: delete headers/footers first, then rules.

Fictional data only.
"""

from __future__ import annotations

import zipfile

import pymupdf
from docx import Document

from doc_redactor import header_footer as hf
from doc_redactor.engine import WordlistRule, build_engine
from doc_redactor.scan import scan_path, write_scan, write_scans
from test_header_footer import HEADER, SPONSOR_FOOTER, SIGN, _docx, _plain_pdf, _repeat_pdf, _tagged_pdf


def _engine():
    return build_engine([], [WordlistRule("项目号", ["ABC-2026-001"], True), WordlistRule("药名", ["Fictizumab"], True)])


def _save(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_option_off_changes_nothing(tmp_path):
    path = _save(tmp_path, "t.pdf", _tagged_pdf())
    scan = scan_path(path, _engine())
    assert scan.hf_route == "" and not scan.hf_active
    assert all(not h.hf_role for h in scan.hits)


def test_tagged_pdf_hits_in_header_are_not_counted_and_header_is_deleted(tmp_path):
    path = _save(tmp_path, "t.pdf", _tagged_pdf())
    scan = scan_path(path, _engine(), remove_hf=True)
    assert scan.hf_route == hf.ROUTE_TAG and scan.hf_active
    in_hf = [h for h in scan.hits if h.hf_role]
    body = [h for h in scan.hits if not h.hf_role]
    assert len(in_hf) == 4  # ABC-2026-001 in each page header
    assert scan.enabled_count() == len(body) == 4 * 2  # Fictizumab, two body lines a page
    assert scan.hf_pages == 4
    roles = {role for (_p, _l), role in scan.hf_lines.items()}
    assert roles == {hf.ROLE_HEADER, hf.ROLE_FOOTER, hf.ROLE_SIGNATURE}
    dest = tmp_path / "out.pdf"
    write_scan(scan, dest)
    text = "".join(p.get_text() for p in pymupdf.open(dest))
    for gone in (HEADER, SPONSOR_FOOTER, SIGN, "ABC-2026-001", "Fictizumab"):
        assert gone not in text
    assert "body first line" in text and "Layout note" in text
    out = pymupdf.open(dest)
    streams = b"".join(out.xref_stream(x) or b"" for x in range(1, out.xref_length()) if out.xref_is_stream(x))
    assert b"Version 1.0" not in streams and b"CONFIDENTIAL" not in streams and b"Envelope" not in streams


def test_file_kept_counts_header_hits_and_replaces_inside_header(tmp_path):
    path = _save(tmp_path, "t.pdf", _tagged_pdf())
    scan = scan_path(path, _engine(), remove_hf=True)
    scan.hf_keep = True
    assert not scan.hf_active
    assert scan.enabled_count() == len(scan.hits) == 12
    dest = tmp_path / "kept.pdf"
    write_scan(scan, dest)
    text = "".join(p.get_text() for p in pymupdf.open(dest))
    assert "ABC-2026-001" not in text and "Version 1.0" in text and SPONSOR_FOOTER in text


def test_repeat_pdf_is_deleted_on_write_when_kept_checked(tmp_path):
    path = _save(tmp_path, "r.pdf", _repeat_pdf())
    scan = scan_path(path, _engine(), remove_hf=True)
    assert scan.hf_route == hf.ROUTE_REPEAT and scan.hf_active
    dest = tmp_path / "r-out.pdf"
    write_scan(scan, dest)
    text = "".join(p.get_text() for p in pymupdf.open(dest))
    assert HEADER not in text and "Page " not in text and "Repeated body sentence" in text


def test_plain_pdf_reports_not_found(tmp_path):
    path = _save(tmp_path, "p.pdf", _plain_pdf())
    scan = scan_path(path, _engine(), remove_hf=True)
    assert scan.hf_route == hf.ROUTE_NONE and not scan.hf_active
    assert scan.enabled_count() == len(scan.hits)


def test_file_with_hits_only_in_header_is_still_written(tmp_path):
    path = _save(tmp_path, "h.pdf", _tagged_pdf())
    engine = build_engine([], [WordlistRule("项目号", ["ABC-2026-001"], True)])
    scan = scan_path(path, engine, remove_hf=True)
    assert scan.enabled_count() == 0 and scan.hf_active
    (result,) = write_scans([scan], tmp_path / "out")
    assert result.dest is not None and result.hf_removed
    assert HEADER not in "".join(p.get_text() for p in pymupdf.open(result.dest))


def test_word_header_hits_hidden_and_header_emptied(tmp_path):
    path = _docx(tmp_path)
    scan = scan_path(path, _engine(), remove_hf=True)
    assert scan.hf_route == hf.ROUTE_WORD and scan.hf_active and scan.hf_watermark
    hidden = [h for h in scan.hits if h.hf_role]
    assert hidden and all(scan.hf_units[h.unit] == h.hf_role for h in hidden)
    assert scan.enabled_count() == 1  # Fictizumab in the body
    dest = tmp_path / "out.docx"
    write_scan(scan, dest)
    assert hf.verify_docx_output(dest) == 0
    body = [p.text for p in Document(dest).paragraphs]
    assert body == ["Body mentions [STUDY DRUG] once."] or "Fictizumab" not in body[0]
    with zipfile.ZipFile(dest) as zf:
        xml = "".join(zf.read(n).decode() for n in zf.namelist() if n.startswith("word/header"))
    assert "ABC-2026-001" not in xml
