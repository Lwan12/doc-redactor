"""ttk clam theme, colours, and fonts. No rounded corners, gradients, or shadows."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG_PAGE = "#fafafb"
BG_SURFACE = "#ffffff"
BG_INSET = "#f7f8f9"
BG_HEAD = "#f1f2f3"
BG_HOVER = "#e7e9eb"
LINE = "#ecedef"
LINE2 = "#e0e2e5"
BD = "#c9ccd1"
INK = "#1f2124"
INK2 = "#62656b"
MUTED = "#6c6c6c"
PLACE = "#9a9da3"
PRIMARY = "#0070dd"
PRIMARY_HOVER = "#005fc0"
OK = "#008035"
OK_BG = "#e8f5ed"
WARN = "#c04700"
WARN_BG = "#fdf1e5"
BAD = "#cb2f39"
BAD_BG = "#fcecec"
HL = "#fff3b0"
HL2 = "#ffd84d"

SANS_CANDIDATES = (
    "Microsoft YaHei",
    "微软雅黑",
    "Noto Sans CJK SC",
    "Noto Sans CJK SC Regular",
    "WenQuanYi Micro Hei",
    "PingFang SC",
    "Noto Sans",
)
MONO_CANDIDATES = (
    "Consolas",
    "Noto Sans Mono CJK SC",
    "DejaVu Sans Mono",
    "Noto Sans Mono",
    "monospace",
)

MARKERS = "①②③④⑤⑥⑦⑧⑨⑩"


def pick_family(available: set[str], candidates: tuple[str, ...]) -> str:
    lowered = {name.lower(): name for name in available}
    for name in candidates:
        if name in available:
            return name
        if name.lower() in lowered:
            return lowered[name.lower()]
    return candidates[-1]


def setup_style(root: tk.Tk) -> dict:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    families = set(tkfont.families(root))
    sans = pick_family(families, SANS_CANDIDATES)
    mono = pick_family(families, MONO_CANDIDATES)
    font_ui = tkfont.Font(root=root, family=sans, size=11)
    font_small = tkfont.Font(root=root, family=sans, size=10)
    font_bold = tkfont.Font(root=root, family=sans, size=11, weight="bold")
    font_title = tkfont.Font(root=root, family=sans, size=12, weight="bold")
    font_mono = tkfont.Font(root=root, family=mono, size=10)
    font_mono_big = tkfont.Font(root=root, family=mono, size=15, weight="bold")
    font_link = tkfont.Font(root=root, family=sans, size=10, underline=True)
    root.option_add("*Font", font_ui)
    root.configure(bg=BG_PAGE)

    style.configure("TFrame", background=BG_PAGE)
    style.configure("Surface.TFrame", background=BG_SURFACE)
    style.configure("Inset.TFrame", background=BG_INSET)
    style.configure("TLabel", background=BG_PAGE, foreground=INK, font=font_ui)
    style.configure("Surface.TLabel", background=BG_SURFACE, foreground=INK, font=font_ui)
    style.configure("Muted.TLabel", background=BG_PAGE, foreground=INK2, font=font_small)
    style.configure("Small.TLabel", background=BG_SURFACE, foreground=INK2, font=font_small)
    style.configure("Ok.TLabel", background=BG_PAGE, foreground=OK, font=font_small)
    style.configure("Bad.TLabel", background=BG_PAGE, foreground=BAD, font=font_small)
    style.configure("Warn.TLabel", background=BG_PAGE, foreground=WARN, font=font_small)
    style.configure("TCheckbutton", background=BG_SURFACE, foreground=INK, font=font_ui)
    style.configure("TRadiobutton", background=BG_SURFACE, foreground=INK, font=font_ui)
    style.configure(
        "TButton",
        background=BG_HEAD,
        foreground=INK,
        font=font_small,
        padding=(10, 4),
        borderwidth=1,
        relief="raised",
    )
    style.map("TButton", background=[("disabled", BG_INSET), ("active", BG_HOVER)])
    style.configure(
        "Primary.TButton",
        background=PRIMARY,
        foreground="#ffffff",
        font=font_small,
        padding=(12, 4),
        borderwidth=0,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", BG_HOVER), ("active", PRIMARY_HOVER)],
        foreground=[("disabled", MUTED), ("active", "#ffffff")],
    )
    style.configure(
        "Treeview",
        background=BG_SURFACE,
        fieldbackground=BG_SURFACE,
        foreground=INK,
        font=font_small,
        rowheight=26,
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background=BG_HEAD,
        foreground=INK2,
        font=font_small,
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Treeview",
        background=[("selected", BG_HOVER)],
        foreground=[("selected", INK)],
    )
    style.configure("TEntry", fieldbackground=BG_SURFACE, foreground=INK)
    style.configure("TCombobox", fieldbackground=BG_SURFACE, foreground=INK)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=BG_HEAD,
        background=INK2,
        borderwidth=1,
        lightcolor=INK2,
        darkcolor=INK2,
    )
    style.configure("TPanedwindow", background=BG_PAGE)
    style.configure("TNotebook", background=BG_PAGE)
    style.configure("TNotebook.Tab", background=BG_HEAD, foreground=INK2, padding=(12, 4), font=font_small)
    style.map("TNotebook.Tab", background=[("selected", BG_SURFACE)], foreground=[("selected", INK)])
    return {
        "sans": sans,
        "mono": mono,
        "ui": font_ui,
        "small": font_small,
        "bold": font_bold,
        "title": font_title,
        "mono_font": font_mono,
        "mono_big": font_mono_big,
        "link": font_link,
    }


def status_style(kind: str) -> tuple[str, str, str]:
    """icon, text, color key."""
    mapping = {
        "pending": ("○", "待处理", INK2),
        "running": ("◔", "处理中", INK),
        "scanned": ("✓", "已扫描", OK),
        "hit": ("✓", "有命中", OK),
        "written": ("✓", "已写出", OK),
        "nohit": ("—", "无命中", MUTED),
        "fail": ("✕", "失败", BAD),
        "cancelled": ("⊘", "已取消", WARN),
        "many": ("⚠", "命中偏多", WARN),
        "nowrite": ("✕", "没写出", BAD),
        "skipped": ("✓", "这个文件之前已写出，已跳过", OK),
    }
    return mapping.get(kind, ("○", kind, INK2))


def marker_for(index: int) -> str:
    if 0 <= index < len(MARKERS):
        return MARKERS[index]
    return str(index + 1)


def fit_label(parent, text: str, *, bg: str, fg: str, font, justify: str = "left") -> tk.Frame:
    """Wrap text to the parent's width. Pack the returned frame with fill="x".

    ``tk.Message`` uses its width as the wrap width and grows in height to fit,
    so a hint is not cut off and is not broken on a fixed wraplength.
    """
    frame = tk.Frame(parent, bg=bg)
    widget = tk.Message(
        frame,
        text=text,
        bg=bg,
        fg=fg,
        font=font,
        justify=justify,
        anchor="center" if justify == "center" else "nw",
        width=40,
        padx=1,
        pady=0,
    )
    widget.pack(fill="x", anchor="center" if justify == "center" else "w")

    def sync(_event=None) -> None:
        width = frame.winfo_width()
        if width <= 2:
            return
        target = max(40, width - 2)
        if int(widget.cget("width")) != target:
            widget.configure(width=target)

    frame.bind("<Configure>", sync)
    frame.after_idle(sync)
    return frame
