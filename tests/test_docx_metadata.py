"""Written docx keeps no document properties. Fictional data only."""

import zipfile
from pathlib import Path

from docx import Document

from doc_redactor.docx_redactor import redact_docx
from doc_redactor.engine import RegexRule, build_engine
from doc_redactor.scan import scan_path, write_scan

SECRET = "Acme Pharma Fictizumab ABC-2026-001"

# Shaped like the app.xml Word writes on save (element order included).
# Word copies the title into TitlesOfParts; HeadingPairs counts those entries.
APP_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    f"<Template>{SECRET}.dotm</Template><TotalTime>3</TotalTime><Pages>1</Pages><Words>2</Words>"
    "<Characters>20</Characters><Application>Microsoft Office Word</Application><DocSecurity>0</DocSecurity>"
    "<Lines>1</Lines><Paragraphs>1</Paragraphs><ScaleCrop>false</ScaleCrop>"
    '<HeadingPairs><vt:vector size="2" baseType="variant">'
    "<vt:variant><vt:lpstr>Title</vt:lpstr></vt:variant><vt:variant><vt:i4>1</vt:i4></vt:variant>"
    "</vt:vector></HeadingPairs>"
    f'<TitlesOfParts><vt:vector size="1" baseType="lpstr"><vt:lpstr>{SECRET} 方案</vt:lpstr></vt:vector></TitlesOfParts>'
    f"<Manager>{SECRET}</Manager><Company>{SECRET}</Company><LinksUpToDate>false</LinksUpToDate>"
    f"<CharactersWithSpaces>22</CharactersWithSpaces><SharedDoc>false</SharedDoc>"
    f"<HyperlinkBase>{SECRET}</HyperlinkBase><HyperlinksChanged>false</HyperlinksChanged>"
    "<AppVersion>16.0000</AppVersion></Properties>"
)
# Word keeps the attached template as an external link that carries the user name.
USER_PATH = "file:///C:\\Users\\zhangsanfeng\\AppData\\Roaming\\Microsoft\\Templates\\AcmePharma.dotm"
SETTINGS_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdTpl1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
    f'Target="{USER_PATH}" TargetMode="External"/></Relationships>'
)
# A SharePoint-style customXml data item (replaces the template's item1.xml).
CUSTOM_ITEM = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<p:properties xmlns:p="http://schemas.microsoft.com/office/2006/metadata/properties" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    f'<documentManagement><Owner xmlns="urn:fictional">{SECRET}</Owner>'
    '<Reviewer xmlns="urn:fictional">zhangsanfeng</Reviewer></documentManagement></p:properties>'
)

CUSTOM_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="Sponsor">'
    f"<vt:lpwstr>{SECRET}</vt:lpwstr></property></Properties>"
)
CUSTOM_CT = (
    '<Override PartName="/docProps/custom.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
)
CUSTOM_REL = (
    '<Relationship Id="rIdCustom9" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" '
    'Target="docProps/custom.xml"/>'
)


def _with_attached_template(settings: bytes) -> bytes:
    """Put <w:attachedTemplate> where Word does: right before <w:defaultTabStop>."""
    text = settings.decode("utf-8")
    if "xmlns:r=" not in text:
        text = text.replace(
            "<w:settings ",
            '<w:settings xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ',
            1,
        )
    tag = '<w:attachedTemplate r:id="rIdTpl1"/>'
    marker = "<w:defaultTabStop"
    assert marker in text
    return text.replace(marker, tag + marker, 1).encode("utf-8")


def _build(path: Path) -> None:
    doc = Document()
    doc.add_paragraph("Project ABC-2026-001")
    cp = doc.core_properties
    cp.author = SECRET
    cp.title = SECRET
    cp.subject = SECRET
    cp.keywords = SECRET
    cp.comments = SECRET
    cp.last_modified_by = SECRET
    cp.category = SECRET
    cp.content_status = SECRET
    cp.identifier = SECRET
    cp.version = SECRET
    raw = path.with_suffix(".raw.docx")
    doc.save(str(raw))
    with zipfile.ZipFile(raw) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/app.xml":
                data = APP_XML.encode()
            elif info.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", CUSTOM_CT.encode() + b"</Types>")
            elif info.filename == "_rels/.rels":
                data = data.replace(b"</Relationships>", CUSTOM_REL.encode() + b"</Relationships>")
            elif info.filename == "word/settings.xml":
                data = _with_attached_template(data)
            elif info.filename == "customXml/item1.xml":
                data = CUSTOM_ITEM.encode()  # the default template already has one item
            dst.writestr(info, data)
        dst.writestr("docProps/custom.xml", CUSTOM_XML)
        dst.writestr("word/_rels/settings.xml.rels", SETTINGS_RELS)
    raw.unlink()


def _assert_clean(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            assert "thumbnail" not in name.lower(), name
            if name.endswith((".xml", ".rels")):
                text = archive.read(name).decode("utf-8")
                for word in ("Acme", "Fictizumab", "ABC-2026", "zhangsanfeng"):
                    assert word not in text, name
        app = archive.read("docProps/app.xml").decode("utf-8")
        settings = archive.read("word/settings.xml").decode("utf-8")
    assert "Microsoft Office Word" in app  # harmless field kept
    assert "TitlesOfParts" not in app and "HeadingPairs" not in app
    assert "attachedTemplate" not in settings
    assert "customXml/item1.xml" in names  # the part stays, only its data goes
    cp = Document(str(path)).core_properties
    for field in ("author", "title", "subject", "keywords", "comments", "last_modified_by", "category"):
        assert not getattr(cp, field)


def test_fixture_carries_word_fields(tmp_path: Path):
    """Guard: the input really has the fields the writer must clear."""
    src = tmp_path / "a.docx"
    _build(src)
    with zipfile.ZipFile(src) as archive:
        app = archive.read("docProps/app.xml").decode("utf-8")
        assert "TitlesOfParts" in app and "HeadingPairs" in app
        assert "attachedTemplate" in archive.read("word/settings.xml").decode("utf-8")
        assert "zhangsanfeng" in archive.read("word/_rels/settings.xml.rels").decode("utf-8")
        assert "Acme" in archive.read("customXml/item1.xml").decode("utf-8")
    Document(str(src))  # python-docx can open it


def _engine():
    return build_engine([RegexRule(name="PROJECT_ID", pattern=r"ABC-\d{4}-\d{3}", flags=0)], [])


def test_scan_write_clears_properties(tmp_path: Path):
    src = tmp_path / "a.docx"
    _build(src)
    scan = scan_path(src, _engine())
    dest = tmp_path / "out.docx"
    write_scan(scan, dest)
    _assert_clean(dest)


def test_cli_redact_clears_properties(tmp_path: Path):
    src = tmp_path / "a.docx"
    _build(src)
    dest = tmp_path / "out.docx"
    redact_docx(src, dest, _engine())
    _assert_clean(dest)
