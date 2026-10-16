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
from doc_redactor.pdf_redactor import redact_pdf

SUPPORTED = {".docx", ".pdf", ".png", ".jpg", ".jpeg"}


def redact_paths(
    inputs: list[str | Path],
    rules_path: str | Path,
    outdir: str | Path,
) -> list[tuple[str, dict[str, int]]]:
    engine = load_rules(rules_path)
    destination = Path(outdir)
    destination.mkdir(parents=True, exist_ok=True)
    summaries: list[tuple[str, dict[str, int]]] = []
    for raw in inputs:
        src = Path(raw)
        counts = redact_path(src, destination, engine)
        summaries.append((src.name, counts))
    return summaries


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc-redactor",
        description="按规则离线脱敏 docx、pdf 和 png/jpg/jpeg。不覆盖原文件。",
    )
    parser.add_argument("inputs", nargs="+", help="输入文件")
    parser.add_argument("-r", "--rules", required=True, help="规则文件 rules.yaml")
    parser.add_argument("-o", "--output", required=True, help="输出目录")
    args = parser.parse_args(argv)
    try:
        summaries = redact_paths(args.inputs, args.rules, args.output)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    for name, counts in summaries:
        print(format_summary(name, counts))
    return 0
