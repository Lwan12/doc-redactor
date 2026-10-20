"""Windows OCR child paths; all documents and image text are fictional."""

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytesseract
from PIL import Image

from doc_redactor import tesseract_cmd


def test_adapter_preserves_process_options_and_parent_environment(tmp_path):
    folder = tmp_path / "中文 软件" / "tesseract"
    (folder / "tessdata").mkdir(parents=True)
    inherited = {"TESSDATA_PREFIX": "旧中文路径", "PATH": "keep-path", "SystemRoot": "keep-root"}
    startup = object()
    calls = []
    def original(include_stdout=True):
        calls.append(include_stdout)
        return {"env": inherited, "stdin": 11, "stderr": 12, "startupinfo": startup,
                "stdout": 13 if include_stdout else 14}
    module = SimpleNamespace(tesseract_cmd=str(folder / "tesseract.exe"), subprocess_args=original)
    cwd = os.getcwd()
    tesseract_cmd._install_windows_ocr_adapter(module)
    wrapped = module.subprocess_args
    tesseract_cmd._install_windows_ocr_adapter(module)
    assert module.subprocess_args is wrapped
    for stdout in (True, False):
        args = module.subprocess_args(stdout)
        assert args["cwd"] == str(folder)
        assert args["env"] == dict(inherited, TESSDATA_PREFIX="tessdata")
        assert args["env"] is not inherited
        assert args["startupinfo"] is startup
        assert (args["stdin"], args["stderr"], args["stdout"]) == (11, 12, 13 if stdout else 14)
    assert calls == [True, False]
    assert inherited["TESSDATA_PREFIX"] == "旧中文路径"
    assert os.getcwd() == cwd


def test_external_install_without_sibling_data_is_unchanged(tmp_path):
    options = {"env": {"TESSDATA_PREFIX": "custom-data"}, "stdin": 1, "stdout": 2}
    module = SimpleNamespace(
        tesseract_cmd=str(tmp_path / "external.exe"), subprocess_args=lambda _stdout=True: options
    )
    tesseract_cmd._install_windows_ocr_adapter(module)
    assert module.subprocess_args() is options
    assert "cwd" not in options


def test_windows_configuration_resolves_exe_without_mutating_environment(tmp_path, monkeypatch):
    folder = tmp_path / "中文 软件" / "tesseract"
    (folder / "tessdata").mkdir(parents=True)
    binary = folder / "tesseract.exe"
    binary.touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESSERACT_CMD", str(binary.relative_to(tmp_path)))
    monkeypatch.setenv("TESSDATA_PREFIX", "retain-parent-value")
    monkeypatch.setattr(tesseract_cmd.sys, "platform", "win32")
    module = SimpleNamespace(tesseract_cmd="", subprocess_args=lambda _stdout=True: {"env": os.environ})
    monkeypatch.setattr(pytesseract, "pytesseract", module)
    cwd = os.getcwd()
    assert tesseract_cmd.configure_tesseract() == str(binary)
    assert module.tesseract_cmd == str(binary)
    assert os.environ["TESSDATA_PREFIX"] == "retain-parent-value"
    assert module.subprocess_args()["env"]["TESSDATA_PREFIX"] == "tessdata"
    assert os.getcwd() == cwd


def test_pytesseract_uses_absolute_image_and_output_paths():
    # The child cwd must not reinterpret a caller's relative image/output path.
    with pytesseract.pytesseract.save(Image.new("RGB", (12, 12), "white")) as (output, image):
        assert Path(output).is_absolute() and Path(image).is_absolute()
        assert Path(image).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="requires the real Windows Tesseract build")
def test_windows_gui_ocr_path_in_chinese_folder(tmp_path, monkeypatch):
    from helpers import ROOT, check_tesseract
    from doc_redactor.config import load_rules
    from doc_redactor.image_redactor import analyze_image_entities

    check_tesseract()
    original = Path(tesseract_cmd.find_tesseract())
    if not (original.parent / "tessdata").is_dir():
        pytest.fail("Windows OCR integration requires sibling tessdata")
    app = tmp_path / "中文 软件"
    shutil.copytree(original.parent, app / "tesseract")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    # Deliberately wrong and non-ASCII: the child must use its own sibling data.
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path / "不存在的语言目录"))
    monkeypatch.setattr(sys, "executable", str(app / "doc-redactor-gui.exe"))
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", pytesseract.pytesseract.tesseract_cmd)
    monkeypatch.setattr(pytesseract.pytesseract, "subprocess_args", pytesseract.pytesseract.subprocess_args)
    cwd = os.getcwd()
    with Image.open(ROOT / "samples" / "sample.png") as opened:
        image = opened.convert("RGB")
    entities = analyze_image_entities(image, load_rules(ROOT / "rules.example.yaml"))
    assert {"PROJECT_ID", "SPONSOR", "DRUG"} <= {item["category"] for item in entities}
    assert os.getcwd() == cwd
    assert os.environ["TESSDATA_PREFIX"].endswith("不存在的语言目录")
