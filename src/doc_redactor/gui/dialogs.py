"""Custom Toplevel dialogs and a corner toast. No messagebox confirms."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from doc_redactor.gui.theme import BAD, BD, BG_INSET, BG_SURFACE, INK, INK2, LINE2, WARN, WARN_BG


def confirm_dialog(
    parent: tk.Tk | tk.Toplevel,
    title: str,
    body: str,
    buttons: list[tuple[str, str, bool]],
    *,
    extra: tk.Widget | None = None,
) -> str | None:
    """buttons: (label, value, primary). Esc / 关闭 = None."""
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=BG_SURFACE)
    dialog.transient(parent)
    dialog.resizable(False, False)
    result: dict[str, str | None] = {"value": None}

    def finish(value: str | None) -> None:
        result["value"] = value
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    dialog.bind("<Escape>", lambda _e: finish(None))

    head = tk.Frame(dialog, bg=BG_INSET)
    head.pack(fill="x")
    tk.Label(head, text=title, bg=BG_INSET, fg=INK, anchor="w").pack(fill="x", padx=14, pady=8)

    wrap = tk.Frame(dialog, bg=BG_SURFACE)
    wrap.pack(fill="both", expand=True, padx=14, pady=12)
    tk.Label(wrap, text=body, bg=BG_SURFACE, fg=INK, justify="left", wraplength=400, anchor="w").pack(fill="x")
    if extra is not None:
        extra.pack(in_=wrap, fill="x", pady=(8, 0))

    foot = tk.Frame(dialog, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
    foot.pack(fill="x")
    inner = tk.Frame(foot, bg=BG_SURFACE)
    inner.pack(fill="x", padx=14, pady=8)
    spacer = ttk.Frame(inner)
    spacer.pack(side="left", expand=True, fill="x")
    for label, value, primary in reversed(buttons):
        style = "Primary.TButton" if primary else "TButton"
        ttk.Button(inner, text=label, style=style, command=lambda v=value: finish(v)).pack(side="right", padx=(8, 0))

    dialog.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    dw, dh = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    x = parent.winfo_rootx() + max(0, (pw - dw) // 2)
    y = parent.winfo_rooty() + max(0, (ph - dh) // 2)
    dialog.geometry(f"+{x}+{y}")
    dialog.grab_set()
    parent.wait_window(dialog)
    return result["value"]


def prompt_dialog(
    parent: tk.Tk | tk.Toplevel,
    title: str,
    body: str,
    buttons: list[tuple[str, str, str]],
    *,
    extra: str | None = None,
    extra_mono: bool = False,
    fonts: dict | None = None,
    focus: str | None = None,
    stay_values: tuple[str, ...] = (),
    on_stay=None,
) -> str | None:
    """buttons: (label, value, kind) where kind is primary / danger / default / normal.

    stay_values keep the window open and call on_stay(value). Esc / close = None.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=BG_SURFACE)
    dialog.transient(parent)
    dialog.resizable(False, False)
    result: dict[str, str | None] = {"value": None}

    def finish(value: str | None) -> None:
        result["value"] = value
        dialog.destroy()

    def on_click(value: str) -> None:
        if value in stay_values:
            if on_stay is not None:
                on_stay(value)
            return
        finish(value)

    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    dialog.bind("<Escape>", lambda _e: finish(None))

    head = tk.Frame(dialog, bg=BG_INSET)
    head.pack(fill="x")
    tk.Label(head, text=title, bg=BG_INSET, fg=INK, anchor="w").pack(fill="x", padx=14, pady=8)

    wrap = tk.Frame(dialog, bg=BG_SURFACE)
    wrap.pack(fill="both", expand=True, padx=14, pady=12)
    tk.Label(wrap, text=body, bg=BG_SURFACE, fg=INK, justify="left", wraplength=620, anchor="w").pack(fill="x")
    if extra:
        extra_font = (fonts or {}).get("small")
        tk.Label(
            wrap,
            text=extra,
            bg=BG_SURFACE,
            fg=INK2,
            justify="left",
            wraplength=620,
            anchor="w",
            font=extra_font,
        ).pack(fill="x", pady=(8, 0))

    foot = tk.Frame(dialog, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
    foot.pack(fill="x")
    inner = tk.Frame(foot, bg=BG_SURFACE)
    inner.pack(fill="x", padx=14, pady=8)
    spacer = ttk.Frame(inner)
    spacer.pack(side="left", expand=True, fill="x")
    widgets: list[tuple[tk.Widget, str]] = []
    for label, value, kind in buttons:
        if kind == "danger":
            btn = tk.Button(
                inner,
                text=label,
                fg=BAD,
                bg=BG_SURFACE,
                activeforeground=BAD,
                activebackground=BG_INSET,
                relief="flat",
                bd=0,
                highlightthickness=0,
                padx=10,
                pady=4,
                command=lambda v=value: on_click(v),
            )
        elif kind == "default":
            btn = tk.Button(
                inner,
                text=label,
                fg=INK,
                bg=BG_INSET,
                activeforeground=INK,
                activebackground=BG_INSET,
                relief="solid",
                bd=1,
                highlightthickness=2,
                highlightbackground=INK,
                highlightcolor=INK,
                padx=10,
                pady=2,
                command=lambda v=value: on_click(v),
            )
        elif kind == "primary":
            btn = ttk.Button(inner, text=label, style="Primary.TButton", command=lambda v=value: on_click(v))
        else:
            btn = ttk.Button(inner, text=label, command=lambda v=value: on_click(v))
        widgets.append((btn, value))
    for btn, _value in reversed(widgets):
        btn.pack(side="right", padx=(8, 0))
    dialog.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    dw, dh = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    x = parent.winfo_rootx() + max(0, (pw - dw) // 2)
    y = parent.winfo_rooty() + max(0, (ph - dh) // 2)
    dialog.geometry(f"+{x}+{y}")
    for btn, value in widgets:
        if focus is not None and value == focus:
            btn.focus_set()
            break
    dialog.grab_set()
    parent.wait_window(dialog)
    return result["value"]


def show_toast(parent: tk.Misc, text: str, undo_cmd, *, delay_ms: int = 10 * 60 * 1000) -> tk.Frame:
    toast = tk.Frame(parent, bg=INK, padx=12, pady=8)
    tk.Label(toast, text=text, bg=INK, fg="#ffffff", wraplength=360, justify="left").pack(side="left")
    ttk.Button(toast, text="撤销", command=undo_cmd).pack(side="left", padx=(12, 0))
    toast.place(relx=1, rely=1, x=-16, y=-64, anchor="se")

    def hide() -> None:
        try:
            toast.destroy()
        except tk.TclError:
            pass

    toast.after(delay_ms, hide)
    return toast


def pane(parent, *, title: str | None = None) -> tuple[tk.Frame, tk.Frame]:
    outer = tk.Frame(parent, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
    header = tk.Frame(outer, bg=BG_INSET)
    header.pack(fill="x")
    if title:
        tk.Label(header, text=title, bg=BG_INSET, fg=INK2, anchor="w").pack(side="left", padx=10, pady=5)
    body = tk.Frame(outer, bg=BG_SURFACE)
    body.pack(fill="both", expand=True)
    return outer, body


def note_bar(parent, text: str, kind: str = "info", *, wraplength: int = 820) -> tk.Frame:
    colors = {
        "info": (BG_INSET, INK),
        "err": ("#fcecec", "#cb2f39"),
        "warn": (WARN_BG, WARN),
        "ok": ("#e8f5ed", "#008035"),
    }
    bg, fg = colors.get(kind, (BG_INSET, INK))
    bar = tk.Frame(parent, bg=bg, highlightthickness=0)
    tk.Label(bar, text=text, bg=bg, fg=fg, anchor="w", justify="left", wraplength=wraplength).pack(
        side="left", fill="x", expand=True, padx=12, pady=6
    )
    return bar
