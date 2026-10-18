"""Result screen: files, preview, categories, locations, write."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from doc_redactor.backpath import (
    MSG_CLEARED_TOP,
    MSG_OUTPUT_HINT,
    MSG_PARTIAL_BOTTOM,
    MSG_PARTIAL_KEEP,
    MSG_RESTORE,
    MSG_WRITTEN_TOP,
    partial_banner,
    unchecked_count,
    unwritten_failure_banner,
    write_allowed,
)
from doc_redactor.gui.steps import StepBar
from doc_redactor.gui.theme import (
    BG_INSET,
    BG_PAGE,
    BG_SURFACE,
    HL,
    HL2,
    INK,
    INK2,
    LINE2,
    MUTED,
    PLACE,
    fit_label,
    marker_for,
    status_style,
)
from doc_redactor.engine import redaction_label
from doc_redactor.scan import KIND_IMAGE, KIND_PDF, KIND_WORD, FileScan, session_totals

# Left is the previous page, right is the next page.
PDF_PAGER = (("‹", -1), ("›", 1))


def clamp_page(index: int, delta: int, count: int) -> int:
    """Page index after moving by delta, kept inside the document."""
    if count <= 0:
        return 0
    return max(0, min(count - 1, index + delta))


def line_visible(text: tk.Text, index: str) -> bool:
    try:
        return bool(text.dlineinfo(index))
    except tk.TclError:
        return False


def line_fully_inside(text: tk.Text, index: str) -> bool:
    """The whole display line is inside the widget, not sliced by its edge."""
    try:
        info = text.dlineinfo(index)
    except tk.TclError:
        return False
    if not info:
        return False
    return info[1] >= 0 and info[1] + info[3] <= text.winfo_height()


# The page marker sits on the first visible line. pady keeps that line a few
# pixels under the widget edge; anything in this band still counts as the top.
_MARKER_TOP = 12


def scroll_page_marker(text: tk.Text, index: str, *, to_top: bool) -> None:
    """Put 「—— 第 N 页 ——」 at the top of the preview.

    Page 1 is the start of the text, so it goes back to the top. A later page
    is scrolled to its marker. Tk will not scroll a short tail into empty
    space, which left the previous page's last lines above the marker; blank
    lines at the end give the marker room to sit on the first visible line.
    The real last line stays reachable, and a line sliced by the top edge is
    nudged down until it is whole.
    """
    if not to_top:
        state = str(text.cget("state"))
        text.configure(state="normal")
        try:
            height = max(text.winfo_height(), 1)
            limit = max(4, height // 8 + 2)
            for _ in range(limit):
                text.yview(index)
                text.update_idletasks()
                info = _line_box(text, index)
                if info is not None and 0 <= info[1] <= _MARKER_TOP:
                    break
                text.insert("end", "\n")
            else:
                text.yview(index)
                text.update_idletasks()
        finally:
            text.configure(state=state)
    else:
        text.yview_moveto(0)
        text.update_idletasks()
    for _ in range(40):
        info = _line_box(text, index)
        if info is None or info[1] >= 0:
            return
        text.yview_scroll(-1, "pixels")
        text.update_idletasks()


def _line_box(text: tk.Text, index: str):
    try:
        return text.dlineinfo(index)
    except tk.TclError:
        return None


class ResultView:
    def __init__(self, app) -> None:
        self.app = app
        self.frame = ttk.Frame(app.container)
        self._photo = None
        self._hit_ranges: dict[str, int] = {}
        self._pdf_pager = None
        self._preview_text = None
        self._preview_scroll = None

    def present(self) -> None:
        self.frame.pack(fill="both", expand=True)
        self.refresh()

    def hide(self) -> None:
        self.frame.pack_forget()

    def refresh(self) -> None:
        self._building = True
        self._pdf_pager = None
        self._preview_text = None
        self._preview_scroll = None
        app = self.app
        app.drop_step_tip()
        for child in self.frame.winfo_children():
            child.destroy()
        fonts = app.fonts
        scans = app.scans
        all_fail = bool(scans) and all(s.status == "fail" for s in scans)
        nohit_all = bool(scans) and all(s.status != "fail" and not s.hits for s in scans) and not any(
            s.status == "fail" for s in scans
        )
        if scans and not any(s.status == "fail" for s in scans) and all(s.status == "nohit" or not s.hits for s in scans):
            nohit_all = True

        head = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        head.pack(fill="x")
        row = tk.Frame(head, bg=BG_SURFACE)
        row.pack(fill="x", padx=12, pady=8)
        title = "已写出" if app.written else "结果预览，写出前可以逐处取消"
        tk.Label(row, text=title, bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(side="right")
        StepBar(row, app).frame.pack(side="left", fill="x", expand=True)

        if not all_fail:
            note = tk.Frame(self.frame, bg=BG_INSET)
            note.pack(fill="x")
            tk.Label(
                note,
                text="ⓘ 原文只在这个窗口里临时显示，不会存进任何文件，关闭窗口就清除。",
                bg=BG_INSET,
                fg=INK,
                font=fonts["small"],
            ).pack(side="left", padx=12, pady=6)
            hide = tk.BooleanVar(value=app.hide_text)
            ttk.Checkbutton(
                note,
                text="隐藏原文，只看位置",
                variable=hide,
                command=lambda: self._set_hide(hide.get()),
            ).pack(side="right", padx=12)

        failures = app.write_failure_rows()
        if failures:
            err = tk.Frame(self.frame, bg="#fcecec")
            err.pack(fill="x")
            tk.Label(
                err,
                text=partial_banner(failures) if app.written else unwritten_failure_banner(failures),
                bg="#fcecec",
                fg="#cb2f39",
                font=fonts["small"],
                wraplength=920,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=12, pady=6)
        if app.written:
            ok = tk.Frame(self.frame, bg="#e8f5ed")
            ok.pack(fill="x")
            tk.Label(
                ok,
                text="✓ " + MSG_WRITTEN_TOP.format(n=app.written_file_count()),
                bg="#e8f5ed",
                fg="#008035",
                font=fonts["small"],
                wraplength=920,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=12, pady=6)
        if app.note_cleared:
            cleared = tk.Frame(self.frame, bg=BG_INSET)
            cleared.pack(fill="x")
            tk.Label(
                cleared,
                text="ⓘ " + MSG_CLEARED_TOP,
                bg=BG_INSET,
                fg=INK,
                font=fonts["small"],
                wraplength=920,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=12, pady=6)

        many = next((s for s in scans if s.too_many), None)
        if many is not None and not all_fail:
            warn = tk.Frame(self.frame, bg="#fdf1e5")
            warn.pack(fill="x")
            n = len(many.hits)
            fit_label(
                warn,
                f"⚠ {many.path.name} 命中了 {n} 处，比平时多很多，可能把不该遮的也遮了。请看高亮；不影响写出，可直接点「写出」。",
                bg="#fdf1e5",
                fg="#c04700",
                font=fonts["small"],
            ).pack(fill="x", padx=12, pady=6)

        self._summary(scans, fonts, all_fail)

        fails = [s for s in scans if s.status == "fail"]
        if fails and app.selected == "all" and not all_fail and not nohit_all:
            err = tk.Frame(self.frame, bg="#fcecec")
            err.pack(fill="x")
            names = "、".join(s.path.name for s in fails)
            tk.Label(
                err,
                text=f"✕ 有 {len(fails)} 个文件没能处理（{names}）。其他文件正常。",
                bg="#fcecec",
                fg="#cb2f39",
                font=fonts["small"],
            ).pack(side="left", padx=12, pady=6)
            ttk.Button(err, text="查看", command=lambda: self._select(scans.index(fails[0]))).pack(side="left")

        body = tk.Frame(self.frame, bg=BG_PAGE)
        if app.narrow:
            body.columnconfigure(0, minsize=180, weight=0)
            body.columnconfigure(1, weight=1)
            body.rowconfigure(0, weight=1)
            body.rowconfigure(1, weight=1)
            left = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
            mid = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            mid.grid(row=0, column=1, sticky="nsew")
            right = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            right.grid(row=1, column=1, sticky="nsew", pady=(8, 0))
        else:
            # 960px: file names and hit text keep their columns. The middle
            # column is what remains, wide enough for 「方案号清单」 on one line.
            body.columnconfigure(0, minsize=280, weight=0)
            body.columnconfigure(1, weight=1, minsize=200)
            body.columnconfigure(2, minsize=340, weight=0)
            body.rowconfigure(0, weight=1)
            left = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
            mid = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            mid.grid(row=0, column=1, sticky="nsew")
            right = tk.Frame(body, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
            right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        self._file_list(left, scans, fonts)
        if app.selected == "all":
            self._all_preview(mid, scans, fonts, all_fail, nohit_all)
        else:
            self._file_preview(mid, scans[app.selected], fonts)
        self._right_pane(right, scans, fonts)
        # Footer first (side=bottom) so the write bar keeps its height when the
        # preview panes want more than the 640px window.
        self._foot(scans, fonts, all_fail, nohit_all)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 4))
        self.app.root.after_idle(lambda: setattr(self, "_building", False))

    def _set_hide(self, value: bool) -> None:
        self.app.hide_text = value
        self.refresh()

    def _select(self, value) -> None:
        if getattr(self, "_building", False):
            return
        if self.app.selected == value:
            return
        self.app.selected = value
        self.app.selected_cat = None
        self.app.current_hit = None
        self.app.pdf_page = 0
        self.refresh()

    def _summary(self, scans, fonts, all_fail: bool) -> None:
        bar = tk.Frame(self.frame, bg=BG_SURFACE)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=4)
        if all_fail:
            tk.Label(inner, text="0", bg=BG_SURFACE, fg=INK, font=fonts["mono_big"]).pack(side="left")
            tk.Label(inner, text=" 个文件可处理", bg=BG_SURFACE, fg=INK, font=fonts["ui"]).pack(side="left")
            tk.Label(
                inner,
                text=f"    ✕ {len(scans)} 个文件全部失败",
                bg=BG_SURFACE,
                fg="#cb2f39",
                font=fonts["small"],
            ).pack(side="left")
            return
        enabled, _counts = session_totals(scans, enabled_only=True)
        if self.app.written:
            for scan in scans:
                if str(scan.path) in self.app.write_errors:
                    enabled -= scan.enabled_count()
        total_hits = sum(len(s.hits) for s in scans)
        cancelled_n = total_hits - enabled
        n_hit = sum(1 for s in scans if s.status != "fail" and s.hits)
        n_no = sum(1 for s in scans if s.status != "fail" and not s.hits)
        n_fail = sum(1 for s in scans if s.status == "fail")
        tk.Label(inner, text=f"共 {len(scans)} 个文件", bg=BG_SURFACE, fg=INK, font=fonts["ui"]).pack(side="left")
        verb = "已替换" if self.app.written else "将替换"
        tk.Label(inner, text=f"    {verb} ", bg=BG_SURFACE, fg=INK, font=fonts["ui"]).pack(side="left")
        tk.Label(inner, text=str(enabled), bg=BG_SURFACE, fg=INK, font=fonts["mono_big"]).pack(side="left")
        tk.Label(inner, text=" 处", bg=BG_SURFACE, fg=INK, font=fonts["ui"]).pack(side="left")
        if cancelled_n:
            tk.Label(
                inner,
                text=f"    （你取消了 {cancelled_n} 处）",
                bg=BG_SURFACE,
                fg="#c04700",
                font=fonts["small"],
            ).pack(side="left")
        tk.Label(inner, text=f"    ✓ 有命中 {n_hit}", bg=BG_SURFACE, fg="#008035", font=fonts["small"]).pack(side="left")
        tk.Label(inner, text=f"    — 无命中 {n_no}", bg=BG_SURFACE, fg=MUTED, font=fonts["small"]).pack(side="left")
        tk.Label(inner, text=f"    ✕ 失败 {n_fail}", bg=BG_SURFACE, fg="#cb2f39", font=fonts["small"]).pack(side="left")

    def _file_list(self, parent, scans, fonts) -> None:
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text=f"文件 {len(scans)}", bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        tree = ttk.Treeview(parent, columns=("name", "n"), show="headings", selectmode="browse")
        tree.heading("name", text="文件")
        tree.heading("n", text="替换数")
        # "⚠ 命中偏多  入组统计报告_V2.pdf" is about 227px in the UI font.
        tree.column("name", width=240, minwidth=220, stretch=True)
        tree.column("n", width=56, minwidth=48, stretch=False, anchor="e")
        tree.tag_configure("ok", foreground="#008035")
        tree.tag_configure("bad", foreground="#cb2f39")
        tree.tag_configure("no", foreground="#6c6c6c")
        tree.tag_configure("warn", foreground="#c04700")
        total, _ = session_totals(scans, enabled_only=True)
        all_id = tree.insert("", "end", values=("▤ 全部文件", total), tags=("ok",))
        ids = [all_id]
        for scan in scans:
            kind, _status_text, tag = _scan_status(scan, self.app)
            icon, text, _ = status_style(kind)
            count = "—" if scan.status == "fail" else scan.enabled_count()
            label = f"{icon} {scan.path.name}"
            if kind in {"many", "nowrite"}:
                label = f"{icon} {text}  {scan.path.name}"
            item = tree.insert("", "end", values=(label, count), tags=(tag,))
            ids.append(item)
        target = ids[0] if self.app.selected == "all" else ids[self.app.selected + 1]
        tree.selection_set(target)
        tree.see(target)
        tree.pack(fill="both", expand=True)

        def on_sel(_e=None):
            sel = tree.selection()
            if not sel:
                return
            index = ids.index(sel[0])
            self._select("all" if index == 0 else index - 1)

        tree.bind("<<TreeviewSelect>>", on_sel)

    def _all_preview(self, parent, scans, fonts, all_fail, nohit_all) -> None:
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text="全部文件合计", bg=BG_INSET, fg=INK, font=fonts["bold"]).pack(side="left", padx=10, pady=5)
        body = tk.Frame(parent, bg=BG_SURFACE)
        body.pack(fill="both", expand=True)
        if all_fail:
            _empty_state(
                body,
                "✕",
                f"{len(scans)} 个文件都没能处理",
                "可能是文件放在了网络盘且已断开，或都不是支持的格式。请把文件复制到本机再拖进来。",
                [("回到选文件", self.app.back_to_files, True)],
                fonts,
                bad=True,
            )
            return
        if nohit_all:
            _empty_state(
                body,
                "—",
                "这批文件里没有找到要遮的内容",
                "文件都正常打开了，但规则一处都没命中。下一步：检查规则里的例子是否和这批文件的写法一致，或换一类规则再扫。",
                [("检查规则", self.app.leave_to_rules, True), ("换一批文件", self.app.back_to_files, False)],
                fonts,
            )
            return
        cats = self.app.category_order()
        ok_scans = [s for s in scans if s.status != "fail"]
        self._category_matrix(body, cats, ok_scans, fonts)

    def _category_matrix(self, parent, cats, ok_scans, fonts) -> None:
        """Category names stay inside the name column. Count columns match the header text."""
        table = tk.Frame(parent, bg=BG_SURFACE)
        table.pack(fill="both", expand=True, padx=1, pady=2)
        headers = ["类别"] + [_short_type(scan) for scan in ok_scans] + ["合计"]
        ncols = len(headers)
        small = fonts["small"]
        count_w = max(small.measure("Word"), small.measure("88")) + 2
        # 「方案号清单」 fits on one line. Longer names wrap inside this column.
        name_w = small.measure("② 方案号清单") + 8
        table.columnconfigure(0, weight=1, minsize=name_w)
        for col in range(1, ncols):
            table.columnconfigure(col, minsize=count_w, weight=0)
        name_labels: list[tk.Label] = []

        def cell(row: int, col: int, text: str, *, header: bool = False, name: bool = False) -> None:
            bg = BG_INSET if header or row == len(cats) + 1 else BG_SURFACE
            fg = INK2 if header else INK
            lab = tk.Label(
                table,
                text=text,
                bg=bg,
                fg=fg,
                font=small,
                anchor="w" if col == 0 else "center",
                justify="left",
                padx=0,
                pady=1,
                wraplength=(name_w - 4) if name else 0,
            )
            lab.grid(row=row, column=col, sticky="nsew")
            if name:
                name_labels.append(lab)

        for col, title in enumerate(headers):
            cell(0, col, title, header=True)
        for index, cat in enumerate(cats):
            cell(index + 1, 0, f"{marker_for(index)} {cat}", name=True)
            total = 0
            for file_index, scan in enumerate(ok_scans):
                count = sum(1 for hit in scan.enabled_hits if hit.category == cat)
                total += count
                cell(index + 1, file_index + 1, str(count))
            cell(index + 1, ncols - 1, str(total))
        cell(len(cats) + 1, 0, "合计", name=True)
        grand = 0
        for file_index, scan in enumerate(ok_scans):
            count = scan.enabled_count()
            grand += count
            cell(len(cats) + 1, file_index + 1, str(count))
        cell(len(cats) + 1, ncols - 1, str(grand))

        def sync(_event=None) -> None:
            bbox = table.grid_bbox(0, 0)
            if not bbox or bbox[2] < 40:
                return
            # Wrap inside the name cell. Wider than the cell clips the last glyph.
            wrap = max(40, bbox[2] - 4)
            for lab in name_labels:
                if int(lab.cget("wraplength")) != wrap:
                    lab.configure(wraplength=wrap)

        table.bind("<Configure>", sync)
        table.after_idle(sync)

    def _file_preview(self, parent, scan: FileScan, fonts) -> None:
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        # Filename on its own row. Radios and the PDF pager each get a row too:
        # at 960px the side panes stay wide enough for full names, so the middle
        # column cannot hold "原文高亮" and "第 n / n 页" on one line.
        title_row = tk.Frame(head, bg=BG_INSET)
        title_row.pack(fill="x")
        tk.Label(title_row, text=scan.path.name, bg=BG_INSET, fg=INK, font=fonts["mono_font"]).pack(
            side="left", padx=10, pady=(4, 0)
        )
        tk.Label(
            title_row, text=_type_name(scan.kind), bg=BG_INSET, fg=INK2, font=fonts["small"], relief="solid", bd=1
        ).pack(side="left", pady=(4, 0))
        has_prev = scan.status != "fail" and bool(scan.hits)
        if has_prev:
            controls = tk.Frame(head, bg=BG_INSET)
            controls.pack(fill="x")
            mode = tk.StringVar(value=self.app.preview_mode)
            if scan.kind == KIND_IMAGE:
                ttk.Radiobutton(
                    controls, text="画框", value="orig", variable=mode, command=lambda: self._set_mode(mode.get())
                ).pack(side="left", padx=(8, 0))
                ttk.Radiobutton(
                    controls, text="涂黑后", value="after", variable=mode, command=lambda: self._set_mode(mode.get())
                ).pack(side="left")
            else:
                ttk.Radiobutton(
                    controls, text="原文高亮", value="orig", variable=mode, command=lambda: self._set_mode(mode.get())
                ).pack(side="left", padx=(8, 0))
                ttk.Radiobutton(
                    controls, text="替换后", value="after", variable=mode, command=lambda: self._set_mode(mode.get())
                ).pack(side="left")
        if scan.kind == KIND_PDF and has_prev and scan.pages:
            pager = tk.Frame(head, bg=BG_INSET)
            pager.pack(fill="x")
            self._pdf_pager = pager
            # Pack left to right: previous, page label, next.
            for glyph, delta in PDF_PAGER:
                ttk.Button(pager, text=glyph, command=lambda d=delta: self._turn(d), width=2).pack(
                    side="left", padx=(8, 0) if delta < 0 else (0, 4)
                )
                if delta < 0:
                    tk.Label(
                        pager,
                        text=f"第 {self.app.pdf_page + 1} / {len(scan.pages)} 页",
                        bg=BG_INSET,
                        fg=INK,
                        font=fonts["mono_font"],
                    ).pack(side="left", padx=4)

        body = tk.Frame(parent, bg=BG_SURFACE)
        if scan.status == "fail":
            body.pack(fill="both", expand=True)
            _empty_state(
                body,
                "✕",
                "打不开这个文件",
                scan.error or "",
                [("从列表移除", lambda: self.app.remove_scan(scan), False)],
                fonts,
                bad=True,
            )
            return
        if not scan.hits:
            body.pack(fill="both", expand=True)
            _empty_state(
                body,
                "—",
                "没有找到要遮的内容",
                "所选规则在这个文件里都没有命中。可以检查规则里的例子，或看看这个文件是不是扫描件。",
                [("去看规则", self.app.leave_to_rules, False)],
                fonts,
            )
            return
        # Pack the locator under the preview before the expanding body, or the
        # 960px middle column clips it.
        foot = tk.Frame(parent, bg=BG_INSET)
        foot.pack(side="bottom", fill="x")
        cur = self.app.hit_by_id(self.app.current_hit)
        if cur is not None:
            hint = f"当前：{cur.location} · {self.app.marker(cur.category)} {cur.category}"
        else:
            hint = "点任一处高亮，或点右边的位置，可以定位"
        fit_label(foot, hint, bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(fill="x", padx=8, pady=4)

        body.pack(fill="both", expand=True)
        if scan.kind == KIND_IMAGE:
            self._image_canvas(body, scan)
        else:
            self._text_preview(body, scan, fonts)

    def _set_mode(self, value: str) -> None:
        self.app.preview_mode = value
        self.refresh()

    def _turn(self, delta: int) -> None:
        scan = self.app.scans[self.app.selected]
        n = len(scan.pages) if scan.pages else 1
        self.app.pdf_page = clamp_page(self.app.pdf_page, delta, n)
        self.refresh()

    def _text_preview(self, parent, scan: FileScan, fonts) -> None:
        host = parent
        scroll = None
        # PDF text is taller than the middle column. A scrollbar keeps the
        # rest of the page reachable instead of clipping the last lines.
        if scan.kind == KIND_PDF:
            host = tk.Frame(parent, bg=BG_SURFACE)
            host.pack(fill="both", expand=True)
            scroll = ttk.Scrollbar(host, orient="vertical")
            scroll.pack(side="right", fill="y")
        text = tk.Text(
            host,
            height=6,
            wrap="word",
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            font=fonts["ui"],
            bg=BG_SURFACE,
            fg=INK,
            yscrollcommand=(scroll.set if scroll is not None else None),
        )
        text.pack(fill="both", expand=True)
        if scroll is not None:
            scroll.configure(command=text.yview)
        self._preview_text = text
        self._preview_scroll = scroll
        text.tag_configure("hit", background=HL, underline=True)
        text.tag_configure("cur", background=HL2, underline=True)
        text.tag_configure("off", foreground=PLACE, underline=True)
        text.tag_configure("redacted", background=BG_INSET, font=fonts["mono_font"])
        text.tag_configure("black", background="#000000", foreground="#000000")
        text.tag_configure("mark", font=fonts["small"])
        text.tag_configure("page", foreground=MUTED, font=fonts["mono_font"])
        text.tag_configure("title", font=fonts["title"])
        self._hit_ranges = {}
        cats = {cat: i for i, cat in enumerate(self.app.category_order())}
        replacements = self.app.ruleset.replacements()

        def insert_run(block: str, block_hits: list, title: bool = False):
            pos = 0
            ordered = sorted(block_hits, key=lambda h: h.start)
            for hit in ordered:
                if hit.start > pos:
                    text.insert("end", block[pos : hit.start], ("title",) if title else ())
                display, tags = self._hit_display(scan, hit, replacements)
                start_index = text.index("end-1c")
                text.insert("end", display, tags)
                end_index = text.index("end-1c")
                tag_name = f"h{hit.hit_id}"
                text.tag_add(tag_name, start_index, end_index)
                self._hit_ranges[tag_name] = hit.hit_id
                if hit.enabled and self.app.preview_mode == "orig":
                    text.insert("end", marker_for(cats.get(hit.category, 0)), ("mark",))
                pos = hit.end
            if pos < len(block):
                text.insert("end", block[pos:], ("title",) if title else ())
            text.insert("end", "\n")

        page_starts: list[str] = []
        if scan.kind == KIND_WORD:
            by_unit: dict[str, list] = {}
            for hit in scan.hits:
                by_unit.setdefault(hit.unit, []).append(hit)
            for i, para in enumerate(scan.paragraphs):
                insert_run(para, by_unit.get(f"p:{i}", []), title=(i == 0))
        else:
            for pi, page in enumerate(scan.pages):
                page_starts.append(text.index("end-1c"))
                text.insert("end", f"—— 第 {pi + 1} 页 ——\n", ("page",))
                page_hits = [h for h in scan.hits if h.page == pi and h.unit.startswith("page:")]
                offset = 0
                for li, line in enumerate(page):
                    line_hits = []
                    for hit in page_hits:
                        if offset <= hit.start < offset + len(line) + 1:
                            line_hits.append(
                                type(hit)(
                                    **{
                                        **hit.__dict__,
                                        "start": hit.start - offset,
                                        "end": hit.end - offset,
                                    }
                                )
                            )
                    insert_run(line, line_hits)
                    offset += len(line) + 1

        def on_click(event):
            index = text.index(f"@{event.x},{event.y}")
            for tag in text.tag_names(index):
                if tag.startswith("h") and tag[1:].isdigit():
                    self.app.current_hit = int(tag[1:])
                    self.refresh()
                    return

        text.bind("<Button-1>", on_click)
        text.configure(state="disabled")
        if page_starts:
            self._reveal_open_page(text, page_starts)
        elif self.app.current_hit is not None:
            tag = f"h{self.app.current_hit}"
            try:
                text.see(f"{tag}.first")
            except tk.TclError:
                pass

    def _reveal_open_page(self, text: tk.Text, page_starts: list[str]) -> None:
        """Scroll so the open page's 「—— 第 N 页 ——」 is the first visible line.

        Page 1 returns to the top. The first configure is often the short
        requested height, so align again when the pane grows.
        """
        index = clamp_page(self.app.pdf_page, 0, len(page_starts))
        marker = page_starts[index]
        state = {"height": 0, "busy": False}

        def show(_event=None) -> None:
            if state["busy"] or not text.winfo_exists():
                return
            height = text.winfo_height()
            if height < 80 or height == state["height"]:
                return
            state["busy"] = True
            state["height"] = height
            try:
                scroll_page_marker(text, marker, to_top=(index == 0))
            except tk.TclError:
                state["height"] = 0
            finally:
                state["busy"] = False

        text.bind("<Configure>", show, add="+")
        text.after_idle(show)

    def _hit_display(self, scan, hit, replacements):
        hide = self.app.hide_text
        cur = self.app.current_hit == hit.hit_id
        if not hit.enabled:
            shown = "▇" * min(len(hit.text), 8) if hide else hit.text
            return shown, ("off",)
        if self.app.preview_mode == "after":
            if scan.kind == KIND_IMAGE:
                return "█" * max(3, min(len(hit.text), 8)), ("black", "cur" if cur else "")
            label = replacements.get(hit.category) or redaction_label(hit.category)
            return label.replace(" ", "\u00a0"), ("redacted", "cur" if cur else "")
        shown = "▇" * min(len(hit.text), 8) if hide else hit.text
        return shown, ("cur" if cur else "hit",)

    def _image_canvas(self, parent, scan: FileScan) -> None:
        canvas = tk.Canvas(parent, bg=BG_PAGE, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        image = scan.image
        # Scale to the pane. A fixed 520px preview is wider than the middle
        # column once the file list and hit list are wide enough at 960px.
        placed = {"scale": 1.0, "x0": 8, "y0": 8}

        def draw(_event=None) -> None:
            if image is None:
                return
            pane_w = canvas.winfo_width()
            pane_h = canvas.winfo_height()
            if pane_w < 30 or pane_h < 30:
                return
            max_w = max(40, pane_w - 16)
            max_h = max(40, pane_h - 16)
            w, h = image.size
            scale = min(max_w / w, max_h / h, 1.0)
            show = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            if self.app.hide_text:
                show = show.point(lambda p: 230)
            self._photo = ImageTk.PhotoImage(show)
            x0, y0 = 8, 8
            placed["scale"] = scale
            placed["x0"] = x0
            placed["y0"] = y0
            canvas.delete("all")
            canvas.create_image(x0, y0, anchor="nw", image=self._photo)
            dashes = {0: None, 1: (6, 3), 2: (2, 3)}
            cats = {cat: i for i, cat in enumerate(self.app.category_order())}
            for hit in scan.hits:
                for box in hit.boxes:
                    left, top, width, height = box
                    x1, y1 = x0 + left * scale, y0 + top * scale
                    x2, y2 = x1 + width * scale, y1 + height * scale
                    fill = ""
                    if hit.enabled and (self.app.preview_mode == "after" or self.app.hide_text):
                        fill = "#000000"
                    outline = "#ffd84d" if self.app.current_hit == hit.hit_id else (INK if hit.enabled else PLACE)
                    width_px = 3 if self.app.current_hit == hit.hit_id else 2
                    dash = dashes.get(cats.get(hit.category, 0) % 3)
                    kwargs = {"outline": outline, "width": width_px, "fill": fill}
                    if dash:
                        kwargs["dash"] = dash
                    canvas.create_rectangle(x1, y1, x2, y2, **kwargs)
                    if hit.enabled:
                        canvas.create_text(
                            x2 - 2,
                            y1 - 2,
                            text=marker_for(cats.get(hit.category, 0)),
                            anchor="se",
                            fill=INK,
                        )

        canvas.bind("<Configure>", draw)
        canvas.bind(
            "<Button-1>",
            lambda e: self._maybe_pick_box(e, scan, placed["scale"], placed["x0"], placed["y0"]),
        )
        canvas.after_idle(draw)

    def _maybe_pick_box(self, event, scan, scale, x0, y0) -> None:
        px = (event.x - x0) / scale
        py = (event.y - y0) / scale
        for hit in scan.hits:
            for left, top, width, height in hit.boxes:
                if left <= px <= left + width and top <= py <= top + height:
                    self.app.current_hit = hit.hit_id
                    self.refresh()
                    return

    def _right_pane(self, parent, scans, fonts) -> None:
        if self.app.selected == "all":
            current = None
            hits = [h for s in scans if s.status != "fail" for h in s.hits]
        else:
            current = scans[self.app.selected]
            hits = list(current.hits) if current.status != "fail" else []
        if self.app.selected_cat:
            hits = [h for h in hits if h.category == self.app.selected_cat]
        cats = self.app.category_order()
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text="按类别汇总" + ("（全部文件）" if current is None else ""), bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        tree = ttk.Treeview(parent, columns=("cat", "n"), show="headings", height=min(6, len(cats) + 1))
        tree.heading("cat", text="类别")
        tree.heading("n", text="个数")
        tree.column("cat", width=140, stretch=True)
        tree.column("n", width=50, anchor="e", stretch=False)
        source = current.enabled_hits if current is not None else [h for s in scans for h in s.enabled_hits]
        if current is not None and current.status == "fail":
            source = []
        for i, cat in enumerate(cats):
            n = sum(1 for h in source if h.category == cat)
            item = tree.insert("", "end", values=(f"{marker_for(i)} {cat}", n))
            if self.app.selected_cat == cat:
                tree.selection_set(item)
        total = len(source)
        tree.insert("", "end", values=("合计", total))
        tree.pack(fill="x")

        def on_cat(_e=None):
            if getattr(self, "_building", False):
                return
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            nxt = None if idx >= len(cats) else cats[idx]
            if self.app.selected_cat == nxt:
                return
            self.app.selected_cat = nxt
            self.refresh()

        tree.bind("<<TreeviewSelect>>", on_cat)

        loc_head = tk.Frame(parent, bg=BG_INSET)
        loc_head.pack(fill="x")
        tk.Label(loc_head, text=f"被替换的位置 {len(hits)}", bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        if self.app.selected_cat:
            ttk.Button(loc_head, text="看全部类别", command=lambda: self._clear_cat()).pack(side="right", padx=6)
        off = unchecked_count(scans)
        if off > 0 and not self.app.written:
            restore = tk.Frame(parent, bg="#fdf1e5")
            restore.pack(fill="x")
            ttk.Button(
                restore,
                text=MSG_RESTORE.format(n=off),
                command=self.app.restore_all_hits,
            ).pack(anchor="w", padx=8, pady=4)

        loc = ttk.Treeview(parent, columns=("on", "loc", "cat", "text"), show="headings", selectmode="browse")
        loc.heading("on", text="" if self.app.written else "替换")
        loc.heading("loc", text="位置")
        loc.heading("cat", text="类")
        loc.heading("text", text="内容")
        # Room for "第 501 页 · 第 1 行" and "PRJ-2026-017-S02", plus the scrollbar.
        loc.column("on", width=32, minwidth=32, stretch=False, anchor="center")
        loc.column("loc", width=140, minwidth=132, stretch=False)
        loc.column("cat", width=28, minwidth=28, stretch=False)
        loc.column("text", width=132, minwidth=124, stretch=True)
        cat_index = {c: i for i, c in enumerate(cats)}
        for hit in hits:
            mark = "✓" if self.app.written and hit.enabled else ("☑" if hit.enabled else "☐")
            content = "▇▇▇▇" if self.app.hide_text else hit.text
            loc.insert(
                "",
                "end",
                iid=str(hit.hit_id),
                values=(mark, hit.location, marker_for(cat_index.get(hit.category, 0)), content),
            )
        if self.app.current_hit is not None and loc.exists(str(self.app.current_hit)):
            loc.selection_set(str(self.app.current_hit))
            loc.see(str(self.app.current_hit))
        loc.pack(fill="both", expand=True)

        def on_loc_click(event):
            row = loc.identify_row(event.y)
            col = loc.identify_column(event.x)
            if not row:
                return
            hid = int(row)
            if col == "#1" and not self.app.written:
                self.app.toggle_hit(hid)
                self.refresh()
                return
            self.app.jump_to_hit(hid)
            self.refresh()

        loc.bind("<Button-1>", on_loc_click)

    def _clear_cat(self) -> None:
        self.app.selected_cat = None
        self.refresh()

    def _foot(self, scans, fonts, all_fail, nohit_all) -> None:
        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(side="bottom", fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        enabled, _ = session_totals(scans, enabled_only=True)
        app = self.app
        if app.written:
            for scan in scans:
                if str(scan.path) in app.write_errors:
                    enabled -= scan.enabled_count()
        n_files = app.written_file_count()
        failures = app.write_failure_rows()
        path = app.output_label()
        buttons = tk.Frame(inner, bg=BG_SURFACE)
        buttons.pack(side="right")
        left = tk.Frame(inner, bg=BG_SURFACE)
        left.pack(side="left", fill="x", expand=True)
        if app.written and failures:
            tk.Label(
                left,
                text=f"✕ {MSG_PARTIAL_BOTTOM.format(n=n_files, m=len(failures))}  {MSG_PARTIAL_KEEP}",
                bg=BG_SURFACE,
                fg="#cb2f39",
                font=fonts["small"],
                anchor="w",
            ).pack(fill="x")
        elif app.written:
            tk.Label(
                left,
                text=f"✓ 已写出 {n_files} 个脱敏文件，共替换 {enabled} 处",
                bg=BG_SURFACE,
                fg="#008035",
                font=fonts["small"],
                anchor="w",
            ).pack(fill="x")
        else:
            tk.Label(
                left,
                text=MSG_OUTPUT_HINT,
                bg=BG_SURFACE,
                fg=INK2,
                font=fonts["small"],
                anchor="w",
                wraplength=480,
                justify="left",
            ).pack(fill="x")
        tk.Label(left, text=path, bg=BG_SURFACE, fg=INK2, font=fonts["mono_font"], anchor="w").pack(fill="x")
        if app.written and failures:
            ttk.Button(buttons, text="打开输出文件夹", style="Primary.TButton", command=app.open_output).pack(side="right")
            ttk.Button(buttons, text="重试失败的", command=lambda: app.write_files(retry=True)).pack(
                side="right", padx=(0, 8)
            )
            ttk.Button(buttons, text="返回修改", command=app.back_to_rules).pack(side="right", padx=(0, 8))
            return
        if app.written:
            ttk.Button(buttons, text="打开输出文件夹", style="Primary.TButton", command=app.open_output).pack(side="right")
            ttk.Button(buttons, text="导出命中清单", command=app.export_hit_list).pack(side="right", padx=(0, 8))
            ttk.Button(buttons, text="改规则再出一份", command=app.back_to_rules).pack(side="right", padx=(0, 8))
            ttk.Button(buttons, text="返回修改", command=app.back_to_rules).pack(side="right", padx=(0, 8))
            return
        can_write = write_allowed(has_fresh_scans=bool(scans)) and enabled > 0 and not all_fail
        ttk.Button(
            buttons,
            text=f"写出 {n_files} 个脱敏文件（替换 {enabled} 处）",
            style="Primary.TButton",
            command=app.write_files,
            state=("normal" if can_write else "disabled"),
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="导出命中清单",
            command=app.export_hit_list,
            state=("normal" if can_write else "disabled"),
        ).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="返回修改", command=app.back_to_rules).pack(side="right", padx=(0, 8))


def _scan_status(scan: FileScan, app) -> tuple[str, str, str]:
    if scan.status == "fail":
        return "fail", "失败", "bad"
    if str(scan.path) in app.write_errors:
        return "nowrite", "没写出", "bad"
    if scan.too_many:
        return "many", "命中偏多", "warn"
    if app.written and scan.enabled_count() > 0:
        return "written", "已写出", "ok"
    if not scan.hits:
        return "nohit", "无命中", "no"
    return "hit", "有命中", "ok"


def _type_name(kind: str) -> str:
    return {"word": "Word", "pdf": "PDF", "image": "图片"}.get(kind, kind)


def _short_type(scan: FileScan) -> str:
    return _type_name(scan.kind)


def _empty_state(parent, icon, title, body, buttons, fonts, bad=False) -> None:
    wrap = tk.Frame(parent, bg=BG_SURFACE)
    wrap.pack(fill="both", expand=True)
    inner = tk.Frame(wrap, bg=BG_SURFACE)
    inner.pack(expand=True, fill="x", padx=12)
    color = "#cb2f39" if bad else MUTED
    tk.Label(inner, text=icon, bg=BG_SURFACE, fg=color, font=fonts["title"]).pack()
    tk.Label(inner, text=title, bg=BG_SURFACE, fg=color if bad else INK, font=fonts["title"]).pack(pady=4)
    fit_label(inner, body, bg=BG_SURFACE, fg=INK2, font=fonts["small"], justify="center").pack(fill="x")
    row = tk.Frame(inner, bg=BG_SURFACE)
    row.pack(pady=10)
    for label, cmd, primary in buttons:
        ttk.Button(row, text=label, style="Primary.TButton" if primary else "TButton", command=cmd).pack(
            side="left", padx=4
        )
