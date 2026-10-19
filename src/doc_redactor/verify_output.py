"""Re-read a written file and count hits that are still visible.

Text is extracted into memory only; nothing is written to disk. The output is
rescanned as it is. A hit that lies entirely inside a placeholder the writer
put there (a replacement label, a short code, a line of the legend page) is
not a leftover; every other hit is.

Any problem in the check itself counts as a leftover: the output is deleted,
and if it cannot be deleted the user is told to delete it by hand.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

# U+00A0 keeps "1 处" on one line when a banner wraps.
MSG_LEFTOVER_HEAD = "{n}\u00a0处写出后仍可见"
MSG_VERIFY_ERROR_HEAD = "写出后核对出错，按有残留处理"
MSG_DELETED_TAIL = "已删除这个输出文件，没有留下含原文的文件"
MSG_DELETE_FAILED_TAIL = "删除失败，这个文件可能含原文，请手动删除「{name}」"
MSG_LEFTOVER = MSG_LEFTOVER_HEAD + "，" + MSG_DELETED_TAIL
MSG_VERIFY_ERROR = MSG_VERIFY_ERROR_HEAD + "，" + MSG_DELETED_TAIL
MSG_FINAL_ADVICE = "这类问题重试也会一样，请点「返回修改」给漏掉的地方加规则，或回上一步检查。"

# Short codes the PDF writer may put in a narrow box: the built-in ones plus
# [R1], [R2]... for custom rules. Treated as placeholders only when the writer
# reports that it used at least one short code.
_SHORT_CODE = re.compile(r"\[(?:PR|PN|SP|SD|NCT|EM|PH|R\d+)\]")
_LEGEND_CODE = re.compile(r"^[ \t]*(\[(?:PR|PN|SP|SD|NCT|EM|PH|R\d+)\])[ \t]*=", re.MULTILINE)
# One explanation line on the legend page: "[SP] = 申办方（[申办方]）".
_LEGEND_LINE = re.compile(r"^\[(?:PR|PN|SP|SD|NCT|EM|PH|R\d+)\] = [^\n]*（[^\n]*）$", re.MULTILINE)


class LeftoverText(ValueError):
    """The written output failed the check. It has been deleted, or the
    message says it could not be.

    ``count`` is the number of visible hits (0 when the check itself failed).
    The message never includes them.
    """

    def __init__(self, count: int, message: str | None = None) -> None:
        self.count = int(count)
        super().__init__(message or MSG_LEFTOVER.format(n=self.count))


def is_final_failure(reason: str | None) -> bool:
    """A failure that a plain retry would reproduce (leftover or check error)."""
    if not reason:
        return False
    # "写出后复核" is leak_check's other-reader check; a retry repeats it too.
    return "写出后仍可见" in reason or MSG_VERIFY_ERROR_HEAD in reason or "写出后复核" in reason


def _is_legend_page(text: str) -> bool:
    from doc_redactor.pdf_redactor import LEGEND_FOOTER, LEGEND_TITLE

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0].replace(" ", "") != LEGEND_TITLE:
        return False
    return LEGEND_FOOTER.replace(" ", "") in "".join(lines).replace(" ", "")


def extract_units(path: Path) -> list[str]:
    """Text of each unit (PDF page / Word text unit), as the scanner reads it."""
    return [_unit_text(unit) for unit in _units(path)]


def _units(path: Path) -> list:
    """Units in the same shape the scanner uses.

    PDF: one string per page. Word: the ``TextUnit`` objects of the shared
    walk (``iter_document_paragraphs``), so hits are found per unit with
    ``TextUnit.find_hits`` exactly as the scanner does. A paragraph with a
    tracked deletion is two units; a hit only in its kept text belongs to the
    text unit, one that touches deleted text to the deleted unit. Each hit is
    therefore counted once.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        from doc_redactor.pdf_redactor import page_textpage, prepare_pdf

        document = pymupdf.open(path)
        try:
            # Same reading as the scanner (scan.py): tags and ActualText
            # dropped, whole page including text outside the page and CropBox.
            prepare_pdf(document)
            return [page.get_text("text", textpage=page_textpage(page)) for page in document]
        finally:
            document.close()
    if suffix == ".docx":
        from docx import Document

        from doc_redactor.docx_redactor import iter_document_paragraphs

        return list(iter_document_paragraphs(Document(str(path))))
    return []


def _unit_text(unit) -> str:
    return unit if isinstance(unit, str) else (unit.text or "")


def placeholder_spans(
    unit: str,
    *,
    labels: Iterable[str] = (),
    short_codes: bool | frozenset[str] = False,
    legend: bool = False,
) -> list[tuple[int, int]]:
    """Character ranges the writer filled in, never original text.

    ``short_codes``: True treats any code shape as a placeholder; a set of
    codes (read from the legend page) only those codes, so a bracketed code
    that was already in the original ("[R123456]") still counts.
    """
    spans: list[tuple[int, int]] = []
    for label in sorted({label for label in labels if label}, key=len, reverse=True):
        start = unit.find(label)
        while start >= 0:
            spans.append((start, start + len(label)))
            start = unit.find(label, start + len(label))
    if short_codes:
        spans.extend(
            m.span() for m in _SHORT_CODE.finditer(unit) if short_codes is True or m.group(0) in short_codes
        )
        if legend:
            from doc_redactor.pdf_redactor import LEGEND_FOOTER, LEGEND_TITLE

            spans.extend(m.span() for m in _LEGEND_LINE.finditer(unit))
            for fixed in (LEGEND_TITLE, LEGEND_FOOTER):
                start = unit.find(fixed)
                if start >= 0:
                    spans.append((start, start + len(fixed)))
    return spans


def _raw_hits(unit, engine, targets: list[str]) -> list[tuple[int, int, str]]:
    text = _unit_text(unit)
    if engine is not None:
        if isinstance(unit, str):
            from doc_redactor.pdf_redactor import find_page_hits

            # A PDF page: also joins words broken by a hyphen at a line end.
            hits = find_page_hits(engine, text)
        else:
            hits = unit.find_hits(engine)
        return [(hit.start, hit.end, hit.text) for hit in hits]
    found = []
    for target in targets:
        start = text.find(target)
        while start >= 0:
            found.append((start, start + len(target), target))
            start = text.find(target, start + len(target))
    if not isinstance(unit, str) and getattr(unit, "is_deletion", False):
        # Same split as TextUnit.find_hits: a deleted unit owns only the
        # hits that touch deleted text.
        from doc_redactor.engine import Hit

        owned = {(h.start, h.end) for h in unit.find_hits(_FixedHits(found, Hit))}
        found = [item for item in found if (item[0], item[1]) in owned]
    return found


class _FixedHits:
    """Engine stand-in that returns a fixed hit list (targets-only check)."""

    def __init__(self, found, hit_type) -> None:
        self._hits = [hit_type(start=s, end=e, category="", text=t) for s, e, t in found]

    def find_hits(self, _text):
        return list(self._hits)


def leftover_hits_in_unit(
    unit,
    *,
    engine=None,
    targets: Iterable[str] = (),
    labels: Iterable[str] = (),
    short_codes: bool | frozenset[str] = False,
    legend: bool = False,
    known: Iterable[str] | None = None,
) -> list[str]:
    """Texts of hits in ``unit`` that are not wholly inside a placeholder.

    ``unit`` is a string (a PDF page) or a Word ``TextUnit``.

    ``known``: hit texts found in the original. When given, a hit whose text
    the original never had is dropped only if it neither contains an original
    hit text nor is part of one (see ``_invented``): removing a word can join
    its neighbours into a new hit (PDF text extraction also moves an inserted
    placeholder to the end of the page), but a leftover always overlaps
    original hit text.
    """
    spans = placeholder_spans(_unit_text(unit), labels=labels, short_codes=short_codes, legend=legend)
    target_list = sorted({t for t in targets if t}, key=len, reverse=True)
    known_set = None if known is None else {_squash(text) for text in known}
    kept = []
    for start, end, text in _raw_hits(unit, engine, target_list):
        if any(s <= start and end <= e for s, e in spans):
            continue
        if known_set is not None and _invented(_squash(text), known_set):
            continue
        kept.append(text)
    return kept


def _squash(text: str) -> str:
    return "".join(text.split())


def _invented(squashed: str, known: set[str]) -> bool:
    """True only for a hit the writer's own changes made up.

    A hit text the original never had is dropped only when it neither
    contains an original hit text nor is part of one. A leftover joined with
    nearby text ("Acme" + "Pharma", "ZZ-77" + "[R2]") or a shorter term left
    after only part of a longer one was covered ("星辰海" of "星辰海医院")
    always shares text with an original hit, so it still counts.
    """
    if squashed in known:
        return False
    return not any(k and (k in squashed or squashed in k) for k in known)


def original_hit_texts(path: Path, engine) -> list[str]:
    """Hit texts in the original file, extracted the same way as the output."""
    return [text for unit in _units(path) for _s, _e, text in _raw_hits(unit, engine, [])]


def count_leftovers(
    path: Path,
    *,
    engine=None,
    targets: Iterable[str] = (),
    kept: Iterable[str] = (),
    labels: Iterable[str] = (),
    short_codes: bool = False,
    known: Iterable[str] | None = None,
) -> int:
    """Hits still visible in ``path``.

    ``targets``: texts that should be gone (used when no engine is given).
    ``kept``: texts of hits the user unchecked; that many occurrences are allowed.
    ``short_codes``: the writer used short codes (and so appended a legend page).
    ``known``: hit texts of the original; see ``leftover_hits_in_unit``.
    """
    known_list = None if known is None else list(known)
    label_list = list(labels)
    target_list = list(targets)
    found: Counter[str] = Counter()
    units = _units(path)
    codes: frozenset[str] = frozenset()
    if short_codes and units and path.suffix.lower() == ".pdf" and _is_legend_page(_unit_text(units[-1])):
        # Only the codes the legend explains are the writer's. No legend page:
        # no code is a placeholder (fail closed).
        codes = frozenset(m.group(1) for m in _LEGEND_CODE.finditer(_unit_text(units[-1])))
    for index, unit in enumerate(units):
        legend = bool(codes) and index == len(units) - 1
        for text in leftover_hits_in_unit(
            unit,
            engine=engine,
            targets=target_list,
            labels=label_list,
            short_codes=codes,
            legend=legend,
            known=known_list,
        ):
            found[text] += 1
    allowed = Counter(kept)
    return sum(max(0, n - allowed.get(text, 0)) for text, n in found.items())


def discard_output(path: Path) -> bool:
    """Delete ``path``. True only when it is confirmed gone afterwards."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        return not path.exists()
    except OSError:
        return False


def check_written(path: Path, count: Callable[[], int]) -> tuple[int, str] | None:
    """Run ``count`` on a written file. None when it is clean.

    Otherwise the file is deleted and ``(leftovers, message)`` is returned.
    An exception from ``count`` counts as a leftover (fail closed).
    """
    try:
        leftover = int(count())
        head = MSG_LEFTOVER_HEAD.format(n=leftover) if leftover else None
    except Exception:  # noqa: BLE001 - any failure of the check blocks the file
        leftover = 0
        head = MSG_VERIFY_ERROR_HEAD
    if head is None:
        return None
    if discard_output(path):
        return leftover, f"{head}，{MSG_DELETED_TAIL}"
    return leftover, f"{head}，{MSG_DELETE_FAILED_TAIL.format(name=path.name)}"
