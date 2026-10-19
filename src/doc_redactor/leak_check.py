"""Factory gate: read a written file the way other tools do, and refuse it if
any of those readings still shows a hit.

The scanner reads a file one way. An AI pipeline reads it many ways: every
XML part and attribute of a Word file, every string object of a PDF, the
whole page including what the CropBox hides, link targets, layer names. This
module builds those readings in memory (nothing is written to disk) and runs
the same engine over each. A hit the user unchecked on purpose is not a leak.

The message never contains the hit text, only a count.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from doc_redactor.engine import RedactionEngine
from doc_redactor.verify_output import count_not_allowed, is_invented, not_allowed

# U+00A0 keeps the count on the same line as 处 when a banner wraps.
MSG_LEAKED_HEAD = "写出后复核：换一种读法仍能读出\u00a0{n}\u00a0处"
MSG_LEAKED = MSG_LEAKED_HEAD + "，已删除这个输出文件，没有写出"
MSG_LEAKED_UNDELETED = MSG_LEAKED_HEAD + "，删除输出失败，请手动删除「{name}」"
# 文案待前端定。命令行和界面用同一句（M34-1）。
MSG_NO_HITS_LINE = "没有命中，没有写出脱敏件。不要把原件当成脱敏件使用"
MSG_NO_HITS = MSG_NO_HITS_LINE + "。如果里面有敏感内容，请先补规则。"

_RASTER = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")
_W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"


class LeakedText(ValueError):
    """The written output is still readable another way. It has been removed
    (or the message says it could not be). ``count`` never comes with the text."""

    def __init__(self, count: int, message: str | None = None) -> None:
        self.count = int(count)
        super().__init__(message or MSG_LEAKED.format(n=self.count))


class NoHits(ValueError):
    """The whole file had no hit, so nothing was written."""

    def __init__(self) -> None:
        super().__init__(MSG_NO_HITS)


def readings(path: Path) -> list[str]:
    """Texts other tools would get from ``path``. PDF and Word only."""
    return [text for _name, texts, _places in views(path) for text in texts]


def views(path: Path) -> list[tuple[str, list[str], bool]]:
    """``(name, texts, places)`` per way of reading ``path``.

    ``places`` is True for a view that reads each place the scan lists once
    (PDF page text, the Word XML): counts add up over its texts, and unchecked
    hits are allowed by count. Other views (PDF objects, metadata, links,
    embedded packages) can show one place several times, so each text counts
    on its own and nothing is allowed there: an unchecked hit is never in
    them, since the scan does not list them.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return _pdf_views(Path(path))
    if suffix == ".docx":
        return _docx_views(Path(path).read_bytes())
    return []


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def count_leaks(
    path: Path,
    engine: RedactionEngine,
    *,
    kept: list[str] | tuple[str, ...] = (),
    labels: list[str] | tuple[str, ...] = (),
    source: Path | None = None,
) -> int:
    """Number of hits across all readings, minus unchecked hits and placeholders.

    ``kept`` are texts of hits the user unchecked, once per hit. Only that many
    occurrences are let through, and only in the views that read the scanned
    places (``views``); the same word in a named destination or in metadata
    still counts (review M34-3). The allowance is the same function as the
    after-write check (``verify_output.not_allowed``). ``labels`` are the
    placeholders the writer used; a hit inside one of them is the writer's own
    text. ``source`` is the original: a hit that no reading of the original
    has, and that neither contains nor is part of any hit there, was made up
    by the writer's own deletions ("Subject [deleted] 001") and is not counted
    (``verify_output.is_invented``).
    """
    label_norm = [_norm(text) for text in labels if text]
    known = None
    if source is not None:
        known = {_norm(hit.text) for text in readings(source) for hit in engine.find_hits(text)}
    # Per hit text, take the largest count any single view leaves after the
    # allowance: the same place shows up in several views.
    most: dict[str, int] = {}
    for _name, texts, places in views(path):
        found: Counter = Counter()
        for text in texts:
            seen: Counter = Counter()
            for hit in engine.find_hits(text):
                flat = _norm(hit.text)
                if not flat:
                    continue
                if any(flat in label for label in label_norm):
                    continue
                if known is not None and is_invented(flat, known):
                    continue
                seen[flat] += 1
            if places:
                found.update(seen)
            else:
                for flat, count in seen.items():
                    found[flat] = max(found[flat], count)
        left = not_allowed(found, kept if places else (), key=_norm)
        for flat, count in left.items():
            most[flat] = max(most.get(flat, 0), count)
    return sum(most.values())


def gate_output(
    path: Path,
    engine: RedactionEngine,
    *,
    kept: list[str] | tuple[str, ...] = (),
    labels: list[str] | tuple[str, ...] = (),
    source: Path | None = None,
) -> None:
    """Raise ``LeakedText`` and remove ``path`` when any reading still shows a hit.

    An error while reading counts as a leak: the file is not let out.
    """
    try:
        found = count_leaks(path, engine, kept=kept, labels=labels, source=source)
    except Exception:
        found = 1
    if found <= 0:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        raise LeakedText(found, MSG_LEAKED_UNDELETED.format(n=found, name=Path(path).name)) from None
    raise LeakedText(found)


# --- Word ---------------------------------------------------------------------


def _docx_views(data: bytes) -> list[tuple[str, list[str], bool]]:
    xml: list[str] = []
    other: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.lower().endswith((".xml", ".rels", ".vml")):
                xml.extend(_xml_readings(archive.read(name)))
    other = [text for text in _zip_readings(data, xml_too=False)]
    return [("Word XML", xml, True), ("Word 内嵌包和二进制", other, False)]


def _zip_readings(data: bytes, xml_too: bool = True) -> list[str]:
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            payload = archive.read(name)
            lowered = name.lower()
            if lowered.endswith((".xml", ".rels", ".vml")):
                if xml_too:
                    out.extend(_xml_readings(payload))
            elif payload[:2] == b"PK":
                try:
                    out.extend(_zip_readings(payload))
                except zipfile.BadZipFile:
                    out.append(payload.decode("latin1"))
            elif lowered.endswith(_RASTER):
                # Pictures are OCR-checked when they are painted.
                continue
            else:
                # OLE .bin, EMF/WMF previews, HTML chunks: text is often UTF-16.
                out.append(payload.decode("utf-8", "replace"))
                out.append(payload.decode("utf-16-le", "replace"))
    return out


def _xml_readings(payload: bytes) -> list[str]:
    from lxml import etree

    try:
        root = etree.fromstring(payload)
    except etree.XMLSyntaxError:
        return [payload.decode("utf-8", "replace")]
    out: list[str] = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag == _W_P:
            # A paragraph as one string, so a word split over runs is whole.
            out.append("".join(element.itertext()))
        elif element.text and element.getparent() is not None and not _inside_paragraph(element):
            out.append(element.text)
        for value in element.attrib.values():
            # One string per attribute (decoded when escaped), so a place counts once.
            out.append(unquote(value) if "%" in value else value)
    return out


def _inside_paragraph(element) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == _W_P:
            return True
        parent = parent.getparent()
    return False


# --- PDF ----------------------------------------------------------------------


def _pdf_views(path: Path) -> list[tuple[str, list[str], bool]]:
    import pymupdf

    from doc_redactor.pdf_redactor import PAGE_TEXT_FLAGS

    flags_text: list[str] = []
    joined: list[str] = []
    plain: list[str] = []
    out: list[str] = []
    document = pymupdf.open(path)
    try:
        for page in document:
            textpage = page.get_textpage(flags=PAGE_TEXT_FLAGS, clip=pymupdf.INFINITE_RECT())
            text = page.get_text("text", textpage=textpage)
            flags_text.append(text)
            joined.append(re.sub(r"(?<=\w)[-\u00ad\u2010]\n(?=\w)", "", text))
            plain.append(page.get_text("text"))
            for link in page.get_links():
                out.append(str(link.get("uri") or ""))
                out.append(str(link.get("file") or ""))
            try:
                out.append(page.get_label())
            except Exception:
                pass
        out.append(repr(document.metadata))
        out.append(document.get_xml_metadata() or "")
        out.extend(str(item[1]) for item in document.get_toc())
        out.extend(str(item.get("name", "")) for item in (document.get_ocgs() or {}).values())
        out.extend(document.embfile_names() or [])
        for xref in range(1, document.xref_length()):
            try:
                source = document.xref_object(xref, compressed=False)
            except Exception:
                continue
            out.extend(_pdf_strings(source))
        out.extend(_pdf_strings(document.pdf_trailer()))
    finally:
        document.close()
    return [
        ("PDF 整页文字", flags_text, True),
        ("PDF 整页文字（连字符拼回）", joined, True),
        ("PDF 页面文字", plain, True),
        ("PDF 对象、元数据和链接", out, False),
    ]


def _pdf_strings(source: str) -> list[str]:
    """Literal ``(...)`` and hex ``<...>`` strings of one object's source, decoded."""
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        if c == "(":
            depth = 0
            buf = []
            while i < n:
                ch = source[i]
                if ch == "\\" and i + 1 < n:
                    nxt = source[i + 1]
                    mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
                    if nxt in mapping:
                        buf.append(mapping[nxt])
                        i += 2
                        continue
                    if nxt.isdigit():
                        digits = re.match(r"[0-7]{1,3}", source[i + 1 : i + 4])
                        if digits:
                            buf.append(chr(int(digits.group(0), 8)))
                            i += 1 + len(digits.group(0))
                            continue
                    buf.append(nxt)
                    i += 2
                    continue
                if ch == "(":
                    depth += 1
                    if depth > 1:
                        buf.append(ch)
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                    buf.append(ch)
                else:
                    buf.append(ch)
                i += 1
            out.append(_pdf_text_string("".join(buf).encode("latin1", "replace")))
            continue
        if c == "<" and source[i + 1 : i + 2] != "<":
            end = source.find(">", i)
            if end < 0:
                break
            digits = re.sub(r"\s", "", source[i + 1 : end])
            if digits and re.fullmatch(r"[0-9A-Fa-f]+", digits):
                if len(digits) % 2:
                    digits += "0"
                out.append(_pdf_text_string(bytes.fromhex(digits)))
            i = end + 1
            continue
        if c == "<":
            i += 2
            continue
        i += 1
    return out


def _pdf_text_string(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("latin1")
