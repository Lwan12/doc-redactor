"""Locate the Tesseract executable without contacting the network."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

TESSERACT_HINT = (
    "未找到 Tesseract OCR。图片脱敏需要 tesseract，并带上 chi_sim 与 eng 语言数据。"
    "请安装后加入 PATH，或设置环境变量 TESSERACT_CMD 指向可执行文件。"
    "示例：TESSERACT_CMD=/path/to/tesseract"
)

def find_tesseract() -> str | None:
    """Search TESSERACT_CMD, then the exe directory and _MEIPASS, then PATH."""
    env = os.environ.get("TESSERACT_CMD", "").strip()
    if env:
        found = _existing_command(env)
        if found:
            return found
    for candidate in _bundled_candidates():
        if candidate.is_file():
            return str(candidate)
    return shutil.which("tesseract")


def configure_tesseract() -> str | None:
    """Point pytesseract at the discovered binary.

    When the binary ships with a sibling tessdata directory (the PyInstaller
    layout), TESSDATA_PREFIX is set so runtime stays offline.
    """
    global _CONFIGURED
    command = find_tesseract()
    if not command:
        return None
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = command
    tessdata = Path(command).resolve().parent / "tessdata"
    if tessdata.is_dir():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
    return command


def require_tesseract() -> str:
    command = configure_tesseract()
    if not command:
        raise RuntimeError(TESSERACT_HINT)
    return command


def _existing_command(value: str) -> str | None:
    path = Path(value)
    if path.is_file():
        return str(path)
    found = shutil.which(value)
    return found


def _bundled_candidates() -> list[Path]:
    names = ("tesseract.exe", "tesseract")
    bases: list[Path] = []
    executable = getattr(sys, "executable", "")
    if executable:
        bases.append(Path(executable).resolve().parent / "tesseract")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass) / "tesseract")
    candidates: list[Path] = []
    for base in bases:
        for name in names:
            candidates.append(base / name)
    return candidates
