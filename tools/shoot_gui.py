"""Drive GUI states under xvfb and save 960×640 screenshots.

Usage:
  xvfb-run -a -s "-screen 0 960x640x24" python tools/shoot_gui.py   # 环境变量 DOC_REDACTOR_FONTS_CONF、DOC_REDACTOR_SHOTS
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_FONTS_CONF = Path(os.environ.get("DOC_REDACTOR_FONTS_CONF", ""))
if _FONTS_CONF.is_file():
    os.environ.setdefault("FONTCONFIG_FILE", str(_FONTS_CONF))
os.environ.setdefault("DOC_REDACTOR_HOME", "/tmp/doc-redactor-shoot-home")

OUT = Path(os.environ.get("DOC_REDACTOR_SHOTS", "screenshots-out"))
# DOC_REDACTOR_SHOT_SIZE=1100x720 matches the design brief; default 960x640.
SIZE = tuple(int(v) for v in os.environ.get("DOC_REDACTOR_SHOT_SIZE", "960x640").split("x"))
# DOC_REDACTOR_SHOT_ONLY=res-hf-,main-hf- shoots only keys with these prefixes.
ONLY = tuple(p for p in os.environ.get("DOC_REDACTOR_SHOT_ONLY", "").split(",") if p)

STATES = [
    ("01-main-empty", "main-empty"),
    ("02-main-files", "main-files"),
    ("03-main-outerr", "main-outerr"),
    ("04-main-running", "main-running"),
    ("05-main-cancelled", "main-cancelled"),
    ("06-res-word", "res-word"),
    ("07-res-word-after", "res-word-after"),
    ("08-res-word-uncheck", "res-word-uncheck"),
    ("09-res-pdf", "res-pdf"),
    ("10-res-img", "res-img"),
    ("11-res-hide", "res-hide"),
    ("12-res-all", "res-all"),
    ("13-res-fail", "res-fail"),
    ("14-res-nohit", "res-nohit"),
    ("15-res-allnohit", "res-allnohit"),
    ("16-res-allfail", "res-allfail"),
    ("17-res-close", "res-close"),
    ("18-res-written", "res-written"),
    ("19-res-many", "res-many"),
    # 20 was the leftover overwrite dialog; retry now skips an already-written dest.
    ("20-res-skipped", "res-skipped"),
    ("21-rules-list", "rules-list"),
    ("22-rules-deleted", "rules-deleted"),
    ("23-rule-new", "rule-new"),
    ("24-rule-guessing", "rule-guessing"),
    ("25-rule-guess", "rule-guess"),
    ("26-rule-unsure", "rule-unsure"),
    ("27-rule-nohit", "rule-nohit"),
    ("28-rule-toomany", "rule-toomany"),
    ("29-rule-invalid", "rule-invalid"),
    ("30-rule-dup", "rule-dup"),
    ("31-rule-placeholder", "rule-placeholder"),
    ("32-rule-tpl", "rule-tpl"),
    ("33-rule-adv", "rule-adv"),
    ("34-rules-lib", "rules-lib"),
    ("35-rules-import", "rules-import"),
    ("36-rules-import-fail", "rules-import-fail"),
    ("37-rules-export", "rules-export"),
    ("38-words-list", "words-list"),
    ("39-words-empty", "words-empty"),
    ("40-words-dup", "words-dup"),
    ("41-words-deleted", "words-deleted"),
    ("42-words-added", "words-added"),
    ("43-plan-list", "plan-list"),
    ("44-plan-empty", "plan-empty"),
    ("45-plan-dup", "plan-dup"),
    ("46-plan-deleted", "plan-deleted"),
    ("47-rule-letters", "rule-letters"),
    ("49-res-backhint", "res-backhint"),
    ("50-res-backwritten", "res-backwritten"),
    ("51-main-rules", "main-rules"),
    ("52-res-restore", "res-restore"),
    ("53-main-rm", "main-rm"),
    ("54-res-partial", "res-partial"),
    ("55-main-rules-kept", "main-rules-kept"),
    ("56-main-rules-changed", "main-rules-changed"),
    ("57-main-first", "main-first"),
    ("58-rules-more", "rules-more"),
    ("58b-rules-more-closed", "rules-more-closed"),
    ("59-rules-reset", "rules-reset"),
    ("60-rules-reset-same", "rules-reset-same"),
    ("61-rules-reset-done", "rules-reset-done"),
    ("62-main-cfgbad", "main-cfgbad"),
    ("63-rules-savefail", "rules-savefail"),
    ("64-main-outmissing", "main-outmissing"),
    ("65-rules-opened", "rules-opened"),
    ("66-res-leftover", "res-leftover"),
    ("66-res-hf-word", "res-hf-word"),
    ("67-res-hf-pdf", "res-hf-pdf"),
    ("68-res-hf-auto", "res-hf-auto"),
    ("69-res-hf-none", "res-hf-none"),
    ("70-res-hf-filecancel", "res-hf-filecancel"),
    ("71-res-hf-after", "res-hf-after"),
    ("72-res-hf-written", "res-hf-written"),
    ("73-main-hf-off", "main-hf-off"),
    ("74-res-hf-auto-ok", "res-hf-auto-ok"),
    ("75-res-hf-mixed", "res-hf-mixed"),
    ("76-res-hf-all-pend", "res-hf-all-pend"),
    ("77-res-hf-auto-undo", "res-hf-auto-undo"),
    ("78-res-hf-mixed-ok", "res-hf-mixed-ok"),
    ("79-res-hf-none-plain", "res-hf-none-plain"),
]


def grab(root, dest: Path) -> None:
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    root.update()
    from PIL import ImageGrab

    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()
    if w < 2 or h < 2:
        w, h = SIZE
        x, y = 0, 0
    try:
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    except Exception:
        image = ImageGrab.grab()
    if image.size != SIZE:
        image = image.resize(SIZE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="PNG")


def _probe_cjk(root, fonts) -> None:
    import tkinter as tk
    from PIL import ImageGrab

    probe = tk.Toplevel(root)
    probe.geometry("420x80+0+0")
    probe.configure(bg="#ffffff")
    tk.Label(
        probe,
        text="中文抽查 文档脱敏 方案号清单",
        bg="#ffffff",
        fg="#000000",
        font=fonts["ui"],
    ).pack(padx=8, pady=20)
    probe.update_idletasks()
    probe.update()
    time.sleep(0.1)
    x, y = probe.winfo_rootx(), probe.winfo_rooty()
    w, h = max(probe.winfo_width(), 2), max(probe.winfo_height(), 2)
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    dest = Path("/tmp/cjk-spotcheck.png")
    image.save(dest)
    dark = 0
    for pixel in image.getdata():
        if pixel[0] < 40 and pixel[1] < 40 and pixel[2] < 40:
            dark += 1
    probe.destroy()
    print(f"cjk probe font={fonts['sans']!r} actual={fonts['ui'].actual('family')!r} dark={dark} -> {dest}")
    if dark < 400:
        raise RuntimeError(f"中文抽查失败：暗像素 {dark}，可能是豆腐块或乱码。字体 {fonts['sans']}")


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    from doc_redactor.gui.app import App, create_root

    root, dnd = create_root()
    app = App(root, dnd, shoot=True)
    root.geometry(f"{SIZE[0]}x{SIZE[1]}+0+0")
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.update()
    try:
        _probe_cjk(root, app.fonts)
    except Exception as exc:
        print(f"FAIL cjk-probe: {exc}", file=sys.stderr)
        try:
            root.destroy()
        except Exception:
            pass
        return 1
    errors = []
    for name, key in STATES:
        if ONLY and not key.startswith(ONLY):
            continue
        try:
            app.apply_shoot_state(key)
            dest = OUT / f"{name}-{SIZE[0]}x{SIZE[1]}.png"
            grab(root, dest)
            print(f"wrote {dest}")
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            print(f"FAIL {key}: {exc}", file=sys.stderr)
    try:
        root.destroy()
    except Exception:
        pass
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
