from pathlib import Path

import pymupdf

from doc_redactor.cli import redact_path
from doc_redactor.config import load_rules
from doc_redactor.pdf_redactor import redact_pdf
from doc_redactor.scan import scan_path, write_scan
from helpers import RULES, assert_report_has_no_secrets


def _build(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 720), "Project ABC-2026-001")
    page.insert_text((72, 700), "Sponsor Acme Pharma")
    page.insert_text((72, 680), "Drug Fictizumab")
    page.insert_text((72, 660), "Again ABC-2026-001")
    page.insert_text((72, 640), "fictizumab stays")
    document.save(path)
    document.close()


def test_pdf_text_is_deleted_and_report_omits_source(tmp_path: Path):
    source = tmp_path / "brief.pdf"
    _build(source)
    original = source.read_bytes()
    outdir = tmp_path / "out"
    output = outdir / "brief.redacted.pdf"
    counts = redact_pdf(source, output, load_rules(RULES))
    assert source.read_bytes() == original

    document = pymupdf.open(output)
    text = "\n".join(page.get_text() for page in document)
    document.close()
    assert "ABC-2026-001" not in text
    assert "Acme Pharma" not in text
    assert "Fictizumab" not in text
    assert "fictizumab stays" in text
    assert "[PROTOCOL]" in text
    assert "[SPONSOR]" in text
    assert "[STUDY DRUG]" in text
    assert text.count("[PROTOCOL]") == 2
    assert counts == {"DRUG": 1, "PROJECT_ID": 2, "SPONSOR": 1}

    redact_path(source, outdir, load_rules(RULES))
    report = assert_report_has_no_secrets(outdir / "brief.report.json")
    assert report == counts


def _hidden_pdf(path: Path) -> None:
    """Body hits plus the same fictional words in metadata, notes, bookmarks, forms, XMP, and attachments."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 720), "Project ABC-2026-001")
    page.insert_text((72, 700), "Sponsor Acme Pharma")
    page.insert_text((72, 680), "Drug Fictizumab")
    page.insert_text((72, 660), "Again ABC-2026-001")
    page.insert_text((72, 640), "fictizumab stays")
    page.insert_text((72, 620), "Contact Zhang Wei-Test phone")
    page.add_text_annot((300, 300), "note: ABC-2026-777 Acme Pharma QXNOTE8841")
    page.add_file_annot((120, 120), b"attach QXATT8841 Acme Pharma", filename="side.txt")
    widget = pymupdf.Widget()
    widget.field_name = "sponsor_field"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_value = "Acme Pharma QXFORM8841"
    widget.rect = pymupdf.Rect(72, 200, 360, 230)
    page.add_widget(widget)
    document.set_metadata(
        {
            "title": "Report ABC-2026-001 QXMETA8841",
            "author": "Acme Pharma",
            "subject": "ABC-2026-001",
            "keywords": "QXKEY8841",
            "creator": "QXCREATOR8841",
            "producer": "QXPRODUCER8841",
        }
    )
    document.set_toc([[1, "ABC-2026-001 section QXBOOK8841", 1]])
    document.set_xml_metadata(
        "<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>"
        "<x:xmpmeta>QXXMP8841 ABC-2026-001 Acme Pharma</x:xmpmeta>"
        "<?xpacket end='w'?>"
    )
    document.embfile_add(
        "note.txt",
        b"embedded QXFILE8841 Acme Pharma ABC-2026-001",
        filename="note.txt",
        ufilename="note.txt",
        desc="QXDESC8841",
    )
    document.save(path)
    document.close()


_HIDDEN_TOKENS = (
    b"ABC-2026",
    b"Acme",
    b"Fictizumab",
    b"QXMETA8841",
    b"QXKEY8841",
    b"QXCREATOR8841",
    b"QXPRODUCER8841",
    b"QXNOTE8841",
    b"QXBOOK8841",
    b"QXFORM8841",
    b"QXXMP8841",
    b"QXFILE8841",
    b"QXATT8841",
    b"QXDESC8841",
    b"sponsor_field",
)


def _assert_hidden_gone(path: Path) -> None:
    document = pymupdf.open(path)
    text = "\n".join(page.get_text() for page in document)
    assert "ABC-2026-001" not in text
    assert "Acme Pharma" not in text
    assert "Fictizumab" not in text
    assert "fictizumab stays" in text
    assert "Zhang Wei-Test" in text
    info = document.metadata
    for key in ("title", "author", "subject", "keywords", "creator", "producer"):
        assert info.get(key) in ("", None)
    assert document.get_toc() == []
    assert list(document[0].annots() or []) == []
    assert list(document[0].widgets() or []) == []
    assert document.embfile_count() == 0
    xml = document.get_xml_metadata() or ""
    assert "QXXMP8841" not in xml
    assert "ABC-2026" not in xml
    assert "Acme" not in xml
    document.close()
    raw = path.read_bytes()
    for token in _HIDDEN_TOKENS:
        assert token not in raw, token


def test_pdf_strips_metadata_notes_bookmarks_forms_and_attachments(tmp_path: Path):
    source = tmp_path / "hidden.pdf"
    _hidden_pdf(source)
    engine = load_rules(RULES)

    cli_out = tmp_path / "cli.redacted.pdf"
    counts = redact_pdf(source, cli_out, engine)
    assert counts["DRUG"] == 1
    assert counts["PROJECT_ID"] == 2
    assert counts["SPONSOR"] >= 1
    _assert_hidden_gone(cli_out)

    scan = scan_path(source, engine)
    assert scan.status != "fail"
    gui_out = tmp_path / "gui.redacted.pdf"
    write_scan(scan, gui_out, engine=engine)
    _assert_hidden_gone(gui_out)
