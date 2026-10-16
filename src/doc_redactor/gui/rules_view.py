"""Rules list, new/edit form, wordlist, library, import, export."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from doc_redactor.gui.theme import (
    BG_INSET,
    BG_PAGE,
    BG_SURFACE,
    BD,
    HL,
    INK,
    INK2,
    LINE,
    LINE2,
    MUTED,
    PLACE,
    fit_label,
)
from doc_redactor.pattern_guess import (
    TEMPLATES,
    TestHit,
    evaluate_rule_form,
)
from doc_redactor.ruleset import (
    DUP_OVERWRITE,
    DUP_RENAME,
    DUP_SKIP,
    HINT_LETTER_DIGIT,
    KIND_WORDLIST,
    LETTER_DIGIT_NAME,
    MSG_IMPORT_FAIL,
    MSG_PLAN_EMPTY,
    PROTOCOL_LIST_NAME,
    UNDO_TTL_SECONDS,
    common_library,
    find_word_hits,
    library_main_items,
)


class RulesView:
    def __init__(self, app) -> None:
        self.app = app
        self.frame = ttk.Frame(app.container)
        self._eval_job = None
        self._test = None
        self._msg = None

    def present(self) -> None:
        self.frame.pack(fill="both", expand=True)
        self.refresh()

    def hide(self) -> None:
        self.frame.pack_forget()

    def refresh(self) -> None:
        for child in self.frame.winfo_children():
            child.destroy()
        page = self.app.rules_page
        if page == "list":
            self._list()
        elif page == "edit":
            self._edit()
        elif page == "words":
            self._words()
        elif page == "lib":
            self._lib()
        elif page == "import":
            self._import()
        else:
            self._list()

    def _head(self, title: str) -> tk.Frame:
        fonts = self.app.fonts
        head = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        head.pack(fill="x")
        inner = tk.Frame(head, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        ttk.Button(inner, text="‹ 返回", command=self.app.rules_back).pack(side="left")
        tk.Label(inner, text=title, bg=BG_SURFACE, fg=INK, font=fonts["title"]).pack(side="left", padx=12)
        label = self.app.ruleset.undo_label()
        if label and self.app.rules_page == "list":
            tk.Label(inner, text=label, bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="right", padx=8)
            ttk.Button(inner, text="撤销", command=self.app.undo_rules).pack(side="right")
        elif self.app.rules_page == "words" and self.app.word_form.get("snap") is not None:
            snap_at = self.app.word_form.get("snap_at") or 0
            if snap_at and time.monotonic() - snap_at >= UNDO_TTL_SECONDS:
                self.app.word_form["snap"] = None
                self.app.word_form["snap_at"] = None
            else:
                tk.Label(inner, text="上一步可撤销", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="right", padx=8)
                ttk.Button(inner, text="撤销", command=self.app.undo_words).pack(side="right")
        return head

    def _list(self) -> None:
        self._head("规则设置")
        fonts = self.app.fonts
        body = tk.Frame(self.frame, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        bar = tk.Frame(body, bg=BG_PAGE)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="新增规则", style="Primary.TButton", command=self.app.new_rule).pack(side="left")
        ttk.Button(bar, text="新增词表", command=self.app.new_words).pack(side="left", padx=6)
        ttk.Button(bar, text="从常用规则库添加", command=self.app.show_library).pack(side="left")
        ttk.Button(bar, text="导入…", command=self.app.browse_import).pack(side="left", padx=6)
        ttk.Button(bar, text="导出…", command=self.app.export_rules_dialog).pack(side="left")
        n = len(self.app.ruleset.rules)
        on = len(self.app.ruleset.enabled_rules())
        tk.Label(bar, text=f"共 {n} 条 · 启用 {on}", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(side="right")

        pane = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        pane.pack(fill="both", expand=True)
        if not self.app.ruleset.rules:
            wrap = tk.Frame(pane, bg=BG_SURFACE)
            wrap.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(wrap, text="＋", bg=BG_SURFACE, fg=MUTED, font=fonts["title"]).pack()
            tk.Label(wrap, text="还没有规则", bg=BG_SURFACE, fg=INK, font=fonts["title"]).pack(pady=4)
            tk.Label(wrap, text="贴一两个真实的例子，工具会自己找规律。", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack()
            row = tk.Frame(wrap, bg=BG_SURFACE)
            row.pack(pady=10)
            ttk.Button(row, text="新增规则", style="Primary.TButton", command=self.app.new_rule).pack(side="left", padx=4)
            ttk.Button(row, text="从常用规则库添加", command=self.app.show_library).pack(side="left", padx=4)
        else:
            tree = ttk.Treeview(
                pane,
                columns=("on", "name", "kind", "ex", "act"),
                show="headings",
                selectmode="browse",
            )
            tree.heading("on", text="启用")
            tree.heading("name", text="规则名")
            tree.heading("kind", text="类型")
            tree.heading("ex", text="例子")
            tree.heading("act", text="操作")
            tree.column("on", width=90, stretch=False)
            tree.column("name", width=140, stretch=False)
            tree.column("kind", width=60, stretch=False)
            tree.column("ex", width=260, stretch=True)
            tree.column("act", width=120, stretch=False)
            tree.tag_configure("on", foreground="#008035")
            tree.tag_configure("off", foreground=MUTED)
            for i, rule in enumerate(self.app.ruleset.rules):
                on_label = "☑ ✓ 启用" if rule.enabled else "☐ ⊘ 停用"
                tree.insert(
                    "",
                    "end",
                    iid=str(i),
                    values=(on_label, rule.name, rule.display_kind(), rule.display_example(), "修改   删除"),
                    tags=("on" if rule.enabled else "off",),
                )
            tree.pack(fill="both", expand=True)

            def on_click(event):
                row = tree.identify_row(event.y)
                col = tree.identify_column(event.x)
                if not row:
                    return
                index = int(row)
                if col == "#1":
                    self.app.toggle_rule(index, not self.app.ruleset.rules[index].enabled)
                    self.refresh()
                elif col == "#5":
                    x = event.x
                    bbox = tree.bbox(row, "act")
                    if bbox and x < bbox[0] + bbox[2] / 2:
                        self.app.edit_rule(index)
                    else:
                        self.app.delete_rule(index)

            tree.bind("<Button-1>", on_click)
            tree.bind("<Double-1>", lambda e: self._edit_selected(tree))
        tk.Label(
            body,
            text="改动要点「确定」才生效。停用不会删掉规则，只是这次不遮。",
            bg=BG_PAGE,
            fg=INK2,
            font=fonts["small"],
        ).pack(anchor="w", pady=(8, 0))

    def _edit_selected(self, tree) -> None:
        sel = tree.selection()
        if sel:
            self.app.edit_rule(int(sel[0]))

    def _edit(self) -> None:
        form = self.app.rule_form
        title = "修改规则" if form.get("index") is not None else "新增规则"
        self._head(title)
        fonts = self.app.fonts
        ev = self._eval()
        body = tk.Frame(self.frame, bg=BG_PAGE)
        body.pack(fill="both", expand=True)
        cols = tk.Frame(body, bg=BG_PAGE)
        cols.pack(fill="both", expand=True, padx=12, pady=8)
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=1)
        left = tk.Frame(cols, bg=BG_PAGE)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = tk.Frame(cols, bg=BG_PAGE)
        right.grid(row=0, column=1, sticky="nsew")

        def row(parent, label):
            r = tk.Frame(parent, bg=BG_PAGE)
            r.pack(fill="x", pady=4)
            tk.Label(r, text=label, bg=BG_PAGE, fg=INK2, font=fonts["small"], width=10, anchor="e").pack(side="left")
            cell = tk.Frame(r, bg=BG_PAGE)
            cell.pack(side="left", fill="x", expand=True, padx=8)
            return cell

        name_var = tk.StringVar(value=form.get("name", ""))
        name_cell = row(left, "规则名")
        name_entry = ttk.Entry(name_cell, textvariable=name_var)
        name_entry.pack(fill="x")
        if ev.duplicate and not form.get("overwrite"):
            name_entry.configure()  # keep
        name_var.trace_add("write", lambda *_: self._set("name", name_var.get()))

        if form.get("template"):
            cell = row(left, "模板")
            kind = tk.StringVar(value=form.get("template_kind", TEMPLATES[0]))
            box = ttk.Combobox(cell, values=list(TEMPLATES), textvariable=kind, state="readonly")
            box.pack(side="left")
            ttk.Button(cell, text="改回贴例子", command=lambda: self._set("template", False)).pack(side="left", padx=8)
            kind.trace_add("write", lambda *_: self._set("template_kind", kind.get()))
            c2 = row(left, "字母几位")
            letters = tk.StringVar(value=str(form.get("letters", 3)))
            ttk.Entry(c2, textvariable=letters, width=6).pack(side="left")
            tk.Label(c2, text="位", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(side="left", padx=4)
            letters.trace_add("write", lambda *_: self._set("letters", _int(letters.get(), 3)))
            c3 = row(left, "分隔符")
            sep = tk.StringVar(value=form.get("sep", "-"))
            ttk.Entry(c3, textvariable=sep, width=6).pack(side="left")
            tk.Label(c3, text="横线填 -，没有就留空", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(side="left", padx=6)
            sep.trace_add("write", lambda *_: self._set("sep", sep.get()))
            c4 = row(left, "数字几位")
            d1 = tk.StringVar(value=str(form.get("digits", 4)))
            d2 = tk.StringVar(value=str(form.get("digits2", 3)))
            ttk.Entry(c4, textvariable=d1, width=6).pack(side="left")
            tk.Label(c4, text="位　再接一段：", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(side="left")
            ttk.Entry(c4, textvariable=d2, width=6).pack(side="left")
            tk.Label(c4, text="位（没有就留空）", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(side="left", padx=4)
            d1.trace_add("write", lambda *_: self._set("digits", _int(d1.get(), 4)))
            d2.trace_add("write", lambda *_: self._set("digits2", _int(d2.get(), 0)))
        else:
            cell = row(left, "例子")
            ex = tk.Text(cell, height=4, font=fonts["mono_font"], relief="solid", bd=1, wrap="word")
            ex.insert("1.0", form.get("examples", ""))
            ex.pack(fill="x")
            hint = tk.Frame(cell, bg=BG_PAGE)
            hint.pack(fill="x", pady=4)
            tk.Label(hint, text="例子都是你自己手上的，只在本机用。", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(
                side="left"
            )
            ttk.Button(hint, text="换成模板", command=lambda: self._set("template", True)).pack(side="right")
            ph = tk.StringVar(value=form.get("placeholder", ""))
            ttk.Entry(cell, textvariable=ph, font=fonts["mono_font"]).pack(fill="x", pady=(6, 0))
            fit_label(
                cell,
                "也可以照样写，如 PROJ-####（# 代表数字，@ 代表字母）。写了这一行，就按它来，上面的例子先不用。",
                bg=BG_PAGE,
                fg=INK2,
                font=fonts["small"],
            ).pack(fill="x", anchor="w")
            ex.bind("<KeyRelease>", lambda _e: self._set("examples", ex.get("1.0", "end-1c")))
            ph.trace_add("write", lambda *_: self._set("placeholder", ph.get()))

        desc_cell = row(left, "工具猜到的")
        desc = ev.guess.description if ev.guess.ok else "—（还没猜）"
        tk.Label(desc_cell, text=desc, bg=BG_PAGE, fg=INK if ev.guess.ok else MUTED, wraplength=360, justify="left").pack(
            anchor="w"
        )

        cs_cell = row(left, "区分大小写")
        cs = tk.BooleanVar(value=form.get("case_sensitive", False))
        ttk.Checkbutton(
            cs_cell,
            text="区分大小写  默认不区分：宁可多遮，不能漏遮",
            variable=cs,
            command=lambda: self._set("case_sensitive", cs.get()),
        ).pack(anchor="w")

        repl_cell = row(left, "替换成")
        repl = tk.StringVar(value=form.get("replacement") or f"[REDACTED:{form.get('name') or '规则名'}]")
        ttk.Entry(repl_cell, textvariable=repl, font=fonts["mono_font"]).pack(fill="x")
        repl.trace_add("write", lambda *_: self._set("replacement", repl.get(), refresh=False))

        if ev.duplicate:
            ov = tk.BooleanVar(value=form.get("overwrite", False))
            ov_cell = row(left, "")
            ttk.Checkbutton(
                ov_cell,
                text=f"覆盖旧的「{form.get('name')}」",
                variable=ov,
                command=lambda: self._set("overwrite", ov.get()),
            ).pack(anchor="w")

        adv_open = form.get("adv_open", False)
        adv_btn = ttk.Button(
            left,
            text=("▼" if adv_open else "▶") + " 高级设置（会写匹配写法的同事才需要）",
            command=lambda: self._set("adv_open", not adv_open),
        )
        adv_btn.pack(anchor="w", pady=(10, 4))
        if adv_open:
            fit_label(
                left,
                "直接改匹配写法，右边的高亮会跟着变。不会写就别动，用例子或模板就行。",
                bg=BG_PAGE,
                fg=INK2,
                font=fonts["small"],
            ).pack(fill="x", anchor="w")
            adv = tk.StringVar(value=form.get("advanced") if form.get("advanced") is not None else (ev.guess.inner_pattern or ""))
            ttk.Entry(left, textvariable=adv, font=fonts["mono_font"]).pack(fill="x", pady=4)
            adv.trace_add("write", lambda *_: self._set("advanced", adv.get()))

        tk.Label(right, text="试一段真实文字", bg=BG_PAGE, fg=INK, font=fonts["bold"]).pack(anchor="w")
        test = tk.Text(right, height=10, font=fonts["ui"], relief="solid", bd=1, wrap="word", undo=True)
        test.insert("1.0", form.get("test", ""))
        test.pack(fill="both", expand=True, pady=4)
        test.tag_configure("hit", background=HL, underline=True)
        self._paint_hits(test, ev.hits)
        test.bind("<KeyRelease>", lambda _e: self._debounce_test(test))

        if form.get("name") == LETTER_DIGIT_NAME:
            fit_label(
                right,
                f"⚠ {HINT_LETTER_DIGIT}。这是宽松写法，只要长得像「字母加数字」都会被遮。请看高亮，确认没有把不该遮的也遮了。",
                bg=BG_PAGE,
                fg="#c04700",
                font=fonts["small"],
            ).pack(fill="x", anchor="w", pady=(0, 4))
        if form.get("test") and ev.guess.ok and "命中" not in ev.message:
            tk.Label(right, text=f"命中 {len(ev.hits)} 处", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(anchor="w")
        tk.Label(right, text="提示：试的文字只在这个窗口里，不会存下来。", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(
            anchor="w", pady=(8, 0)
        )

        tone = {"ok": "#008035", "warn": "#c04700", "err": "#cb2f39", "n": INK2}[ev.tone]
        bar_bg = {"ok": "#e8f5ed", "warn": "#fdf1e5", "err": "#fcecec", "n": BG_INSET}[ev.tone]
        bar = tk.Frame(body, bg=bar_bg)
        bar.pack(fill="x", padx=12, pady=(0, 8))
        fit_label(
            bar,
            f"{ev.icon} {ev.message}",
            bg=bar_bg,
            fg=tone,
            font=fonts["small"],
        ).pack(fill="x", padx=10, pady=6)

        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        why = (
            "点「确定」才会保存。没点之前，现有规则和原文件都不会变。"
            if ev.can_save
            else f"还不能确定：{ev.message}"
        )
        tk.Label(inner, text=why, bg=BG_SURFACE, fg=INK2, font=fonts["small"], wraplength=620, justify="left").pack(
            side="left"
        )
        ttk.Button(inner, text="取消", command=self.app.cancel_rule_edit).pack(side="right")
        ttk.Button(
            inner,
            text="确定",
            style="Primary.TButton",
            command=self.app.save_rule,
            state=("normal" if ev.can_save else "disabled"),
        ).pack(side="right", padx=8)

    def _eval(self):
        form = self.app.rule_form
        names = self.app.ruleset.names()
        editing = None
        if form.get("index") is not None:
            editing = self.app.ruleset.rules[form["index"]].name
        advanced = form.get("advanced")
        return evaluate_rule_form(
            name=form.get("name", ""),
            examples_text=form.get("examples", ""),
            placeholder=form.get("placeholder", ""),
            test_text=form.get("test", ""),
            existing_names=names,
            overwrite=bool(form.get("overwrite")),
            case_sensitive=bool(form.get("case_sensitive")),
            template=form.get("template_kind") if form.get("template") else None,
            template_letters=int(form.get("letters") or 3),
            template_sep=form.get("sep", "-"),
            template_digits=int(form.get("digits") or 4),
            template_digits2=int(form.get("digits2") or 0),
            advanced=advanced,
            guessing=bool(form.get("guessing")),
            editing_name=editing,
        )

    def _set(self, key, value, refresh=True) -> None:
        if self.app.rule_form.get(key) == value:
            return
        self.app.rule_form[key] = value
        if key in {"examples", "placeholder", "template", "template_kind", "letters", "sep", "digits", "digits2"}:
            self.app.rule_form["advanced"] = None
            self.app.rule_form["guessing"] = False
        if refresh:
            self.app.root.after_idle(self.refresh)

    def _debounce_test(self, widget) -> None:
        if self._eval_job:
            self.app.root.after_cancel(self._eval_job)
        self._eval_job = self.app.root.after(250, lambda: self._set("test", widget.get("1.0", "end-1c")))

    def _set_word_test(self, text: str) -> None:
        if self.app.word_form.get("test") == text:
            return
        self.app.word_form["test"] = text
        self.app.root.after_idle(self.refresh)

    def _paint_hits(self, widget, hits) -> None:
        widget.tag_remove("hit", "1.0", "end")
        for hit in hits:
            start = f"1.0+{hit.start}c"
            end = f"1.0+{hit.end}c"
            widget.tag_add("hit", start, end)

    def _words(self) -> None:
        form = self.app.word_form
        title = "新增词表" if form.get("index") is None else f"修改词表：{form.get('name') or ''}"
        self._head(title)
        fonts = self.app.fonts
        body = tk.Frame(self.frame, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        left = tk.Frame(body, bg=BG_PAGE)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        right.grid(row=0, column=1, sticky="nsew")

        def labeled(text):
            r = tk.Frame(left, bg=BG_PAGE)
            r.pack(fill="x", pady=4)
            tk.Label(r, text=text, bg=BG_PAGE, fg=INK2, font=fonts["small"], width=10, anchor="e").pack(side="left")
            cell = tk.Frame(r, bg=BG_PAGE)
            cell.pack(side="left", fill="x", expand=True, padx=8)
            return cell

        name_var = tk.StringVar(value=form.get("name", ""))
        ttk.Entry(labeled("词表名"), textvariable=name_var).pack(fill="x")
        name_var.trace_add("write", lambda *_: form.__setitem__("name", name_var.get()))
        cs = tk.BooleanVar(value=form.get("case_sensitive", False))
        ttk.Checkbutton(
            labeled("区分大小写"),
            text="区分大小写  默认不区分：宁可多遮，不能漏遮",
            variable=cs,
            command=lambda: form.__setitem__("case_sensitive", cs.get()),
        ).pack(anchor="w")
        add_cell = labeled("加词")
        is_plan = (form.get("name") == PROTOCOL_LIST_NAME) or form.get("kind") == "plan"
        box = tk.Text(add_cell, height=5, relief="solid", bd=1, wrap="word")
        box.insert("1.0", form.get("incoming", ""))
        box.pack(fill="x")
        fit_label(
            add_cell,
            "每行写一个方案号，比如 AAA0000。可以一次粘贴一整列。"
            if is_plan
            else "每行写一个词，比如「华曜医药」。可以一次粘贴很多行。",
            bg=BG_PAGE,
            fg=INK2,
            font=fonts["small"],
        ).pack(fill="x", anchor="w", pady=(2, 0))
        btns = tk.Frame(add_cell, bg=BG_PAGE)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="添加", style="Primary.TButton", command=lambda: self.app.add_word_lines(box.get("1.0", "end-1c"))).pack(
            side="left"
        )
        ttk.Button(btns, text="从文本文件导入…", command=self.app.import_word_file).pack(side="left", padx=8)
        msg = form.get("message")
        if msg:
            tone, text = msg
            color = {"ok": "#008035", "warn": "#c04700", "n": INK2, "err": "#cb2f39"}[tone]
            icon = {"ok": "✓ ", "warn": "⚠ ", "n": "ⓘ ", "err": "✕ "}[tone]
            tk.Label(left, text=icon + text, bg=BG_PAGE, fg=color, font=fonts["small"], wraplength=360, justify="left").pack(
                anchor="w", pady=6
            )
        fit_label(
            left,
            "词表里的词，在文件里出现一次就遮一次。写得越全越好：全称、简称、英文名都放进去。",
            bg=BG_PAGE,
            fg=INK2,
            font=fonts["small"],
        ).pack(fill="x", anchor="w", pady=8)

        words = form.get("words") or []
        sel = set(form.get("sel") or [])
        rh = tk.Frame(right, bg=BG_INSET)
        rh.pack(fill="x")
        tk.Label(rh, text=f"词表 共 {len(words)} 个词", bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        ttk.Button(
            rh,
            text=f"删除所选（{len(sel)}）" if sel else "删除所选",
            command=self.app.delete_selected_words,
            state=("normal" if sel else "disabled"),
        ).pack(side="right", padx=8, pady=4)
        if words:
            tree = ttk.Treeview(right, columns=("ck", "w"), show="headings", selectmode="extended")
            tree.heading("ck", text="选")
            tree.heading("w", text="词")
            tree.column("ck", width=36, stretch=False, anchor="center")
            tree.column("w", width=200, stretch=True)
            for i, word in enumerate(words):
                tree.insert("", "end", iid=str(i), values=("☑" if i in sel else "☐", word))
            tree.pack(fill="both", expand=True)

            def toggle(event):
                row = tree.identify_row(event.y)
                if not row:
                    return
                i = int(row)
                current = set(form.get("sel") or [])
                if i in current:
                    current.remove(i)
                else:
                    current.add(i)
                form["sel"] = list(current)
                self.refresh()

            tree.bind("<Button-1>", toggle)
        else:
            wrap = tk.Frame(right, bg=BG_SURFACE)
            wrap.pack(fill="both", expand=True)
            inner = tk.Frame(wrap, bg=BG_SURFACE)
            inner.pack(expand=True, fill="x", padx=16)
            tk.Label(inner, text="＋", bg=BG_SURFACE, fg=MUTED, font=fonts["title"]).pack()
            empty_title = MSG_PLAN_EMPTY if is_plan else "词表还是空的"
            empty_next = (
                "清单为空时不会命中。在左边每行写一个方案号点「添加」，或点「从文本文件导入…」。"
                if is_plan
                else "在左边的框里每行写一个词，点「添加」。也可以点「从文本文件导入…」，文件里每行一个词。"
            )
            tk.Label(inner, text=empty_title, bg=BG_SURFACE, fg=INK, font=fonts["title"]).pack(pady=4)
            fit_label(
                inner,
                empty_next,
                bg=BG_SURFACE,
                fg=INK2,
                font=fonts["small"],
                justify="center",
            ).pack(fill="x")

        tk.Label(left, text="试一段真实文字", bg=BG_PAGE, fg=INK, font=fonts["bold"]).pack(anchor="w", pady=(8, 0))
        test = tk.Text(left, height=4, relief="solid", bd=1, wrap="word")
        test.insert("1.0", form.get("test", ""))
        test.pack(fill="x", pady=4)
        test.tag_configure("hit", background=HL, underline=True)
        hits = find_word_hits(words, form.get("test") or "", case_sensitive=bool(form.get("case_sensitive")))
        self._paint_hits(test, [TestHit(start=s, end=e, text=t) for s, e, t in hits])
        test.bind(
            "<KeyRelease>",
            lambda _e: self._set_word_test(test.get("1.0", "end-1c")),
        )
        if form.get("test"):
            if hits:
                tk.Label(
                    left,
                    text=f"✓ 命中 {len(hits)} 处。",
                    bg=BG_PAGE,
                    fg="#008035",
                    font=fonts["small"],
                ).pack(anchor="w")
            else:
                fit_label(
                    left,
                    "⚠ 这段文字里没有命中。请检查词表里有没有这个词，或换一段文字再试。",
                    bg=BG_PAGE,
                    fg="#c04700",
                    font=fonts["small"],
                ).pack(fill="x", anchor="w")

        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        name = form.get("name", "").strip()
        dup = form.get("index") is None and name in self.app.ruleset.names()
        if dup:
            why = f"已经有一条叫「{name}」的规则了。请换个名字。"
        elif not name:
            why = "还不能确定：请先写词表名。"
        elif not words:
            why = f"还不能确定：{MSG_PLAN_EMPTY}。" if is_plan else "还不能确定：词表还是空的，先加几个词。"
        else:
            why = "点「确定」才会保存。没点之前，现有规则不变。"
        tk.Label(inner, text=why, bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="left")
        ttk.Button(inner, text="取消", command=self.app.cancel_rule_edit).pack(side="right")
        ttk.Button(
            inner,
            text="确定",
            style="Primary.TButton",
            command=self.app.save_words,
            state=("disabled" if (not name or not words or dup) else "normal"),
        ).pack(side="right", padx=8)

    def _touch_words(self) -> None:
        self.app.root.after_idle(self.refresh)

    def _lib(self) -> None:
        self._head("常用规则库")
        fonts = self.app.fonts
        items = common_library()
        checked = self.app.lib_checked
        if len(checked) != len(items):
            checked = [item.default_checked for item in items]
            self.app.lib_checked = checked
        body = tk.Frame(self.frame, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        fit_label(
            body,
            "勾上要用的，点「添加到我的规则」。例子都是占位的，加进去后可以再改。方案号清单、申办方、药名是词表：每行写一个词（或一个号）就行，不用写规则。",
            bg=BG_PAGE,
            fg=INK2,
            font=fonts["small"],
        ).pack(fill="x", pady=(0, 8))
        pane = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        pane.pack(fill="both", expand=True)
        tree = ttk.Treeview(pane, columns=("ck", "name", "kind", "ex", "def"), show="headings")
        tree.heading("ck", text="选")
        tree.heading("name", text="规则")
        tree.heading("kind", text="类型")
        tree.heading("ex", text="例子")
        tree.heading("def", text="默认 / 提醒")
        tree.column("ck", width=40, stretch=False, anchor="center")
        tree.column("name", width=200, stretch=False)
        tree.column("kind", width=56, stretch=False)
        tree.column("ex", width=220, stretch=True)
        tree.column("def", width=220, stretch=False)
        tree.tag_configure("off", foreground=MUTED)
        parent_on = {
            item.name: bool(checked[i]) for i, item in enumerate(items) if not item.is_subitem
        }
        for i, item in enumerate(items):
            gated = item.is_subitem and not parent_on.get(item.parent_name, False)
            if gated:
                mark = "—"
            else:
                mark = "☑" if checked[i] else "☐"
            display = f"└ {item.name}" if item.is_subitem else item.name
            if item.is_subitem:
                note = "⚠ 可能误伤"
            else:
                note = "默认勾上" if item.default_checked else "同事自己开"
            tree.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    mark,
                    display,
                    "词表" if item.kind == KIND_WORDLIST else "规则",
                    item.example,
                    note,
                ),
                tags=("off" if gated else ()),
            )
        tree.pack(fill="both", expand=True)
        fit_label(
            body,
            f"字母加数字：⚠ {HINT_LETTER_DIGIT}。至少 2 个字母 + 3～4 位数字，可带横杠和后缀；默认不勾。方案号清单没勾时不可勾。",
            bg=BG_PAGE,
            fg="#c04700",
            font=fonts["small"],
        ).pack(fill="x", pady=(6, 0))

        def toggle(event):
            row = tree.identify_row(event.y)
            if not row:
                return
            self.app.toggle_library(int(row))

        tree.bind("<Button-1>", toggle)
        mains = library_main_items(items)
        n = sum(1 for item, on in zip(items, checked) if on and not item.is_subitem)
        sub_n = sum(1 for item, on in zip(items, checked) if on and item.is_subitem)
        extra = "，另选了备选子项 1 个" if sub_n else ""
        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        tk.Label(
            inner,
            text=f"已选 {n} / {len(mains)} 条{extra}。字母加数字是方案号清单的备选子项，不单独占条数。",
            bg=BG_SURFACE,
            fg=INK2,
            font=fonts["small"],
            wraplength=620,
            justify="left",
        ).pack(side="left")
        ttk.Button(inner, text="取消", command=lambda: self.app.show_rules_page("list")).pack(side="right")
        total = n + sub_n
        ttk.Button(
            inner,
            text=f"添加到我的规则（{total} 条）",
            style="Primary.TButton",
            command=self.app.add_library,
            state=("normal" if total else "disabled"),
        ).pack(side="right", padx=8)

    def _import(self) -> None:
        self._head("导入规则")
        fonts = self.app.fonts
        preview = self.app.import_preview
        body = tk.Frame(self.frame, bg=BG_PAGE)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        if preview is None or preview.failed:
            msg = (preview.message if preview else MSG_IMPORT_FAIL)
            bar = tk.Frame(body, bg="#fcecec", highlightthickness=1, highlightbackground="#cb2f39")
            bar.pack(fill="x")
            tk.Label(bar, text="✕ " + msg, bg="#fcecec", fg="#cb2f39", font=fonts["small"], wraplength=800, justify="left").pack(
                side="left", padx=12, pady=8
            )
            ttk.Button(bar, text="重新选择文件…", command=self.app.browse_import).pack(side="right", padx=8)
            foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            foot.pack(fill="x")
            inner = tk.Frame(foot, bg=BG_SURFACE)
            inner.pack(fill="x", padx=12, pady=8)
            ttk.Button(inner, text="返回", style="Primary.TButton", command=lambda: self.app.show_rules_page("list")).pack(
                side="right"
            )
            return
        tk.Label(
            body,
            text=f"文件：{preview.path}　先看一遍，点「确认导入」才会写入。",
            bg=BG_PAGE,
            fg=INK2,
            font=fonts["small"],
        ).pack(anchor="w")
        counts = tk.Frame(body, bg=BG_PAGE)
        counts.pack(fill="x", pady=8)
        tk.Label(counts, text=f"＋ 新增 {preview.new_count} 条", bg=BG_PAGE, fg="#008035", font=fonts["small"]).pack(
            side="left", padx=(0, 16)
        )
        tk.Label(counts, text=f"⚠ 重名 {preview.dup_count} 条", bg=BG_PAGE, fg="#c04700", font=fonts["small"]).pack(
            side="left", padx=(0, 16)
        )
        tk.Label(counts, text=f"✕ 无效 {preview.bad_count} 条", bg=BG_PAGE, fg="#cb2f39", font=fonts["small"]).pack(
            side="left"
        )
        pane = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        pane.pack(fill="both", expand=True)
        tree = ttk.Treeview(pane, columns=("st", "name", "ex", "how"), show="headings")
        tree.heading("st", text="状态")
        tree.heading("name", text="规则名")
        tree.heading("ex", text="例子")
        tree.heading("how", text="怎么处理")
        tree.column("st", width=90, stretch=False)
        tree.column("name", width=140, stretch=False)
        tree.column("ex", width=200, stretch=True)
        tree.column("how", width=140, stretch=False)
        for i, item in enumerate(preview.items):
            if item.status == "new":
                st, how = "＋ 新增", "直接导入"
            elif item.status == "dup":
                st, how = "⚠ 重名", item.action
            else:
                st, how = "✕ 无效", "无法导入，已跳过"
            tree.insert("", "end", iid=str(i), values=(st, item.name, item.example, how))
        tree.pack(fill="both", expand=True)
        action_row = tk.Frame(body, bg=BG_PAGE)
        action_row.pack(fill="x", pady=6)
        tk.Label(action_row, text="选中重名行后选择怎么处理：", bg=BG_PAGE, fg=INK2, font=fonts["small"]).pack(side="left")
        how = tk.StringVar(value=DUP_SKIP)
        box = ttk.Combobox(action_row, values=[DUP_SKIP, DUP_RENAME, DUP_OVERWRITE], textvariable=how, state="readonly", width=12)
        box.pack(side="left", padx=8)

        def apply_how(_e=None):
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            item = preview.items[i]
            if item.status != "dup":
                return
            item.action = how.get()
            self.refresh()

        box.bind("<<ComboboxSelected>>", apply_how)
        n = preview.will_import_count()
        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        tk.Label(inner, text=f"将导入 {n} 条。重名的默认跳过，现有规则不变。", bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(
            side="left"
        )
        ttk.Button(inner, text="取消", command=lambda: self.app.show_rules_page("list")).pack(side="right")
        ttk.Button(
            inner,
            text=f"确认导入（{n} 条）",
            style="Primary.TButton",
            command=self.app.confirm_import,
            state=("normal" if n else "disabled"),
        ).pack(side="right", padx=8)


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default
