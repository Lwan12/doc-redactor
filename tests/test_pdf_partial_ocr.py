"""Partial PDF exports retain only failed OCR pictures and remain explicitly unsafe.

All documents and names here are fictional. No private sample is used.
"""
import hashlib
import io
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from doc_redactor import cli, pdf_redactor, scan as scan_module, tesseract_cmd
from doc_redactor.engine import RegexRule, build_engine
from doc_redactor.image_redactor import MSG_NO_OCR, WriteBlocked
from doc_redactor.leak_check import LeakedText, NoHits


def _engine():
    return build_engine([RegexRule("PROJECT_ID", r"ABC-\d{4}-\d{3}", 0)], [])


def _png(size):
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((10, 10), "ABC-2026-001", fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _pdf(path, *, repeated=False, text=True):
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), "ABC-2026-001 Keep this sentence.")
    failed = page.insert_image(pymupdf.Rect(72, 120, 183, 161), stream=_png((111, 41)))
    page.insert_image(pymupdf.Rect(72, 220, 193, 262), stream=_png((121, 42)))
    if repeated:
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(72, 120, 183, 161), xref=failed)
    doc.save(path)
    doc.close()


def _fake_ocr(image, _engine):
    if image.size == (111, 41):
        import pytesseract
        raise pytesseract.TesseractError(1, "fake OCR failure")
    return [{
        "category": "PROJECT_ID", "text": "ABC-2026-001", "start": 0,
        "end": 12, "boxes": [(6, 6, 100, 25)],
    }]


@pytest.fixture
def ocr_failure(monkeypatch):
    # This suite tests the failure handling, not availability of a local OCR binary.
    monkeypatch.setattr(tesseract_cmd, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(scan_module, "analyze_image_entities", _fake_ocr)
    monkeypatch.setattr(pdf_redactor, "analyze_image_entities", _fake_ocr)


def _pixels(path, width):
    with pymupdf.open(path) as doc:
        for page in doc:
            for item in page.get_images(full=True):
                if item[2] == width:
                    return pdf_redactor.pdf_xref_image(doc, item[0], item[1]).tobytes()
    raise AssertionError("image missing")


def _assert_partial(source, dest, counts):
    assert dest.is_file()
    assert _pixels(source, 111) == _pixels(dest, 111), "failed OCR image must stay unchanged"
    assert _pixels(source, 121) != _pixels(dest, 121), "later image must still be redacted"
    with pymupdf.open(dest) as doc:
        text = "".join(page.get_text() for page in doc)
    assert "ABC-2026-001" not in text
    assert "Keep this sentence." in text
    assert counts == {"PROJECT_ID": 2}
    warning = dest.with_suffix(".warnings.json")
    raw = warning.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["status"] == "partial"
    assert "图片需人工检查" in data["message"]
    assert data["skipped_images"][0]["page"] == 1
    assert data["skipped_images"][0]["index"] == 1
    assert "ABC-2026-001" not in raw
    assert "fake OCR failure" not in raw
    assert str(source) not in raw


def test_scan_write_keeps_failed_picture_and_checks_other_content(tmp_path, ocr_failure):
    source = tmp_path / "fictional.pdf"
    _pdf(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    scan = scan_module.scan_path(source, _engine())
    assert scan.blocked_reason is None
    assert scan.will_write()
    assert len(scan.skipped_images) == 1
    assert len(scan.hits) == 2
    [result] = scan_module.write_scans([scan], tmp_path / "out", engine=_engine())
    assert not result.error and not result.skipped
    assert result.dest.name == "fictional.partial-redacted.pdf"
    assert result.skipped_images == scan.skipped_images
    _assert_partial(source, result.dest, result.counts)
    assert json.loads(result.report.read_text()) == {"PROJECT_ID": 2}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_cli_partial_file_is_marked_and_returns_two(tmp_path, ocr_failure, capsys):
    source = tmp_path / "fictional.pdf"
    _pdf(source)
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "regex_rules:\n  - name: PROJECT_ID\n    pattern: 'ABC-\\d{4}-\\d{3}'\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert cli.main([str(source), "-r", str(rules), "-o", str(out)]) == 2
    captured = capsys.readouterr()
    assert "部分脱敏" in captured.out and "图片需人工检查" in captured.out
    assert "第 1 页图 1" in captured.out
    assert not (out / "fictional.redacted.pdf").exists()
    _assert_partial(source, out / "fictional.partial-redacted.pdf", {"PROJECT_ID": 2})


def test_cli_rename_failure_removes_unmarked_partial_output(tmp_path, ocr_failure, monkeypatch):
    source = tmp_path / "locked.pdf"
    _pdf(source)
    out = tmp_path / "out"
    original = Path.replace
    def locked(self, target):
        if str(target).endswith(".partial-redacted.pdf"):
            raise PermissionError("target is locked")
        return original(self, target)
    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(PermissionError):
        cli.redact_path(source, out, _engine())
    assert not list(out.glob("*.pdf"))
    assert not list(out.glob("*.warnings.json"))


def test_repeated_failed_xref_lists_every_page(tmp_path, ocr_failure):
    source = tmp_path / "repeated.pdf"
    _pdf(source, repeated=True)
    scan = scan_module.scan_path(source, _engine())
    assert [(s["page"], s["index"]) for s in scan.skipped_images] == [(1, 1), (2, 1)]
    counts = pdf_redactor.redact_pdf(source, tmp_path / "direct.pdf", _engine())
    assert [(s["page"], s["index"]) for s in counts.skipped_images] == [(1, 1), (2, 1)]


def test_no_hits_does_not_export_unmodified_partial_file(tmp_path, ocr_failure, monkeypatch):
    source = tmp_path / "nohits.pdf"
    _pdf(source, text=False)
    def all_fail(*_args):
        raise RuntimeError("fake OCR failure")
    monkeypatch.setattr(scan_module, "analyze_image_entities", all_fail)
    monkeypatch.setattr(pdf_redactor, "analyze_image_entities", all_fail)
    scan = scan_module.scan_path(source, _engine())
    assert len(scan.skipped_images) == 2
    assert not scan.will_write()
    [result] = scan_module.write_scans([scan], tmp_path / "out", engine=_engine())
    assert result.skipped and result.dest is None
    with pytest.raises(NoHits):
        cli.redact_path(source, tmp_path / "cli-out", _engine())
    assert not list(tmp_path.rglob("*.partial-redacted.pdf"))


def test_missing_ocr_and_bad_decode_remain_blocking(tmp_path, monkeypatch):
    source = tmp_path / "blocked.pdf"
    _pdf(source)
    monkeypatch.setattr(tesseract_cmd, "find_tesseract", lambda: None)
    scan = scan_module.scan_path(source, _engine())
    assert scan.blocked_reason and not scan.skipped_images
    with pytest.raises(WriteBlocked):
        pdf_redactor.redact_pdf(source, tmp_path / "missing.pdf", _engine())
    monkeypatch.setattr(tesseract_cmd, "find_tesseract", lambda: "/fake/tesseract")
    def bad_decode(*_args):
        raise WriteBlocked("fake broken image")
    monkeypatch.setattr(scan_module, "pdf_xref_image", bad_decode)
    monkeypatch.setattr(pdf_redactor, "pdf_xref_image", bad_decode)
    scan = scan_module.scan_path(source, _engine())
    assert "打不开" in scan.blocked_reason and not scan.skipped_images
    with pytest.raises(WriteBlocked):
        pdf_redactor.redact_pdf(source, tmp_path / "bad.pdf", _engine())


def test_language_data_failure_stays_blocking(tmp_path, ocr_failure, monkeypatch):
    source = tmp_path / "language.pdf"
    _pdf(source)
    def no_languages(*_args):
        import pytesseract
        raise pytesseract.TesseractError(1, "Tesseract couldn't load any languages!")
    monkeypatch.setattr(scan_module, "analyze_image_entities", no_languages)
    scan = scan_module.scan_path(source, _engine())
    assert scan.blocked_reason == MSG_NO_OCR.format(n=1)
    assert not scan.skipped_images


@pytest.mark.parametrize("route", ["gui", "cli"])
def test_partial_does_not_bypass_leak_gate(tmp_path, ocr_failure, monkeypatch, route):
    source = tmp_path / "leak.pdf"
    _pdf(source)
    def reject(path, *_args, **_kwargs):
        Path(path).unlink()
        raise LeakedText(1)
    out = tmp_path / "out"
    if route == "gui":
        monkeypatch.setattr(scan_module, "gate_output", reject)
        scan = scan_module.scan_path(source, _engine())
        [result] = scan_module.write_scans([scan], out, engine=_engine())
        assert result.error and result.dest is None
    else:
        monkeypatch.setattr(cli, "gate_output", reject)
        with pytest.raises(LeakedText):
            cli.redact_path(source, out, _engine())
    assert not list(out.glob("*.pdf"))
    assert not list(out.glob("*.warnings.json"))


def test_inline_ocr_failure_does_not_block_text(tmp_path, ocr_failure, monkeypatch):
    source = tmp_path / "inline.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ABC-2026-001")
    xref = page.get_contents()[0]
    ops = b"q 100 0 0 50 72 400 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\xff\xff\x00 EI Q\n"
    doc.update_stream(xref, doc.xref_stream(xref) + b"\n" + ops)
    doc.save(source)
    doc.close()
    def fail(*_args):
        raise RuntimeError("fake inline OCR error")
    monkeypatch.setattr(scan_module, "analyze_image_entities", fail)
    scan = scan_module.scan_path(source, _engine())
    assert not scan.blocked_reason
    assert scan.skipped_images[0]["unit"] == "pdfinline:0:0"
    [result] = scan_module.write_scans([scan], tmp_path / "out", engine=_engine())
    assert result.dest and result.dest.is_file()
    with pymupdf.open(source) as before, pymupdf.open(result.dest) as after:
        assert pdf_redactor.inline_images(before[0])[0][1].tobytes() == pdf_redactor.inline_images(after[0])[0][1].tobytes()


@pytest.mark.parametrize("route", ["gui", "cli"])
def test_inline_paint_cannot_modify_overlapping_skipped_image(tmp_path, ocr_failure, monkeypatch, route):
    source = tmp_path / "overlap.pdf"
    _pdf(source)
    with pymupdf.open(source) as doc:
        xref = doc[0].get_contents()[0]
        # Page height 842: this inline image overlaps the failed xref at y=120.
        y = int(doc[0].rect.height - 161)
        ops = f"q 111 0 0 41 72 {y} cm BI /W 2 /H 2 /CS /G /BPC 8 ID ".encode() + b"\x00\xff\xff\x00 EI Q\n"
        doc.update_stream(xref, doc.xref_stream(xref) + b"\n" + ops)
        doc.saveIncr()
    def overlapping_ocr(image, engine):
        if image.size == (2, 2):
            return [{"category": "PROJECT_ID", "text": "ABC-2026-001", "start": 0, "end": 12, "boxes": [(0, 0, 1, 1)]}]
        return _fake_ocr(image, engine)
    monkeypatch.setattr(scan_module, "analyze_image_entities", overlapping_ocr)
    monkeypatch.setattr(pdf_redactor, "analyze_image_entities", overlapping_ocr)
    out = tmp_path / "out"
    if route == "gui":
        scanned = scan_module.scan_path(source, _engine())
        [result] = scan_module.write_scans([scanned], out, engine=_engine())
        assert result.error and result.dest is None
    else:
        with pytest.raises(WriteBlocked):
            cli.redact_path(source, out, _engine())
    assert not list(out.glob("*.pdf"))
