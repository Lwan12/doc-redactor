"""Main screen: files + rules + output + scan."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from doc_redactor.gui.theme import (
    BG_HOVER,
    BG_INSET,
    BG_PAGE,
    BG_SURFACE,
    BD,
    INK,
    INK2,
    LINE2,
    MUTED,
    PLACE,
    status_style,
)


class MainView:
    def __init__(self, app) -> None:
        self.app = app
        self.frame = ttk.Frame(app.container)
        self._body: tk.Frame | None = None

    def present(self) -> None:
        self.frame.pack(fill="both", expand=True)
        self.refresh()

    def hide(self) -> None:
        self.frame.pack_forget()

    def refresh(self) -> None:
        for child in self.frame.winfo_children():
            child.destroy()
        app = self.app
        fonts = app.fonts
        running = app.running
        head = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        head.pack(fill="x")
        _steps(head, 2 if running or app.cancelled else (1 if app.files else 0), fonts)

        body = tk.Frame(self.frame, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=270, weight=0)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        right.grid(row=0, column=1, sticky="nsew")

        if not app.files:
            self._empty_drop(left, fonts)
        else:
            self._file_table(left, fonts, running)

        self._rules_pane(right, fonts, running)
        self._foot(running, fonts)

    def _empty_drop(self, parent, fonts) -> None:
        wrap = tk.Frame(parent, bg=BG_INSET)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)
        inner = tk.Frame(wrap, bg=BG_INSET)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(inner, text="⤓", bg=BG_INSET, fg=MUTED, font=self.app.fonts["title"]).pack()
        tk.Label(inner, text="把要脱敏的文件拖到这里", bg=BG_INSET, fg=INK, font=fonts["title"]).pack(pady=(8, 4))
        hint = (
            "支持 Word（.docx）、PDF、图片（.png .jpg）。可以一次放很多个，也可以放整个文件夹。"
            if self.app.dnd_ok
            else "未装上拖入支持，请点「选择文件…」。支持 Word（.docx）、PDF、图片（.png .jpg）。"
        )
        tk.Label(inner, text=hint, bg=BG_INSET, fg=INK2, font=fonts["small"], wraplength=420, justify="center").pack()
        row = tk.Frame(inner, bg=BG_INSET)
        row.pack(pady=10)
        ttk.Button(row, text="选择文件…", style="Primary.TButton", command=self.app.browse_files).pack(side="left", padx=4)
        ttk.Button(row, text="选择文件夹…", command=self.app.browse_folder).pack(side="left", padx=4)
        if not self.app.dnd_ok:
            tk.Label(
                inner,
                text="当前环境没有拖入，只能用上面的按钮。",
                bg=BG_INSET,
                fg=INK2,
                font=fonts["small"],
            ).pack()

    def _file_table(self, parent, fonts, running: bool) -> None:
        bar = tk.Frame(parent, bg=BG_INSET)
        bar.pack(fill="x")
        tk.Label(
            bar,
            text=f"待处理文件 {len(self.app.files)} 个",
            bg=BG_INSET,
            fg=INK,
            font=fonts["small"],
        ).pack(side="left", padx=10, pady=5)
        ttk.Button(bar, text="清空", command=self.app.clear_files, state=("disabled" if running else "normal")).pack(
            side="right", padx=8, pady=4
        )
        ttk.Button(bar, text="添加文件…", command=self.app.browse_files, state=("disabled" if running else "normal")).pack(
            side="right", pady=4
        )
        columns = ("name", "type", "size", "status")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        tree.heading("name", text="文件名")
        tree.heading("type", text="类型")
        tree.heading("size", text="大小")
        tree.heading("status", text="状态")
        tree.column("name", width=220, stretch=True)
        tree.column("type", width=60, stretch=False)
        tree.column("size", width=70, stretch=False, anchor="e")
        tree.column("status", width=90, stretch=False)
        tree.tag_configure("ok", foreground="#008035")
        tree.tag_configure("bad", foreground="#cb2f39")
        tree.tag_configure("wait", foreground="#62656b")
        tree.tag_configure("run", foreground="#1f2124")
        tree.tag_configure("cx", foreground="#c04700")
        tree.tag_configure("no", foreground="#6c6c6c")
        for index, path in enumerate(self.app.files):
            kind = _type_label(path.suffix)
            size = self.app.file_sizes[index] if index < len(self.app.file_sizes) else ""
            st = self.app.file_status[index] if index < len(self.app.file_status) else "pending"
            icon, text, _color = status_style(st)
            tag = {"pending": "wait", "running": "run", "scanned": "ok", "fail": "bad", "cancelled": "cx"}.get(st, "wait")
            tree.insert("", "end", values=(path.name, kind, size, f"{icon} {text}"), tags=(tag,))
        tree.pack(fill="both", expand=True)
        if self.app.dnd_ok:
            self.app.bind_drop(tree)

    def _rules_pane(self, parent, fonts, running: bool) -> None:
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text="要遮哪些内容", bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        body = tk.Frame(parent, bg=BG_SURFACE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        state = "disabled" if running else "normal"
        fg = PLACE if running else INK
        for index, rule in enumerate(self.app.ruleset.rules):
            row = tk.Frame(body, bg=BG_SURFACE)
            row.pack(fill="x", pady=2)
            var = tk.BooleanVar(value=rule.enabled)
            extra = rule.display_example() if rule.kind == "wordlist" else f"例子 {rule.display_example()}"
            cb = ttk.Checkbutton(
                row,
                text=f"{rule.name}  {extra}",
                variable=var,
                state=state,
                command=lambda i=index, v=var: self.app.toggle_rule(i, v.get()),
            )
            cb.pack(anchor="w")
        n = len(self.app.ruleset.enabled_rules())
        tk.Label(
            body,
            text=f"已选 {n} 类。命中的内容会换成 [REDACTED:类别]；PDF 和图片会涂黑。",
            bg=BG_SURFACE,
            fg=INK2,
            font=fonts["small"],
            wraplength=240,
            justify="left",
        ).pack(anchor="w", pady=(12, 8))
        ttk.Button(body, text="规则设置…", command=self.app.show_rules, state=state).pack(anchor="w")

    def _foot(self, running: bool, fonts) -> None:
        app = self.app
        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        if running:
            n = len(app.files) or 1
            done = sum(1 for s in app.file_status if s in {"scanned", "fail"})
            current = min(done + 1, n)
            tk.Label(inner, text=f"正在扫描 {current} / {n} 个", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(
                side="left"
            )
            bar = ttk.Progressbar(inner, mode="determinate", maximum=n, value=done)
            bar.pack(side="left", fill="x", expand=True, padx=10)
            ttk.Button(inner, text="取消", command=app.cancel_scan).pack(side="right")
            return
        left = tk.Frame(inner, bg=BG_SURFACE)
        left.pack(side="left", fill="x", expand=True)
        row = tk.Frame(left, bg=BG_SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="输出到", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="left")
        entry = tk.Entry(
            row,
            textvariable=app.output_var,
            font=fonts["mono_font"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground="#cb2f39" if app.output_error else BD,
            highlightcolor="#cb2f39" if app.output_error else BD,
        )
        entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="更改…", command=app.browse_output).pack(side="left")
        if app.output_error:
            tk.Label(left, text=app.output_error, bg=BG_SURFACE, fg="#cb2f39", font=fonts["small"], anchor="w").pack(
                fill="x", pady=(4, 0)
            )
        else:
            tk.Label(
                left,
                text="原文件不会被修改，脱敏后的文件另存到这里。",
                bg=BG_SURFACE,
                fg=INK2,
                font=fonts["small"],
                anchor="w",
            ).pack(fill="x", pady=(2, 0))
        right = tk.Frame(inner, bg=BG_SURFACE)
        right.pack(side="right", padx=(12, 0))
        if app.cancelled:
            tk.Label(right, text="⊘ 已取消，没有写出任何文件", bg=BG_SURFACE, fg="#c04700", font=fonts["small"]).pack(
                side="left", padx=(0, 8)
            )
        can_go = bool(app.files) and bool(app.ruleset.enabled_rules()) and not app.output_error
        label = "重新扫描" if app.cancelled else "开始扫描"
        ttk.Button(
            right,
            text=label,
            style="Primary.TButton",
            command=app.start_scan,
            state=("normal" if can_go else "disabled"),
        ).pack(side="left")
        if not app.files:
            tk.Label(right, text="先添加文件", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="left", padx=8)
        elif not app.ruleset.enabled_rules():
            tk.Label(right, text="至少选一类规则", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="left", padx=8)


def _steps(parent, current: int, fonts) -> None:
    names = ["选文件", "选规则", "开始扫描", "查看结果"]
    row = tk.Frame(parent, bg=BG_SURFACE)
    row.pack(fill="x", padx=12, pady=8)
    for i, name in enumerate(names):
        weight = fonts["bold"] if i == current else fonts["small"]
        color = INK if i == current else INK2
        tk.Label(row, text=f"{i + 1} {name}", bg=BG_SURFACE, fg=color, font=weight).pack(side="left")
        if i < len(names) - 1:
            tk.Label(row, text=" › ", bg=BG_SURFACE, fg=PLACE, font=fonts["small"]).pack(side="left")


def _type_label(suffix: str) -> str:
    lowered = suffix.lower()
    if lowered == ".docx":
        return "Word"
    if lowered == ".pdf":
        return "PDF"
    if lowered in {".png", ".jpg", ".jpeg"}:
        return "图片"
    return lowered.lstrip(".")
