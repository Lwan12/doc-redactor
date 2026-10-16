"""Headless scan/write on samples: original terms gone, report has no source text."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from PIL import Image

from doc_redactor.ruleset import load_ruleset
from doc_redactor.scan import scan_path, write_report, write_scan
from doc_redactor.tesseract_cmd import TESSERACT_HINT, find_tesseract
from helpers import ROOT, RULES, SECRETS, load_make_samples

pytestmark = pytest.mark.skipif(find_tesseract() is None, reason=TESSERACT_HINT)

SAMPLES = ROOT / "samples"


def _ocr(image: Image.Image) -> str:
    from doc_redactor.tesseract_cmd import configure_tesseract
    import pytesseract

    configure_tesseract()
    return pytesseract.image_to_string(image, lang="chi_sim+eng")


def _compact(text: str) -> str:
    return "".join(text.split()).lower()


def _xml_of_docx(path: Path) -> str:
    chunks = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                chunks.append(archive.read(name).decode("utf-8", errors="ignore"))
    return "\n".join(chunks)


def test_samples_and_extra_embedded_docx(tmp_path: Path):
    engine = load_ruleset(RULES).to_engine()
    make = load_make_samples()
    extra_png = tmp_path / "extra.png"
    extra_docx = tmp_path / "extra.docx"
    make.render_png(extra_png)
    make.render_docx(extra_docx, extra_png)

    sources = [
        SAMPLES / "sample.docx",
        SAMPLES / "sample.pdf",
        SAMPLES / "sample.png",
        extra_docx,
    ]
    for src in sources:
        scan = scan_path(src, engine)
        assert scan.status != "fail", scan.error
        dest = tmp_path / f"{src.stem}.redacted{src.suffix}"
        counts = write_scan(scan, dest)
        report = tmp_path / f"{src.stem}.report.json"
        write_report(report, counts)
        raw = report.read_text(encoding="utf-8")
        for secret in SECRETS:
            assert secret not in raw
        payload = json.loads(raw)
        assert payload
        assert all(isinstance(v, int) for v in payload.values())
        assert "start" not in raw and "text" not in raw

        if src.suffix.lower() == ".docx":
            xml = _xml_of_docx(dest)
            for secret in ("ABC-2026-001", "Acme Pharma", "Fictizumab"):
                assert secret not in xml
            document = Document(str(dest))
            joined = "\n".join(p.text for p in document.paragraphs)
            assert "ABC-2026-001" not in joined
        elif src.suffix.lower() == ".pdf":
            doc = pymupdf.open(dest)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            assert "ABC-2026-001" not in text
            assert "Acme Pharma" not in text
            assert "Fictizumab" not in text
        else:
            with Image.open(dest) as image:
                folded = _compact(_ocr(image))
            assert "abc-2026-001" not in folded
            assert "acmepharma" not in folded
            assert "fictizumab" not in folded
