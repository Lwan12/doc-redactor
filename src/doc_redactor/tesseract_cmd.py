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

    On Windows, bundled language data is selected in each OCR child's own
    environment and working directory. The app's cwd is never changed.
    """
    command = find_tesseract()
    if not command:
        return None
    import pytesseract

    # The child cwd can differ from ours, so never leave the executable relative.
    command = str(Path(command).resolve())
    pytesseract.pytesseract.tesseract_cmd = command
    tessdata = Path(command).parent / "tessdata"
    if sys.platform == "win32":
        _install_windows_ocr_adapter(pytesseract.pytesseract)
    elif tessdata.is_dir():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
    return command


def _install_windows_ocr_adapter(module) -> None:
    """Adapt the pinned pytesseract 0.3.13 OCR subprocess kwargs, once.

    It has no public per-call cwd/env option. Preserve its pipes, startupinfo
    and timeout handling; do not patch subprocess itself or temporarily chdir.
    Version queries don't load tessdata. get_languages bypasses this helper
    and is not used by the app.
    """
    original = module.subprocess_args
    if getattr(original, "_doc_redactor_windows_ocr", False):
        return

    def child_args(include_stdout=True):
        kwargs = original(include_stdout)
        folder = Path(module.tesseract_cmd).parent
        if (folder / "tessdata").is_dir():
            # Tesseract 5.5.3 passes getenv(TESSDATA_PREFIX) to std::filesystem.
            # Its Windows build can reject a Chinese absolute path. A relative
            # ASCII path avoids that conversion, even when folder is Chinese.
            kwargs["cwd"] = str(folder)
            env = kwargs.get("env")
            kwargs["env"] = dict(os.environ if env is None else env)
            kwargs["env"]["TESSDATA_PREFIX"] = "tessdata"
        return kwargs

    child_args._doc_redactor_windows_ocr = True
    module.subprocess_args = child_args


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
