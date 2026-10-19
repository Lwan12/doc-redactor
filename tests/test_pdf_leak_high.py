"""PDF 漏遮（出品审 AI 可读性报告第 1–4 条）：结构树和标记内容里的实际文字、
页面外和裁切框外的文字、连字符断行的词、链接地址，以及自定义文档属性、页标签、图层名。

每条都用解析器读输出文件，断言编造的敏感词不再出现。全部是编造数据。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pymupdf
import pytest

from doc_redactor.engine import RegexRule, WordlistRule, build_engine
from doc_redactor.pdf_redactor import redact_pdf
from doc_redactor.scan import scan_path, write_scans

WORDS = ("Acme Pharma", "Fictizumab", "星辰海", "ABC-2026-001")


def _engine():
    return build_engine(
        [RegexRule(name="PROJECT_ID", pattern=r"(?<![A-Za-z0-9])ABC-\d{4}-\d{3}(?![A-Za-z0-9])", flags=0)],
        [
            WordlistRule(category="SPONSOR", terms=["Acme Pharma", "星辰海"], case_sensitive=False),
            WordlistRule(category="DRUG", terms=["Fictizumab"], case_sensitive=True),
        ],
    )


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _join_hyphens(text: str) -> str:
    # pdftotext 默认的读法：行尾连字符后接下一行，拼回一个词。
    return re.sub(r"(?<=\w)[-\u00ad\u2010]\n(?=\w)", "", text)


def _decode_pdf_strings(source: str) -> str:
    """把对象源码里的十六进制串（含 UTF-16BE）也解成文字，原样串保留。"""
    parts = [source]
    for match in re.finditer(r"<([0-9A-Fa-f\s]{4,})>", source):
        raw = bytes.fromhex(re.sub(r"\s", "", match.group(1)) + ("0" if len(re.sub(r"\s", "", match.group(1))) % 2 else ""))
        if raw.startswith(b"\xfe\xff"):
            parts.append(raw[2:].decode("utf-16-be", "replace"))
        parts.append(raw.decode("latin1"))
    return "\n".join(parts)


def _pdftotext(path: Path) -> str:
    if shutil.which("pdftotext") is None:
        return ""
    try:
        probe = subprocess.run(["pdftotext", "-v"], capture_output=True)
    except OSError:
        return ""
    if b"Poppler" not in (probe.stdout + probe.stderr) and b"poppler" not in (probe.stdout + probe.stderr):
        return ""
    result = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True)
    return result.stdout.decode("utf-8", "replace")


def read_pdf_every_way(path: Path) -> dict[str, str]:
    """输出文件的多种读法：默认取字、全页不裁剪、拼回连字符、链接、页标签、图层、元数据、全部对象源码。"""
    document = pymupdf.open(path)
    try:
        views: dict[str, str] = {}
        views["pymupdf text"] = "".join(page.get_text("text") for page in document)
        unclipped = "".join(
            page.get_text(
                "text",
                flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_MEDIABOX_CLIP,
                clip=pymupdf.INFINITE_RECT(),
            )
            for page in document
        )
        views["pymupdf 全页"] = unclipped
        views["拼回连字符"] = _join_hyphens(unclipped)
        views["links"] = repr([page.get_links() for page in document])
        views["page labels"] = repr(document.get_page_labels()) + " ".join(page.get_label() for page in document)
        views["ocg"] = repr(document.get_ocgs())
        views["metadata"] = repr(document.metadata)
        objects = []
        for xref in range(1, document.xref_length()):
            try:
                objects.append(document.xref_object(xref, compressed=False))
                if document.xref_is_stream(xref):
                    objects.append((document.xref_stream(xref) or b"").decode("latin1"))
            except Exception:
                continue
        objects.append(document.pdf_trailer())
        views["全部对象"] = _decode_pdf_strings("\n".join(objects))
    finally:
        document.close()
    views["pdftotext"] = _join_hyphens(_pdftotext(path))
    return views


def assert_no_words(path: Path, words=WORDS) -> None:
    for name, text in read_pdf_every_way(path).items():
        flat = _norm(text)
        for word in words:
            assert _norm(word) not in flat, f"{name} 读出了编造词 #{WORDS.index(word) + 1 if word in WORDS else 0}"


def _write_both_ways(source: Path, tmp_path: Path) -> list[Path]:
    """命令行写出和界面写出两条路都走一遍，返回两个输出。"""
    engine = _engine()
    cli_out = tmp_path / "cli" / f"{source.stem}.redacted.pdf"
    redact_pdf(source, cli_out, engine)
    scan = scan_path(source, engine)
    assert scan.status == "ok", scan.error
    results = write_scans([scan], tmp_path / "gui", engine=engine)
    assert results[0].dest is not None, results[0].error
    return [cli_out, results[0].dest]


def _wrap_contents(document, page, prefix: bytes, suffix: bytes = b"\nEMC\n") -> None:
    for xref in page.get_contents():
        stream = document.xref_stream(xref)
        document.update_stream(xref, prefix + b"\n" + stream + suffix)


def _tagged_pdf(path: Path) -> None:
    """可见字和 ActualText 一样（常见的带标签 PDF），另有一段可见字是别的、ActualText 是原词。
    结构树里的 Figure 带 /Alt 原词。"""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "Sponsor Acme Pharma")
    document.save(path)
    document.close()
    document = pymupdf.open(path)
    page = document[0]
    _wrap_contents(document, page, b"/Span <</ActualText (Acme Pharma) /E (Acme Pharma)>> BDC")
    # 第二段：字形是「Zeta Corp」，实际文字写的是原词。
    extra = document.get_new_xref()
    document.update_object(extra, "<<>>")
    document.update_stream(
        extra,
        b"/Span <</ActualText <FEFF00460069006300740069007A0075006D00610062>>> BDC\n"
        b"BT /helv 11 Tf 1 0 0 1 72 680 Tm (Zeta Corp) Tj ET\nEMC\n",
    )
    contents = page.get_contents()
    document.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in contents + [extra]) + "]")
    root = document.get_new_xref()
    figure = document.get_new_xref()
    document.update_object(figure, f"<</Type/StructElem/S/Figure/P {root} 0 R/Alt(Acme Pharma ABC-2026-001)>>")
    document.update_object(root, f"<</Type/StructTreeRoot/K[{figure} 0 R]>>")
    catalog = document.pdf_catalog()
    document.xref_set_key(catalog, "StructTreeRoot", f"{root} 0 R")
    document.xref_set_key(catalog, "MarkInfo", "<</Marked true>>")
    data = document.tobytes()
    document.close()
    path.write_bytes(data)


def test_actualtext_alt_and_struct_tree_are_gone(tmp_path: Path):
    source = tmp_path / "tagged.pdf"
    _tagged_pdf(source)
    # 原件确实能读出这些词，否则测试没意义。
    raw_views = read_pdf_every_way(source)
    assert "acmepharma" in _norm(raw_views["pymupdf text"])
    assert "fictizumab" in _norm(raw_views["全部对象"])
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        document = pymupdf.open(output)
        catalog = document.pdf_catalog()
        assert document.xref_get_key(catalog, "StructTreeRoot")[0] == "null"
        # 可见的别的字还在，没被当成原词删掉。
        assert "Zeta Corp" in document[0].get_text()
        document.close()


def test_tagged_hit_is_located_not_blocked(tmp_path: Path):
    """ActualText 和字形一致时，命中要能定位并写出，不再报「无法定位」。"""
    source = tmp_path / "tagged.pdf"
    _tagged_pdf(source)
    scan = scan_path(source, _engine())
    assert scan.unlocated_count == 0
    assert [hit.category for hit in scan.hits] == ["SPONSOR"]


def test_optional_content_stays_marked(tmp_path: Path):
    """图层的 /OC 标记不能被一起去掉，否则隐藏图层会变成可见。"""
    source = tmp_path / "layer.pdf"
    document = pymupdf.open()
    ocg = document.add_ocg("Fictizumab layer 星辰海", on=False)
    page = document.new_page()
    page.insert_text((72, 100), "Sponsor Acme Pharma")
    page.insert_text((72, 140), "hidden note", oc=ocg)
    document.save(source)
    document.close()
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        document = pymupdf.open(output)
        streams = b"".join(document.xref_stream(x) for x in document[0].get_contents())
        assert b"/OC" in streams
        assert "hidden note" not in document[0].get_text()
        document.close()


def _offpage_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "Visible line")
    page.insert_text((72, -30), "Off Acme Pharma top")
    page.insert_text((72, 780), "Crop Fictizumab out")
    document.save(path)
    document.close()
    document = pymupdf.open(path)
    document[0].set_cropbox(pymupdf.Rect(0, 0, 595, 700))
    document.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    document.close()


def test_text_outside_page_and_cropbox_is_scanned_and_removed(tmp_path: Path):
    source = tmp_path / "offpage.pdf"
    _offpage_pdf(source)
    views = read_pdf_every_way(source)
    assert "acmepharma" in _norm(views["pymupdf 全页"])
    assert "fictizumab" in _norm(views["pymupdf 全页"])
    scan = scan_path(source, _engine())
    assert sorted(hit.category for hit in scan.hits) == ["DRUG", "SPONSOR"]
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        assert "Visible line" in pymupdf.open(output)[0].get_text()


def _hyphen_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "The study drug Fictizu-")
    page.insert_text((72, 114), "mab is given daily.")
    page.insert_text((72, 160), "Sponsor Acme Phar-")
    page.insert_text((72, 174), "ma, and a well-known term.")
    document.save(path)
    document.close()


def test_hyphen_broken_word_is_joined_before_matching(tmp_path: Path):
    source = tmp_path / "hyphen.pdf"
    _hyphen_pdf(source)
    assert "fictizumab" in _norm(read_pdf_every_way(source)["拼回连字符"])
    scan = scan_path(source, _engine())
    assert sorted(hit.category for hit in scan.hits) == ["DRUG", "SPONSOR"]
    assert scan.unlocated_count == 0
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        text = pymupdf.open(output)[0].get_text()
        assert "is given daily." in text
        assert "well-known" in text


def test_hyphen_join_needs_letters_on_both_sides():
    from doc_redactor.pdf_redactor import find_page_hits

    engine = _engine()
    # 行尾是减号但下一行是数字开头，不拼。
    assert find_page_hits(engine, "ABC-\n2026-001 x") == []
    hits = find_page_hits(engine, "drug Fictizu-\nmab daily")
    assert [(h.category, h.start, h.end) for h in hits] == [("DRUG", 5, 17)]


def _hidden_parts_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "Sponsor Acme Pharma")
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(72, 120, 300, 140),
            "uri": "https://acme-pharma.example/ABC-2026-001/Fictizumab",
        }
    )
    document.add_ocg("Fictizumab layer 星辰海", on=True)
    document.set_page_labels([{"startpage": 0, "prefix": "Acme Pharma-", "style": "D", "firstpagenum": 1}])
    document.save(path)
    document.close()
    document = pymupdf.open(path)
    info = document.xref_get_key(-1, "Info")
    if info[0] == "xref":
        info_xref = int(info[1].split()[0])
    else:
        info_xref = document.get_new_xref()
        document.update_object(info_xref, "<<>>")
        document.xref_set_key(-1, "Info", f"{info_xref} 0 R")
    document.xref_set_key(info_xref, "Sponsor", "(Acme Pharma)")
    document.xref_set_key(info_xref, "ContractNo", "(ABC-2026-001)")
    document.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    document.close()


def test_links_info_keys_page_labels_and_layer_names_are_cleared(tmp_path: Path):
    source = tmp_path / "hidden.pdf"
    _hidden_parts_pdf(source)
    views = read_pdf_every_way(source)
    assert "abc-2026-001" in _norm(views["links"])
    assert "acmepharma" in _norm(views["page labels"])
    assert "fictizumab" in _norm(views["ocg"])
    assert "abc-2026-001" in _norm(views["全部对象"])
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        document = pymupdf.open(output)
        assert document[0].get_links() == []
        document.close()


@pytest.mark.parametrize("words", [("Fictizumab",)])
def test_reader_helper_sees_utf16_strings(tmp_path: Path, words):
    """自检：读法能解出 UTF-16 十六进制串，第一条测试的断言才有分量。"""
    source = tmp_path / "tagged.pdf"
    _tagged_pdf(source)
    with pytest.raises(AssertionError):
        assert_no_words(source, words)
