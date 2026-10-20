"""Scan files for hits, then write only the hits still checked. No tkinter."""

from __future__ import annotations

import errno
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import pymupdf
from PIL import Image
from docx import Document

from doc_redactor.docx_redactor import (
    apply_hits_to_paragraph,
    count_docx_comments,
    docx_media_block_reason,
    finish_document,
    iter_document_paragraphs,
    scrub_docx_hidden,
)
from doc_redactor import header_footer as hf
from doc_redactor.engine import Hit, RedactionEngine, counts_from_categories, merge_counts, redaction_label
from doc_redactor.leak_check import MSG_NO_HITS, LeakedText, gate_output
from doc_redactor.image_redactor import (
    WriteBlocked,
    _format_for_suffix,
    analyze_image_entities,
    is_embedded_raster_suffix,
    is_raster_suffix,
    paint_image_boxes,
    PdfImageOcrFailed,
    checked_pdf_image_entities,
    skipped_pdf_image,
    write_partial_warning,
)
from doc_redactor.pdf_redactor import (
    MSG_UNLOCATED,
    RedactionCounts,
    UnlocatedText,
    append_legend,
    clear_pdf_hidden,
    find_page_hits,
    inline_boxes_to_rects,
    inline_images,
    locate_text_hits,
    legend_lines,
    page_textpage,
    paint_inline_images,
    skipped_image_rects,
    pdf_image_block_reason,
    pdf_xref_image,
    prepare_pdf,
    redact_text_placeholders,
)
from doc_redactor.image_redactor import (
    MSG_BAD_IMAGE,
    MSG_BAD_IMAGE_AT,
    MSG_INLINE_FAILED,
    MSG_NO_OCR,
)
from doc_redactor.tesseract_cmd import require_tesseract
from doc_redactor.verify_output import check_written, count_leftovers

FILE_HIT_WARN = 500
SUPPORTED = {".docx", ".pdf", ".png", ".jpg", ".jpeg"}
KIND_WORD = "word"
KIND_PDF = "pdf"
KIND_IMAGE = "image"

MSG_ENCRYPTED_PDF = (
    "这个 PDF 设了打开密码，工具打不开它。请先在 PDF 阅读器里另存一份没有密码的，再拖进来。"
)
MSG_UNSUPPORTED = "不支持这个文件类型。请用 Word（.docx）、PDF 或图片（.png .jpg）。"
MSG_MISSING = "找不到这个文件。"
MSG_OPEN_FAIL = "打不开这个文件。请确认文件没有损坏，再试一次。"
MSG_ALREADY_WRITTEN = "这个文件之前已写出，已跳过"


def output_dir_unwritable(exc: OSError) -> str:
    """Friendly write-folder reason. No exception text, path, or source text."""
    err = getattr(exc, "errno", None)
    if isinstance(exc, PermissionError) or err in {errno.EACCES, errno.EPERM}:
        why = "没有权限"
    elif err in {errno.ENOSPC, errno.EDQUOT}:
        why = "磁盘已满"
    elif err == errno.EROFS:
        why = "只读"
    else:
        why = "无法创建"
    return f"输出文件夹不能写入：{why}"


def mkdir_output(path: Path) -> None:
    """Create an output directory. OSError becomes ValueError with a safe message."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(output_dir_unwritable(exc)) from exc


def blocked_write_results(scans: list[FileScan], message: str) -> list[WriteResult]:
    """Nothing was written. Writable files carry the folder reason; others stay skipped."""
    results: list[WriteResult] = []
    for scan in scans:
        if scan.status == "fail" or not scan.enabled_hits:
            results.append(
                WriteResult(
                    path=scan.path,
                    dest=None,
                    report=None,
                    counts={},
                    skipped=True,
                    error=scan.error if scan.status == "fail" else None,
                )
            )
        else:
            results.append(
                WriteResult(path=scan.path, dest=None, report=None, counts={}, skipped=True, error=message)
            )
    return results


def too_many_file_hits(count: int) -> bool:
    return count > FILE_HIT_WARN


@dataclass
class LocatedHit:
    hit_id: int
    category: str
    text: str
    location: str
    enabled: bool = True
    unit: str = ""
    start: int = 0
    end: int = 0
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    page: int = 0
    # OCR hit that only matched with one misread character (shown as 近似).
    approx: bool = False
    # Set when the hit lies in a header/footer block that 「去掉页眉页脚」 finds.
    hf_role: str = ""
    # Which way that block was found (tag / sign / repeat / word).
    hf_source: str = ""


@dataclass
class FileScan:
    path: Path
    kind: str
    status: str = "ok"  # ok, fail, nohit, cancelled, pending, running
    error: str | None = None
    hits: list[LocatedHit] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    pages: list[list[str]] = field(default_factory=list)
    image: Image.Image | None = None
    too_many: bool = False
    size_label: str = ""
    # Hits found in text whose rectangles could not be placed. The text itself
    # is not stored. A positive count blocks the write of this file.
    unlocated_count: int = 0
    # Why this file must not be written (an image that cannot be checked).
    # Counts only, never source text. Set at scan time; blocks the write.
    blocked_reason: str | None = None
    # Decoded PDF pictures whose OCR failed, retained unchanged in a partial export.
    skipped_images: list[dict] = field(default_factory=list)
    # Word comments found in the file. The write deletes them all.
    comment_count: int = 0
    # 「去掉页眉页脚」. Empty route: the option was off at scan time (or image).
    hf_route: str = ""
    hf_plan: object = field(default=None, repr=False)
    hf_keep: bool = False
    # Per file, not remembered: the user confirmed the blocks found by
    # repetition. The default lives in header_footer.REPEAT_CONFIRMED_BY_DEFAULT.
    hf_confirmed: bool = hf.REPEAT_CONFIRMED_BY_DEFAULT
    hf_watermark: bool = False
    hf_watermark_text: str = field(default="", repr=False)
    # Word: paragraph unit -> role. PDF: (page, line) -> role, and
    # (page, line) -> source. Preview only.
    hf_units: dict = field(default_factory=dict, repr=False)
    hf_lines: dict = field(default_factory=dict, repr=False)
    hf_line_sources: dict = field(default_factory=dict, repr=False)

    def hf_has(self, source: str) -> bool:
        return hf.has_source(self.hf_plan, source)

    @property
    def hf_has_sign(self) -> bool:
        return self.hf_has(hf.SOURCE_SIGN)

    @property
    def hf_has_repeat(self) -> bool:
        return self.hf_has(hf.SOURCE_REPEAT)

    @property
    def hf_found(self) -> bool:
        """Something to remove was found (a signing line alone counts)."""
        return self.hf_route in (hf.ROUTE_TAG, hf.ROUTE_REPEAT, hf.ROUTE_WORD) or self.hf_has_sign

    def hf_source_active(self, source: str) -> bool:
        """Blocks of this source are deleted on write."""
        if not self.hf_found or self.hf_keep or self.status == "fail":
            return False
        if source == hf.SOURCE_REPEAT:
            return bool(self.hf_confirmed)
        return True

    @property
    def hf_active(self) -> bool:
        """Something of this file's headers/footers is deleted on write."""
        if self.hf_plan is None:
            return False
        return any(self.hf_source_active(c.source) for c in self.hf_plan.candidates)

    @property
    def hf_pending(self) -> bool:
        """Blocks found by repetition, not confirmed: kept in the output."""
        return self.hf_has_repeat and not self.hf_keep and self.status != "fail" and not self.hf_confirmed

    def hf_write_plan(self):
        return hf.plan_for_write(self.hf_plan, self.hf_source_active(hf.SOURCE_REPEAT))

    @property
    def hf_delete_pages(self) -> int:
        """PDF pages where something is deleted. Word has no fixed pages: 0."""
        if self.kind != KIND_PDF or self.hf_plan is None:
            return 0
        sources = [s for s in (hf.SOURCE_TAG, hf.SOURCE_SIGN, hf.SOURCE_REPEAT) if self.hf_source_active(s)]
        return len(hf.pages_of(self.hf_plan, sources))

    @property
    def hf_pending_pages(self) -> int:
        """Pages with blocks waiting for confirmation. A page can be in both
        this count and ``hf_delete_pages``; the two are never added up."""
        if not self.hf_pending:
            return 0
        return len(hf.pages_of(self.hf_plan, (hf.SOURCE_REPEAT,)))

    @property
    def hf_sign_pages(self) -> int:
        if self.hf_plan is None or not self.hf_source_active(hf.SOURCE_SIGN):
            return 0
        return len(hf.pages_of(self.hf_plan, (hf.SOURCE_SIGN,)))

    def hf_hidden(self, hit: LocatedHit) -> bool:
        return bool(hit.hf_role) and self.hf_source_active(hit.hf_source or hf.SOURCE_TAG)

    @property
    def visible_hits(self) -> list[LocatedHit]:
        """Hits shown and counted. Those inside a block to be deleted are not."""
        return [hit for hit in self.hits if not self.hf_hidden(hit)]

    @property
    def enabled_hits(self) -> list[LocatedHit]:
        return [hit for hit in self.visible_hits if hit.enabled]

    def will_write(self) -> bool:
        return bool(self.enabled_hits) or self.hf_active

    def enabled_count(self) -> int:
        return len(self.enabled_hits)

    def category_counts(self, *, enabled_only: bool = True) -> dict[str, int]:
        chosen = self.enabled_hits if enabled_only else self.hits
        return counts_from_categories([hit.category for hit in chosen])


@dataclass
class WriteResult:
    path: Path
    dest: Path | None
    report: Path | None
    counts: dict[str, int]
    skipped: bool = False
    error: str | None = None
    note: str | None = None
    short_count: int = 0
    blank_count: int = 0
    hf_removed: bool = False
    skipped_images: list[dict] = field(default_factory=list)


def set_file_hf_confirmed(scan: FileScan, value: bool, *, written: bool = False) -> bool:
    """Confirm (or undo) one file's header/footer blocks found by repetition.

    Per file only; there is no "confirm all". Returns True when the flag
    changed. Ignored after writing, for failed files, files kept by the user,
    and files with nothing found by repetition.
    """
    if written or scan.status == "fail" or scan.hf_keep or not scan.hf_has_repeat:
        return False
    value = bool(value)
    if scan.hf_confirmed == value:
        return False
    scan.hf_confirmed = value
    return True


class IdGen:
    def __init__(self, start: int = 1) -> None:
        self._n = start - 1

    def next(self) -> int:
        self._n += 1
        return self._n


def file_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return KIND_WORD
    if suffix == ".pdf":
        return KIND_PDF
    if is_raster_suffix(suffix):
        return KIND_IMAGE
    return None


def size_label(path: Path) -> str:
    try:
        n = path.stat().st_size
    except OSError:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        kb = n / 1024
        return f"{kb:.0f} KB" if kb >= 10 else f"{kb:.1f} KB"
    mb = n / (1024 * 1024)
    return f"{mb:.1f} MB"


def scan_path(
    path: Path, engine: RedactionEngine, ids: IdGen | None = None, *, remove_hf: bool = False
) -> FileScan:
    ids = ids or IdGen()
    target = Path(path)
    kind = file_kind(target)
    base = FileScan(path=target, kind=kind or "", size_label=size_label(target) if target.exists() else "")
    if not target.is_file():
        base.status = "fail"
        base.error = MSG_MISSING
        return base
    if kind is None:
        base.status = "fail"
        base.error = MSG_UNSUPPORTED
        return base
    try:
        if kind == KIND_WORD:
            return _scan_docx(target, engine, ids, base, remove_hf)
        if kind == KIND_PDF:
            return _scan_pdf(target, engine, ids, base, remove_hf)
        return _scan_image_file(target, engine, ids, base)
    except ValueError as exc:
        message = str(exc)
        if "加密" in message or "password" in message.lower():
            base.error = MSG_ENCRYPTED_PDF
        else:
            base.error = message
        base.status = "fail"
        return base
    except Exception:
        base.status = "fail"
        base.error = MSG_OPEN_FAIL
        return base


def scan_paths(
    paths: list[Path],
    engine: RedactionEngine,
    *,
    cancel: Event | None = None,
    progress=None,
    remove_hf: bool = False,
) -> list[FileScan]:
    ids = IdGen()
    results: list[FileScan] = []
    for index, path in enumerate(paths):
        if cancel is not None and cancel.is_set():
            leftover = FileScan(
                path=Path(path),
                kind=file_kind(Path(path)) or "",
                status="cancelled",
                size_label=size_label(Path(path)) if Path(path).exists() else "",
            )
            results.append(leftover)
            continue
        if progress is not None:
            progress(index, Path(path), "running")
        results.append(scan_path(Path(path), engine, ids, remove_hf=remove_hf))
        if progress is not None:
            progress(index, Path(path), results[-1].status)
    return results


def destination_for(path: Path, outdir: Path, *, partial: bool = False) -> Path:
    marker = "partial-redacted" if partial else "redacted"
    return Path(outdir) / f"{path.stem}.{marker}{path.suffix}"


def report_for(path: Path, outdir: Path) -> Path:
    return Path(outdir) / f"{path.stem}.report.json"


def sequenced_path(path: Path) -> Path:
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def write_scans(
    scans: list[FileScan],
    outdir: Path | str,
    *,
    replacements: dict[str, str] | None = None,
    overwrite: bool = False,
    sequence: bool = False,
    engine: RedactionEngine | None = None,
) -> list[WriteResult]:
    destination = Path(outdir)
    try:
        mkdir_output(destination)
    except ValueError as exc:
        return blocked_write_results(scans, str(exc))
    mapping = replacements or {}
    written: list[WriteResult] = []
    for scan in scans:
        if scan.status == "fail":
            written.append(WriteResult(path=scan.path, dest=None, report=None, counts={}, skipped=True, error=scan.error))
            continue
        if scan.blocked_reason:
            written.append(
                WriteResult(
                    path=scan.path, dest=None, report=None, counts={}, skipped=True, error=scan.blocked_reason
                )
            )
            continue
        if scan.unlocated_count > 0:
            written.append(
                WriteResult(
                    path=scan.path,
                    dest=None,
                    report=None,
                    counts={},
                    skipped=True,
                    error=MSG_UNLOCATED.format(n=scan.unlocated_count),
                )
            )
            continue
        if not scan.will_write():
            # No hit at all: say so, so the original is not taken for a redacted copy.
            note = MSG_NO_HITS if not scan.hits else None
            written.append(WriteResult(path=scan.path, dest=None, report=None, counts={}, skipped=True, note=note))
            continue
        dest = destination_for(scan.path, destination, partial=bool(scan.skipped_images))
        if dest.exists() and not overwrite:
            if sequence:
                dest = sequenced_path(dest)
            else:
                report_path = (
                    dest.with_name(f"{dest.stem}.report.json")
                    if scan.skipped_images else report_for(scan.path, destination)
                )
                written.append(
                    WriteResult(
                        path=scan.path,
                        dest=dest,
                        report=report_path if report_path.is_file() else None,
                        counts={},
                        skipped=True,
                        note=MSG_ALREADY_WRITTEN,
                    )
                )
                continue
        try:
            counts = write_scan(scan, dest, replacements=mapping, engine=engine)
        except Exception as exc:
            # A failed write must not leave a half file that a retry would
            # mistake for "already written".
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            written.append(WriteResult(path=scan.path, dest=None, report=None, counts={}, skipped=True, error=str(exc)))
            continue
        # Unlocated files were skipped above, so a leftover here is never one
        # of those hits counted twice. Any error inside the check blocks only
        # this file; the output is deleted, or the message names it for the
        # user to delete by hand.
        problem = check_written(
            dest,
            lambda: _leftovers_after_write(scan, dest, mapping, engine, counts),
            tagged=lambda: dest.suffix.lower() == ".pdf"
            and _leftovers_after_write(scan, dest, mapping, engine, counts, ignore_tagged=True) == 0,
        )
        if problem is not None:
            written.append(
                WriteResult(path=scan.path, dest=None, report=None, counts={}, skipped=True, error=problem[1])
            )
            continue
        if engine is not None and scan.kind in (KIND_WORD, KIND_PDF):
            labels = set(mapping.values()) | {engine.label(hit.category) for hit in scan.hits}
            labels |= {redaction_label(hit.category) for hit in scan.hits}
            try:
                gate_output(
                    dest,
                    engine,
                    kept=[hit.text for hit in scan.hits if not hit.enabled],
                    labels=sorted(labels),
                    source=scan.path,
                )
            except LeakedText as exc:
                written.append(WriteResult(path=scan.path, dest=None, report=None, counts={}, skipped=True, error=str(exc)))
                continue
        if dest.stem == f"{scan.path.stem}.redacted":
            report_path = destination / f"{scan.path.stem}.report.json"
        else:
            report_path = dest.with_name(f"{dest.stem}.report.json")
        try:
            write_report(report_path, counts)
            write_partial_warning(dest, scan.skipped_images)
        except OSError as exc:
            # Never advertise a partial export whose required warning was not saved.
            dest.unlink(missing_ok=True)
            report_path.unlink(missing_ok=True)
            written.append(WriteResult(
                path=scan.path, dest=None, report=None, counts={}, skipped=True,
                error=output_dir_unwritable(exc),
            ))
            continue
        written.append(
            WriteResult(
                path=scan.path,
                dest=dest,
                report=report_path,
                counts=counts,
                short_count=int(getattr(counts, "short_count", 0) or 0),
                blank_count=int(getattr(counts, "blank_count", 0) or 0),
                hf_removed=scan.hf_active,
                skipped_images=list(scan.skipped_images),
            )
        )
    return written


def _leftovers_after_write(
    scan: FileScan, dest: Path, mapping: dict[str, str], engine, counts=None, ignore_tagged: bool = False
) -> int:
    if scan.kind not in (KIND_WORD, KIND_PDF):
        return 0
    labels = set(mapping.values()) | {redaction_label(hit.category) for hit in scan.hits}
    if engine is not None:
        labels |= {engine.label(hit.category) for hit in scan.hits}
    return count_leftovers(
        dest,
        engine=engine,
        targets=[hit.text for hit in scan.enabled_hits],
        kept=[hit.text for hit in scan.hits if not hit.enabled],
        labels=labels,
        short_codes=int(getattr(counts, "short_count", 0) or 0) > 0,
        known=[hit.text for hit in scan.hits],
        ignore_tagged=ignore_tagged,
    )


def write_report(path: Path, counts: dict[str, int]) -> None:
    path.write_text(
        json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_scan(
    scan: FileScan,
    dest: Path,
    *,
    replacements: dict[str, str] | None = None,
    engine: RedactionEngine | None = None,
) -> dict[str, int]:
    if scan.unlocated_count > 0:
        raise UnlocatedText(scan.unlocated_count)
    if scan.blocked_reason:
        raise WriteBlocked(scan.blocked_reason)
    if Path(dest).resolve() == Path(scan.path).resolve():
        raise ValueError("输出路径与原文件相同，已拒绝覆盖原文件")
    mapping = replacements or {}

    def label(category: str) -> str:
        return mapping.get(category) or redaction_label(category)

    mkdir_output(dest.parent)
    if scan.kind == KIND_WORD:
        return _write_docx(scan, dest, label, engine)
    if scan.kind == KIND_PDF:
        return _write_pdf(scan, dest, label, engine)
    if scan.kind == KIND_IMAGE:
        return _write_image(scan, dest)
    raise ValueError(MSG_UNSUPPORTED)


def session_totals(scans: list[FileScan], *, enabled_only: bool = True) -> tuple[int, dict[str, int]]:
    total = 0
    counts: dict[str, int] = {}
    for scan in scans:
        if scan.status == "fail":
            continue
        hits = scan.enabled_hits if enabled_only else scan.hits
        total += len(hits)
        merge_counts(counts, counts_from_categories([hit.category for hit in hits]))
    return total, counts


def _finish(scan: FileScan) -> FileScan:
    if scan.status == "fail":
        return scan
    if not scan.hits and scan.unlocated_count <= 0 and not scan.blocked_reason and not scan.hf_found and not scan.skipped_images:
        scan.status = "nohit"
    scan.too_many = too_many_file_hits(len(scan.hits))
    return scan


def _scan_docx(path: Path, engine: RedactionEngine, ids: IdGen, scan: FileScan, remove_hf: bool = False) -> FileScan:
    document = Document(str(path))
    roles: dict[str, str] = {}
    if remove_hf:
        plan = hf.identify_docx(document)
        scan.hf_plan = plan
        scan.hf_route = plan.route
        scan.hf_watermark = plan.has_watermark
        scan.hf_watermark_text = plan.watermark_text
        if plan.candidates:
            roles = _docx_unit_roles(document, {c.part for c in plan.candidates})
        scan.hf_units = dict(roles)
    paragraphs: list[str] = []
    # Body and header/footer paragraphs are numbered together; every other
    # corner (footnotes, alt text, link targets...) counts on its own.
    numbers: dict[str, int] = {}
    for index, paragraph in enumerate(iter_document_paragraphs(document)):
        text = paragraph.text or ""
        paragraphs.append(text)
        # A tracked deletion comes just before its paragraph and is reported
        # under that paragraph's number.
        deleted = paragraph.is_deletion
        where = paragraph.where
        number = numbers.get(where, 0)
        unit_name = "段" if not where or where in ("脚注", "尾注", "批注") else "处"
        location = f"第 {number + 1} {unit_name}"
        if where:
            location = f"{where} {location}"
        if deleted:
            location += " · 修订删除"
        else:
            numbers[where] = number + 1
        for hit in paragraph.find_hits(engine):
            scan.hits.append(
                LocatedHit(
                    hit_id=ids.next(),
                    category=hit.category,
                    text=hit.text,
                    location=location,
                    unit=f"p:{index}",
                    start=hit.start,
                    end=hit.end,
                    hf_role=roles.get(f"p:{index}", ""),
                    hf_source=hf.SOURCE_WORD if f"p:{index}" in roles else "",
                )
            )
    scan.paragraphs = paragraphs
    scan.comment_count = count_docx_comments(document)
    scan.blocked_reason = docx_media_block_reason(path)
    if not scan.blocked_reason:
        _scan_docx_media(path, engine, ids, scan)
    return _finish(scan)


def _docx_unit_roles(document, parts: set[str]) -> dict[str, str]:
    """Text units of header/footer parts that will be removed.

    Same walk as ``iter_document_paragraphs``: body units first, then each
    section's own headers and footers (text boxes, content controls and
    tracked changes inside them included).
    """
    from doc_redactor.docx_redactor import _owned_headers_footers, iter_text_units

    index = sum(1 for _ in iter_text_units(document._element))
    roles: dict[str, str] = {}
    for section in document.sections:
        for part in _owned_headers_footers(section):
            role = hf.ROLE_HEADER if type(part).__name__ == "_Header" else hf.ROLE_FOOTER
            name = str(part.part.partname)
            for _unit in iter_text_units(part._element):
                if name in parts:
                    roles[f"p:{index}"] = role
                index += 1
    return roles


def _scan_docx_media(path: Path, engine: RedactionEngine, ids: IdGen, scan: FileScan) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = [info.filename for info in archive.infolist() if _is_media(info.filename)]
            payloads = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile):
        return
    if not payloads:
        return
    # docx_media_block_reason already refused files with pictures that do not
    # decode, or no Tesseract. Anything failing here still blocks the write.
    for media_index, (name, payload) in enumerate(payloads.items(), start=1):
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                opened.load()
                entities = analyze_image_entities(opened, engine)
        except Exception as exc:
            scan.blocked_reason = _blocked_message(exc)
            scan.hits = [hit for hit in scan.hits if not hit.unit.startswith("media:")]
            return
        for entity in entities:
            top = min(box[1] for box in entity["boxes"]) if entity["boxes"] else 0
            scan.hits.append(
                LocatedHit(
                    hit_id=ids.next(),
                    category=entity["category"],
                    text=entity["text"],
                    approx=bool(entity.get("approx")),
                    location=f"内嵌图 {media_index} · 图内 第 {_row_of(top)} 行",
                    unit=f"media:{name}",
                    boxes=list(entity["boxes"]),
                )
            )


def _scan_pdf(path: Path, engine: RedactionEngine, ids: IdGen, scan: FileScan, remove_hf: bool = False) -> FileScan:
    document = pymupdf.open(path)
    try:
        if document.is_encrypted:
            raise ValueError(MSG_ENCRYPTED_PDF)
        plan = None
        if remove_hf:
            # Before prepare_pdf: it drops the /Pagination artifact tags.
            plan = hf.identify_pdf(document)
            scan.hf_plan = plan
            scan.hf_route = plan.route
        prepare_pdf(document)
        pages: list[list[str]] = []
        for page_index, page in enumerate(document):
            textpage = page_textpage(page)
            text = page.get_text("text", textpage=textpage)
            lines = text.splitlines()
            pages.append(lines)
            if plan is not None and plan.candidates:
                for line_index, (role, source) in hf.pdf_line_blocks(page, plan, lines).items():
                    scan.hf_lines[(page_index, line_index)] = role
                    scan.hf_line_sources[(page_index, line_index)] = source
            found = find_page_hits(engine, text)
            located = locate_text_hits(page, found, textpage)
            for hit, rects in zip(found, located):
                block = hf.block_at(plan, page_index, _union(rects))
                if not rects:
                    # Count only. The hit text is not kept on the scan.
                    scan.unlocated_count += 1
                    continue
                line_no = text[: hit.start].count("\n") + 1
                scan.hits.append(
                    LocatedHit(
                        hit_id=ids.next(),
                        category=hit.category,
                        text=hit.text,
                        location=f"第 {page_index + 1} 页 · 第 {line_no} 行",
                        unit=f"page:{page_index}",
                        start=hit.start,
                        end=hit.end,
                        rects=rects,
                        page=page_index,
                        hf_role=block[0],
                        hf_source=block[1],
                    )
                )
        scan.pages = pages
        scan.blocked_reason = pdf_image_block_reason(document)
        if not scan.blocked_reason:
            try:
                _scan_pdf_images(document, engine, ids, scan)
            except Exception as exc:
                scan.blocked_reason = _blocked_message(exc)
                scan.hits = [hit for hit in scan.hits if not hit.unit.startswith(("pdfimg:", "pdfinline:"))]
    finally:
        document.close()
    return _finish(scan)


def _union(rects):
    if not rects:
        return None
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _scan_pdf_images(document, engine: RedactionEngine, ids: IdGen, scan: FileScan) -> None:
    seen: set[int] = set()
    skipped_xrefs: dict[int, int] = {}
    image_index = 0
    for page in document:
        images = page.get_images(full=True) or []
        masks = {item[1] for item in images if len(item) > 1 and item[1]}
        for item in images:
            xref = item[0]
            if xref in skipped_xrefs:
                location = skipped_pdf_image(page.number + 1, skipped_xrefs[xref], f"pdfimg:{xref}")
                if location not in scan.skipped_images:
                    scan.skipped_images.append(location)
            if not xref or xref in seen or xref in masks:
                continue
            seen.add(xref)
            image_index += 1
            try:
                image = pdf_xref_image(document, xref, item[1] if len(item) > 1 else 0)
            except Exception as exc:
                raise WriteBlocked(MSG_BAD_IMAGE_AT.format(page=page.number + 1, index=image_index)) from exc
            try:
                entities = _ocr_entities(image, engine, page.number + 1, image_index)
            except PdfImageOcrFailed:
                scan.skipped_images.append(skipped_pdf_image(page.number + 1, image_index, f"pdfimg:{xref}"))
                skipped_xrefs[xref] = image_index
                continue
            for entity in entities:
                top = min(box[1] for box in entity["boxes"]) if entity["boxes"] else 0
                scan.hits.append(
                    LocatedHit(
                        hit_id=ids.next(),
                        category=entity["category"],
                        text=entity["text"],
                        approx=bool(entity.get("approx")),
                        location=f"第 {page.number + 1} 页图 {image_index} · 图内 第 {_row_of(top)} 行",
                        unit=f"pdfimg:{xref}",
                        boxes=list(entity["boxes"]),
                        page=page.number,
                    )
                )
    for page in document:
        try:
            found = inline_images(page)
        except WriteBlocked as exc:
            index = image_index + getattr(exc, "inline_position", 0) + 1
            raise WriteBlocked(MSG_BAD_IMAGE_AT.format(page=page.number + 1, index=index)) from exc
        for inline_index, (info, image) in enumerate(found):
            image_index += 1
            try:
                entities = _ocr_entities(image, engine, page.number + 1, image_index)
            except PdfImageOcrFailed:
                scan.skipped_images.append(skipped_pdf_image(
                    page.number + 1, image_index, f"pdfinline:{page.number}:{inline_index}"
                ))
                continue
            for entity in entities:
                top = min(box[1] for box in entity["boxes"]) if entity["boxes"] else 0
                scan.hits.append(
                    LocatedHit(
                        hit_id=ids.next(),
                        category=entity["category"],
                        text=entity["text"],
                        approx=bool(entity.get("approx")),
                        location=f"第 {page.number + 1} 页图 {image_index} · 图内 第 {_row_of(top)} 行",
                        unit=f"pdfinline:{page.number}:{inline_index}",
                        boxes=list(entity["boxes"]),
                        rects=inline_boxes_to_rects(info, image.size, entity["boxes"]),
                        page=page.number,
                    )
                )


def _ocr_entities(image, engine: RedactionEngine, page_no: int, index: int) -> list[dict]:
    """OCR one PDF image. An OCR error names the image instead of calling it unopenable."""
    return checked_pdf_image_entities(image, engine, page_no, index, analyze=analyze_image_entities)


def _blocked_message(exc: Exception) -> str:
    if isinstance(exc, WriteBlocked):
        return str(exc)
    if isinstance(exc, RuntimeError) and "Tesseract" in str(exc):
        return MSG_NO_OCR.format(n=1)
    return MSG_BAD_IMAGE.format(n=1)


def _scan_image_file(path: Path, engine: RedactionEngine, ids: IdGen, scan: FileScan) -> FileScan:
    require_tesseract()
    with Image.open(path) as opened:
        opened.load()
        image = opened.convert("RGB")
        entities = analyze_image_entities(image, engine)
        scan.image = image.copy()
    for entity in entities:
        top = min(box[1] for box in entity["boxes"]) if entity["boxes"] else 0
        scan.hits.append(
            LocatedHit(
                hit_id=ids.next(),
                category=entity["category"],
                text=entity["text"],
                approx=bool(entity.get("approx")),
                location=f"图内 第 {_row_of(top)} 行",
                unit="image",
                boxes=list(entity["boxes"]),
            )
        )
    return _finish(scan)


def _write_docx(scan: FileScan, dest: Path, label, engine: RedactionEngine | None) -> dict[str, int]:
    document = Document(str(scan.path))
    # Unit numbers are those of the scan, so take the units before any
    # header/footer part is emptied (emptying would renumber what follows).
    units = list(iter_document_paragraphs(document))
    if scan.hf_active:
        # Headers/footers first, then the rules (header-footer-flow.md 5).
        # Hits inside removed parts are hidden, so none of them is applied.
        hf.apply_docx(document, scan.hf_write_plan())
    by_unit: dict[str, list[LocatedHit]] = {}
    for hit in scan.enabled_hits:
        by_unit.setdefault(hit.unit, []).append(hit)
    categories: list[str] = []
    for index, paragraph in enumerate(units):
        unit_hits = by_unit.get(f"p:{index}", [])
        if not unit_hits:
            continue
        engine_hits = [
            Hit(start=hit.start, end=hit.end, category=hit.category, text=hit.text) for hit in unit_hits
        ]
        categories.extend(apply_hits_to_paragraph(paragraph, engine_hits, label))
    finish_document(document)
    scrub_docx_hidden(document)
    mkdir_output(dest.parent)
    document.save(str(dest))
    if scan.hf_active and hf.verify_docx_output(dest):
        raise hf.HeaderFooterLeft(hf.verify_docx_output(dest), 0)
    media_counts = _write_docx_media(dest, by_unit)
    counts = counts_from_categories(categories)
    merge_counts(counts, media_counts)
    expected = scan.category_counts(enabled_only=True)
    # Prefer the scan-side enabled counts so uncheck stays consistent even if a
    # paragraph apply skipped a stale span. Image/media still merged from work.
    if expected:
        return expected
    return counts


def _write_docx_media(dest: Path, by_unit: dict[str, list[LocatedHit]]) -> dict[str, int]:
    media_units = {key: hits for key, hits in by_unit.items() if key.startswith("media:")}
    if not media_units:
        return {}
    with zipfile.ZipFile(dest, "r") as archive:
        names = [info.filename for info in archive.infolist() if _is_media(info.filename)]
        payloads = {name: archive.read(name) for name in names}
        infos = archive.infolist()
    replaced: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    for name, payload in payloads.items():
        hits = media_units.get(f"media:{name}", [])
        if not hits:
            continue
        boxes: list[tuple[int, int, int, int]] = []
        for hit in hits:
            boxes.extend(hit.boxes)
            counts[hit.category] = counts.get(hit.category, 0) + 1
        with Image.open(io.BytesIO(payload)) as opened:
            opened.load()
            painted = paint_image_boxes(opened, boxes)
        buf = io.BytesIO()
        fmt = _format_for_suffix(Path(name).suffix)
        if fmt == "JPEG":
            painted.save(buf, format=fmt, quality=95)
        else:
            painted.save(buf, format=fmt)
        replaced[name] = buf.getvalue()
    if not replaced:
        return {}
    buffer_path = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(dest, "r") as source, zipfile.ZipFile(buffer_path, "w") as target:
        for info in infos:
            data = replaced.get(info.filename, source.read(info.filename))
            cloned = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            cloned.compress_type = info.compress_type
            cloned.external_attr = info.external_attr
            cloned.flag_bits = info.flag_bits
            cloned.create_system = info.create_system
            target.writestr(cloned, data)
    buffer_path.replace(dest)
    return counts


def _write_pdf(scan: FileScan, dest: Path, label, engine: RedactionEngine | None) -> RedactionCounts:
    document = pymupdf.open(scan.path)
    try:
        if document.is_encrypted:
            raise ValueError(MSG_ENCRYPTED_PDF)
        if scan.hf_active:
            # Tags and the signing line always; blocks found by repetition
            # only after the user confirmed this file (产品 10-19, 选 b).
            # Before prepare_pdf, which drops the /Pagination artifact tags.
            hf.apply_pdf(document, scan.hf_write_plan())
        prepare_pdf(document)
        order = list(getattr(engine, "category_order", []) or [])
        items = []
        for hit in scan.enabled_hits:
            # Image hits are black boxes. They have no text rectangle.
            if not hit.rects or str(hit.unit).startswith(("pdfimg:", "pdfinline:")):
                continue
            items.append((hit.page, list(hit.rects), hit.category, hit.text))
        stats = redact_text_placeholders(document, items, label, order)
        image_units = [hit for hit in scan.enabled_hits if hit.unit.startswith("pdfimg:")]
        if image_units:
            by_xref: dict[int, list[LocatedHit]] = {}
            for hit in image_units:
                xref = int(hit.unit.split(":", 1)[1])
                by_xref.setdefault(xref, []).append(hit)
            for page in document:
                images = page.get_images(full=True) or []
                for item in images:
                    xref = item[0]
                    if xref not in by_xref:
                        continue
                    image = pdf_xref_image(document, xref, item[1] if len(item) > 1 else 0)
                    boxes: list[tuple[int, int, int, int]] = []
                    for hit in by_xref[xref]:
                        boxes.extend(hit.boxes)
                    painted = paint_image_boxes(image, boxes)
                    buf = io.BytesIO()
                    painted.save(buf, format="PNG")
                    page.replace_image(xref, stream=buf.getvalue())
        inline_units = [hit for hit in scan.enabled_hits if hit.unit.startswith("pdfinline:")]
        if inline_units:
            by_page: dict[int, dict[int, list[LocatedHit]]] = {}
            for hit in inline_units:
                _tag, page_no, index = hit.unit.split(":")
                by_page.setdefault(int(page_no), {}).setdefault(int(index), []).append(hit)
            for page_no, by_index in by_page.items():
                page = document[page_no]
                found = inline_images(page)
                jobs = []
                for index, hits in by_index.items():
                    if index >= len(found):
                        raise WriteBlocked(MSG_INLINE_FAILED.format(n=len(by_index)))
                    info, image = found[index]
                    jobs.append((info, image.size, [box for hit in hits for box in hit.boxes]))
                paint_inline_images(page, jobs, protected_rects=skipped_image_rects(page, scan.skipped_images))
        if stats.used:
            append_legend(document, legend_lines(stats.used))
        clear_pdf_hidden(document)
        mkdir_output(dest.parent)
        document.save(dest, garbage=4, clean=True)
    finally:
        document.close()
    return RedactionCounts(
        scan.category_counts(enabled_only=True),
        short_count=stats.short_count,
        blank_count=stats.blank_count,
        skipped_images=scan.skipped_images,
    )


def _write_image(scan: FileScan, dest: Path) -> dict[str, int]:
    boxes: list[tuple[int, int, int, int]] = []
    for hit in scan.enabled_hits:
        boxes.extend(hit.boxes)
    with Image.open(scan.path) as opened:
        opened.load()
        painted = paint_image_boxes(opened, boxes)
    suffix = dest.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        painted.save(dest, format="JPEG", quality=95)
    else:
        painted.save(dest, format="PNG")
    return scan.category_counts(enabled_only=True)


def _is_media(name: str) -> bool:
    lowered = name.lower()
    if not lowered.startswith("word/media/"):
        return False
    return is_embedded_raster_suffix(Path(lowered).suffix)


def _row_of(top: int, row_height: int = 36) -> int:
    return max(1, int(top) // row_height + 1)
