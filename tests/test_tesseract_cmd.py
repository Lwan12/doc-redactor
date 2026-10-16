import sys
from pathlib import Path

import pytesseract

from doc_redactor.tesseract_cmd import configure_tesseract, find_tesseract


def test_env_is_preferred(monkeypatch, tmp_path: Path):
    binary = tmp_path / "from-env"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("TESSERACT_CMD", str(binary))
    monkeypatch.setattr("doc_redactor.tesseract_cmd.shutil.which", lambda _name: "/usr/bin/tesseract")
    assert find_tesseract() == str(binary)


def test_exe_dir_beats_meipass_and_path(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    app_dir = tmp_path / "app"
    bundled = app_dir / "tesseract"
    bundled.mkdir(parents=True)
    exe_copy = bundled / "tesseract.exe"
    exe_copy.write_text("exe", encoding="utf-8")
    meipass = tmp_path / "meipass"
    (meipass / "tesseract").mkdir(parents=True)
    (meipass / "tesseract" / "tesseract.exe").write_text("mei", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(app_dir / "doc-redactor.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr("doc_redactor.tesseract_cmd.shutil.which", lambda _name: "/usr/bin/tesseract")
    assert find_tesseract() == str(exe_copy)


def test_meipass_then_path(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    meipass = tmp_path / "meipass"
    binary = meipass / "tesseract" / "tesseract"
    binary.parent.mkdir(parents=True)
    binary.write_text("mei", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(app_dir / "doc-redactor"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr("doc_redactor.tesseract_cmd.shutil.which", lambda _name: None)
    assert find_tesseract() == str(binary)

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr("doc_redactor.tesseract_cmd.shutil.which", lambda _name: "/opt/tesseract")
    assert find_tesseract() == "/opt/tesseract"


def test_configure_sets_sibling_tessdata(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    folder = tmp_path / "tesseract"
    folder.mkdir()
    binary = folder / "tesseract"
    binary.write_text("x", encoding="utf-8")
    (folder / "tessdata").mkdir()
    monkeypatch.setenv("TESSERACT_CMD", str(binary))
    monkeypatch.setattr(
        pytesseract.pytesseract,
        "tesseract_cmd",
        pytesseract.pytesseract.tesseract_cmd,
        raising=False,
    )
    assert configure_tesseract() == str(binary)
    assert Path(pytesseract.pytesseract.tesseract_cmd) == binary
    import os

    assert os.environ["TESSDATA_PREFIX"] == str(folder / "tessdata")
