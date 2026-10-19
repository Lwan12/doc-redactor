"""Re-read a written file and count hits that are still visible.

Text is extracted into memory only; nothing is written to disk. Placeholder
labels are stripped first so a label (or a category name on a legend page)
never counts as a leftover.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterable

MSG_LEFTOVER = "{n} 处写出后仍可见，已删除这个输出文件，没有留下含原文的文件"

# Short codes the PDF writer may put in a narrow box: the built-in ones plus
# [R1], [R2]... for custom rules. Stripped only when the writer reports that
# it used at least one short code.
_SHORT_CODE = re.compile(r"\[(?:PR|PN|SP|SD|NCT|EM|PH|R\d+)\]")


class LeftoverText(ValueError):
    """Hits are still visible after writing. The output has been deleted.

    ``count`` is the number of such hits. The message does not include them.
    """

    def __init__(self, count: int) -> None:
        self.count = int(count)
        super().__init__(MSG_LEFTOVER.format(n=self.count))


def _is_legend_page(text: str) -> bool:
    from doc_redactor.pdf_redactor import LEGEND_FOOTER, LEGEND_TITLE

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0].replace(" ", "") != LEGEND_TITLE:
        return False
    return LEGEND_FOOTER.replace(" ", "") in "".join(lines).replace(" ", "")


def extract_units(path: Path, *, drop_legend: bool = False) -> list[str]:
    """Text units in the same shape the scanner uses (PDF page / Word paragraph).

    ``drop_legend``: leave out a trailing legend page the PDF writer appended.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        document = pymupdf.open(path)
        try:
            pages = [page.get_text("text") for page in document]
        finally:
            document.close()
        if drop_legend and pages and _is_legend_page(pages[-1]):
            pages = pages[:-1]
        return pages
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
    short_codes: bool = False,
) -> int:
    """Hits still visible in ``path``.

    ``targets``: texts that should be gone (used when no engine is given).
    ``kept``: texts of hits the user unchecked; that many occurrences are allowed.
    ``short_codes``: the writer used short codes, so strip them and skip the
    legend page it appended.
    """
    label_list = sorted({label for label in labels if label}, key=len, reverse=True)
    found: Counter[str] = Counter()
    target_list = sorted({t for t in targets if t}, key=len, reverse=True)
    for unit in extract_units(path, drop_legend=short_codes):
        for label in label_list:
            unit = unit.replace(label, " " * len(label))
        if short_codes:
            unit = _SHORT_CODE.sub(lambda m: " " * len(m.group(0)), unit)
        if engine is not None:
            for hit in engine.find_hits(unit):
                found[hit.text] += 1
        else:
            for target in target_list:
                found[target] += unit.count(target)
    allowed = Counter(kept)
    return sum(max(0, n - allowed.get(text, 0)) for text, n in found.items())
