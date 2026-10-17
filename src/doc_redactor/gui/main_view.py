"""File step and rule step. The page frames are created once and only raised."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from doc_redactor.backpath import (
    MSG_BACK_FILES,
    MSG_CANCEL,
    MSG_CANCEL_EXTRA,
    MSG_CLEAR_FILES,
    MSG_OUTPUT_PARENT,
    MSG_REMOVE_SELECTED,
    rescan_hint,
)
from doc_redactor.gui.steps import StepBar
from doc_redactor.gui.theme import (
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
        # These two frames stay for the life of the window. Going back raises
        # one of them; it does not destroy the page or the files and rules on it.
        self.file_page = tk.Frame(self.frame, bg=BG_PAGE)
        self.rule_page = tk.Frame(self.frame, bg=BG_PAGE)
        self.file_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.rule_page.place(relx=0, rely=0, relwidth=1, relheight=1)

    def present(self) -> None:
        self.frame.pack(fill="both", expand=True)
        self.refresh()

    def hide(self) -> None:
        self.frame.pack_forget()

    def refresh(self) -> None:
        app = self.app
        app.drop_step_tip()
        use_rules = app.main_step == 1 or app.running or app.cancelled
        page = self.rule_page if use_rules else self.file_page
        for child in page.winfo_children():
            child.destroy()
        if use_rules:
            self._build_rules(page)
            self.rule_page.tkraise()
        else:
            self._build_files(page)
            self.file_page.tkraise()

    def _head(self, parent) -> None:
        head = tk.Frame(parent, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        head.pack(fill="x")
        StepBar(head, self.app).frame.pack(fill="x", padx=12, pady=(8, 8))
        # A pinned tip is a separate window under the step label. Leave room so
        # it does not cover the file list title in the screenshot.
        if getattr(self.app, "pin_step_tip", None) is not None:
            tk.Frame(head, height=26, bg=BG_SURFACE).pack(fill="x")

    def _build_files(self, parent) -> None:
        app = self.app
        fonts = app.fonts
        self._head(parent)
        body = tk.Frame(parent, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        pane = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        pane.pack(fill="both", expand=True)
        if not app.files:
            self._empty_drop(pane, fonts)
        else:
            self._file_picker(pane, fonts)
        self._file_foot(parent, fonts)

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
        tk.Label(inner, text=hint, bg=BG_INSET, fg=INK2, font=fonts["small"], wraplength=480, justify="center").pack()
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
        if self.app.dnd_ok:
            self.app.bind_drop(wrap)

    def _file_picker(self, parent, fonts) -> None:
        app = self.app
        bar = tk.Frame(parent, bg=BG_INSET)
        bar.pack(fill="x")
        tk.Label(bar, text=f"已添加 {len(app.files)} 个文件", bg=BG_INSET, fg=INK, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        ttk.Button(bar, text="清空", command=app.clear_files).pack(side="right", padx=8, pady=4)
        ttk.Button(bar, text="添加文件…", command=app.browse_files).pack(side="right", pady=4)
        selected = len(app.file_selected)
        if selected:
            ttk.Button(
                bar,
                text=MSG_REMOVE_SELECTED.format(n=selected),
                command=app.remove_selected_files,
            ).pack(side="right", padx=(0, 8), pady=4)
        columns = ("pick", "name", "type", "size", "act")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        tree.heading("pick", text="选")
        tree.heading("name", text="文件名")
        tree.heading("type", text="类型")
        tree.heading("size", text="大小")
        tree.heading("act", text="操作")
        tree.column("pick", width=36, stretch=False, anchor="center")
        tree.column("name", width=280, stretch=True)
        tree.column("type", width=70, stretch=False)
        tree.column("size", width=80, stretch=False, anchor="e")
        tree.column("act", width=64, stretch=False, anchor="center")
        for index, path in enumerate(app.files):
            size = app.file_sizes[index] if index < len(app.file_sizes) else ""
            mark = "☑" if str(path) in app.file_selected else "☐"
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(mark, path.name, _type_label(path.suffix), size, "移除"),
            )
        tree.pack(fill="both", expand=True)

        def on_click(event):
            row = tree.identify_row(event.y)
            col = tree.identify_column(event.x)
            if not row:
                return
            index = int(row)
            if index >= len(app.files):
                return
            if col == "#1":
                app.toggle_file_selected(app.files[index])
            elif col == "#5":
                app.remove_paths({str(app.files[index])})

        tree.bind("<Button-1>", on_click)
        if app.dnd_ok:
            app.bind_drop(tree)
        hint = tk.Frame(parent, bg=BG_INSET)
        hint.pack(fill="x")
        tk.Label(
            hint,
            text=MSG_CLEAR_FILES.format(n=len(app.files)),
            bg=BG_INSET,
            fg=INK2,
            font=fonts["small"],
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=10, pady=6)

    def _file_foot(self, parent, fonts) -> None:
        app = self.app
        foot = tk.Frame(parent, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
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
            tk.Label(left, text=MSG_OUTPUT_PARENT, bg=BG_SURFACE, fg=INK2, font=fonts["small"], anchor="w").pack(
                fill="x", pady=(2, 0)
            )
        can_next = bool(app.files) and not app.output_error
        ttk.Button(
            inner,
            text="下一步：选规则",
            style="Primary.TButton",
            command=app.go_rules_step,
            state=("normal" if can_next else "disabled"),
        ).pack(side="right", padx=(12, 0))
        if not app.files:
            tk.Label(inner, text="先添加文件", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="right", padx=8)

    def _build_rules(self, parent) -> None:
        app = self.app
        fonts = app.fonts
        running = app.running
        self._head(parent)
        body = tk.Frame(parent, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=270, weight=0)
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        right.grid(row=0, column=1, sticky="nsew")
        self._rule_files(left, fonts, running)
        self._rules_pane(right, fonts, running)
        self._rule_foot(parent, fonts, running)

    def _rule_files(self, parent, fonts, running: bool) -> None:
        app = self.app
        bar = tk.Frame(parent, bg=BG_INSET)
        bar.pack(fill="x")
        tk.Label(bar, text=f"已添加的文件 {len(app.files)} 个", bg=BG_INSET, fg=INK, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
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
        for index, path in enumerate(app.files):
            size = app.file_sizes[index] if index < len(app.file_sizes) else ""
            state = app.file_status[index] if index < len(app.file_status) else "pending"
            icon, text, _color = status_style(state)
            tag = {"pending": "wait", "running": "run", "scanned": "ok", "fail": "bad", "cancelled": "cx"}.get(
                state, "wait"
            )
            tree.insert("", "end", values=(path.name, _type_label(path.suffix), size, f"{icon} {text}"), tags=(tag,))
        tree.pack(fill="both", expand=True)
        if app.dnd_ok and not running:
            app.bind_drop(tree)

    def _rules_pane(self, parent, fonts, running: bool) -> None:
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text="要遮哪些内容", bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        body = tk.Frame(parent, bg=BG_SURFACE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        state = "disabled" if running else "normal"
        for index, rule in enumerate(self.app.ruleset.rules):
            row = tk.Frame(body, bg=BG_SURFACE)
            row.pack(fill="x", pady=2)
            var = tk.BooleanVar(value=rule.enabled)
            extra = rule.display_example() if rule.kind == "wordlist" else f"例子 {rule.display_example()}"
            ttk.Checkbutton(
                row,
                text=f"{rule.name}  {extra}",
                variable=var,
                state=state,
                command=lambda i=index, v=var: self.app.toggle_rule(i, v.get()),
            ).pack(anchor="w")
        n = len(self.app.ruleset.enabled_rules())
        tk.Label(
            body,
            text=(
                f"已选 {n} 类。\n"
                "命中的内容会换成该类的占位符\n"
                "(可在规则设置里改)；\n"
                "PDF 的占位符放不下会缩小或截断；\n"
                "图片会涂黑。"
            ),
            bg=BG_SURFACE,
            fg=INK2,
            font=fonts["small"],
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(12, 8))
        ttk.Button(body, text="规则设置…", command=self.app.show_rules, state=state).pack(anchor="w")

    def _rule_foot(self, parent, fonts, running: bool) -> None:
        app = self.app
        foot = tk.Frame(parent, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        if running:
            n = len(app.files) or 1
            done = sum(1 for state in app.file_status if state in {"scanned", "fail"})
            current = min(done + 1, n)
            tk.Label(inner, text=f"正在扫描 {current} / {n} 个", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(
                side="left"
            )
            ttk.Progressbar(inner, mode="determinate", maximum=n, value=done).pack(
                side="left", fill="x", expand=True, padx=10
            )
            ttk.Button(inner, text="取消", command=app.cancel_scan).pack(side="right")
            return
        hint = rescan_hint(
            from_result=app.from_result,
            cancelled=app.cancelled,
            signature_matches=app.signature_matches_memory(),
            unchecked=app.remembered_unchecks(),
        )
        row = tk.Frame(inner, bg=BG_SURFACE)
        row.pack(fill="x")
        can_go = bool(app.files) and bool(app.ruleset.enabled_rules()) and not app.output_error
        label = "重新扫描" if (app.cancelled or app.from_result) else "开始扫描"
        ttk.Button(
            row,
            text=label,
            style="Primary.TButton",
            command=app.start_scan,
            state=("normal" if can_go else "disabled"),
        ).pack(side="right")
        if not app.ruleset.enabled_rules():
            tk.Label(row, text="至少选一类规则", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="right", padx=8)
        ttk.Button(row, text="‹ 返回选文件", command=app.back_to_files).pack(side="left")
        tk.Label(row, text=MSG_BACK_FILES, bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="left", padx=8)
        if app.cancelled:
            tk.Label(row, text=MSG_CANCEL, bg=BG_SURFACE, fg="#c04700", font=fonts["small"]).pack(side="left", padx=(4, 0))
            tk.Label(row, text=MSG_CANCEL_EXTRA, bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="left", padx=8)
        if hint:
            color = "#c04700" if hint.startswith("规则改过了") else INK2
            prefix = "⚠ " if hint.startswith("规则改过了") else ""
            tk.Label(
                inner,
                text=prefix + hint,
                bg=BG_SURFACE,
                fg=color,
                font=fonts["small"],
                anchor="e",
                justify="right",
            ).pack(fill="x", pady=(4, 0))


def _type_label(suffix: str) -> str:
    lowered = suffix.lower()
    if lowered == ".docx":
        return "Word"
    if lowered == ".pdf":
        return "PDF"
    if lowered in {".png", ".jpg", ".jpeg"}:
        return "图片"
    return lowered.lstrip(".")
