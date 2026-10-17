"""Redact docx body, nested tables, headers/footers, and embedded images."""

from __future__ import annotations

import zipfile
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from doc_redactor.engine import Hit, RedactionEngine, merge_counts, redaction_label
from doc_redactor.image_redactor import is_raster_suffix, redact_image_bytes


def redact_docx(src: Path, dest: Path, engine: RedactionEngine) -> dict[str, int]:
    if src.resolve() == dest.resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")
    document = Document(str(src))
    counts: dict[str, int] = {}
    _redact_container(document, engine, counts)
    for section in document.sections:
        for header_footer in _owned_headers_footers(section):
            _redact_container(header_footer, engine, counts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(dest))
    merge_counts(counts, _redact_media(dest, engine))
    return counts


def _redact_container(container, engine: RedactionEngine, counts: dict[str, int]) -> None:
    for paragraph in container.paragraphs:
        merge_counts(counts, _counts(_redact_paragraph(paragraph, engine)))
    for table in container.tables:
        _redact_table(table, engine, counts)


def _redact_table(table: Table, engine: RedactionEngine, counts: dict[str, int]) -> None:
    seen: set[int] = set()
    for row in table.rows:
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen:
                continue
            seen.add(marker)
            _redact_container(cell, engine, counts)


def _owned_headers_footers(section):
    """Yield header/footer parts this section defines.

    Reading ``.paragraphs`` on a missing part creates an empty header. Skip
    parts that are only linked to the previous section.
    """
    parts = (
        section.header,
        section.footer,
        section.first_page_header,
        section.first_page_footer,
        section.even_page_header,
        section.even_page_footer,
    )
    for part in parts:
        if not part.is_linked_to_previous:
            yield part


def _redact_paragraph(paragraph: Paragraph, engine: RedactionEngine) -> list[str]:
    """Replace each hit inside runs, keeping the first involved run's formatting.

    Matches are applied from the right. The placeholder is written into the
    first run that overlaps the hit; text inside the hit is removed from the
    later runs.
    """
    categories: list[str] = []
    for _ in range(10000):
        text, spans = _index_runs(paragraph)
        hits = engine.find_hits(text)
        if not hits:
            break
        hit = hits[-1]
        if not _apply_hit(spans, hit, replacement=engine.label(hit.category)):
            break
        categories.append(hit.category)
    else:
        raise RuntimeError("同一段落命中过多，已停止")
    categories.reverse()
    return categories


def _index_runs(paragraph: Paragraph):
    spans = []
    position = 0
    for run in paragraph.runs:
        run_text = run.text
        spans.append((run, position, position + len(run_text), run_text))
        position += len(run_text)
    text = "".join(item[3] for item in spans)
    return text, spans


def iter_document_paragraphs(document):
    """Yield paragraphs in the same order as redact_docx (body, tables, headers/footers)."""
    yield from _iter_container_paragraphs(document)
    for section in document.sections:
        for header_footer in _owned_headers_footers(section):
            yield from _iter_container_paragraphs(header_footer)


def _iter_container_paragraphs(container):
    for paragraph in container.paragraphs:
        yield paragraph
    for table in container.tables:
        yield from _iter_table_paragraphs(table)


def _iter_table_paragraphs(table: Table):
    seen: set[int] = set()
    for row in table.rows:
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen:
                continue
            seen.add(marker)
            yield from _iter_container_paragraphs(cell)


def apply_hits_to_paragraph(paragraph: Paragraph, hits: list[Hit], replacement_for) -> list[str]:
    """Apply stored hits from the right. replacement_for(category) -> str."""
    ordered = sorted(hits, key=lambda item: item.start, reverse=True)
    categories: list[str] = []
    for hit in ordered:
        text, spans = _index_runs(paragraph)
        rebuilt = Hit(
            start=hit.start,
            end=hit.end,
            category=hit.category,
            text=text[hit.start : hit.end] if 0 <= hit.start <= hit.end <= len(text) else hit.text,
        )
        if not _apply_hit(spans, rebuilt, replacement=replacement_for(hit.category)):
            continue
        categories.append(hit.category)
    categories.reverse()
    return categories


def _apply_hit(spans, hit: Hit, replacement: str | None = None) -> bool:
    involved = [span for span in spans if span[1] < hit.end and span[2] > hit.start]
    if not involved:
        return False
    if replacement is None:
        replacement = redaction_label(hit.category)
    first_run, first_start, _first_end, first_text = involved[0]
    local_start = hit.start - first_start
    if len(involved) == 1:
        local_end = hit.end - first_start
        first_run.text = first_text[:local_start] + replacement + first_text[local_end:]
        return True
    first_run.text = first_text[:local_start] + replacement
    for middle_run, _start, _end, _text in involved[1:-1]:
        middle_run.text = ""
    last_run, last_start, _last_end, last_text = involved[-1]
    local_end = hit.end - last_start
    last_run.text = last_text[local_end:]
    return True


def _counts(categories: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for category in categories:
        counts[category] = counts.get(category, 0) + 1
    return counts


def _redact_media(path: Path, engine: RedactionEngine) -> dict[str, int]:
    with zipfile.ZipFile(path, "r") as archive:
        names = [info.filename for info in archive.infolist() if _is_media_image(info.filename)]
        if not names:
            return {}
        payloads = {name: archive.read(name) for name in names}
        infos = archive.infolist()

    counts: dict[str, int] = {}
    replaced: dict[str, bytes] = {}
    for name, payload in payloads.items():
        suffix = Path(name).suffix
        new_bytes, image_counts = redact_image_bytes(payload, suffix, engine)
        replaced[name] = new_bytes
        merge_counts(counts, image_counts)

    buffer_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(buffer_path, "w") as target:
        for info in infos:
            data = replaced.get(info.filename, source.read(info.filename))
            cloned = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            cloned.compress_type = info.compress_type
            cloned.external_attr = info.external_attr
            cloned.flag_bits = info.flag_bits
            cloned.create_system = info.create_system
            target.writestr(cloned, data)
    buffer_path.replace(path)
    return counts


def _is_media_image(name: str) -> bool:
    lowered = name.lower()
    if not lowered.startswith("word/media/"):
        return False
    return is_raster_suffix(Path(lowered).suffix)
