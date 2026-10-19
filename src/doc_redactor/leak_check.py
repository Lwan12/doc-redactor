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
from pathlib import Path
from urllib.parse import unquote

from doc_redactor.engine import RedactionEngine

# U+00A0 keeps the count on the same line as 处 when a banner wraps.
MSG_LEAKED_HEAD = "写出后复核：换一种读法仍能读出\u00a0{n}\u00a0处"
MSG_LEAKED = MSG_LEAKED_HEAD + "，已删除这个输出文件，没有写出"
MSG_LEAKED_UNDELETED = MSG_LEAKED_HEAD + "，删除输出失败，请手动删除「{name}」"
MSG_NO_HITS = "整份文件没有找到要遮的内容，没有写出。原件没有处理过，不要当成脱敏件交出去；如果里面有敏感内容，请先补规则。"

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
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return _pdf_readings(Path(path))
    if suffix == ".docx":
        return _zip_readings(Path(path).read_bytes())
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
    """Number of hits across all readings, minus kept text and placeholders.

    ``kept`` are texts of hits the user unchecked; the same text elsewhere is
    therefore not counted either. ``labels`` are the placeholders the writer
    used; a hit inside one of them is the writer's own text. ``source`` is the
    original: a hit that no reading of the original has, and that neither
    contains nor is part of any hit there, was made up by the writer's own
    deletions ("Subject [deleted] 001") and is not counted (same rule as the
    rescan in verify_output).
    """
    kept_norm = {_norm(text) for text in kept if text}
    label_norm = [_norm(text) for text in labels if text]
    known = None
    if source is not None:
        known = {_norm(hit.text) for text in readings(source) for hit in engine.find_hits(text)}
    # The same place shows up in several readings (an attribute and its
    # decoded form, metadata and the Info object). Per hit text, take the
    # largest count any single reading gives.
    most: dict[str, int] = {}
    for text in readings(path):
        seen: dict[str, int] = {}
        for hit in engine.find_hits(text):
            flat = _norm(hit.text)
            if not flat or flat in kept_norm:
                continue
            if any(flat in label for label in label_norm):
                continue
            if known is not None and flat not in known and not any(k and (k in flat or flat in k) for k in known):
                continue
            seen[flat] = seen.get(flat, 0) + 1
        for flat, count in seen.items():
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


def _zip_readings(data: bytes) -> list[str]:
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            payload = archive.read(name)
            lowered = name.lower()
            if lowered.endswith((".xml", ".rels", ".vml")):
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
            out.append(value)
            if "%" in value:
                out.append(unquote(value))
    return out


def _inside_paragraph(element) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == _W_P:
            return True
        parent = parent.getparent()
    return False


# --- PDF ----------------------------------------------------------------------


def _pdf_readings(path: Path) -> list[str]:
    import pymupdf

    from doc_redactor.pdf_redactor import PAGE_TEXT_FLAGS

    out: list[str] = []
    document = pymupdf.open(path)
    try:
        for page in document:
            textpage = page.get_textpage(flags=PAGE_TEXT_FLAGS, clip=pymupdf.INFINITE_RECT())
            text = page.get_text("text", textpage=textpage)
            out.append(text)
            out.append(re.sub(r"(?<=\w)[-\u00ad\u2010]\n(?=\w)", "", text))
            out.append(page.get_text("text"))
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
    return out


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
