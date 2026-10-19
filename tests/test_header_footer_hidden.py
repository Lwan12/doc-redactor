"""PR #28 合并前：旋转页、标记里的隐形文字不准静默跳过（方向.md 验收口径 2）.

Made-up content only (ABC-2026-001, Acme Pharma, Fictizumab).
"""

from __future__ import annotations

import pymupdf

from doc_redactor import header_footer as hf
from doc_redactor.scan import scan_path, write_scans
from test_header_footer import HDR_TAG, HEADER, W, H, _tagged_pdf, _wrap_last
from test_header_footer_scan import _engine, _save

HIDDEN = "Acme Pharma hidden header note"


def _tagged_with_hidden(pages: int = 4) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=W, height=H)
        page.insert_text((72, 55), HEADER, fontsize=10)
        _wrap_last(doc, page, HDR_TAG)
        # Invisible text (render mode 3) inside the header tag: not seen, but
        # any text extraction (a web AI) still reads it.
        page.insert_text((300, 55), HIDDEN, fontsize=8, render_mode=3)
        _wrap_last(doc, page, HDR_TAG)
        page.insert_text((72, 400), f"Fictizumab body {i}", fontsize=10)
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<</Marked true>>")
    return doc.tobytes()


def _all_text(doc) -> str:
    return "\n".join(page.get_text() for page in doc)


def test_invisible_text_inside_a_header_tag_is_removed():
    doc = pymupdf.open("pdf", _tagged_with_hidden())
    assert HIDDEN in _all_text(doc)
    plan = hf.identify_pdf(doc)
    assert plan.route == hf.ROUTE_TAG
    hf.apply_pdf(doc, hf.plan_for_write(plan, False))
    out = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    text = _all_text(out)
    assert HIDDEN not in text and HEADER not in text
    assert text.count("Fictizumab body") == 4
    streams = b"".join(out.xref_stream(x) or b"" for x in range(1, out.xref_length()) if out.xref_is_stream(x))
    assert b"hidden header" not in streams


def _rotated_tagged() -> bytes:
    doc = pymupdf.open("pdf", _tagged_pdf())
    doc[2].set_rotation(90)
    return doc.tobytes()


def test_apply_refuses_a_file_with_rotated_pages_and_changes_nothing():
    doc = pymupdf.open("pdf", _rotated_tagged())
    plan = hf.identify_pdf(doc)
    assert plan.skipped_rotated == 1
    before = [doc.xref_stream(x) for page in doc for x in page.get_contents()]
    try:
        hf.apply_pdf(doc, hf.plan_for_write(plan, True))
    except hf.RotatedPages as exc:
        assert exc.count == 1 and "旋转" in str(exc) and "去掉页眉页脚" in str(exc)
    else:  # pragma: no cover - the failing-first case
        raise AssertionError("rotated page was skipped silently")
    assert [doc.xref_stream(x) for page in doc for x in page.get_contents()] == before


def test_rotated_page_fails_the_write_with_a_reason_unless_kept(tmp_path):
    path = _save(tmp_path, "rot.pdf", _rotated_tagged())
    scan = scan_path(path, _engine(), remove_hf=True)
    (result,) = write_scans([scan], tmp_path / "out")
    assert result.dest is None and result.skipped
    assert "旋转" in (result.error or "")
    assert not list((tmp_path / "out").glob("*.pdf"))
    # Unticking the box writes the file (headers kept, rules still applied).
    scan.hf_keep = True
    (result,) = write_scans([scan], tmp_path / "out2")
    assert result.dest is not None and result.dest.is_file()
    assert "ABC-2026-001" not in _all_text(pymupdf.open(result.dest))
