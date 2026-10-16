"""Desktop window. tkinter is imported only when main() runs."""

from pathlib import Path
import os


def _prepare_fonts() -> None:
    conf = Path("/workspace/fonts/fonts.conf")
    if conf.is_file():
        os.environ.setdefault("FONTCONFIG_FILE", str(conf))
    tcl = Path("/usr/share/tcltk/tcl8.6")
    tk = Path("/usr/share/tcltk/tk8.6")
    if tcl.is_dir():
        os.environ.setdefault("TCL_LIBRARY", str(tcl))
    if tk.is_dir():
        os.environ.setdefault("TK_LIBRARY", str(tk))


def main() -> None:
    _prepare_fonts()
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "未找到 tkinter。请安装带 Tcl/Tk 的 Python（Linux 上常见包名是 python3-tk）。"
        ) from exc
    from doc_redactor.gui.app import run

    run()
