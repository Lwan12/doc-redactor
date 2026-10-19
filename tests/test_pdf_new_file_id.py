"""Written PDFs get a new file identifier (/ID); nothing of the original's /ID is kept.

PyMuPDF keeps the first /ID string of the original when it saves. leak_check
skips /ID strings with control characters (#45), so a term followed by one
there would stay in the output unchecked. Fictional data only.
"""

from pathlib import Path

import pymupdf

from doc_redactor.engine import WordlistRule, build_engine
from doc_redactor.scan import scan_path, write_scans


def _source(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Sponsor Acme Pharma visit")
    document.xref_set_key(-1, "ID", "[(Acme Pharma\\001) <0102030405060708>]")
    document.save(path, no_new_id=True)
    document.close()


def test_written_pdf_does_not_keep_original_file_id(tmp_path: Path):
    src = tmp_path / "id.pdf"
    _source(src)
    assert b"Acme Pharma\\001" in src.read_bytes()
    engine = build_engine([], [WordlistRule("申办方", ["Acme Pharma"], True)])
    scan = scan_path(src, engine)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.dest is not None, res.error
    data = res.dest.read_bytes()
    assert b"Acme Pharma" not in data
    out = pymupdf.open(res.dest)
    try:
        assert "Acme" not in out.xref_get_key(-1, "ID")[1]
    finally:
        out.close()
