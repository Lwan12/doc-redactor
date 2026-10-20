"""Command line entry: python -m doc_redactor <files> -r rules.yaml -o outdir."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from doc_redactor.config import load_rules
from doc_redactor.docx_redactor import redact_docx
from doc_redactor.engine import RedactionEngine
from doc_redactor.image_redactor import is_raster_suffix, redact_image_file
from doc_redactor.image_redactor import MSG_NO_OCR_FIX, WriteBlocked, blocked_advice
from doc_redactor.leak_check import LeakedText, NoHits, gate_output
from doc_redactor.pdf_redactor import UnlocatedText, redact_pdf
from doc_redactor.verify_output import LeftoverText, check_written, count_leftovers, original_hit_texts

SUPPORTED = {".docx", ".pdf", ".png", ".jpg", ".jpeg"}


def redact_paths(
    inputs: list[str | Path],
    rules_path: str | Path,
    outdir: str | Path,
) -> tuple[list[tuple[str, dict[str, int]]], list[str]]:
    """Redact each file. A file with unlocatable text is skipped, not written.

    A file with no hit at all is not written. A file whose output still
    shows a hit after writing (the rescan, then the other-reader check) is
    deleted and skipped the same way. Other errors still propagate. The second
    list is ``name: reason`` lines for files that were not written.
    """
    engine = load_rules(rules_path)
    destination = Path(outdir)
    destination.mkdir(parents=True, exist_ok=True)
    summaries: list[tuple[str, dict[str, int]]] = []
    blocked: list[str] = []
    for raw in inputs:
        src = Path(raw)
        try:
            counts = redact_path(src, destination, engine)
        except (UnlocatedText, LeftoverText, LeakedText, NoHits, WriteBlocked) as exc:
            line = f"{src.name}: {exc}"
            if isinstance(exc, WriteBlocked) and blocked_advice(str(exc)) == MSG_NO_OCR_FIX:
                line += f"。{MSG_NO_OCR_FIX}"
            blocked.append(line)
            continue
        summaries.append((src.name, counts))
    return summaries, blocked


def redact_path(src: Path, outdir: Path, engine: RedactionEngine) -> dict[str, int]:
    if not src.is_file():
        raise FileNotFoundError(f"输入文件不存在: {src}")
    suffix = src.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"不支持的文件类型: {src.name}")
    redacted = outdir / f"{src.stem}.redacted{src.suffix}"
    report = outdir / f"{src.stem}.report.json"
    if suffix == ".docx":
        counts = redact_docx(src, redacted, engine)
    elif suffix == ".pdf":
        counts = redact_pdf(src, redacted, engine)
    elif is_raster_suffix(suffix):
        counts = redact_image_file(src, redacted, engine)
    else:
        raise ValueError(f"不支持的文件类型: {src.name}")
    if not counts:
        # Writing it would hand back the original under a ".redacted" name.
        redacted.unlink(missing_ok=True)
        raise NoHits()
    if suffix in (".docx", ".pdf"):
        # Unlocated hits never reach here: redact_pdf raises before saving.
        labels = {engine.label(category) for category in counts}
        short = int(getattr(counts, "short_count", 0) or 0) > 0
        known = original_hit_texts(src, engine)
        check = dict(engine=engine, labels=labels, short_codes=short, known=known)
        problem = check_written(
            redacted,
            lambda: count_leftovers(redacted, **check),
            tagged=lambda: suffix == ".pdf" and count_leftovers(redacted, ignore_tagged=True, **check) == 0,
        )
        if problem is not None:
            raise LeftoverText(*problem)
        # Then read it the other ways an AI tool would (leak_check).
        gate_output(redacted, engine, labels=[engine.label(category) for category in counts], source=src)
    report.write_text(
        json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return counts


def format_summary(name: str, counts: dict[str, int]) -> str:
    lines = [name]
    if not counts:
        lines.append("  （无命中）")
    else:
        for key in sorted(counts):
            lines.append(f"  {key}: {counts[key]}")
    return "\n".join(lines)


def _utf8_stdio() -> None:
    """Chinese help and messages must not crash on an English Windows pipe (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="doc-redactor",
        description="按规则离线脱敏 docx、pdf 和 png/jpg/jpeg。不覆盖原文件。",
    )
    parser.add_argument("inputs", nargs="+", help="输入文件")
    parser.add_argument("-r", "--rules", required=True, help="规则文件 rules.yaml")
    parser.add_argument("-o", "--output", required=True, help="输出目录")
    args = parser.parse_args(argv)
    try:
        summaries, blocked = redact_paths(args.inputs, args.rules, args.output)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    for name, counts in summaries:
        print(format_summary(name, counts))
    for line in blocked:
        print(line, file=sys.stderr)
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
