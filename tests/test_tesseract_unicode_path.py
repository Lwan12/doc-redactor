"""Windows OCR child paths; all documents and image text are fictional."""

import os
import shutil
import sys
import tempfile
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


def test_windows_ocr_child_gets_ascii_names_when_temp_path_is_chinese(tmp_path):
    """Windows Tesseract rejects a Chinese absolute path. The child must see ASCII names."""
    folder = tmp_path / "中文 软件" / "tesseract"
    (folder / "tessdata").mkdir(parents=True)
    seen = []

    def original(input_filename, output_filename_base, extension, lang, config="", nice=0, timeout=0):
        seen.append((input_filename, output_filename_base, extension, lang))
        assert input_filename.isascii() and not Path(input_filename).is_absolute()
        assert output_filename_base.isascii() and not Path(output_filename_base).is_absolute()
        (folder / f"{output_filename_base}.{extension}").write_text("Project ABC-2026-001\n", encoding="utf-8")

    module = SimpleNamespace(
        tesseract_cmd=str(folder / "tesseract.exe"),
        subprocess_args=lambda include_stdout=True: {"cwd": str(folder)},
        run_tesseract=original,
    )
    tesseract_cmd._install_windows_ocr_adapter(module)
    tesseract_cmd._install_windows_ocr_adapter(module)
    chinese_temp = tmp_path / "临时 目录"
    chinese_temp.mkdir()
    image = chinese_temp / "CAPA回复1.PNG"
    image.write_bytes(b"png-bytes")
    output_base = str(chinese_temp / "tess_out")
    module.run_tesseract(str(image), output_base, "txt", "chi_sim+eng")
    assert seen == [(seen[0][0], seen[0][1], "txt", "chi_sim+eng")]
    assert (folder / seen[0][0]).is_file() is False
    assert list(folder.glob("ocr*")) == []
    assert Path(output_base + ".txt").read_text(encoding="utf-8") == "Project ABC-2026-001\n"


def test_windows_ocr_staging_cleans_up_when_the_child_fails(tmp_path):
    folder = tmp_path / "中文 软件" / "tesseract"
    (folder / "tessdata").mkdir(parents=True)

    def original(input_filename, output_filename_base, extension, lang, config="", nice=0, timeout=0):
        assert input_filename.isascii() and not Path(input_filename).is_absolute()
        (folder / f"{output_filename_base}.{extension}").write_text("partial", encoding="utf-8")
        raise RuntimeError("filesystem error: Cannot convert character sequence: Illegal byte sequence")

    module = SimpleNamespace(
        tesseract_cmd=str(folder / "tesseract.exe"),
        subprocess_args=lambda include_stdout=True: {},
        run_tesseract=original,
    )
    tesseract_cmd._install_windows_ocr_adapter(module)
    image = tmp_path / "临时 目录" / "page.PNG"
    image.parent.mkdir()
    image.write_bytes(b"png-bytes")
    with pytest.raises(RuntimeError, match="Illegal byte sequence"):
        module.run_tesseract(str(image), str(tmp_path / "临时 目录" / "out"), "tsv", "eng")
    assert list(folder.glob("ocr*")) == []


def test_pytesseract_uses_absolute_image_and_output_paths():
    # The child cwd must not reinterpret a caller's relative image/output path.
    with pytesseract.pytesseract.save(Image.new("RGB", (12, 12), "white")) as (output, image):
        assert Path(output).is_absolute() and Path(image).is_absolute()
        assert Path(image).is_file()


def test_real_tesseract_reads_staged_names_from_a_chinese_temp(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("Windows tesseract.exe needs the DLLs beside it; the packaged path is a separate check")
    binary = shutil.which("tesseract")
    if not binary:
        pytest.skip("needs tesseract")
    data_dirs = [
        Path(os.environ["TESSDATA_PREFIX"]) if os.environ.get("TESSDATA_PREFIX") else None,
        Path("/opt/homebrew/share/tessdata"),
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tessdata"),
    ]
    source_data = next((path for path in data_dirs if path and (path / "eng.traineddata").is_file()), None)
    if source_data is None:
        pytest.skip("needs eng.traineddata")
    folder = tmp_path / "中文 软件" / "tesseract"
    (folder / "tessdata").mkdir(parents=True)
    shutil.copy(binary, folder / Path(binary).name)
    for name in ("eng.traineddata", "chi_sim.traineddata"):
        if (source_data / name).is_file():
            shutil.copy(source_data / name, folder / "tessdata" / name)
    module = pytesseract.pytesseract
    saved = (module.tesseract_cmd, module.subprocess_args, module.run_tesseract)
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "临时 目录"))
    try:
        module.tesseract_cmd = str(folder / Path(binary).name)
        tesseract_cmd._install_windows_ocr_adapter(module)
        image = Image.new("RGB", (900, 80), "white")
        from PIL import ImageDraw, ImageFont

        font_file = next(
            (
                path
                for path in (
                    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
                    Path("C:/Windows/Fonts/arial.ttf"),
                )
                if path.is_file()
            ),
            None,
        )
        if font_file is None:
            pytest.skip("needs a TrueType font")
        draw_font = ImageFont.truetype(str(font_file), 42)
        ImageDraw.Draw(image).text((12, 16), "ABC-2026-001", fill="black", font=draw_font)
        text = pytesseract.image_to_string(image, lang="eng")
    finally:
        module.tesseract_cmd, module.subprocess_args, module.run_tesseract = saved
    assert "ABC-2026-001" in "".join(text.split())
    assert list(folder.glob("ocr*")) == []


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
