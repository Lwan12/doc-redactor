from pathlib import Path

import pymupdf

from doc_redactor.cli import redact_path
from doc_redactor.config import load_rules
from doc_redactor.pdf_redactor import redact_pdf
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
    assert counts == {"DRUG": 1, "PROJECT_ID": 2, "SPONSOR": 1}

    redact_path(source, outdir, load_rules(RULES))
    report = assert_report_has_no_secrets(outdir / "brief.report.json")
    assert report == counts
