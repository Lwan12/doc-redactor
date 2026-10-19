"""Write then re-check: leftovers are reported, the output is deleted. Fictional data only."""

from pathlib import Path

import pymupdf
import pytest
from docx import Document

import doc_redactor.docx_redactor as docx_redactor
import doc_redactor.scan as scan_mod
from doc_redactor.cli import main
from doc_redactor.engine import RegexRule, WordlistRule, build_engine
from doc_redactor.scan import scan_path, write_scans
from helpers import RULES


def _engine():
    return build_engine(
        [RegexRule(name="PROJECT_ID", pattern=r"ABC-\d{4}-\d{3}", flags=0)],
        [WordlistRule(category="SPONSOR", terms=["Acme Pharma"], case_sensitive=True)],
    )


def _docx(path: Path) -> None:
    doc = Document()
    doc.add_paragraph("Project ABC-2026-001 by Acme Pharma")
    doc.add_paragraph("Second ABC-2026-002")
    doc.sections[0].header.paragraphs[0].text = "Header Acme Pharma"
    doc.save(str(path))


def _pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Project ABC-2026-001 by Acme Pharma")
    page.insert_text((72, 100), "Second ABC-2026-002")
    doc.save(str(path))
    doc.close()


def _skip_first_paragraph_hit(monkeypatch):
    real = docx_redactor.apply_hits_to_paragraph
    state = {"n": 0}

    def leaky(paragraph, hits, replacement_for):
        state["n"] += 1
        if state["n"] == 1:
            return [hit.category for hit in hits]  # pretend, write nothing
        return real(paragraph, hits, replacement_for)

    monkeypatch.setattr(scan_mod, "apply_hits_to_paragraph", leaky)


@pytest.mark.parametrize("maker,name", [(_docx, "a.docx"), (_pdf, "a.pdf")])
def test_clean_write_passes(tmp_path: Path, maker, name):
    src = tmp_path / name
    maker(src)
    engine = _engine()
    scan = scan_path(src, engine)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest is not None and res.dest.is_file()


def test_docx_leak_is_reported_and_deleted(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.docx"
    _docx(src)
    engine = _engine()
    scan = scan_path(src, engine)
    _skip_first_paragraph_hit(monkeypatch)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error and "2\u00a0处写出后仍可见" in res.error
    assert res.dest is None
    assert not any((tmp_path / "out").glob("*.docx"))
    assert not any((tmp_path / "out").glob("*.report.json"))


def test_pdf_leak_is_reported_and_deleted(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.pdf"
    _pdf(src)
    engine = _engine()
    scan = scan_path(src, engine)
    for hit in scan.hits:
        if hit.text == "ABC-2026-002":
            hit.rects = []  # writer has nowhere to redact
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error and "1\u00a0处写出后仍可见" in res.error
    assert not any((tmp_path / "out").glob("*.pdf"))


def test_unchecked_hit_is_not_a_leftover(tmp_path: Path):
    src = tmp_path / "a.docx"
    _docx(src)
    engine = _engine()
    scan = scan_path(src, engine)
    scan.hits[0].enabled = False
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest.is_file()


def test_without_engine_uses_hit_texts(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.docx"
    _docx(src)
    scan = scan_path(src, _engine())
    _skip_first_paragraph_hit(monkeypatch)
    [res] = write_scans([scan], tmp_path / "out")
    assert res.error and "写出后仍可见" in res.error


def test_cli_leak_exits_nonzero_and_deletes(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / "in.docx"
    doc = Document()
    doc.add_paragraph("Project ABC-2026-001")
    doc.save(str(src))
    monkeypatch.setattr(
        docx_redactor, "_redact_paragraph", lambda p, e: [h.category for h in e.find_hits(p.text)]
    )
    outdir = tmp_path / "out"
    code = main([str(src), "-r", str(RULES), "-o", str(outdir)])
    assert code == 1
    assert "写出后仍可见" in capsys.readouterr().err
    assert not (outdir / "in.redacted.docx").exists()
    assert not (outdir / "in.report.json").exists()


def _short_code_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Sponsor SP and Fictizumab", fontsize=11)
    doc.save(str(path))
    doc.close()


def _short_code_engine():
    # "SP" is narrow, so the writer falls back to the short code [SP] and adds
    # a legend page. Neither the code nor the legend is original text.
    return build_engine([], [WordlistRule(category="SPONSOR", terms=["SP"], case_sensitive=True)])


def test_short_code_and_legend_are_not_leftovers(tmp_path: Path):
    src = tmp_path / "s.pdf"
    _short_code_pdf(src)
    engine = _short_code_engine()
    scan = scan_path(src, engine)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest is not None and res.dest.is_file()
    assert res.short_count == 1


def test_cli_short_code_is_not_a_leftover(tmp_path: Path):
    from doc_redactor.verify_output import count_leftovers
    from doc_redactor.pdf_redactor import redact_pdf

    src = tmp_path / "s.pdf"
    _short_code_pdf(src)
    engine = _short_code_engine()
    out = tmp_path / "s.redacted.pdf"
    counts = redact_pdf(src, out, engine)
    assert counts.short_count == 1
    labels = {engine.label(category) for category in counts}
    assert count_leftovers(out, engine=engine, labels=labels, short_codes=True) == 0
    # A real leftover is still found on a page that also has short codes.
    assert count_leftovers(src, engine=engine, labels=labels, short_codes=True) == 1


def test_cli_leak_blocks_only_that_file(tmp_path: Path, monkeypatch, capsys):
    leak = tmp_path / "leak.docx"
    doc = Document()
    doc.add_paragraph("Project ABC-2026-001")
    doc.save(str(leak))
    ok = tmp_path / "ok.pdf"
    _pdf(ok)
    monkeypatch.setattr(
        docx_redactor, "_redact_paragraph", lambda p, e: [h.category for h in e.find_hits(p.text)]
    )
    outdir = tmp_path / "out"
    code = main([str(leak), str(ok), "-r", str(RULES), "-o", str(outdir)])
    assert code == 1
    err = capsys.readouterr().err
    assert "leak.docx: 1\u00a0处写出后仍可见" in err
    assert "错误" not in err
    assert not (outdir / "leak.redacted.docx").exists()
    assert (outdir / "ok.redacted.pdf").is_file()


def test_unlocated_file_is_blocked_once_not_rechecked(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.pdf"
    _pdf(src)
    engine = _engine()
    scan = scan_path(src, engine)
    scan.unlocated_count = 2
    calls = []
    monkeypatch.setattr(scan_mod, "count_leftovers", lambda *a, **k: calls.append(1) or 0)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error and "2 处无法定位" in res.error
    assert "写出后仍可见" not in res.error
    assert calls == []
    assert not any((tmp_path / "out").glob("*.pdf"))


def test_cli_unlocated_reports_only_unlocated(tmp_path: Path, monkeypatch, capsys):
    import doc_redactor.pdf_redactor as pdf_redactor

    src = tmp_path / "in.pdf"
    _pdf(src)
    monkeypatch.setattr(pdf_redactor, "locate_text_hits", lambda page, found, tp: [[] for _ in found])
    outdir = tmp_path / "out"
    code = main([str(src), "-r", str(RULES), "-o", str(outdir)])
    err = capsys.readouterr().err
    assert code == 1
    assert "无法定位" in err and "写出后仍可见" not in err
    assert not (outdir / "in.redacted.pdf").exists()


# --- Round 2: the check itself must fail closed. -----------------------------


def _ok_pdf(path: Path) -> None:
    _pdf(path)


def test_verify_error_is_treated_as_leftover(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.docx"
    _docx(src)
    engine = _engine()
    scan = scan_path(src, engine)

    def boom(*_a, **_k):
        raise RuntimeError("checker broke")

    monkeypatch.setattr(scan_mod, "count_leftovers", boom)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.dest is None and res.error
    assert "核对出错" in res.error
    assert not any((tmp_path / "out").glob("*.docx"))
    assert not any((tmp_path / "out").glob("*.report.json"))


def test_verify_oserror_blocks_only_that_file(tmp_path: Path, monkeypatch):
    a = tmp_path / "a.docx"
    _docx(a)
    b = tmp_path / "b.pdf"
    _ok_pdf(b)
    engine = _engine()
    scans = [scan_path(a, engine), scan_path(b, engine)]
    real = scan_mod.count_leftovers

    def flaky(path, **kwargs):
        if Path(path).suffix == ".docx":
            raise PermissionError("locked")
        return real(path, **kwargs)

    monkeypatch.setattr(scan_mod, "count_leftovers", flaky)
    res_a, res_b = write_scans(scans, tmp_path / "out", engine=engine)
    assert res_a.dest is None and "核对出错" in res_a.error
    assert "输出文件夹不能写入" not in res_a.error
    assert res_b.error is None and res_b.dest.is_file()
    assert not (tmp_path / "out" / "a.redacted.docx").exists()


def _block_unlink(monkeypatch, suffix: str):
    real = Path.unlink

    def stubborn(self, *args, **kwargs):
        if self.name.endswith(suffix):
            raise PermissionError("in use")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stubborn)


def test_delete_failure_says_delete_by_hand(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.docx"
    _docx(src)
    engine = _engine()
    scan = scan_path(src, engine)
    _skip_first_paragraph_hit(monkeypatch)
    _block_unlink(monkeypatch, ".redacted.docx")
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.dest is None and res.error
    assert "删除失败" in res.error and "手动删除" in res.error
    assert "a.redacted.docx" in res.error
    assert "没有留下含原文的文件" not in res.error


def test_cli_verify_error_exits_nonzero_and_deletes(tmp_path: Path, monkeypatch, capsys):
    import doc_redactor.cli as cli_mod

    src = tmp_path / "in.docx"
    doc = Document()
    doc.add_paragraph("Project ABC-2026-001")
    doc.save(str(src))

    def boom(*_a, **_k):
        raise OSError("checker broke")

    monkeypatch.setattr(cli_mod, "count_leftovers", boom)
    outdir = tmp_path / "out"
    code = main([str(src), "-r", str(RULES), "-o", str(outdir)])
    assert code == 1
    err = capsys.readouterr().err
    assert "核对出错" in err
    assert not (outdir / "in.redacted.docx").exists()
    assert not (outdir / "in.report.json").exists()


def test_cli_delete_failure_names_the_file(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / "in.docx"
    doc = Document()
    doc.add_paragraph("Project ABC-2026-001")
    doc.save(str(src))
    monkeypatch.setattr(
        docx_redactor, "_redact_paragraph", lambda p, e: [h.category for h in e.find_hits(p.text)]
    )
    _block_unlink(monkeypatch, ".redacted.docx")
    outdir = tmp_path / "out"
    code = main([str(src), "-r", str(RULES), "-o", str(outdir)])
    assert code == 1
    err = capsys.readouterr().err
    assert "删除失败" in err and "in.redacted.docx" in err
    assert "没有留下含原文的文件" not in err


# --- Round 2: rescan the real output, drop only hits inside a placeholder. ---


def test_replacement_inside_sensitive_term_is_still_caught(tmp_path: Path, monkeypatch):
    src = tmp_path / "h.docx"
    doc = Document()
    doc.add_paragraph("第一处 星辰海医院")
    doc.add_paragraph("第二处 星辰海医院")
    doc.save(str(src))
    engine = build_engine([], [WordlistRule(category="SITE", terms=["星辰海医院"], case_sensitive=True)])
    scan = scan_path(src, engine)
    _skip_first_paragraph_hit(monkeypatch)
    [res] = write_scans([scan], tmp_path / "out", replacements={"SITE": "医院"}, engine=engine)
    assert res.error and "写出后仍可见" in res.error
    assert not any((tmp_path / "out").glob("*.docx"))


def _subject_engine():
    return build_engine(
        [RegexRule(name="SUBJECT", pattern=r"Subject\s+\d{3}", flags=0)],
        [WordlistRule(category="SPONSOR", terms=["Acme Pharma"], case_sensitive=True)],
    )


def test_placeholder_does_not_invent_a_hit_docx(tmp_path: Path):
    src = tmp_path / "s.docx"
    doc = Document()
    doc.add_paragraph("Subject Acme Pharma 001")
    doc.save(str(src))
    engine = _subject_engine()
    scan = scan_path(src, engine)
    assert [h.text for h in scan.hits] == ["Acme Pharma"]
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest is not None and res.dest.is_file()


def test_placeholder_does_not_invent_a_hit_pdf(tmp_path: Path):
    src = tmp_path / "s.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Subject Acme Pharma 001")
    d.save(str(src))
    d.close()
    engine = _subject_engine()
    scan = scan_path(src, engine)
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest is not None and res.dest.is_file()


def test_hit_partly_inside_placeholder_counts():
    from doc_redactor.verify_output import leftover_hits_in_unit

    engine = build_engine([], [WordlistRule(category="SITE", terms=["星辰海医院"], case_sensitive=True)])
    assert leftover_hits_in_unit("甲 星辰海医院 乙", engine=engine, labels=["医院"]) == ["星辰海医院"]
    assert leftover_hits_in_unit("甲 医院 乙", engine=engine, labels=["医院"]) == []


def test_short_code_span_only_when_writer_used_short_codes():
    from doc_redactor.verify_output import leftover_hits_in_unit

    engine = _short_code_engine()
    assert leftover_hits_in_unit("Sponsor [SP] x", engine=engine, short_codes=True) == []
    assert leftover_hits_in_unit("Sponsor [SP] x", engine=engine, short_codes=False) == ["SP"]
    # Outside any code it still counts.
    assert leftover_hits_in_unit("Sponsor SP [SP]", engine=engine, short_codes=True) == ["SP"]


def test_legend_page_is_handled_by_spans_not_dropped(tmp_path: Path):
    from doc_redactor.pdf_redactor import append_legend
    from doc_redactor.verify_output import count_leftovers

    path = tmp_path / "l.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Body text")
    append_legend(d, ["[SP] = 申办方（[申办方]）"])
    # A leftover that somehow sits on the legend page is still seen.
    d[-1].insert_text((72, 300), "Acme Pharma")
    d.save(str(path))
    d.close()
    engine = build_engine([], [WordlistRule(category="SPONSOR", terms=["Acme Pharma", "SP"], case_sensitive=True)])
    assert count_leftovers(path, engine=engine, labels=["[申办方]"], short_codes=True) == 1


def test_leftover_message_keeps_count_with_unit():
    from doc_redactor.verify_output import MSG_LEFTOVER

    assert "{n}\u00a0处" in MSG_LEFTOVER


def test_leftover_banner_does_not_suggest_retry():
    from doc_redactor.backpath import partial_banner
    from doc_redactor.verify_output import MSG_LEFTOVER

    text = partial_banner([("a.docx", MSG_LEFTOVER.format(n=1))])
    assert "重试失败的" not in text
    assert "返回修改" in text
    # An ordinary failure still offers retry.
    assert "重试失败的" in partial_banner([("b.docx", "输出文件夹里这个文件正被别的程序占用")])


def test_cli_placeholder_does_not_invent_a_hit(tmp_path: Path):
    from doc_redactor.pdf_redactor import redact_pdf
    from doc_redactor.verify_output import count_leftovers, original_hit_texts

    src = tmp_path / "s.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Subject Acme Pharma 001")
    d.save(str(src))
    d.close()
    engine = _subject_engine()
    out = tmp_path / "s.redacted.pdf"
    redact_pdf(src, out, engine)
    known = original_hit_texts(src, engine)
    assert count_leftovers(out, engine=engine, labels=["[SPONSOR]"], known=known) == 0
    # The same check still sees the original when nothing was removed.
    assert count_leftovers(src, engine=engine, labels=["[SPONSOR]"], known=known) == 1


# --- Word tracked changes: the check counts hits the way the scanner does ---
# A paragraph with a tracked deletion is read twice (as it reads now, and as
# it read before the changes). A hit only in kept text belongs to the first
# reading; a hit that touches deleted text belongs to the second. Each hit is
# counted once.

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _del_run(text: str):
    from docx.oxml import parse_xml

    return parse_xml(
        f'<w:del xmlns:w="{_W}" w:id="9" w:author="Tester" w:date="2026-01-01T00:00:00Z">'
        f'<w:r><w:delText xml:space="preserve">{text}</w:delText></w:r></w:del>'
    )


def _revision_docx(path: Path) -> None:
    doc = Document()
    # Kept hit next to a deletion.
    p = doc.add_paragraph("Sponsor Acme Pharma ")._p
    p.append(_del_run("old words"))
    # Hit split between kept text and deleted text.
    p = doc.add_paragraph("Partner Acme ")._p
    p.append(_del_run("Pharma"))
    doc.add_paragraph("Project ABC-2026-001")
    doc.save(str(path))


def _live_text(path: Path) -> str:
    return "\n".join(p.text for p in Document(str(path)).paragraphs)


def test_unchecked_kept_hit_beside_deletion_is_not_blocked(tmp_path: Path):
    src = tmp_path / "rev.docx"
    _revision_docx(src)
    engine = _engine()
    scan = scan_path(src, engine)
    kept = [h for h in scan.hits if h.text == "Acme Pharma" and "修订删除" not in h.location]
    assert len(kept) == 1
    kept[0].enabled = False
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest is not None and res.dest.is_file()
    assert "Sponsor Acme Pharma" in _live_text(res.dest)
    assert "ABC-2026-001" not in _live_text(res.dest)


def test_unchecked_hit_across_kept_and_deleted_is_not_blocked(tmp_path: Path):
    src = tmp_path / "rev.docx"
    _revision_docx(src)
    engine = _engine()
    scan = scan_path(src, engine)
    cross = [h for h in scan.hits if "修订删除" in h.location]
    assert len(cross) == 1
    cross[0].enabled = False
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error is None and res.dest is not None and res.dest.is_file()


def test_revision_hits_counted_once(tmp_path: Path):
    from doc_redactor.verify_output import count_leftovers, original_hit_texts

    src = tmp_path / "rev.docx"
    _revision_docx(src)
    engine = _engine()
    # Kept hit: 1 (not 2), split hit: 1, project id: 1. Same as the scanner.
    assert count_leftovers(src, engine=engine) == 3 == len(scan_path(src, engine).hits)
    assert sorted(original_hit_texts(src, engine)) == ["ABC-2026-001", "Acme Pharma", "Acme Pharma"]
    # Without an engine (targets only) the same split applies.
    assert count_leftovers(src, targets=["Acme Pharma"]) == 2


def test_cli_revision_docx_passes(tmp_path: Path):
    src = tmp_path / "rev.docx"
    _revision_docx(src)
    out = tmp_path / "out"
    assert main([str(src), "-r", str(RULES), "-o", str(out)]) == 0
    assert any(out.glob("*.docx"))


# --- A leftover joined with nearby text into a hit the original never had ---
# The "known" filter drops a hit only when it neither contains an original hit
# text nor is part of one. Each case below is a real leftover that must count.


def _known_engine(words: dict, regex: dict | None = None):
    return build_engine(
        [RegexRule(name=n, pattern=p, flags=0) for n, p in (regex or {}).items()],
        [WordlistRule(category=c, terms=t, case_sensitive=True) for c, t in words.items()],
    )


def test_nested_cjk_term_left_after_only_tail_covered():
    from doc_redactor.verify_output import leftover_hits_in_unit

    engine = _known_engine({"SPONSOR": ["星辰海"], "SITE": ["星辰海医院"]})
    out = "研究中心 星辰海[SITE] 入组"
    assert leftover_hits_in_unit(out, engine=engine, labels=["[SITE]"], known=["星辰海医院"]) == ["星辰海"]


def test_nested_ascii_term_left_after_only_tail_covered():
    from doc_redactor.verify_output import leftover_hits_in_unit

    engine = _known_engine({"SPONSOR": ["Acme", "Acme Pharma"]})
    out = "Sponsor Acme [SPONSOR] visit"
    assert leftover_hits_in_unit(out, engine=engine, labels=["[SPONSOR]"], known=["Acme Pharma"]) == ["Acme"]


def test_pattern_hit_left_after_longer_term_half_covered():
    from doc_redactor.verify_output import leftover_hits_in_unit

    engine = _known_engine({"SITE": ["Subject 002 Site"]}, {"SUBJECT": r"Subject\s+\d{3}"})
    out = "visit Subject 002 [SITE] today"
    assert leftover_hits_in_unit(out, engine=engine, labels=["[SITE]"], known=["Subject 002 Site"]) == [
        "Subject 002"
    ]


def test_leftover_joined_with_neighbour_after_placeholder_moved():
    from doc_redactor.verify_output import leftover_hits_in_unit

    # PDF text extraction put the placeholder for Zeta at the end of the page,
    # so the leaked "Acme" now reads as "Acme  Pharma".
    engine = _known_engine({"SPONSOR": ["Acme", "Acme Pharma"], "SITE": ["Zeta"]})
    out = "Sponsor Acme  Pharma visit\n[SITE]\n"
    hits = leftover_hits_in_unit(out, engine=engine, labels=["[SITE]"], known=["Acme", "Zeta"])
    assert len(hits) == 1 and "Acme" in hits[0]


def test_leftover_joined_with_moved_short_code():
    from doc_redactor.verify_output import leftover_hits_in_unit

    engine = _known_engine({"SITE": ["Zeta"]}, {"CODE": r"ZZ-\d+(?:\s+\S+)?"})
    out = "intro  middle\nZZ-77\n[R2]\n"
    hits = leftover_hits_in_unit(out, engine=engine, short_codes=True, known=["Zeta", "ZZ-77"])
    assert len(hits) == 1 and hits[0].startswith("ZZ-77")


def test_leftover_joined_with_neighbour_pdf_end_to_end(tmp_path: Path):
    src = tmp_path / "f5.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Sponsor Acme Zeta Pharma visit")
    d.save(str(src))
    d.close()
    engine = _known_engine({"SPONSOR": ["Acme", "Acme Pharma"], "SITE": ["Zeta"]})
    scan = scan_path(src, engine)
    for hit in scan.hits:
        if hit.text == "Acme":
            hit.rects = []  # the writer misses this one
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error and "写出后仍可见" in res.error
    assert not any((tmp_path / "out").glob("*.pdf"))


def test_original_bracket_code_is_not_taken_for_a_short_code(tmp_path: Path):
    from doc_redactor.pdf_redactor import append_legend
    from doc_redactor.verify_output import count_leftovers

    # "[R123456]" was in the original; the writer only used [SP].
    path = tmp_path / "c.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Ref [R123456] by [SP]")
    append_legend(d, ["[SP] = 申办方（[申办方]）"])
    d.save(str(path))
    d.close()
    engine = build_engine(
        [RegexRule(name="RECORD", pattern=r"R\d{6}", flags=0)],
        [WordlistRule(category="SPONSOR", terms=["SP"], case_sensitive=True)],
    )
    assert count_leftovers(path, engine=engine, labels=["[申办方]"], short_codes=True) == 1


def test_replacement_text_left_in_actual_text_is_a_leftover(tmp_path: Path):
    # Glyphs are removed but the tagged text ("ActualText") still holds the
    # original. Text extraction reads it, so it is a real leftover.
    src = tmp_path / "at.pdf"
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 72), "Sponsor Acme Pharma visit")
    xref = page.get_contents()[0]
    body = d.xref_stream(xref)
    d.update_stream(xref, b"/Span <</ActualText (Sponsor Acme Pharma visit)>> BDC\n" + body + b"\nEMC\n")
    d.save(str(src))
    d.close()
    engine = _engine()
    scan = scan_path(src, engine)
    assert [h.text for h in scan.hits] == ["Acme Pharma"]
    [res] = write_scans([scan], tmp_path / "out", engine=engine)
    assert res.error and "写出后仍可见" in res.error
    assert not any((tmp_path / "out").glob("*.pdf"))
