"""Result screen: files, preview, categories, locations, write."""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import ImageTk

from doc_redactor.backpath import (
    MSG_CLEARED_TOP,
    MSG_OUTPUT_HINT,
    MSG_PARTIAL_BOTTOM,
    failed_file_count,
    MSG_PARTIAL_KEEP,
    MSG_RESTORE,
    MSG_WRITTEN_TOP,
    partial_banner,
    unchecked_count,
    unwritten_failure_banner,
    write_allowed,
)
from doc_redactor.gui import hf_text
from doc_redactor.gui.steps import StepBar

HF_GREY = "#e4e6e8"
HF_INK = "#5d6066"
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
    WARN,
    WARN_BG,
    fit_label,
    marker_for,
    status_style,
)
from doc_redactor import header_footer as hf
from doc_redactor.engine import redaction_label
from doc_redactor.verify_output import is_final_failure
from doc_redactor.pdf_redactor import (
    MSG_PDF_WIDTH,
    ShortPlan,
    fit_notice,
    pdf_fit_counts,
    placeholder_choice,
    placeholder_rect,
)
from doc_redactor.leak_check import MSG_NO_HITS, MSG_NO_HITS_LINE
from doc_redactor.scan import KIND_IMAGE, KIND_PDF, KIND_WORD, FileScan

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
        nohit_all = bool(scans) and all(s.status != "fail" and not _has_findings(s) for s in scans) and not any(
            s.status == "fail" for s in scans
        )
        if scans and not any(s.status == "fail" for s in scans) and all(
            s.status == "nohit" or not _has_findings(s) for s in scans
        ):
            nohit_all = True

        head = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        head.pack(fill="x")
        row = tk.Frame(head, bg=BG_SURFACE)
        row.pack(fill="x", padx=12, pady=4)
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
            ).pack(side="left", padx=12, pady=3)
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
            # Laid out by wrap_lines, so a comma never starts a line.
            _wrap_label(
                err,
                partial_banner(failures) if app.written else unwritten_failure_banner(failures),
                bg="#fcecec",
                fg="#cb2f39",
                font=fonts["small"],
            ).pack(fill="x", padx=12, pady=6)
        if app.written:
            ok = tk.Frame(self.frame, bg="#e8f5ed")
            ok.pack(fill="x")
            tk.Label(
                ok,
                text="✓ " + MSG_WRITTEN_TOP.format(n=app.written_file_count())
                + ("已删除 " + str(app.hf_written_count()) + " 个文件的页眉页脚。" if app.hf_written_count() else ""),
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
            # The hit list's own column widths set this pane. A smaller minimum
            # leaves the middle column enough width that a PDF page still fits.
            body.columnconfigure(2, minsize=300, weight=0)
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
        enabled = writable_hit_total(scans, written=self.app.written, write_errors=self.app.write_errors)
        cancelled_n = sum(len(s.visible_hits) - s.enabled_count() for s in scans if s.status != "fail")
        n_hit = sum(1 for s in scans if s.status != "fail" and _has_findings(s))
        n_no = sum(1 for s in scans if s.status != "fail" and not _has_findings(s))
        # Same count as the bottom bar: scan failures plus files not written.
        n_fail = failed_file_count(scans, self.app.write_errors)
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
        for part, tone in hf_text.top_parts(scans, self.app.remove_hf, self.app.written):
            tk.Label(
                inner,
                text=f"    {part}",
                bg=BG_SURFACE,
                fg=_tone_fg(tone),
                font=fonts["small"],
            ).pack(side="left")

    def _file_list(self, parent, scans, fonts) -> None:
        # Pack the notice first so the tree keeps the remaining height.
        # The filename column is too narrow for the full sentence.
        notices = []
        for scan in scans:
            text = _pdf_fit_notice(scan, self.app)
            if text:
                notices.append((scan.path.name, text, "info"))
            note, tone = hf_text.file_note_tone(scan, self.app.remove_hf)
            if note:
                notices.append((scan.path.name, note, tone))
        if notices:
            box = tk.Frame(parent, bg=BG_INSET)
            box.pack(side="bottom", fill="x")
            for name, text, *tone in notices:
                tk.Label(box, text=name, bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
                    anchor="w", padx=8, pady=(2, 0)
                )
                fg = _tone_fg(tone[0]) if tone else INK
                _wrap_label(box, text, bg=BG_INSET, fg=fg, font=fonts["small"]).pack(fill="x", padx=6, pady=(0, 2))
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
        total = writable_hit_total(scans, written=self.app.written, write_errors=self.app.write_errors)
        all_id = tree.insert("", "end", values=("▤ 全部文件", total), tags=("ok",))
        ids = [all_id]
        hf_rows: dict[str, int] = {}
        for index, scan in enumerate(scans):
            kind, status_text, tag = _scan_status(scan, self.app)
            icon, text, _ = status_style(kind)
            if kind == "unlocated":
                text = status_text
            # A blocked or refused file writes nothing, so its row adds nothing
            # to 全部文件.
            if counts_toward_write(scan, written=self.app.written, write_errors=self.app.write_errors):
                count = scan.enabled_count()
            else:
                count = "—"
            label = f"{icon} {scan.path.name}"
            if kind in {"skipped", "unlocated"}:
                label = f"{icon} {scan.path.name}  {text}"
            elif kind in {"many", "nowrite", "nohit_unwritten"}:
                label = f"{icon} {text}  {scan.path.name}"
            item = tree.insert("", "end", values=(label, count), tags=(tag,))
            ids.append(item)
            sub = _hf_file_row(scan, self.app)
            if sub is not None:
                # One line under the file: its own 「去掉页眉页脚」 box.
                row = tree.insert("", "end", values=(sub, ""), tags=("hf_off" if _hf_box_disabled(scan, self.app) else "hf",))
                hf_rows[row] = index
        tree.tag_configure("hf", foreground=INK)
        tree.tag_configure("hf_off", foreground=PLACE)
        target = ids[0] if self.app.selected == "all" else ids[self.app.selected + 1]
        tree.selection_set(target)
        tree.see(target)
        tree.pack(fill="both", expand=True)

        def on_sel(_e=None):
            sel = tree.selection()
            if not sel:
                return
            if sel[0] in hf_rows:
                index = hf_rows[sel[0]]
                scan = scans[index]
                if not _hf_box_disabled(scan, self.app):
                    self.app.toggle_file_hf(scan)
                    return
                tree.selection_set(ids[index + 1])
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
                # 文案待前端定。
                "文件都正常打开了，但规则一处都没命中，所以不会写出任何文件，原件也不要当成脱敏件交出去。下一步：检查规则里的例子是否和这批文件的写法一致，或换一类规则再扫。",
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
        # After a write, a file that was not written keeps its found counts,
        # says 未写出 in the 合计 row, and adds nothing to the totals.
        counted = [not self.app.written or self._counts_toward_write(scan) for scan in ok_scans]
        for index, cat in enumerate(cats):
            cell(index + 1, 0, f"{marker_for(index)} {cat}", name=True)
            total = 0
            for file_index, scan in enumerate(ok_scans):
                count = sum(1 for hit in scan.enabled_hits if hit.category == cat)
                if counted[file_index]:
                    total += count
                cell(index + 1, file_index + 1, str(count))
            cell(index + 1, ncols - 1, str(total))
        cell(len(cats) + 1, 0, "合计", name=True)
        grand = 0
        for file_index, scan in enumerate(ok_scans):
            count = scan.enabled_count()
            if counted[file_index]:
                grand += count
                cell(len(cats) + 1, file_index + 1, str(count))
            else:
                cell(len(cats) + 1, file_index + 1, "—")
        cell(len(cats) + 1, ncols - 1, str(grand))
        # The column stays narrow ("—"); the note names the file under the table.
        skipped = [scan.path.name for scan, ok in zip(ok_scans, counted) if not ok]
        if skipped:
            note = MSG_MATRIX_UNWRITTEN.format(names="、".join(skipped))
            _wrap_label(parent, note, bg=BG_SURFACE, fg=INK2, font=small).pack(side="bottom", fill="x", padx=8, pady=(0, 4))

        def sync(_event=None) -> None:
            bbox = table.grid_bbox(0, 0)
            if not bbox or bbox[2] < 40:
                return
            # Wrap inside the name cell. Wider than the cell clips the last glyph.
            wrap = max(40, bbox[2] - 4)
            for lab in name_labels:
                if int(lab.cget("wraplength")) != wrap:
                    lab.configure(wraplength=wrap)

        # Grid works out column widths at idle time, after <Configure>; read
        # the cell width then, or the names wrap to the previous width.
        table.bind("<Configure>", lambda _e: table.after_idle(sync), add="+")
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
        notice = _pdf_fit_notice(scan, self.app) if scan.kind == KIND_PDF else ""
        if notice:
            _wrap_label(head, notice, bg=BG_INSET, fg=INK, font=fonts["small"]).pack(fill="x", padx=8, pady=(1, 0))
        if has_prev:
            notes = hf_text.preview_notes(scan, self.app.remove_hf)
            for i, line in enumerate(notes):
                _wrap_label(head, line, bg=BG_INSET, fg=INK if i == 0 else INK2, font=fonts["small"]).pack(
                    fill="x", padx=8, pady=(1 if i == 0 else 0, 1 if i == len(notes) - 1 else 0)
                )
            bar = hf_text.confirm_bar(scan, self.app.remove_hf, self.app.written)
            if bar is not None:
                self._confirm_bar(head, scan, bar, fonts)
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
            unlocated = int(getattr(scan, "unlocated_count", 0) or 0)
            if unlocated > 0:
                _empty_state(
                    body,
                    "⚠",
                    f"{unlocated} 处无法定位，未写出",
                    "文字里找到了，但定不到页面上的位置。这个文件不写出，避免原文留下。",
                    [],
                    fonts,
                )
                return
            _empty_state(
                body,
                "—",
                "没有找到要遮的内容",
                # 文案待前端定。
                f"所选规则在这个文件里都没有命中。{MSG_NO_HITS_LINE}。可以检查规则里的例子，或看看这个文件是不是扫描件。",
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
        # 「去掉页眉页脚」 blocks (方案.md 2.11). Text cannot draw a dashed frame,
        # so the block is grey with strike-through and a framed label.
        text.tag_configure("hf_del", background=HF_GREY, foreground=HF_INK, overstrike=1)
        text.tag_configure("hf_label", background="#ffffff", foreground=INK, font=fonts["bold"], relief="solid", borderwidth=1)
        text.tag_configure("hf_note", background=HF_GREY, foreground=INK2, font=fonts["small"])
        text.tag_configure("hf_gap", font=fonts["small"])
        text.tag_configure("hf_bar", foreground=LINE2)
        text.tag_configure("hf_keep", foreground=INK2, font=fonts["small"], relief="solid", borderwidth=1)
        # 待确认: light orange, no strike-through (dashed frame cannot be drawn in Text).
        text.tag_configure("hf_pend", background=WARN_BG)
        text.tag_lower("hf_pend")
        text.tag_configure("hf_pend_label", background="#ffffff", foreground=WARN, font=fonts["small"], relief="solid", borderwidth=1)
        self._hit_ranges = {}
        cats = {cat: i for i, cat in enumerate(self.app.category_order())}
        replacements = self.app.ruleset.replacements()
        self._short_plan = ShortPlan([rule.name for rule in self.app.ruleset.rules])

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

        after = self.app.preview_mode == "after"
        hide = self.app.hide_text
        state = {"block": None}

        def hf_line(role: str, line: str, line_hits: list, title: bool = False, source: str = "") -> None:
            """One line of a header/footer. ``state`` keeps the open block.

            Three looks (方案.md 2.11): 将删除 grey + strike-through, above any
            hit highlight; 待确认 light orange, hits shown and counted; kept, a
            grey bar and a label.
            """
            source = source or (hf.SOURCE_WORD if scan.kind == KIND_WORD else hf.SOURCE_TAG)
            style = hf_text.block_style(scan, source) if self.app.remove_hf else hf_text.STYLE_KEEP
            key = (role, source, style)
            if style == hf_text.STYLE_DELETE:
                if state["block"] != key:
                    close_block()
                    state["block"] = key
                    if not after:
                        text.insert("end", hf_text.block_label(role, scan, source), ("hf_label",))
                        text.insert("end", "\n", ("hf_del",))
                if after:
                    text.insert("end", "\n", ("hf_gap",))
                    return
                shown = ("▇" * min(len(line.strip()), 12)) if hide else line
                text.insert("end", shown + "\n", ("hf_del",))
                return
            if style == hf_text.STYLE_PENDING:
                if state["block"] != key:
                    close_block()
                    state["block"] = key
                    text.insert("end", hf_text.block_label(role, scan, source), ("hf_pend_label",))
                    text.insert("end", "\n", ("hf_pend",))
                start = text.index("end-1c")
                insert_run(line, line_hits, title=title)
                text.tag_add("hf_pend", start, "end-1c")
                return
            close_block()
            text.insert("end", "▏", ("hf_bar",))
            if self.app.remove_hf:
                text.insert("end", hf_text.block_label(role, scan, source), ("hf_keep",))
                text.insert("end", " ")
            insert_run(line, line_hits, title=title)

        def close_block() -> None:
            block = state["block"]
            state["block"] = None
            if block is None:
                return
            role, _source, style = block
            if style == hf_text.STYLE_PENDING:
                text.insert("end", "\n", ("hf_gap",))
                return
            if after:
                return
            if role == hf.ROLE_SIGNATURE:
                text.insert("end", hf_text.SIGN_NOTE + "\n", ("hf_note",))
            if role == hf.ROLE_HEADER and scan.kind == KIND_WORD and scan.hf_watermark:
                mark = "▇▇" if hide else (scan.hf_watermark_text or "")
                text.insert("end", f"水印「{mark}」在页眉里，一起删除\n", ("hf_note",))

        page_starts: list[str] = []
        if scan.kind == KIND_WORD:
            by_unit: dict[str, list] = {}
            for hit in scan.hits:
                by_unit.setdefault(hit.unit, []).append(hit)
            roles = scan.hf_units or {}
            # Word headers go on top of the preview, footers at the bottom.
            heads = [i for i in range(len(scan.paragraphs)) if roles.get(f"p:{i}") == hf.ROLE_HEADER]
            foots = [i for i in range(len(scan.paragraphs)) if roles.get(f"p:{i}") == hf.ROLE_FOOTER]
            body_ids = [i for i in range(len(scan.paragraphs)) if f"p:{i}" not in roles]
            for i in heads:
                hf_line(hf.ROLE_HEADER, scan.paragraphs[i], by_unit.get(f"p:{i}", []))
            close_block()
            for i in body_ids:
                insert_run(scan.paragraphs[i], by_unit.get(f"p:{i}", []), title=(i == 0))
            for i in foots:
                hf_line(hf.ROLE_FOOTER, scan.paragraphs[i], by_unit.get(f"p:{i}", []))
            close_block()
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
                    role = scan.hf_lines.get((pi, li)) if scan.hf_lines else None
                    if role:
                        source = (scan.hf_line_sources or {}).get((pi, li), "")
                        hf_line(role, line, line_hits, source=source)
                    else:
                        close_block()
                        insert_run(line, line_hits)
                    offset += len(line) + 1
                close_block()

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
            if scan.kind == KIND_PDF and hit.rects:
                plan = getattr(self, "_short_plan", None) or ShortPlan(
                    [rule.name for rule in self.app.ruleset.rules]
                )
                code, _title = plan.resolve(hit.category)
                anchor = placeholder_rect(hit.rects, label, code, getattr(hit, "text", "") or "")
                if anchor is None:
                    return "", ("redacted", "cur" if cur else "")
                _kind, shown = placeholder_choice(anchor, label, code)
                if not shown:
                    return "", ("redacted", "cur" if cur else "")
                return shown.replace(" ", "\u00a0"), ("redacted", "cur" if cur else "")
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

    def _confirm_bar(self, head, scan, bar, fonts) -> None:
        """「⚠ 待确认，不删」 / 「✓ 已确认删除」 strip: status and its button on one
        row, the explanation under them across the whole column.

        Side by side, the button and the status left the text about three
        characters wide at 960×640; it grew tall enough to push the radios,
        the pager and the preview out of the column (#28 r2 M3).
        """
        warn = bar["tone"] == hf_text.TONE_WARN
        bg = WARN_BG if warn else "#e8f5ed"
        fg = WARN if warn else "#008035"
        strip = tk.Frame(head, bg=bg)
        strip.pack(fill="x", padx=0, pady=(1, 0))
        top = tk.Frame(strip, bg=bg)
        top.pack(fill="x")
        ttk.Button(
            top,
            text=bar["button"],
            command=lambda: self.app.toggle_file_hf_confirm(scan),
            state="normal" if bar["enabled"] else "disabled",
        ).pack(side="right", padx=8, pady=(3, 0))
        tk.Label(top, text=bar["status"], bg=bg, fg=fg, font=fonts["small"]).pack(side="left", padx=8, pady=(3, 0))
        label = _wrap_label(strip, bar["text"], bg=bg, fg=INK, font=fonts["small"])
        label.pack(fill="x", padx=8, pady=(0, 3))
        label.hf_bar = bar

    def _right_pane(self, parent, scans, fonts) -> None:
        if self.app.selected == "all":
            current = None
            hits = [h for s in scans if s.status != "fail" for h in s.visible_hits]
        else:
            current = scans[self.app.selected]
            # Hits inside a block to be deleted are not listed or counted.
            hits = list(current.visible_hits) if current.status != "fail" else []
        if self.app.selected_cat:
            hits = [h for h in hits if h.category == self.app.selected_cat]
        cats = self.app.category_order()
        head = tk.Frame(parent, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text="按类别汇总" + ("（全部文件）" if current is None else ""), bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
            side="left", padx=10, pady=5
        )
        # The table sits in its own box so a scrollbar can join it when the
        # pane is too short for every row (see _fit_right_pane).
        cat_box = tk.Frame(parent, bg=BG_SURFACE)
        tree = ttk.Treeview(cat_box, columns=("cat", "n"), show="headings", height=min(6, len(cats) + 1))
        tree.heading("cat", text="类别")
        tree.heading("n", text="个数")
        tree.column("cat", width=140, stretch=True)
        tree.column("n", width=50, anchor="e", stretch=False)
        source = current.enabled_hits if current is not None else [h for s in scans for h in s.enabled_hits]
        if current is not None and current.status == "fail":
            source = []
        # After a write, hits of a file that was not written are counted apart
        # so 合计 matches 已替换.
        unwritten = []
        if self.app.written:
            pool = [current] if current is not None else scans
            blocked = [s for s in pool if s.status != "fail" and not self._counts_toward_write(s)]
            unwritten = [h for s in blocked for h in s.enabled_hits]
            blocked_ids = {id(h) for h in unwritten}
            source = [h for h in source if id(h) not in blocked_ids]
        for i, cat in enumerate(cats):
            n = sum(1 for h in source if h.category == cat)
            item = tree.insert("", "end", values=(f"{marker_for(i)} {cat}", n))
            if self.app.selected_cat == cat:
                tree.selection_set(item)
        total = len(source)
        tree.insert("", "end", values=("合计", total))
        if unwritten:
            tree.insert("", "end", values=(MSG_UNWRITTEN, len(unwritten)))
            tree.configure(height=min(7, len(cats) + 2))
        cat_scroll = ttk.Scrollbar(cat_box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=cat_scroll.set)
        tree.pack(side="left", fill="x", expand=True)
        cat_box.pack(fill="x")
        # 「去掉页眉页脚」 lines under 合计. They take part in sharing the pane
        # height (see _fit_right_pane): folded to one line, then hidden, when
        # the window is short. The same numbers stay in the top summary.
        hf_rows = []
        for label_text, value, tone in hf_text.summary_rows(current, self.app.remove_hf, self.app.written, scans):
            row = tk.Frame(parent, bg=BG_SURFACE)
            row.pack(fill="x")
            fg = _tone_fg(tone)
            name = tk.Label(row, text=label_text, bg=BG_SURFACE, fg=WARN if tone == hf_text.TONE_WARN else INK, font=fonts["small"])
            name.pack(side="left", anchor="n", padx=(10, 6), pady=3)
            values = tk.Frame(row, bg=BG_SURFACE)
            values.pack(side="right", padx=(0, 4), pady=3)
            extra = []
            for i, line in enumerate(value.split("\n")):
                label = tk.Label(values, text=line, bg=BG_SURFACE, fg=fg if i == 0 else INK2, font=fonts["small"])
                label.pack(anchor="e")
                if i:
                    extra.append(label)
            hf_rows.append(_HfRow(row, name, values, tuple(extra)))

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
        # Same count as the top summary: checked hits in files that are (or
        # will be) written. Hits of other files are named apart.
        pool = [current] if current is not None else scans
        header = location_header(
            pool,
            written=self.app.written,
            write_errors=self.app.write_errors,
            category=self.app.selected_cat,
        )
        tk.Label(loc_head, text=header, bg=BG_INSET, fg=INK2, font=fonts["small"]).pack(
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
        # Room for "第 501 页 · 第 1 行" (125px) and "PRJ-2026-017-S02" (124px).
        loc.column("on", width=32, minwidth=32, stretch=False, anchor="center")
        loc.column("loc", width=128, minwidth=126, stretch=False)
        loc.column("cat", width=28, minwidth=28, stretch=False)
        loc.column("text", width=126, minwidth=124, stretch=True)
        cat_index = {c: i for i, c in enumerate(cats)}
        unwritten_ids = set()
        if self.app.written:
            for scan in scans:
                if scan.status != "fail" and not self._counts_toward_write(scan):
                    unwritten_ids.update(h.hit_id for h in scan.hits)
            if any(h.hit_id in unwritten_ids and h.enabled for h in hits):
                width = fonts["small"].measure(MSG_UNWRITTEN) + 12
                # Take the room from the content column so the pane keeps its
                # width and the middle column does not shrink.
                spare = max(0, width - 32)
                loc.column("on", width=width, minwidth=width)
                loc.column("text", width=max(60, 126 - spare), minwidth=max(60, 124 - spare))
        for hit in hits:
            if self.app.written and hit.enabled and hit.hit_id in unwritten_ids:
                mark = MSG_UNWRITTEN
            else:
                mark = "✓" if self.app.written and hit.enabled else ("☑" if hit.enabled else "☐")
            content = "▇▇▇▇" if self.app.hide_text else hit.text
            if getattr(hit, "approx", False):
                # OCR read a look-alike; show what it read so the user can judge
                # (文案待前端定). Screen only: nothing here is written to disk.
                content = f"近似 {content}"
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
        self._fit_right_pane(parent, cat_box, tree, cat_scroll, loc, hf_rows)

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

    def _fit_right_pane(self, parent, cat_box, tree, cat_scroll, loc, hf_rows=()) -> None:
        """Share the pane height so the location list keeps LOC_MIN_ROWS full rows.

        The category table gives up rows first (down to CAT_MIN_ROWS) and
        then scrolls; no row is dropped. If that is not enough, the
        header/footer lines fold to their first line, then hide (their
        numbers are also in the top summary). Tall windows keep every row.
        """
        wanted = int(tree.cget("height"))
        total_rows = len(tree.get_children())
        state = {"busy": False, "hf": HF_FULL}
        hf_frames = {r.frame for r in hf_rows}

        def fit(_event=None) -> None:
            if state["busy"] or not parent.winfo_exists():
                return
            avail = parent.winfo_height()
            if avail < 50:
                return
            state["busy"] = True
            try:
                row_h = _tree_row_height(tree)
                shown = int(tree.cget("height"))
                # Heading plus borders: what a table needs beyond its rows.
                overhead = max(0, tree.winfo_reqheight() - shown * row_h)
                others = sum(
                    child.winfo_reqheight()
                    for child in parent.pack_slaves()
                    if child not in (cat_box, loc) and child not in hf_frames
                )
                if "heights" not in state:
                    state["heights"] = [hf_row_heights(r) for r in hf_rows]
                heights = state["heights"]
                level = hf_level(
                    avail - others,
                    heights,
                    row_h=row_h,
                    overhead=overhead,
                    wanted=wanted,
                )
                if level != state["hf"]:
                    for r in hf_rows:
                        _show_hf_row(r, level)
                    state["hf"] = level
                hf_h = sum(h[level] for h in heights)
                rows = category_rows_that_fit(
                    avail - others - hf_h, row_h=row_h, overhead=overhead, wanted=wanted
                )
                if rows != shown:
                    tree.configure(height=rows)
                if rows < total_rows:
                    if not cat_scroll.winfo_ismapped():
                        cat_scroll.pack(side="right", fill="y", before=tree)
                    sel = tree.selection()
                    if sel:
                        tree.see(sel[0])
                elif cat_scroll.winfo_ismapped():
                    cat_scroll.pack_forget()
            finally:
                state["busy"] = False

        parent.bind("<Configure>", lambda _e: parent.after_idle(fit), add="+")
        parent.after_idle(fit)

    def _counts_toward_write(self, scan) -> bool:
        return counts_toward_write(scan, written=self.app.written, write_errors=self.app.write_errors)

    def _clear_cat(self) -> None:
        self.app.selected_cat = None
        self.refresh()

    def _foot(self, scans, fonts, all_fail, nohit_all) -> None:
        foot = tk.Frame(self.frame, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(side="bottom", fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        app = self.app
        enabled = writable_hit_total(scans, written=app.written, write_errors=app.write_errors)
        n_files = app.written_file_count()
        failures = app.write_failure_rows()
        n_failed = failed_file_count(scans, app.write_errors)
        path = app.output_label()
        if any(scan.kind == KIND_PDF for scan in scans):
            tk.Label(
                inner,
                text=MSG_PDF_WIDTH,
                bg=BG_SURFACE,
                fg=INK2,
                font=fonts["small"],
                anchor="w",
                justify="left",
            ).pack(side="bottom", fill="x")
        buttons = tk.Frame(inner, bg=BG_SURFACE)
        buttons.pack(side="right")
        left = tk.Frame(inner, bg=BG_SURFACE)
        left.pack(side="left", fill="x", expand=True)
        if app.written and n_failed:
            tk.Label(
                left,
                text=f"✕ {MSG_PARTIAL_BOTTOM.format(n=n_files, m=n_failed)}  {MSG_PARTIAL_KEEP}",
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
            # Wrap to the room the buttons leave: a fixed 480px ran under them
            # at 960px and cut the last character (#28 r2 m5).
            _wrap_label(left, _output_hint(scans, app), bg=BG_SURFACE, fg=INK2, font=fonts["small"]).pack(fill="x")
        tk.Label(left, text=path, bg=BG_SURFACE, fg=INK2, font=fonts["mono_font"], anchor="w").pack(fill="x")
        if app.written and failures:
            ttk.Button(buttons, text="打开输出文件夹", style="Primary.TButton", command=app.open_output).pack(side="right")
            if any(not is_final_failure(reason) for _name, reason in failures):
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
        can_write = write_allowed(has_fresh_scans=bool(scans)) and (enabled > 0 or n_files > 0) and not all_fail
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


def _enabled_in(scan, category) -> int:
    if category is None:
        return scan.enabled_count()
    return sum(1 for hit in scan.enabled_hits if hit.category == category)


def writable_hit_total(scans, *, written: bool, write_errors: dict, category: str | None = None) -> int:
    """Checked hits in files that will be (or were) written.

    The one count behind the top summary, the 全部文件 row, the write
    button and the 被替换的位置 header. A file blocked by unlocated hits,
    and after a write a file the write refused, add nothing. ``category``
    narrows it to one category (the right pane's filter).
    """
    return sum(
        _enabled_in(scan, category)
        for scan in scans
        if counts_toward_write(scan, written=written, write_errors=write_errors)
    )


def unwritten_hit_total(scans, *, written: bool, write_errors: dict, category: str | None = None) -> int:
    """Checked hits left out of writable_hit_total (failed files aside)."""
    return sum(
        _enabled_in(scan, category)
        for scan in scans
        if scan.status != "fail" and not counts_toward_write(scan, written=written, write_errors=write_errors)
    )


def location_header(scans, *, written: bool, write_errors: dict, category: str | None = None) -> str:
    """「被替换的位置 N」, N from writable_hit_total; 「（另 M 处未写出）」 when some are left out."""
    n = writable_hit_total(scans, written=written, write_errors=write_errors, category=category)
    left = unwritten_hit_total(scans, written=written, write_errors=write_errors, category=category)
    if left:
        return MSG_LOC_HEADER_UNWRITTEN.format(n=n, m=left)
    return MSG_LOC_HEADER.format(n=n)


# The location list keeps this many full rows; the category table above it
# gives up rows (and scrolls) first, but keeps at least CAT_MIN_ROWS.
LOC_MIN_ROWS = 3
CAT_MIN_ROWS = 3


def category_rows_that_fit(space: int, *, row_h: int, overhead: int, wanted: int) -> int:
    """Rows for the category table when ``space`` px holds both tables.

    Each table costs ``overhead`` (heading and borders) plus its rows; the
    location list is held to LOC_MIN_ROWS. Never more than ``wanted``, never
    fewer than CAT_MIN_ROWS (or ``wanted`` when that is smaller).
    """
    row_h = max(1, row_h)
    room = space - 2 * overhead - LOC_MIN_ROWS * row_h
    fit = room // row_h
    return max(min(CAT_MIN_ROWS, wanted), min(wanted, fit))


# How much of a 「去掉页眉页脚」 line shows in the right pane.
HF_FULL, HF_FOLDED, HF_HIDDEN = 0, 1, 2


@dataclass(frozen=True)
class _HfRow:
    frame: object
    name: object
    values: object
    extra: tuple


def hf_row_heights(row: _HfRow) -> tuple[int, int, int]:
    """(full, folded, hidden) heights of one line, measured while it is full."""
    full = row.frame.winfo_reqheight()
    return full, full - sum(label.winfo_reqheight() for label in row.extra), 0


def hf_level(space: int, heights, *, row_h: int, overhead: int, wanted: int) -> int:
    """Least folding of the header/footer lines that leaves the location list
    LOC_MIN_ROWS rows next to a category table of at least CAT_MIN_ROWS."""
    cat_rows = min(CAT_MIN_ROWS, wanted)
    need = 2 * overhead + (cat_rows + LOC_MIN_ROWS) * max(1, row_h)
    for level in (HF_FULL, HF_FOLDED):
        if space - sum(h[level] for h in heights) >= need:
            return level
    return HF_HIDDEN if heights else HF_FULL


def _show_hf_row(row: _HfRow, level: int) -> None:
    for label in row.extra:
        label.pack_forget()
    if level == HF_HIDDEN:
        row.name.pack_forget()
        row.values.pack_forget()
        row.frame.configure(height=1)
        return
    if not row.name.winfo_ismapped():
        row.name.pack(side="left", anchor="n", padx=(10, 6), pady=3)
        row.values.pack(side="right", padx=(0, 4), pady=3)
    if level == HF_FULL:
        for label in row.extra:
            label.pack(anchor="e")


def _tree_row_height(tree) -> int:
    try:
        value = ttk.Style(tree).lookup("Treeview", "rowheight")
        return int(float(value)) if value else 20
    except (tk.TclError, ValueError):
        return 20


def counts_toward_write(scan, *, written: bool, write_errors: dict) -> bool:
    if scan.status == "fail":
        return False
    if int(getattr(scan, "unlocated_count", 0) or 0) > 0:
        return False
    return not (written and str(scan.path) in write_errors)


MSG_UNWRITTEN = "未写出"
MSG_LOC_HEADER = "被替换的位置 {n}"
MSG_LOC_HEADER_UNWRITTEN = "被替换的位置 {n}（另 {m} 处未写出）"
MSG_MATRIX_UNWRITTEN = "{names} 未写出，不计入合计"


# Characters that must not begin a line, and ones that must not end one.
NO_LINE_START = frozenset("，。、；：！？）」』》〉】,.;:!?)%")
NO_LINE_END = frozenset("（「『《〈【(")


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "_-/")


def _wrap_units(text: str) -> list[str]:
    """Pieces that may not be split. A normal space is its own piece.

    A non-breaking space glues its neighbours (so "2 处" stays together),
    closing punctuation sticks to the character before it, opening
    punctuation to the one after it, and an ASCII word stays whole.
    """
    units: list[str] = []
    buf = ""
    for ch in text:
        if ch == " ":
            if buf:
                units.append(buf)
                buf = ""
            units.append(" ")
            continue
        glue = bool(buf) and (
            ch == "\u00a0"
            or buf[-1] == "\u00a0"
            or buf[-1] in NO_LINE_END
            or ch in NO_LINE_START
            or (_is_word_char(ch) and _is_word_char(buf[-1]))
        )
        if glue:
            buf += ch
            continue
        if buf:
            units.append(buf)
        buf = ch
    if buf:
        units.append(buf)
    return units


def wrap_lines(text: str, width: int, measure) -> list[str]:
    """Break ``text`` into lines no wider than ``width`` where possible.

    ``measure`` returns a string's width (``Font.measure`` in the UI). Breaks
    fall between pieces from ``_wrap_units``, so a line never starts with a
    comma or full stop. A piece wider than the whole line is kept whole.
    """
    lines: list[str] = []
    current = ""
    for unit in _wrap_units(text):
        if unit == " ":
            if current:
                current += " "
            continue
        candidate = current + unit
        if not current.strip() or measure(candidate) <= width:
            current = candidate
            continue
        lines.append(current.rstrip(" "))
        current = unit
    if current.strip():
        lines.append(current.rstrip(" "))
    return lines


def _wrap_label(parent, text: str, *, bg: str, fg: str, font) -> tk.Label:
    """Wrap to the parent width using the font's own line spacing.

    ``tk.Message`` adds extra leading, which pushed the PDF preview's last
    line out of the 640px window. Tk's own wraplength breaks a long CJK run
    at any character, so a 「，」 could start a line; the lines are laid out
    here instead. ``logical_text`` keeps the unwrapped sentence.
    """
    label = tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=fg,
        font=font,
        justify="left",
        anchor="w",
        wraplength=0,
    )
    label.logical_text = text
    measure = tkfont.Font(font=label.cget("font")).measure
    # Start narrow, as wraplength=40 did, so one long line cannot widen the column.
    label.configure(text="\n".join(wrap_lines(text, 40, measure)))
    state = {"width": None}

    def sync(_event=None) -> None:
        width = parent.winfo_width()
        if width <= 20:
            return
        target = max(40, width - _horizontal_chrome(label))
        if state["width"] == target:
            return
        state["width"] = target
        label.configure(text="\n".join(wrap_lines(text, target, measure)))

    parent.bind("<Configure>", sync, add="+")
    parent.after_idle(sync)
    return label


def _horizontal_chrome(label: tk.Label) -> int:
    """Pixels on both sides of the text: pack padx plus the label's own padding.

    Leaving these out let a full line run under the column edge, so the
    last glyph (the 「空」 of 只留了空白) was cut in half.
    """
    total = 0
    try:
        padx = label.pack_info().get("padx", 0)
    except tk.TclError:
        padx = 0
    if isinstance(padx, (tuple, list)):
        total += sum(int(v) for v in padx)
    else:
        parts = str(padx).split()
        total += sum(int(float(v)) for v in parts) if len(parts) > 1 else 2 * int(float(parts[0] or 0))
    for option in ("padx", "borderwidth", "highlightthickness"):
        try:
            total += 2 * int(float(label.cget(option)))
        except (tk.TclError, ValueError):
            pass
    return total


def _has_findings(scan: FileScan) -> bool:
    if scan.hits:
        return True
    return int(getattr(scan, "unlocated_count", 0) or 0) > 0


def _pdf_fit_notice(scan: FileScan, app) -> str:
    if scan.kind != KIND_PDF or scan.status == "fail":
        return ""
    unlocated = int(getattr(scan, "unlocated_count", 0) or 0)
    if not scan.hits and unlocated <= 0:
        return ""
    short_count = 0
    blank_count = 0
    if scan.hits:
        replacements = app.ruleset.replacements()

        def label(category: str) -> str:
            custom = replacements.get(category)
            if isinstance(custom, str) and custom.strip():
                return custom
            return redaction_label(category)

        order = [rule.name for rule in app.ruleset.rules]
        short_count, blank_count = pdf_fit_counts(scan.enabled_hits, label, order)
    return fit_notice(short_count, blank_count, unlocated)


def _scan_status(scan: FileScan, app) -> tuple[str, str, str]:
    if scan.status == "fail":
        return "fail", "失败", "bad"
    if str(scan.path) in app.write_errors:
        return "nowrite", "没写出", "bad"
    if app.write_notes.get(str(scan.path)) == MSG_NO_HITS:
        return "nohit_unwritten", MSG_NO_HITS, "warn"
    if str(scan.path) in app.write_notes:
        return "skipped", app.write_notes[str(scan.path)], "ok"
    if scan.too_many:
        return "many", "命中偏多", "warn"
    if app.written and scan.enabled_count() > 0:
        return "written", "已写出", "ok"
    unlocated = int(getattr(scan, "unlocated_count", 0) or 0)
    if unlocated > 0:
        return "unlocated", f"{unlocated} 处无法定位", "warn"
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


def _hf_file_row(scan: FileScan, app) -> str | None:
    if not hf_text.shows_box(scan, app.remove_hf):
        return None
    return hf_text.box_label(scan, app.written)


def _hf_box_disabled(scan: FileScan, app) -> bool:
    return hf_text.box_disabled(scan, app.written)


def _tone_fg(tone: str) -> str:
    return {
        hf_text.TONE_WARN: WARN,
        hf_text.TONE_OK: "#008035",
        hf_text.TONE_MUTED: MUTED,
    }.get(tone, INK)


def _output_hint(scans, app) -> str:
    return hf_text.output_hint(scans, app.remove_hf, MSG_OUTPUT_HINT)
