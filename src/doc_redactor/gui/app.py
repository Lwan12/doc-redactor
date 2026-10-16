"""Main window: three screens, scan thread, screenshot hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from tkinter import filedialog, ttk
import tkinter as tk

from doc_redactor.gui.dialogs import confirm_dialog, show_toast
from doc_redactor.gui.main_view import MainView
from doc_redactor.gui.result_view import ResultView
from doc_redactor.gui.rules_view import RulesView
from doc_redactor.gui.theme import BG_PAGE, setup_style, marker_for
from doc_redactor.pattern_guess import evaluate_rule_form
from doc_redactor.ruleset import (
    KIND_PATTERN,
    KIND_WORDLIST,
    LETTER_DIGIT_INNER,
    LETTER_DIGIT_NAME,
    PROTOCOL_LIST_NAME,
    UNDO_TTL_SECONDS,
    Rule,
    RuleSet,
    add_words,
    apply_library_toggle,
    common_library,
    library_item_to_rule,
    word_dup_message,
)
from doc_redactor.scan import (
    FileScan,
    conflicting_destinations,
    scan_paths,
    size_label,
    write_report,
    write_scans,
)


SUPPORTED = [
    ("支持的文件", "*.docx *.pdf *.png *.jpg *.jpeg"),
    ("所有文件", "*.*"),
]


def create_root():
    dnd = None
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD

        root = TkinterDnD.Tk()
        dnd = DND_FILES
    except Exception:
        root = tk.Tk()
    return root, dnd


class App:
    def __init__(self, root: tk.Tk, dnd_type=None, *, shoot: bool = False) -> None:
        self.root = root
        self.dnd_type = dnd_type
        self.dnd_ok = dnd_type is not None
        self.shoot = shoot
        self.fonts = setup_style(root)
        root.title("文档脱敏工具")
        root.minsize(960, 640)
        root.geometry("960x640")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Configure>", self._on_configure)

        self.ruleset = RuleSet()
        self.ruleset.seed_defaults()
        self.files: list[Path] = []
        self.file_sizes: list[str] = []
        self.file_status: list[str] = []
        self.output_var = tk.StringVar(value=str(_default_output()))
        self.output_error: str | None = None
        self.running = False
        self.cancelled = False
        self.written = False
        self.scans: list[FileScan] = []
        self.selected: int | str = 0
        self.preview_mode = "orig"
        self.hide_text = False
        self.selected_cat: str | None = None
        self.current_hit: int | None = None
        self.pdf_page = 0
        self.narrow = False
        self.screen = "main"
        self.rules_page = "list"
        self.rule_form: dict = _empty_rule_form()
        self.word_form: dict = _empty_word_form()
        self.lib_checked = [item.default_checked for item in common_library()]
        self.import_preview = None
        self.cancel_event: Event | None = None
        self.toast = None
        self._undo_job = None
        self._word_undo_job = None
        self._drop_bound = set()

        self.container = ttk.Frame(root)
        self.container.pack(fill="both", expand=True)
        self.main_view = MainView(self)
        self.result_view = ResultView(self)
        self.rules_view = RulesView(self)
        self.show("main")
        self._check_output()

    def show(self, name: str) -> None:
        self.screen = name
        self.main_view.hide()
        self.result_view.hide()
        self.rules_view.hide()
        if name == "main":
            self.main_view.present()
        elif name == "result":
            self.result_view.present()
        else:
            self.rules_view.present()

    def refresh(self) -> None:
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        try:
            if self.screen == "main":
                self.main_view.refresh()
            elif self.screen == "result":
                self.result_view.refresh()
            else:
                self.rules_view.refresh()
        finally:
            self._refreshing = False

    def bind_drop(self, widget) -> None:
        if not self.dnd_ok or id(widget) in self._drop_bound:
            return
        try:
            widget.drop_target_register(self.dnd_type)

            def on_drop(event):
                paths = self.root.tk.splitlist(event.data)
                self.add_files(paths)

            widget.dnd_bind("<<Drop>>", on_drop)
            self._drop_bound.add(id(widget))
        except Exception:
            self.dnd_ok = False

    def add_files(self, paths) -> None:
        for raw in paths:
            path = Path(str(raw).strip("{}"))
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".docx", ".pdf", ".png", ".jpg", ".jpeg"}:
                continue
            if path in self.files:
                continue
            self.files.append(path)
            self.file_sizes.append(size_label(path))
            self.file_status.append("pending")
        self.cancelled = False
        self.refresh()

    def browse_files(self) -> None:
        selected = filedialog.askopenfilenames(title="选择文件", filetypes=SUPPORTED)
        if selected:
            self.add_files(selected)

    def browse_folder(self) -> None:
        chosen = filedialog.askdirectory(title="选择文件夹")
        if not chosen:
            return
        folder = Path(chosen)
        found = []
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() in {".docx", ".pdf", ".png", ".jpg", ".jpeg"} and path.is_file():
                found.append(path)
        self.add_files(found)

    def clear_files(self) -> None:
        self.files = []
        self.file_sizes = []
        self.file_status = []
        self.refresh()

    def browse_output(self) -> None:
        chosen = filedialog.askdirectory(title="选择输出目录")
        if chosen:
            self.output_var.set(chosen)
            self._check_output()
            self.refresh()

    def _check_output(self) -> None:
        path = Path(self.output_var.get().strip() or ".")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doc_redactor_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            self.output_error = None
        except OSError:
            self.output_error = "✕ 这个文件夹不能写入（没有权限，或磁盘已满）。请点「更改…」换一个，例如桌面。"

    def toggle_rule(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self.ruleset.rules):
            self.ruleset.set_enabled(index, enabled)
            self.refresh()

    def show_rules(self) -> None:
        self.rules_page = "list"
        self.show("rules")

    def show_rules_page(self, page: str) -> None:
        self.rules_page = page
        self.show("rules")

    def rules_back(self) -> None:
        if self.rules_page != "list":
            self.rules_page = "list"
            self.refresh()
            return
        if self.scans:
            self.show("result")
        else:
            self.show("main")

    def new_rule(self) -> None:
        self.rule_form = _empty_rule_form()
        self.rules_page = "edit"
        self.refresh()

    def new_words(self) -> None:
        self.word_form = _empty_word_form()
        self.rules_page = "words"
        self.refresh()

    def edit_rule(self, index: int) -> None:
        rule = self.ruleset.rules[index]
        if rule.kind == KIND_WORDLIST:
            self.word_form = {
                "index": index,
                "name": rule.name,
                "words": list(rule.terms),
                "sel": [],
                "incoming": "",
                "message": None,
                "snap": None,
                "snap_at": None,
                "case_sensitive": rule.case_sensitive,
                "test": "",
                "kind": "plan" if rule.name == PROTOCOL_LIST_NAME else "words",
            }
            self.rules_page = "words"
            self.refresh()
            return
        self.rule_form = _empty_rule_form()
        self.rule_form.update(
            {
                "index": index,
                "name": rule.name,
                "examples": "\n".join(rule.examples),
                "placeholder": rule.placeholder,
                "case_sensitive": rule.case_sensitive,
                "replacement": rule.replacement_text(),
                "advanced": None,
            }
        )
        self.rules_page = "edit"
        self.refresh()

    def delete_rule(self, index: int) -> None:
        rule = self.ruleset.delete(index)
        self.refresh()
        if self.toast is not None:
            try:
                self.toast.destroy()
            except tk.TclError:
                pass
        self.toast = show_toast(
            self.root,
            f"已删除「{rule.name}」。10 分钟内可撤销。",
            self.undo_rules,
        )
        self._arm_undo_expiry()

    def _arm_undo_expiry(self) -> None:
        if self._undo_job is not None:
            try:
                self.root.after_cancel(self._undo_job)
            except tk.TclError:
                pass
        self._undo_job = self.root.after(int(UNDO_TTL_SECONDS * 1000), self._expire_undo)

    def _expire_undo(self) -> None:
        self._undo_job = None
        self.ruleset.undo_label()
        if self.toast is not None:
            try:
                self.toast.destroy()
            except tk.TclError:
                pass
            self.toast = None
        if self.screen == "rules":
            self.refresh()

    def undo_rules(self) -> None:
        self.ruleset.undo()
        if self._undo_job is not None:
            try:
                self.root.after_cancel(self._undo_job)
            except tk.TclError:
                pass
            self._undo_job = None
        if self.toast is not None:
            try:
                self.toast.destroy()
            except tk.TclError:
                pass
            self.toast = None
        self.refresh()

    def cancel_rule_edit(self) -> None:
        self.rules_page = "list"
        self.refresh()

    def save_rule(self) -> None:
        form = self.rule_form
        ev = evaluate_rule_form(
            name=form.get("name", ""),
            examples_text=form.get("examples", ""),
            placeholder=form.get("placeholder", ""),
            test_text=form.get("test", ""),
            existing_names=self.ruleset.names(),
            overwrite=bool(form.get("overwrite")),
            case_sensitive=bool(form.get("case_sensitive")),
            template=form.get("template_kind") if form.get("template") else None,
            template_letters=int(form.get("letters") or 3),
            template_sep=form.get("sep", "-"),
            template_digits=int(form.get("digits") or 4),
            template_digits2=int(form.get("digits2") or 0),
            advanced=form.get("advanced"),
            editing_name=None
            if form.get("index") is None
            else self.ruleset.rules[form["index"]].name,
        )
        if not ev.can_save:
            self.refresh()
            return
        examples = [line.strip() for line in form.get("examples", "").splitlines() if line.strip()]
        rule = Rule(
            name=form.get("name", "").strip(),
            kind=KIND_PATTERN,
            enabled=True,
            examples=examples,
            placeholder=form.get("placeholder", ""),
            pattern=ev.pattern,
            case_sensitive=bool(form.get("case_sensitive")),
            replacement=form.get("replacement") or "",
        )
        if form.get("index") is not None:
            self.ruleset.replace(form["index"], rule)
        else:
            self.ruleset.add(rule, overwrite=bool(form.get("overwrite")))
        self.rules_page = "list"
        self.refresh()

    def add_word_lines(self, raw: str) -> None:
        form = self.word_form
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            form["message"] = ("n", "请先写要加的词，每行一个。")
            self.refresh()
            return
        form["snap"] = list(form.get("words") or [])
        merged, added, skipped = add_words(form.get("words") or [], lines, case_sensitive=bool(form.get("case_sensitive")))
        form["words"] = merged
        if skipped:
            form["message"] = ("warn", word_dup_message(skipped, added))
            form["incoming"] = "\n".join(skipped) if skipped else ""
        else:
            form["message"] = ("ok", word_dup_message(skipped, added))
            form["incoming"] = ""
        self.refresh()

    def delete_selected_words(self) -> None:
        form = self.word_form
        sel = set(form.get("sel") or [])
        if not sel:
            return
        form["snap"] = list(form.get("words") or [])
        words = form.get("words") or []
        n = len(sel)
        form["words"] = [w for i, w in enumerate(words) if i not in sel]
        form["sel"] = []
        form["message"] = ("n", f"已删除 {n} 个词。10 分钟内可撤销。")
        form["snap_at"] = time.monotonic()
        self._arm_word_undo_expiry()
        self.refresh()

    def _arm_word_undo_expiry(self) -> None:
        if self._word_undo_job is not None:
            try:
                self.root.after_cancel(self._word_undo_job)
            except tk.TclError:
                pass
        self._word_undo_job = self.root.after(int(UNDO_TTL_SECONDS * 1000), self._expire_word_undo)

    def _expire_word_undo(self) -> None:
        self._word_undo_job = None
        form = self.word_form
        form["snap"] = None
        form["snap_at"] = None
        if self.screen == "rules" and self.rules_page == "words":
            self.refresh()

    def undo_words(self) -> None:
        form = self.word_form
        if form.get("snap") is None:
            return
        form["words"] = form["snap"]
        form["snap"] = None
        form["snap_at"] = None
        form["sel"] = []
        form["message"] = ("n", "已撤销上一步。")
        if self._word_undo_job is not None:
            try:
                self.root.after_cancel(self._word_undo_job)
            except tk.TclError:
                pass
            self._word_undo_job = None
        self.refresh()

    def import_word_file(self) -> None:
        path = filedialog.askopenfilename(title="从文本文件导入", filetypes=[("文本", "*.txt *.csv"), ("所有文件", "*.*")])
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8-sig")
        self.add_word_lines(text)

    def save_words(self) -> None:
        form = self.word_form
        name = (form.get("name") or "").strip()
        words = form.get("words") or []
        if not name or not words:
            return
        if name == PROTOCOL_LIST_NAME:
            form["case_sensitive"] = False
        rule = Rule(
            name=name,
            kind=KIND_WORDLIST,
            enabled=True,
            terms=list(words),
            case_sensitive=bool(form.get("case_sensitive")),
        )
        if form.get("index") is None:
            self.ruleset.add(rule)
        else:
            self.ruleset.replace(form["index"], rule)
        self.rules_page = "list"
        self.refresh()

    def show_library(self) -> None:
        self.lib_checked = [item.default_checked for item in common_library()]
        self.rules_page = "lib"
        self.refresh()

    def toggle_library(self, index: int) -> None:
        items = common_library()
        self.lib_checked = apply_library_toggle(items, self.lib_checked, index)
        self.refresh()

    def add_library(self) -> None:
        self.ruleset.add_from_library(common_library(), self.lib_checked)
        self.rules_page = "list"
        self.refresh()

    def browse_import(self) -> None:
        path = filedialog.askopenfilename(title="导入规则", filetypes=[("YAML", "*.yaml *.yml"), ("所有文件", "*.*")])
        if not path:
            return
        self.import_preview = self.ruleset.preview_import(path)
        self.rules_page = "import"
        self.refresh()

    def confirm_import(self) -> None:
        if self.import_preview is None:
            return
        self.ruleset.apply_import(self.import_preview)
        self.rules_page = "list"
        self.refresh()

    def export_rules_dialog(self) -> None:
        choice = confirm_dialog(
            self.root,
            "导出规则",
            "得到一个规则文件，换电脑可导入。里面只有规则，没有测试用的文字，也没有任何被处理文件的内容。",
            [
                ("取消", "cancel", False),
                ("只导出勾选的几条", "enabled", False),
                ("导出全部", "all", True),
            ],
        )
        if choice in (None, "cancel"):
            return
        path = filedialog.asksaveasfilename(
            title="导出规则",
            defaultextension=".yaml",
            filetypes=[("YAML", "*.yaml *.yml")],
        )
        if not path:
            return
        self.ruleset.save(path, only_enabled=(choice == "enabled"))

    def start_scan(self) -> None:
        if not self.files or not self.ruleset.enabled_rules() or self.output_error:
            return
        self._check_output()
        if self.output_error:
            self.refresh()
            return
        self.running = True
        self.cancelled = False
        self.written = False
        self.file_status = ["pending"] * len(self.files)
        self.refresh()
        self.cancel_event = Event()
        engine = self.ruleset.to_engine()
        paths = list(self.files)

        def worker():
            def progress(index, _path, status):
                mapped = {"running": "running", "ok": "scanned", "nohit": "scanned", "fail": "fail"}.get(status, status)

                def apply():
                    if index < len(self.file_status):
                        self.file_status[index] = mapped
                    self.refresh()

                self.root.after(0, apply)

            results = scan_paths(paths, engine, cancel=self.cancel_event, progress=progress)
            self.root.after(0, lambda: self._scan_done(results))

        Thread(target=worker, daemon=True).start()

    def cancel_scan(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.running = False
        self.cancelled = True
        self.file_status = [
            "cancelled" if s in {"pending", "running"} else s for s in self.file_status
        ]
        self.refresh()

    def _scan_done(self, results: list[FileScan]) -> None:
        if self.cancelled:
            return
        self.running = False
        self.scans = results
        self.file_status = [
            "fail" if s.status == "fail" else ("scanned" if s.status != "cancelled" else "cancelled") for s in results
        ]
        self.selected = 0 if results else "all"
        self.preview_mode = "orig"
        self.hide_text = False
        self.selected_cat = None
        self.current_hit = None
        self.pdf_page = 0
        self.written = False
        self.show("result")

    def back_to_files(self) -> None:
        self.show("main")

    def category_order(self) -> list[str]:
        names = []
        for rule in self.ruleset.rules:
            if rule.name not in names:
                names.append(rule.name)
        for scan in self.scans:
            for hit in scan.hits:
                if hit.category not in names:
                    names.append(hit.category)
        return names

    def marker(self, category: str) -> str:
        order = self.category_order()
        try:
            return marker_for(order.index(category))
        except ValueError:
            return "①"

    def hit_by_id(self, hit_id: int | None):
        if hit_id is None:
            return None
        for scan in self.scans:
            for hit in scan.hits:
                if hit.hit_id == hit_id:
                    return hit
        return None

    def toggle_hit(self, hit_id: int) -> None:
        hit = self.hit_by_id(hit_id)
        if hit is None or self.written:
            return
        hit.enabled = not hit.enabled

    def jump_to_hit(self, hit_id: int) -> None:
        for i, scan in enumerate(self.scans):
            for hit in scan.hits:
                if hit.hit_id == hit_id:
                    self.selected = i
                    self.current_hit = hit_id
                    if hit.unit.startswith("page:"):
                        self.pdf_page = hit.page
                    return

    def remove_scan(self, scan: FileScan) -> None:
        if scan in self.scans:
            self.scans.remove(scan)
        self.selected = "all" if self.scans else 0
        self.refresh()

    def write_files(self) -> None:
        outdir = Path(self.output_var.get().strip())
        conflicts = conflicting_destinations(self.scans, outdir)
        overwrite = True
        sequence = False
        if conflicts:
            names = "\n".join(p.name for p in conflicts)
            choice = confirm_dialog(
                self.root,
                "输出文件夹里已经有同名文件",
                f"输出文件夹里已有 {len(conflicts)} 个同名文件：\n{names}\n写出会覆盖它们，覆盖后无法恢复。",
                [
                    ("取消，先不写出", "cancel", False),
                    ("都另存，名字后加序号", "seq", False),
                    (f"覆盖这 {len(conflicts)} 个", "over", True),
                ],
            )
            if choice in (None, "cancel"):
                return
            if choice == "seq":
                sequence = True
                overwrite = False
            else:
                overwrite = True
        write_scans(
            self.scans,
            outdir,
            replacements=self.ruleset.replacements(),
            overwrite=overwrite,
            sequence=sequence,
            engine=self.ruleset.to_engine(),
        )
        self.written = True
        self.refresh()

    def export_hit_list(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出命中清单",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        payload = {}
        for scan in self.scans:
            if scan.status == "fail":
                continue
            counts = scan.category_counts(enabled_only=True)
            if counts:
                payload[scan.path.name] = counts
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def open_output(self) -> None:
        path = Path(self.output_var.get().strip())
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def on_close(self) -> None:
        if self.scans and not self.written:
            choice = confirm_dialog(
                self.root,
                "脱敏文件还没有写出",
                "关闭后，这次的扫描结果和原文预览会清除，要重新扫描才能再写出。",
                [("回去写出", "back", False), ("仍然关闭", "close", True)],
            )
            if choice != "close":
                if self.screen != "result":
                    self.show("result")
                return
        self.scans = []
        self.root.destroy()

    def _on_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        narrow = event.width < 960
        if narrow != self.narrow:
            self.narrow = narrow
            if self.screen == "result":
                self.refresh()

    def apply_shoot_state(self, key: str) -> None:
        from doc_redactor.gui.demo import demo_files, demo_scans
        from doc_redactor.scan import MSG_ENCRYPTED_PDF

        if getattr(self, "_overlay_frame", None) is not None:
            try:
                self._overlay_frame.destroy()
            except tk.TclError:
                pass
            self._overlay_frame = None
        self.cancelled = False
        self.running = False
        self.written = False
        self.hide_text = False
        self.preview_mode = "orig"
        self.selected_cat = None
        self.current_hit = None
        self.pdf_page = 0
        self.output_error = None
        self.rules_page = "list"
        self.rule_form = _empty_rule_form()
        self.word_form = _empty_word_form()
        self.import_preview = None
        self.files = demo_files()
        self.file_sizes = ["86 KB", "1.2 MB", "2.4 MB", "640 KB", "41 KB"]
        self.file_status = ["pending"] * 5
        self.scans = []
        self.selected = 0
        self.ruleset = RuleSet()
        self.ruleset.seed_defaults()
        self.lib_checked = [item.default_checked for item in common_library()]
        if self.toast is not None:
            try:
                self.toast.destroy()
            except tk.TclError:
                pass
            self.toast = None

        if key == "main-empty":
            self.files = []
            self.file_sizes = []
            self.file_status = []
            self.show("main")
            return
        if key == "main-files":
            self.show("main")
            return
        if key == "main-outerr":
            self.output_error = "✕ 这个文件夹不能写入（没有权限，或磁盘已满）。请点「更改…」换一个，例如桌面。"
            self.show("main")
            return
        if key == "main-running":
            self.running = True
            self.file_status = ["scanned", "scanned", "running", "pending", "pending"]
            self.show("main")
            return
        if key == "main-cancelled":
            self.cancelled = True
            self.file_status = ["scanned", "scanned", "cancelled", "cancelled", "cancelled"]
            self.show("main")
            return

        many = key == "res-many"
        nohit = key in {"res-allnohit", "res-nohit"} and key == "res-allnohit"
        all_fail = key == "res-allfail"
        self.scans = demo_scans(many=many, nohit=key == "res-allnohit", all_fail=all_fail)
        self.selected = 0

        if key.startswith("res-"):
            if key == "res-word":
                self.selected = 0
            elif key == "res-word-after":
                self.selected = 0
                self.preview_mode = "after"
            elif key == "res-word-uncheck":
                self.selected = 0
                if self.scans[0].hits:
                    self.scans[0].hits[3].enabled = False
            elif key == "res-pdf":
                self.selected = 1
                self.selected_cat = "项目号"
                self.pdf_page = 1
            elif key == "res-img":
                self.selected = 2
                self.selected_cat = "项目号"
            elif key == "res-hide":
                self.selected = 0
                self.hide_text = True
                self.selected_cat = "项目号"
            elif key == "res-all":
                self.selected = "all"
            elif key == "res-fail":
                self.selected = 3
            elif key == "res-nohit":
                self.selected = 4
            elif key == "res-allnohit":
                self.selected = "all"
            elif key == "res-allfail":
                self.selected = "all"
            elif key == "res-close":
                self.selected = 0
                self.show("result")
                self._overlay(
                    "脱敏文件还没有写出",
                    "关闭后，这次的扫描结果和原文预览会清除，要重新扫描才能再写出。",
                    [("回去写出", False), ("仍然关闭", True)],
                )
                return
            elif key == "res-written":
                self.selected = 0
                self.written = True
            elif key == "res-many":
                self.selected = 1
            elif key == "res-conflict":
                self.selected = 0
                self.show("result")
                self._overlay(
                    "输出文件夹里已经有同名文件",
                    "继续写出的话，这 2 个文件会被盖掉：访视记录_受试者012.docx、入组统计报告_V2.pdf。原文件还在，被盖掉的是以前写出的脱敏文件。",
                    [("取消", False), ("都另存加序号", False), ("覆盖这 2 个", True)],
                )
                return
            self.show("result")
            return

        # rules
        if key == "rules-list":
            self.show_rules_page("list")
            return
        if key == "rules-deleted":
            if self.ruleset.rules:
                self.ruleset.delete(len(self.ruleset.rules) - 1)
            self.show_rules_page("list")
            self.toast = show_toast(self.root, self.ruleset.undo_label() or "已删除。10 分钟内可撤销。", self.undo_rules)
            return
        if key.startswith("rule-") or key == "rule-new":
            self.rule_form = _empty_rule_form()
            self.rules_page = "edit"
            sample = "本次访视按 AAA-0000-000 方案执行，附件 AAA-0000-000-A3 已归档；旧项目 BBB-0000-000 不再使用。访视日期 2026-09-12。"
            if key == "rule-new":
                pass
            elif key == "rule-guessing":
                self.rule_form["examples"] = "AAA-0000-000"
                self.rule_form["guessing"] = True
            elif key == "rule-guess":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["examples"] = "AAA-0000-000\nBBB-0000-000"
                self.rule_form["test"] = sample
            elif key == "rule-unsure":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["examples"] = "AAA-0000-000\n华曜医药\n2026/9/12"
                self.rule_form["test"] = sample
            elif key == "rule-nohit":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["examples"] = "AAA-0000-000"
                self.rule_form["test"] = "今天的访视按计划进行，没有新的安全性事件。"
            elif key == "rule-toomany":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["placeholder"] = "####"
                self.rule_form["test"] = "入组编号 " + "、".join(str(1001 + i) for i in range(24)) + "，共 24 例。"
            elif key == "rule-invalid":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["examples"] = "AAA-0000-000"
                self.rule_form["test"] = sample
                self.rule_form["adv_open"] = True
                self.rule_form["advanced"] = "AAA-(\\d{4}"
            elif key == "rule-dup":
                self.rule_form["name"] = "项目号"
                self.rule_form["examples"] = "AAA-0000-000"
                self.rule_form["test"] = sample
            elif key == "rule-placeholder":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["placeholder"] = "PROJ-####"
                self.rule_form["test"] = "PROJ-0000 与 PROJ-0001 已归档，AAA-0000-000 不算。"
            elif key == "rule-tpl":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["template"] = True
                self.rule_form["test"] = sample
            elif key == "rule-adv":
                self.rule_form["name"] = "访视项目号"
                self.rule_form["examples"] = "AAA-0000-000\nBBB-0000-000"
                self.rule_form["test"] = sample
                self.rule_form["adv_open"] = True
            elif key == "rule-letters":
                self.rule_form["name"] = LETTER_DIGIT_NAME
                self.rule_form["examples"] = "AA-K0000"
                self.rule_form["advanced"] = LETTER_DIGIT_INNER
                self.rule_form["adv_open"] = True
                self.rule_form["test"] = "方案 AAA0000 与 AA-K0000 已归档，ABC-1 太短不算。"
            self.show("rules")
            return
        if key == "rules-lib":
            self.show_library()
            return
        if key == "rules-import":
            from doc_redactor.ruleset import ImportItem, ImportPreview

            self.import_preview = ImportPreview(
                path=Path("我的规则.yaml"),
                items=[
                    ImportItem("项目号", "AAA-0000-000", "dup", action="跳过"),
                    ImportItem("NCT号", "NCT00000000", "dup", action="跳过"),
                    ImportItem("手机号", "138 0000 0000", "new"),
                    ImportItem("邮箱", "name@example.com", "new"),
                    ImportItem("内部编号", "ZX-001", "new"),
                    ImportItem("（没有名字）", "读不出匹配写法", "bad"),
                ],
            )
            self.show_rules_page("import")
            return
        if key == "rules-import-fail":
            from doc_redactor.ruleset import ImportPreview, MSG_IMPORT_FAIL

            self.import_preview = ImportPreview(path=Path("坏文件.txt"), failed=True, message=MSG_IMPORT_FAIL)
            self.show_rules_page("import")
            return
        if key == "rules-export":
            self.show_rules_page("list")
            self._overlay(
                "导出规则",
                "得到一个规则文件，换电脑可导入。里面只有规则，没有测试用的文字，也没有任何被处理文件的内容。\n\n○ 全部\n○ 只导出勾选的几条",
                [("取消", False), ("导出…", True)],
            )
            return
        if key.startswith("words-"):
            sponsors = [
                "华曜医药",
                "华曜医药有限公司",
                "华曜",
                "瑞岚生物",
                "北辰制药",
                "Acme Pharma",
            ]
            self.word_form = _empty_word_form()
            sponsor_i = self.ruleset.find_index("申办方")
            self.word_form["index"] = sponsor_i
            self.word_form["name"] = "申办方"
            self.word_form["words"] = list(sponsors)
            self.word_form["kind"] = "words"
            self.word_form["test"] = "监查员已向华曜医药提交访视摘要，华曜医药有限公司已确认。"
            self.rules_page = "words"
            if key == "words-empty":
                self.word_form["index"] = None
                self.word_form["name"] = ""
                self.word_form["words"] = []
                self.word_form["test"] = ""
            elif key == "words-dup":
                self.show("rules")
                self.add_word_lines("华曜医药\n北辰制药\n新词药业")
                return
            elif key == "words-deleted":
                self.word_form["sel"] = [2, 3]
                self.show("rules")
                self.delete_selected_words()
                return
            elif key == "words-added":
                self.show("rules")
                self.add_word_lines("远航生物\n泰和制药")
                return
            self.show("rules")
            return
        if key.startswith("plan-"):
            self.word_form = _empty_word_form()
            plan_i = self.ruleset.find_index(PROTOCOL_LIST_NAME)
            self.word_form["index"] = plan_i
            self.word_form["name"] = PROTOCOL_LIST_NAME
            self.word_form["kind"] = "plan"
            self.word_form["case_sensitive"] = False
            self.rules_page = "words"
            if key == "plan-empty":
                self.word_form["words"] = []
            elif key == "plan-list":
                self.show("rules")
                self.add_word_lines("AAA0000\nAA-K0000\nBBB0000")
                self.word_form["test"] = "本次方案编号 aaa0000（见 AA-K0000），另有 XYZ9999 不在清单里。"
                self.refresh()
                return
            elif key == "plan-dup":
                self.show("rules")
                self.add_word_lines("AAA0000\nAA-K0000")
                self.add_word_lines("aaa0000\nBBB0000")
                self.word_form["test"] = "编号 aaa0000、BBB0000。"
                self.refresh()
                return
            elif key == "plan-deleted":
                self.show("rules")
                self.add_word_lines("AAA0000\nAA-K0000\nBBB0000")
                self.word_form["sel"] = [1]
                self.delete_selected_words()
                self.word_form["test"] = "编号 AAA0000、AA-K0000。"
                self.refresh()
                return
            self.show("rules")
            return
        self.show("main")

    def _overlay(self, title: str, body: str, buttons: list[tuple[str, bool]]) -> None:
        from doc_redactor.gui.theme import BG_INSET, BG_SURFACE, INK, LINE2

        if getattr(self, "_overlay_frame", None) is not None:
            try:
                self._overlay_frame.destroy()
            except tk.TclError:
                pass
        mask = tk.Frame(self.root, bg="#d0d0d0")
        mask.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = tk.Frame(mask, bg=BG_SURFACE, highlightthickness=1, highlightbackground="#62656b")
        box.place(relx=0.5, rely=0.5, anchor="center")
        head = tk.Frame(box, bg=BG_INSET)
        head.pack(fill="x")
        tk.Label(head, text=title, bg=BG_INSET, fg=INK, anchor="w").pack(fill="x", padx=14, pady=8)
        tk.Label(box, text=body, bg=BG_SURFACE, fg=INK, justify="left", wraplength=400, anchor="w").pack(
            fill="x", padx=14, pady=12
        )
        foot = tk.Frame(box, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=14, pady=8)
        for label, primary in reversed(buttons):
            ttk.Button(inner, text=label, style="Primary.TButton" if primary else "TButton").pack(side="right", padx=(8, 0))
        self._overlay_frame = mask


def _empty_rule_form() -> dict:
    return {
        "index": None,
        "name": "",
        "examples": "",
        "placeholder": "",
        "test": "",
        "template": False,
        "template_kind": "字母+数字编号",
        "letters": 3,
        "sep": "-",
        "digits": 4,
        "digits2": 3,
        "case_sensitive": False,
        "replacement": "",
        "overwrite": False,
        "advanced": None,
        "adv_open": False,
        "guessing": False,
    }


def _empty_word_form() -> dict:
    return {
        "index": None,
        "name": "",
        "words": [],
        "sel": [],
        "incoming": "",
        "message": None,
        "snap": None,
        "snap_at": None,
        "case_sensitive": False,
        "test": "",
        "kind": "words",
    }


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Path.home() / "脱敏输出" / stamp


def run(*, shoot: bool = False) -> None:
    root, dnd = create_root()
    App(root, dnd, shoot=shoot)
    root.mainloop()
