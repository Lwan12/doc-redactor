"""Word 漏遮（出品审 AI 可读性报告第 9、11、14、17–20 条）：脚注、尾注、批注正文，
图片替代文字，超链接地址、书签名、域代码，嵌入的 Excel 等对象。

每条都解压输出文件，读全部 XML 的文字和属性、内层压缩包和二进制部件，
再用 python-docx（以及装了的话 pandoc）读一遍，断言编造的敏感词不再出现。全部是编造数据。
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest
from docx import Document
from lxml import etree

from doc_redactor.docx_redactor import iter_document_paragraphs, redact_docx
from doc_redactor.engine import RegexRule, WordlistRule, build_engine
from doc_redactor.scan import scan_path, write_scans
from helpers import needs_tesseract

# Pictures need Tesseract now: without it the write is refused, not done with the images skipped.
# 本机没装跳过；CI 设了 REQUIRE_TESSERACT=1 时找不到就失败（tests/helpers.py）。
needs_ocr = needs_tesseract

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORDS = ("Acme Pharma", "Acme_Pharma", "Fictizumab", "星辰海", "ABC-2026-001", "ABC_2026_001")
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa1\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


def _xml_text(data: bytes) -> str:
    """一个 XML 部件里的全部文字和全部属性值（实体已展开）。"""
    root = etree.fromstring(data)
    parts = []
    for element in root.iter():
        if element.text:
            parts.append(element.text)
        parts.extend(element.attrib.values())
    return "\n".join(parts)


def _zip_views(data: bytes, prefix: str = "") -> dict[str, str]:
    views = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            payload = archive.read(name)
            key = prefix + name
            if name.endswith((".xml", ".rels")):
                text = _xml_text(payload)
                views[key] = text + "\n" + unquote(text)
            elif payload[:2] == b"PK":
                views.update(_zip_views(payload, key + "!"))
            elif not name.lower().endswith((".png", ".jpg", ".jpeg")):
                views[key] = payload.decode("utf-16-le", "replace") + payload.decode("latin1")
    return views


def read_docx_every_way(path: Path) -> dict[str, str]:
    views = _zip_views(path.read_bytes())
    document = Document(str(path))
    views["python-docx 段落"] = "\n".join(p.text for p in document.paragraphs)
    if shutil.which("pandoc"):
        result = subprocess.run(["pandoc", "-f", "docx", "-t", "markdown", str(path)], capture_output=True)
        views["pandoc"] = result.stdout.decode("utf-8", "replace")
    return views


def assert_no_words(path: Path, words=WORDS) -> None:
    for name, text in read_docx_every_way(path).items():
        flat = _norm(text)
        for word in words:
            assert _norm(word) not in flat, f"{name} 读出了编造词 #{words.index(word) + 1}"


def _write_both_ways(source: Path, tmp_path: Path) -> list[Path]:
    engine = _engine()
    cli_out = tmp_path / "cli" / f"{source.stem}.redacted.docx"
    redact_docx(source, cli_out, engine)
    scan = scan_path(source, engine)
    assert scan.status == "ok", scan.error
    results = write_scans([scan], tmp_path / "gui", engine=engine)
    assert results[0].dest is not None, results[0].error
    return [cli_out, results[0].dest]


# --- 构造输入 -----------------------------------------------------------------

_CT_NOTES = {
    "footnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "endnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
}
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _note_xml(kind: str, text: str) -> bytes:
    tag = kind[:-1]
    return (
        f'<w:{kind} xmlns:w="{W}">'
        f'<w:{tag} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{tag}>'
        f'<w:{tag} w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:{tag}>'
        f'<w:{tag} w:id="1"><w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:{tag}>'
        f"</w:{kind}>"
    ).encode("utf-8")


def _rewrite(path: Path, edit) -> None:
    """按部件改写一个 docx。edit(files) 原地改 files 字典。"""
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    edit(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    path.write_bytes(buffer.getvalue())


def _add_rel(files, rels_name: str, rid: str, reltype: str, target: str, external: bool = False) -> None:
    mode = ' TargetMode="External"' if external else ""
    rel = f'<Relationship Id="{rid}" Type="{reltype}" Target="{target}"{mode}/>'.encode()
    files[rels_name] = files[rels_name].replace(b"</Relationships>", rel + b"</Relationships>")


def _add_type(files, part: str, ctype: str) -> None:
    item = f'<Override PartName="/{part}" ContentType="{ctype}"/>'.encode()
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(b"</Types>", item + b"</Types>")


def _add_default(files, ext: str, ctype: str) -> None:
    if f'Extension="{ext}"'.encode() in files["[Content_Types].xml"]:
        return
    item = f'<Default Extension="{ext}" ContentType="{ctype}"/>'.encode()
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(b"</Types>", item + b"</Types>")


def _body_xml(files, extra: str) -> None:
    files["word/document.xml"] = files["word/document.xml"].replace(
        b"<w:sectPr", extra.encode("utf-8") + b"<w:sectPr", 1
    )


def _base(path: Path) -> None:
    document = Document()
    document.add_paragraph("Sponsor Acme Pharma in body.")
    document.save(str(path))


def _notes_docx(path: Path) -> None:
    _base(path)

    def edit(files):
        for kind, text in (("footnotes", "Footnote: Acme Pharma, Fictizumab"), ("endnotes", "尾注：星辰海 ABC-2026-001")):
            files[f"word/{kind}.xml"] = _note_xml(kind, text)
            _add_type(files, f"word/{kind}.xml", _CT_NOTES[kind])
            _add_rel(files, "word/_rels/document.xml.rels", f"rIdN{kind}", _RT + kind, f"{kind}.xml")
        _body_xml(
            files,
            '<w:p><w:r><w:t>see note</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r>'
            '<w:r><w:endnoteReference w:id="1"/></w:r></w:p>',
        )

    _rewrite(path, edit)


def test_footnotes_and_endnotes_are_redacted(tmp_path: Path):
    source = tmp_path / "notes.docx"
    _notes_docx(source)
    with pytest.raises(AssertionError):
        assert_no_words(source)
    scan = scan_path(source, _engine())
    locations = sorted(hit.location for hit in scan.hits)
    assert any(loc.startswith("脚注") for loc in locations)
    assert any(loc.startswith("尾注") for loc in locations)
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        with zipfile.ZipFile(output) as archive:
            assert "[SPONSOR]" in archive.read("word/footnotes.xml").decode()
            assert "[SPONSOR]" in archive.read("word/endnotes.xml").decode()


def _alt_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("Sponsor Acme Pharma in body.")
    document.add_picture(io.BytesIO(PNG))
    document.save(str(path))

    def edit(files):
        xml = files["word/document.xml"].decode("utf-8")
        xml = re.sub(
            r"<wp:docPr ([^>]*?)/>",
            '<wp:docPr id="99" name="Acme Pharma logo" descr="Alt: Acme Pharma Fictizumab" title="星辰海 ABC-2026-001"/>',
            xml,
        )
        xml = re.sub(
            r"<pic:cNvPr ([^>]*?)/>",
            '<pic:cNvPr id="0" name="Fictizumab.png" descr="Acme Pharma chart"/>',
            xml,
        )
        files["word/document.xml"] = xml.encode("utf-8")

    _rewrite(path, edit)


@needs_ocr
def test_image_alt_text_is_redacted(tmp_path: Path):
    source = tmp_path / "alt.docx"
    _alt_docx(source)
    with pytest.raises(AssertionError):
        assert_no_words(source)
    scan = scan_path(source, _engine())
    assert sum(1 for hit in scan.hits if hit.location.startswith("图片替代文字")) >= 5
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        assert len(Document(str(output)).inline_shapes) == 1


def _links_docx(path: Path) -> None:
    _base(path)

    def edit(files):
        _add_rel(
            files,
            "word/_rels/document.xml.rels",
            "rIdL1",
            _RT + "hyperlink",
            "https://example.org/ABC-2026-001/%E6%98%9F%E8%BE%B0%E6%B5%B7?drug=Fictizumab",
            external=True,
        )
        _body_xml(
            files,
            '<w:p><w:hyperlink r:id="rIdL1" w:tooltip="Acme Pharma site"><w:r><w:t>web link</w:t></w:r></w:hyperlink></w:p>'
            '<w:p><w:bookmarkStart w:id="7" w:name="Acme_Pharma_ABC_2026_001"/><w:r><w:t>target</w:t></w:r>'
            '<w:bookmarkEnd w:id="7"/><w:bookmarkStart w:id="8" w:name="_Toc123456"/><w:bookmarkEnd w:id="8"/></w:p>'
            '<w:p><w:hyperlink w:anchor="Acme_Pharma_ABC_2026_001"><w:r><w:t>jump</w:t></w:r></w:hyperlink></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> HYPERLINK "https://example.org/Fict</w:instrText></w:r>'
            '<w:r><w:instrText xml:space="preserve">izumab" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>here</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> REF Acme_Pharma_ABC_2026_001 \\h </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>ref</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
            '<w:p><w:fldSimple w:instr=" QUOTE &quot;Acme Pharma&quot; "><w:r><w:t>quoted</w:t></w:r></w:fldSimple></w:p>',
        )

    _rewrite(path, edit)


def test_hyperlink_targets_bookmarks_and_field_codes_are_redacted(tmp_path: Path):
    source = tmp_path / "links.docx"
    _links_docx(source)
    with pytest.raises(AssertionError):
        assert_no_words(source)
    scan = scan_path(source, _engine())
    wheres = {hit.location.split(" ")[0] for hit in scan.hits}
    assert {"超链接地址", "域代码"} <= wheres
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")
        names = re.findall(r'w:bookmarkStart [^>]*w:name="([^"]+)"', xml)
        assert "_Toc123456" in names
        renamed = [name for name in names if name != "_Toc123456"]
        assert len(renamed) == 1
        # 内部跳转和 REF 域跟着改名，链接不断。
        assert f'w:anchor="{renamed[0]}"' in xml
        assert f"REF {renamed[0]} " in xml
        # 域的显示文字还在。
        assert "here" in xml and "quoted" in xml


def _embed_docx(path: Path) -> None:
    _base(path)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as book:
        book.writestr("[Content_Types].xml", "<Types/>")
        book.writestr("xl/sharedStrings.xml", "<sst><si><t>Acme Pharma Fictizumab ABC-2026-001</t></si></sst>")

    def edit(files):
        files["word/embeddings/Microsoft_Excel_Worksheet.xlsx"] = inner.getvalue()
        files["word/embeddings/oleObject1.bin"] = "OLE 星辰海 Acme Pharma".encode("utf-16-le")
        files["word/media/preview1.png"] = PNG
        _add_default(files, "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        _add_default(files, "bin", "application/vnd.openxmlformats-officedocument.oleObject")
        _add_default(files, "png", "image/png")
        rels = "word/_rels/document.xml.rels"
        _add_rel(files, rels, "rIdE1", _RT + "package", "embeddings/Microsoft_Excel_Worksheet.xlsx")
        _add_rel(files, rels, "rIdE2", _RT + "oleObject", "embeddings/oleObject1.bin")
        _add_rel(files, rels, "rIdP1", _RT + "image", "media/preview1.png")
        obj = (
            '<w:p><w:r><w:object w:dxaOrig="1440" w:dyaOrig="720" '
            'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">'
            '<v:shape id="_x0000_i10{n}" type="#_x0000_t75" style="width:72pt;height:36pt" o:ole="">'
            '<v:imagedata r:id="rIdP1" o:title="Acme Pharma sheet"/></v:shape>'
            '<o:OLEObject Type="Embed" ProgID="{prog}" ShapeID="_x0000_i10{n}" DrawAspect="Content" '
            'ObjectID="_10{n}" r:id="{rid}"/></w:object></w:r></w:p>'
        )
        _body_xml(files, obj.format(n=1, prog="Excel.Sheet.12", rid="rIdE1") + obj.format(n=2, prog="Package", rid="rIdE2"))

    _rewrite(path, edit)


@needs_ocr
def test_embedded_objects_are_dropped_and_preview_kept(tmp_path: Path):
    source = tmp_path / "embed.docx"
    _embed_docx(source)
    with pytest.raises(AssertionError):
        assert_no_words(source)
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            xml = archive.read("word/document.xml").decode("utf-8")
            rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            types = archive.read("[Content_Types].xml").decode("utf-8")
        assert not any(name.startswith("word/embeddings/") for name in names)
        assert "OLEObject" not in xml
        assert "embeddings/" not in rels
        assert "embeddings" not in types
        # 预览图留着，文件里看得到一张图。
        assert "word/media/preview1.png" in names
        assert xml.count("v:imagedata") == 2
        Document(str(output))


def test_new_units_come_after_body_in_a_stable_order(tmp_path: Path):
    """扫描和写出按序号对齐：同一文件两次遍历得到一样的单元序列。"""
    source = tmp_path / "links.docx"
    _links_docx(source)
    first = [unit.text for unit in iter_document_paragraphs(Document(str(source)))]
    second = [unit.text for unit in iter_document_paragraphs(Document(str(source)))]
    assert first == second
    assert first[0] == "Sponsor Acme Pharma in body."


def test_unchecked_hit_in_footnote_stays(tmp_path: Path):
    """取消勾选只留下那一处，别的照遮。"""
    source = tmp_path / "notes.docx"
    _notes_docx(source)
    engine = _engine()
    scan = scan_path(source, engine)
    for hit in scan.hits:
        if hit.location.startswith("脚注") and hit.category == "DRUG":
            hit.enabled = False
    results = write_scans([scan], tmp_path / "gui", engine=engine)
    xml = zipfile.ZipFile(results[0].dest).read("word/footnotes.xml").decode()
    assert "Fictizumab" in xml
    assert "Acme Pharma" not in xml


def _obscure_docx(path: Path) -> None:
    """报告第 13、21 条：修订作者、批注作者、样式名、文档变量、模板路径、altChunk。"""
    document = Document()
    document.add_paragraph("Sponsor Acme Pharma in body.")
    document.styles.add_style("Fictizumab Heading", 1)
    document.add_paragraph("styled", style="Fictizumab Heading")
    document.save(str(path))

    def edit(files):
        _body_xml(
            files,
            '<w:p><w:ins w:id="31" w:author="Acme Pharma reviewer" w:date="2026-01-01T00:00:00Z">'
            '<w:r><w:t>added</w:t></w:r></w:ins></w:p>'
            '<w:altChunk r:id="rIdA1"/>',
        )
        files["word/afchunk.htm"] = "<html><body>Acme Pharma Fictizumab ABC-2026-001</body></html>".encode()
        _add_default(files, "htm", "application/xhtml+xml")
        _add_rel(files, "word/_rels/document.xml.rels", "rIdA1", _RT + "aFChunk", "afchunk.htm")
        settings = files["word/settings.xml"].decode("utf-8")
        settings = settings.replace(
            "</w:settings>",
            '<w:attachedTemplate r:id="rIdT1"/><w:docVars><w:docVar w:name="sponsor" w:val="星辰海 ABC-2026-001"/></w:docVars></w:settings>',
        )
        if "xmlns:r=" not in settings.split(">", 2)[1]:
            settings = settings.replace("<w:settings ", f'<w:settings xmlns:r="{R}" ', 1)
        files["word/settings.xml"] = settings.encode("utf-8")
        files["word/_rels/settings.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rIdT1" Type="{_RT}attachedTemplate" '
            'Target="file:///C:/Acme%20Pharma/Fictizumab.dotm" TargetMode="External"/></Relationships>'
        ).encode()

    _rewrite(path, edit)


def test_authors_style_names_docvars_template_and_altchunk(tmp_path: Path):
    source = tmp_path / "obscure.docx"
    _obscure_docx(source)
    with pytest.raises(AssertionError):
        assert_no_words(source)
    for output in _write_both_ways(source, tmp_path):
        assert_no_words(output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            xml = archive.read("word/document.xml").decode("utf-8")
        assert "word/afchunk.htm" not in names
        assert "altChunk" not in xml
        # 修订标记本身还在，只换了作者名。
        assert "<w:ins " in xml
        document = Document(str(output))
        assert document.paragraphs[1].style.name != "Fictizumab Heading"


# --- M33-1：外部链接地址整条换成占位符 -------------------------------------------

LINK_PLACEHOLDER = "https://redacted.invalid/"  # 写出时外部链接地址一律换成它
LINK_VARIANTS = ("acme-pharma", "acme_pharma", "acmepharma", "fictizumab", "abc-2026-001")


def _hyphen_links_docx(path: Path) -> None:
    """规则写的是「Acme Pharma」，链接里写成 acme-pharma（规则匹配不到），外加页眉里的链接和域代码。"""
    document = Document()
    document.add_paragraph("Sponsor Acme Pharma in body.")
    document.sections[0].header.add_paragraph("Header.")
    document.save(str(path))

    def edit(files):
        _add_rel(
            files, "word/_rels/document.xml.rels", "rIdH1", _RT + "hyperlink",
            "https://acme-pharma.example/trials/42?ref=acme_pharma", external=True,
        )
        _body_xml(
            files,
            '<w:p><w:hyperlink r:id="rIdH1"><w:r><w:t>portal</w:t></w:r></w:hyperlink></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> HYPERLINK "https://acme-pharma.exa</w:instrText></w:r>'
            '<w:r><w:instrText xml:space="preserve">mple/subject/7" \\o "tip" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>field link</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
            '<w:p><w:fldSimple w:instr=" HYPERLINK https://www.acmepharma.example/x "><w:r><w:t>simple</w:t></w:r></w:fldSimple></w:p>',
        )
        header = next(n for n in files if n.startswith("word/header") and n.endswith(".xml"))
        rels = header.replace("word/", "word/_rels/") + ".rels"
        files.setdefault(
            rels,
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
        )
        _add_rel(files, rels, "rIdH2", _RT + "hyperlink", "mailto:pi@acme-pharma.example", external=True)
        files[header] = files[header].replace(
            b"</w:hdr>",
            f'<w:p><w:hyperlink xmlns:r="{R}" r:id="rIdH2"><w:r><w:t>mail</w:t></w:r></w:hyperlink></w:p></w:hdr>'.encode(),
        )

    _rewrite(path, edit)


def _link_views(path: Path) -> dict[str, str]:
    views = {k: v for k, v in _zip_views(path.read_bytes()).items() if k.endswith((".xml", ".rels"))}
    result = subprocess.run(["pandoc", "-f", "docx", "-t", "native", str(path)], capture_output=True, check=True)
    views["pandoc native"] = result.stdout.decode("utf-8", "replace")
    return views


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="没装 pandoc")
def test_external_link_targets_become_a_placeholder(tmp_path: Path):
    source = tmp_path / "hyphen-links.docx"
    _hyphen_links_docx(source)
    before = _link_views(source)
    assert "acme-pharma" in before["pandoc native"]
    for output in _write_both_ways(source, tmp_path):
        views = _link_views(output)
        for name, text in views.items():
            flat = text.casefold()
            for number, word in enumerate(LINK_VARIANTS, 1):
                assert word not in flat, f"{output.parent.name} {name} 读出了链接变体 #{number}"
        shown = views["pandoc native"]
        assert LINK_PLACEHOLDER in shown, shown
        # 链接本身和显示文字都还在（pandoc 不显示域代码链接和页眉，直接看 XML）。
        for label in ('Str "portal"', 'Str "field"'):
            assert label in shown
        with zipfile.ZipFile(output) as archive:
            body = archive.read("word/document.xml").decode()
            rels = [archive.read(n).decode() for n in archive.namelist() if n.endswith(".rels") and "word/" in n]
        assert "simple" in body
        assert body.count(f"HYPERLINK &quot;{LINK_PLACEHOLDER}&quot;") + body.count(f'HYPERLINK "{LINK_PLACEHOLDER}"') == 2
        assert '\\o "tip"' in body or "\\o &quot;tip&quot;" in body
        assert sum(r.count(LINK_PLACEHOLDER) for r in rels) == 2
