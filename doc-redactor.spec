# -*- mode: python ; coding: utf-8 -*-
"""Windows onedir package: CLI and GUI executables in one folder.

onefile is not used. It unpacks into a temp directory on every launch.
Do not MERGE the two Analysis objects: MERGE gives each executable onefile
extraction semantics even when COLLECT is used. Both executables share the
collected files because they sit in the same directory.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = SPECPATH
TESSERACT = os.path.join(ROOT, "build", "tesseract")
MANIFEST = os.path.join(ROOT, "packaging", "asInvoker.manifest")

if not os.path.isdir(TESSERACT):
    raise SystemExit(
        "build/tesseract is missing. Stage tesseract.exe, its DLLs, and "
        "tessdata/eng.traineddata plus tessdata/chi_sim.traineddata first."
    )
if not os.path.isfile(MANIFEST):
    raise SystemExit(f"manifest not found: {MANIFEST}")


def _collected():
    datas = [(TESSERACT, "tesseract")]
    binaries = []
    hidden = [
        "pytesseract",
        "yaml",
        "docx",
        "regex",
        "PIL",
        "cv2",
        "presidio_image_redactor",
        "doc_redactor.gui.app",
        "doc_redactor.gui.theme",
        "doc_redactor.gui.main_view",
        "doc_redactor.gui.result_view",
        "doc_redactor.gui.rules_view",
        "doc_redactor.gui.dialogs",
        "doc_redactor.gui.demo",
        "tkinterdnd2",
    ]
    datas += collect_data_files("presidio_analyzer")
    extra_datas, extra_binaries, extra_hidden = collect_all("pymupdf")
    datas += extra_datas
    binaries += extra_binaries
    hidden += extra_hidden
    try:
        dnd_datas, dnd_binaries, dnd_hidden = collect_all("tkinterdnd2")
        datas += dnd_datas
        binaries += dnd_binaries
        hidden += dnd_hidden
    except Exception:
        pass
    return datas, binaries, hidden


datas, binaries, hiddenimports = _collected()


def _analysis(script):
    return Analysis(
        [os.path.join(ROOT, "tools", script)],
        pathex=[os.path.join(ROOT, "src")],
        binaries=list(binaries),
        datas=list(datas),
        hiddenimports=list(hiddenimports),
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )


def _exe(pyz, analysis, name, console):
    return EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # UPX-packed binaries are a common antivirus heuristic.
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        # Default is already false. Keep it explicit so the build cannot request elevation.
        uac_admin=False,
        uac_uiaccess=False,
        manifest=MANIFEST,
        # '.' keeps collected files beside the executables, including tesseract/.
        contents_directory=".",
    )


a_cli = _analysis("pyinstaller_entry_cli.py")
pyz_cli = PYZ(a_cli.pure)
exe_cli = _exe(pyz_cli, a_cli, "doc-redactor", console=True)

a_gui = _analysis("pyinstaller_entry_gui.py")
pyz_gui = PYZ(a_gui.pure)
exe_gui = _exe(pyz_gui, a_gui, "doc-redactor-gui", console=False)

coll = COLLECT(
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="doc-redactor",
)
