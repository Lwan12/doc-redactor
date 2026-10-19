"""Re-read a written file and count hits that are still visible.

Text is extracted into memory only; nothing is written to disk. Placeholder
labels are stripped first so a label (or a category name on a legend page)
never counts as a leftover.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

MSG_LEFTOVER = "{n} 处写出后仍可见，已删除这个输出文件，没有留下含原文的文件"


def extract_units(path: Path) -> list[str]:
    """Text units in the same shape the scanner uses (PDF page / Word paragraph)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        document = pymupdf.open(path)
        try:
            return [page.get_text("text") for page in document]
        finally:
            document.close()
    if suffix == ".docx":
        from docx import Document

        from doc_redactor.docx_redactor import iter_document_paragraphs

        return [paragraph.text or "" for paragraph in iter_document_paragraphs(Document(str(path)))]
    return []


def count_leftovers(
    path: Path,
    *,
    engine=None,
    targets: Iterable[str] = (),
    kept: Iterable[str] = (),
    labels: Iterable[str] = (),
) -> int:
    """Hits still visible in ``path``.

    ``targets``: texts that should be gone (used when no engine is given).
    ``kept``: texts of hits the user unchecked; that many occurrences are allowed.
    """
    label_list = sorted({label for label in labels if label}, key=len, reverse=True)
    found: Counter[str] = Counter()
    target_list = sorted({t for t in targets if t}, key=len, reverse=True)
    for unit in extract_units(path):
        for label in label_list:
            unit = unit.replace(label, " " * len(label))
        if engine is not None:
            for hit in engine.find_hits(unit):
                found[hit.text] += 1
        else:
            for target in target_list:
                found[target] += unit.count(target)
    allowed = Counter(kept)
    return sum(max(0, n - allowed.get(text, 0)) for text, n in found.items())
