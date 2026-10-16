"""Zip the Windows onedir folder and write SHA256SUMS.txt.

The folder must already contain both executables, an asInvoker manifest
embedded in each exe, and tesseract/ with DLLs plus chi_sim and eng data.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "doc-redactor.exe",
    "doc-redactor-gui.exe",
    "tesseract/tesseract.exe",
    "tesseract/tessdata/eng.traineddata",
    "tesseract/tessdata/chi_sim.traineddata",
)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _contains(data: bytes, text: str) -> bool:
    return text.encode("utf-8") in data or text.encode("utf-16le") in data


def assert_as_invoker(path: Path) -> None:
    data = path.read_bytes()
    if not _contains(data, 'level="asInvoker"') and not _contains(data, "level='asInvoker'"):
        fail(f"{path.name} manifest is missing requestedExecutionLevel asInvoker")
    if _contains(data, "requireAdministrator"):
        fail(f"{path.name} manifest requests administrator")


def assert_layout(root: Path) -> None:
    if not root.is_dir():
        fail(f"onedir folder not found: {root}")
    for relative in REQUIRED:
        if not (root / relative).is_file():
            fail(f"package missing {relative}")
    dlls = [path for path in (root / "tesseract").glob("*.dll") if path.is_file()]
    if not dlls:
        fail("package tesseract/ has no DLLs")
    assert_as_invoker(root / "doc-redactor.exe")
    assert_as_invoker(root / "doc-redactor-gui.exe")


def write_zip(root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    top = root.name
    files = sorted(path for path in root.rglob("*") if path.is_file())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            archive.write(path, f"{top}/{relative}")


def write_sums(zip_path: Path, sums_path: Path) -> str:
    digest = hashlib.sha256()
    with zip_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    line = f"{digest.hexdigest()}  {zip_path.name}\n"
    sums_path.write_text(line, encoding="ascii")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        fail("usage: make_windows_zip.py <onedir> <zip-path> <SHA256SUMS.txt>")
    root = Path(args[0])
    zip_path = Path(args[1])
    sums_path = Path(args[2])
    assert_layout(root)
    write_zip(root, zip_path)
    digest = write_sums(zip_path, sums_path)
    print(f"{digest}  {zip_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
