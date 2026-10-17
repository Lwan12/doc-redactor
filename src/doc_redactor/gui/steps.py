"""Wizard step bar. Visited steps are Labels with a click binding."""

from __future__ import annotations

import tkinter as tk

from doc_redactor.backpath import STEP_NAMES, step_clickable, step_tip
from doc_redactor.gui.theme import BG_SURFACE, INK, INK2, PLACE


class StepBar:
    def __init__(self, parent, app) -> None:
        self.app = app
        self.frame = tk.Frame(parent, bg=BG_SURFACE)
        self._labels: list[tk.Label] = []
        self.build()

    def build(self) -> None:
        self.hide_tip(force=True)
        for child in self.frame.winfo_children():
            child.destroy()
        self._labels = []
        app = self.app
        current = app.current_step()
        reached = app.reached
        running = app.running
        fonts = app.fonts
        tip = step_tip(on_result=app.screen == "result", written=app.written)
        for index, name in enumerate(STEP_NAMES):
            clickable = step_clickable(target=index, current=current, reached=reached, running=running)
            if index == current:
                font = fonts["bold"]
                color = INK
            elif clickable:
                font = fonts["link"]
                color = INK
            else:
                font = fonts["small"]
                color = INK2
            label = tk.Label(
                self.frame,
                text=f"{index + 1} {name}",
                bg=BG_SURFACE,
                fg=color,
                font=font,
                cursor="hand2" if clickable else "",
            )
            label.pack(side="left")
            self._labels.append(label)
            if clickable:
                label.bind("<Button-1>", lambda _event, step=index: app.on_step(step))
                label.bind("<Enter>", lambda _event, widget=label, text=tip: self.show_tip(widget, text))
                label.bind("<Leave>", lambda _event: self.hide_tip())
            if index < len(STEP_NAMES) - 1:
                tk.Label(self.frame, text=" › ", bg=BG_SURFACE, fg=PLACE, font=fonts["small"]).pack(side="left")
        pinned = getattr(app, "pin_step_tip", None)
        if pinned is not None and 0 <= pinned < len(self._labels):
            self.show_tip(self._labels[pinned], tip, pinned=True)

    def show_tip(self, widget, text: str, *, pinned: bool = False) -> None:
        self.hide_tip(force=True)
        tip = tk.Toplevel(widget)
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            tip,
            text=text,
            bg="#1f2124",
            fg="#ffffff",
            font=self.app.fonts["small"],
            padx=8,
            pady=4,
        ).pack()
        def place() -> None:
            try:
                tip.update_idletasks()
                x = widget.winfo_rootx()
                y = widget.winfo_rooty() + widget.winfo_height() + 2
                tip.geometry(f"+{x}+{y}")
            except tk.TclError:
                return

        place()
        tip.after_idle(place)
        self.app._step_tip = tip
        self.app._step_tip_pinned = pinned

    def hide_tip(self, *, force: bool = False) -> None:
        if (
            not force
            and getattr(self.app, "_step_tip_pinned", False)
            and getattr(self.app, "pin_step_tip", None) is not None
        ):
            return
        tip = getattr(self.app, "_step_tip", None)
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass
            self.app._step_tip = None
        if force:
            self.app._step_tip_pinned = False
