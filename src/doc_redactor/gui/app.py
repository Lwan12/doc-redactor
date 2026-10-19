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

from doc_redactor.backpath import (
    STEP_FILES,
    STEP_RESULT,
    STEP_RULES,
    STEP_SCAN,
    ScanTicket,
    allocate_batch_dir,
    apply_unchecks,
    bump_generation,
    capture_unchecks,
    decide_close,
    decide_leave,
    decide_written,
    discard_scan_results,
    hit_list_payload,
    is_current,
    is_write_failure,
    merge_kept,
    protect_written,
    retain_editor_draft,
    session_signature,
    step_clickable,
    unchecked_count,
    without_paths,
    write_allowed,
)
from doc_redactor.gui.dialogs import confirm_dialog, prompt_dialog, radio_dialog, show_toast
from doc_redactor.gui.main_view import MainView
from doc_redactor.gui.result_view import ResultView
from doc_redactor.gui.rules_view import RulesView
from doc_redactor.gui.theme import BG_PAGE, setup_style, marker_for
from doc_redactor.pattern_guess import evaluate_rule_form
from doc_redactor.persist import (
    MSG_BAD_BODY,
    MSG_BAD_FILE,
    MSG_BAD_TITLE,
    MSG_EXPORT_BODY,
    MSG_OPENED,
    MSG_RESET_CONFIRM,
    MSG_RESET_DONE,
    MSG_RESET_SAME,
    MSG_RESET_TITLE,
    changed_from_defaults,
    check_output_dir,
    default_output_dir,
    load_session,
    reset_body,
    reveal_directory,
    save_session,
    settings_dir,
)
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
    default_library_checked,
    word_dup_message,
)
from doc_redactor.scan import (
    MSG_ALREADY_WRITTEN,
    FileScan,
    blocked_write_results,
    output_dir_unwritable,
    scan_paths,
    size_label,
    write_scans,
)

_EXPORT_CHOICES = (("全部规则", "all"), ("只导出勾选的几条", "enabled"))


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

        self.files: list[Path] = []
        self.file_sizes: list[str] = []
        self.file_status: list[str] = []
        self.file_selected: set[str] = set()
        self.main_step = 0
        self.reached = 0
        self.from_result = False
        self.uncheck_memory = None
        self.note_cleared = False
        self.write_errors: dict[str, str] = {}
        self.write_notes: dict[str, str] = {}
        self.batch_dir: Path | None = None
        self.kept_outputs: list[Path] = []
        self.pin_step_tip = None
        self.output_display: str | None = None
        self._step_tip = None
        self._step_tip_pinned = False
        self.first_run_hint = False
        self.save_failed = False
        self.corrupt_name = None
        self.settings_open_msg = None
        self._reset_lib_snap = None
        self.ruleset = RuleSet()
        self.lib_checked = default_library_checked()
        # Screenshots use a fictional drive letter so the picture does not show this machine.
        if shoot:
            self.ruleset.seed_defaults()
            shown_output = r"D:\脱敏输出"
        else:
            loaded = load_session(default_output=str(default_output_dir()))
            self.ruleset = RuleSet(loaded.rules)
            self.lib_checked = loaded.library_checked
            shown_output = loaded.output_dir
            self.first_run_hint = loaded.first_run
            self.save_failed = loaded.save_failed
            self.corrupt_name = loaded.corrupt_name
        self.output_var = tk.StringVar(value=shown_output)
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
        self.import_preview = None
        self.cancel_event: Event | None = None
        self.scan_generation = 0
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
        if not shoot:
            self._check_output()
            if self.corrupt_name:
                self.root.after_idle(self._show_corrupt_dialog)

    def show(self, name: str) -> None:
        self.screen = name
        self.reached = max(self.reached, self.current_step())
        self.main_view.hide()
        self.result_view.hide()
        self.rules_view.hide()
        if name == "main":
            self.main_view.present()
        elif name == "result":
            self.result_view.present()
        else:
            self.rules_view.present()

    def drop_step_tip(self) -> None:
        tip = getattr(self, "_step_tip", None)
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass
            self._step_tip = None
        self._step_tip_pinned = False

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

    def toggle_file_selected(self, path: Path) -> None:
        key = str(path)
        if key in self.file_selected:
            self.file_selected.remove(key)
        else:
            self.file_selected.add(key)
        self.refresh()

    def remove_paths(self, drop: set[str]) -> None:
        self.files, self.file_sizes, self.file_status = without_paths(
            self.files, self.file_sizes, self.file_status, drop
        )
        self.file_selected -= drop
        self.refresh()

    def remove_selected_files(self) -> None:
        if self.file_selected:
            self.remove_paths(set(self.file_selected))

    def go_rules_step(self) -> None:
        if not self.files or self.output_error:
            return
        self.main_step = 1
        self.cancelled = False
        self.show("main")

    def current_step(self) -> int:
        if self.screen == "result":
            return STEP_RESULT
        if self.running:
            return STEP_SCAN
        if self.cancelled and self.main_step != 0:
            return STEP_SCAN
        if self.main_step == 1:
            return STEP_RULES
        return STEP_FILES

    def on_step(self, index: int) -> None:
        if not step_clickable(
            target=index, current=self.current_step(), reached=self.reached, running=self.running
        ):
            return
        self.request_leave(index)

    def signature_matches_memory(self) -> bool:
        if self.uncheck_memory is None:
            return True
        return session_signature(self.files, self.ruleset.rules) == self.uncheck_memory.signature

    def remembered_unchecks(self) -> int:
        if self.uncheck_memory is None:
            return 0
        return len(self.uncheck_memory.keys)

    def output_label(self) -> str:
        if self.output_display:
            return self.output_display
        if self.batch_dir is not None:
            return str(self.batch_dir)
        parent = Path(self.output_var.get().strip() or ".")
        return str(allocate_batch_dir(parent, datetime.now()))

    def request_leave(self, target) -> bool:
        """Step bar and back buttons share this. Stay leaves the preview alone."""
        decision = decide_leave(
            on_result=self.screen == "result",
            written=self.written,
            has_preview=bool(self.scans),
            unchecked=unchecked_count(self.scans),
        )
        if decision.prompt:
            choice = confirm_dialog(
                self.root,
                decision.title,
                decision.body,
                [
                    (decision.stay, "stay", False),
                    (decision.confirm, "leave", True),
                ],
            )
            if choice != "leave":
                return False
        if decision.clear_preview:
            self._clear_preview()
        self._go(target)
        return True

    def _clear_preview(self) -> None:
        signature = session_signature(self.files, self.ruleset.rules)
        self.uncheck_memory = capture_unchecks(self.scans, signature)
        self.scans = []
        self.from_result = True
        self.note_cleared = False
        self.written = False
        self.write_errors = {}
        self.write_notes = {}
        self.file_status = ["pending"] * len(self.files)
        self.kept_outputs = protect_written(self.kept_outputs)

    def _go(self, target) -> None:
        if target == STEP_FILES:
            self.cancelled = False
            self.main_step = 0
            self.show("main")
        elif target == STEP_RULES:
            self.cancelled = False
            self.main_step = 1
            self.show("main")
        elif target == STEP_SCAN:
            self.cancelled = False
            self.main_step = 1
            self.show("main")
            self.start_scan()
        elif target == "rules":
            self.cancelled = False
            self.main_step = 1
            self.rules_page = "list"
            self.show("rules")

    def leave_to_rules(self) -> None:
        if self.screen == "result":
            self.request_leave("rules")
        else:
            self.show_rules()

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
        self.files, self.file_sizes, self.file_status = [], [], []
        self.file_selected = set()
        self.refresh()

    def browse_output(self) -> None:
        chosen = filedialog.askdirectory(title="选择输出目录")
        if chosen:
            self.output_var.set(chosen)
            self._check_output()
            self.persist_now()
            self.refresh()

    def _check_output(self) -> None:
        self.output_error = check_output_dir(self.output_var.get()).error

    def persist_now(self) -> None:
        if self.shoot:
            return
        ok = save_session(
            self.ruleset.rules,
            self.lib_checked,
            self.output_var.get().strip() or str(default_output_dir()),
        )
        self.save_failed = not ok

    def dismiss_first_run(self) -> None:
        self.first_run_hint = False
        self.refresh()

    def open_settings_location(self) -> None:
        result = reveal_directory(settings_dir())
        if result.copied:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(result.path)
            except tk.TclError:
                pass
        self.settings_open_msg = result.message
        if self.screen == "rules":
            self.refresh()

    def _show_corrupt_dialog(self) -> None:
        name = self.corrupt_name
        extra = MSG_BAD_FILE.format(name=name) if name else None
        prompt_dialog(
            self.root,
            MSG_BAD_TITLE,
            MSG_BAD_BODY,
            [("打开保存位置", "open", "normal"), ("知道了", "ok", "primary")],
            extra=extra,
            extra_mono=True,
            fonts=self.fonts,
            focus="ok",
            stay_values=("open",),
            on_stay=lambda _v: self.open_settings_location(),
        )

    def ask_restore_defaults(self) -> None:
        n, x = changed_from_defaults(self.ruleset.rules)
        if n == 0:
            prompt_dialog(
                self.root,
                MSG_RESET_TITLE,
                MSG_RESET_SAME,
                [("知道了", "ok", "default")],
                fonts=self.fonts,
                focus="ok",
            )
            return
        choice = prompt_dialog(
            self.root,
            MSG_RESET_TITLE,
            reset_body(n, x),
            [("取消", "cancel", "default"), (MSG_RESET_CONFIRM, "reset", "danger")],
            fonts=self.fonts,
            focus="cancel",
        )
        if choice != "reset":
            return
        self._reset_lib_snap = list(self.lib_checked)
        self.ruleset.restore_defaults(label=MSG_RESET_DONE)
        self.lib_checked = default_library_checked()
        self.persist_now()
        self._arm_undo_expiry()
        self.refresh()

    def toggle_rule(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self.ruleset.rules):
            self.ruleset.set_enabled(index, enabled)
            self.persist_now()
            self.refresh()

    def show_rules(self) -> None:
        self.rules_page = "list"
        self.show("rules")

    def show_rules_page(self, page: str) -> None:
        self.rules_page = page
        self.show("rules")

    def rules_back(self) -> None:
        if self.rules_page != "list":
            self.rule_form = retain_editor_draft(self.rule_form)
            self.word_form = retain_editor_draft(self.word_form)
            self.rules_page = "list"
            self.refresh()
            return
        self.main_step = 1 if self.files else 0
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
                "replacement": rule.replacement_text(),
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
        self._reset_lib_snap = None
        rule = self.ruleset.delete(index)
        self.persist_now()
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
        if self._reset_lib_snap is not None:
            self.lib_checked = self._reset_lib_snap
            self._reset_lib_snap = None
        self.persist_now()
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
        self.rule_form = retain_editor_draft(self.rule_form)
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
        self._reset_lib_snap = None
        if form.get("index") is not None:
            self.ruleset.replace(form["index"], rule)
        else:
            self.ruleset.add(rule, overwrite=bool(form.get("overwrite")))
        self.persist_now()
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
            replacement=form.get("replacement") or "",
        )
        self._reset_lib_snap = None
        if form.get("index") is None:
            self.ruleset.add(rule)
        else:
            self.ruleset.replace(form["index"], rule)
        self.persist_now()
        self.rules_page = "list"
        self.refresh()

    def show_library(self) -> None:
        items = common_library()
        if len(self.lib_checked) != len(items):
            self.lib_checked = default_library_checked()
        self.rules_page = "lib"
        self.refresh()

    def toggle_library(self, index: int) -> None:
        items = common_library()
        self.lib_checked = apply_library_toggle(items, self.lib_checked, index)
        self.persist_now()
        self.refresh()

    def add_library(self) -> None:
        self._reset_lib_snap = None
        self.ruleset.add_from_library(common_library(), self.lib_checked)
        self.persist_now()
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
        self._reset_lib_snap = None
        self.ruleset.apply_import(self.import_preview)
        self.persist_now()
        self.rules_page = "list"
        self.refresh()

    def export_rules_dialog(self) -> None:
        choice = radio_dialog(
            self.root,
            "导出规则",
            MSG_EXPORT_BODY,
            list(_EXPORT_CHOICES),
            default="all",
            ok_label="导出…",
            cancel_label="取消",
        )
        if not choice:
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
        self.scan_generation = bump_generation(self.scan_generation)
        ticket = ScanTicket(self.scan_generation)
        self.running = True
        self.cancelled = False
        self.written = False
        self.write_errors = {}
        self.write_notes = {}
        self.note_cleared = False
        self.main_step = 1
        self.file_status = ["pending"] * len(self.files)
        self.show("main")
        self.cancel_event = Event()
        engine = self.ruleset.to_engine()
        paths = list(self.files)

        def worker():
            def progress(index, _path, status):
                mapped = {"running": "running", "ok": "scanned", "nohit": "scanned", "fail": "fail"}.get(status, status)

                def apply(index=index, mapped=mapped, ticket=ticket):
                    if not is_current(ticket, self.scan_generation):
                        return
                    if index < len(self.file_status):
                        self.file_status[index] = mapped
                    self.refresh()

                self.root.after(0, apply)

            results = scan_paths(paths, engine, cancel=self.cancel_event, progress=progress)
            self.root.after(0, lambda results=results, ticket=ticket: self._scan_done(results, ticket))

        Thread(target=worker, daemon=True).start()

    def cancel_scan(self) -> None:
        self.scan_generation = bump_generation(self.scan_generation)
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.running = False
        self.cancelled = True
        self.scans = discard_scan_results(self.scans)
        self.file_status = [
            "cancelled" if s in {"pending", "running"} else s for s in self.file_status
        ]
        self.main_step = 1
        self.refresh()

    def _scan_done(self, results: list[FileScan], ticket: ScanTicket) -> None:
        if not is_current(ticket, self.scan_generation):
            return
        if self.cancelled:
            self.scans = discard_scan_results(results)
            return
        self.running = False
        signature = session_signature(self.files, self.ruleset.rules)
        outcome = apply_unchecks(results, self.uncheck_memory, signature)
        self.note_cleared = outcome == "cleared"
        self.uncheck_memory = None
        self.from_result = False
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
        self.write_errors = {}
        self.write_notes = {}
        self.batch_dir = None
        self.show("result")

    def back_to_files(self) -> None:
        self.request_leave(STEP_FILES)

    def back_to_rules(self) -> None:
        self.request_leave(STEP_RULES)

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

    def written_file_count(self) -> int:
        total = 0
        for scan in self.scans:
            if scan.status == "fail" or str(scan.path) in self.write_errors:
                continue
            if int(getattr(scan, "unlocated_count", 0) or 0) > 0:
                continue
            if scan.enabled_count() > 0:
                total += 1
        return total

    def write_failure_rows(self) -> list[tuple[str, str]]:
        rows = []
        for scan in self.scans:
            reason = self.write_errors.get(str(scan.path))
            if reason:
                rows.append((scan.path.name, reason))
        return rows

    def restore_all_hits(self) -> None:
        if self.written:
            return
        for scan in self.scans:
            for hit in scan.hits:
                hit.enabled = True
        self.refresh()

    def write_files(self, *, retry: bool = False) -> None:
        """Each write gets a new timestamp folder. Retry stays in that folder and skips successes."""
        if not write_allowed(has_fresh_scans=bool(self.scans)) or self.running:
            return
        if retry:
            if not self.write_errors or self.batch_dir is None:
                return
            batch = self.batch_dir
            targets = [scan for scan in self.scans if str(scan.path) in self.write_errors]
        else:
            parent = Path(self.output_var.get().strip() or ".")
            batch = allocate_batch_dir(parent, datetime.now())
            targets = list(self.scans)
        if not targets:
            return
        # A fresh write always gets a new timestamp folder, so it never meets an
        # existing file. Retry stays in that folder and still does not overwrite.
        try:
            results = write_scans(
                targets,
                batch,
                replacements=self.ruleset.replacements(),
                overwrite=False,
                sequence=False,
                engine=self.ruleset.to_engine(),
            )
        except OSError as exc:
            results = blocked_write_results(targets, output_dir_unwritable(exc))
        except ValueError as exc:
            message = str(exc)
            if not message.startswith("输出文件夹不能写入"):
                message = "输出文件夹不能写入：无法创建"
            results = blocked_write_results(targets, message)
        new_paths = []
        for scan, item in zip(targets, results):
            key = str(scan.path)
            if is_write_failure(scan, item):
                self.write_errors[key] = item.error or "没写出"
                self.write_notes.pop(key, None)
            else:
                self.write_errors.pop(key, None)
                if item.dest is not None:
                    new_paths.append(item.dest)
                if item.note:
                    self.write_notes[key] = item.note
                else:
                    self.write_notes.pop(key, None)
        self.kept_outputs = merge_kept(self.kept_outputs, new_paths)
        self.batch_dir = batch
        self.output_display = None
        self.written = decide_written(self.written, results)
        self.refresh()

    def export_hit_list(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出命中清单",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        payload = hit_list_payload(
            self.scans,
            replacements=self.ruleset.replacements(),
            category_order=[rule.name for rule in self.ruleset.rules],
        )
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def open_output(self) -> None:
        path = self.batch_dir if self.batch_dir is not None else Path(self.output_var.get().strip())
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def on_close(self) -> None:
        if decide_close(on_result=self.screen == "result", written=self.written, has_preview=bool(self.scans)):
            choice = confirm_dialog(
                self.root,
                "脱敏文件还没有写出",
                "关闭后，这次的扫描结果和原文预览会清除，要重新扫描才能再写出。",
                [("回去写出", "back", False), ("仍然关闭", "close", True)],
            )
            if choice != "close":
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
        menu = getattr(self.rules_view, "_more_menu", None)
        if menu is not None:
            try:
                menu.unpost()
            except tk.TclError:
                pass
        self.cancelled = False
        self.running = False
        self.written = False
        self.hide_text = False
        self.preview_mode = "orig"
        self.selected_cat = None
        self.current_hit = None
        self.pdf_page = 0
        self.output_error = None
        self.first_run_hint = False
        self.save_failed = False
        self.corrupt_name = None
        self.settings_open_msg = None
        self._reset_lib_snap = None
        self.rules_page = "list"
        self.rule_form = _empty_rule_form()
        self.word_form = _empty_word_form()
        self.import_preview = None
        self.main_step = 0
        self.reached = 0
        self.from_result = False
        self.uncheck_memory = None
        self.note_cleared = False
        self.write_errors = {}
        self.write_notes = {}
        self.batch_dir = None
        self.kept_outputs = []
        self.pin_step_tip = None
        self.output_display = None
        self.file_selected = set()
        self.output_var.set(r"D:\脱敏输出")
        self.drop_step_tip()
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
        if key == "main-first":
            self.files = []
            self.file_sizes = []
            self.file_status = []
            self.first_run_hint = True
            self.show("main")
            return
        if key == "main-files":
            self.show("main")
            return
        if key == "main-outerr":
            self.output_error = "✕ 这个文件夹不能写入（没有权限，或磁盘已满）。请点「更改…」换一个，例如桌面。"
            self.show("main")
            return
        if key == "main-outmissing":
            from doc_redactor.persist import MSG_OUTPUT_MISSING

            self.output_error = MSG_OUTPUT_MISSING
            self.show("main")
            return
        if key == "main-cfgbad":
            self.files = []
            self.file_sizes = []
            self.file_status = []
            self.show("main")
            self._overlay(
                MSG_BAD_TITLE,
                MSG_BAD_BODY,
                [("打开保存位置", False), ("知道了", True)],
                extra=MSG_BAD_FILE.format(name=r"...\doc-redactor\settings.损坏.20261018-2054.json"),
            )
            return
        if key == "main-running":
            self.running = True
            self.main_step = 1
            self.file_status = ["scanned", "scanned", "running", "pending", "pending"]
            self.show("main")
            return
        if key == "main-cancelled":
            self.cancelled = True
            self.main_step = 1
            self.file_status = ["scanned", "scanned", "cancelled", "cancelled", "cancelled"]
            self.show("main")
            return
        if key == "main-rules":
            self.main_step = 1
            self.pin_step_tip = 0
            self.show("main")
            return
        if key == "main-rm":
            self.file_selected = {str(self.files[1]), str(self.files[3])}
            self.show("main")
            return
        if key in {"main-rules-kept", "main-rules-changed"}:
            from doc_redactor.backpath import UncheckMemory, session_signature

            self.main_step = 1
            self.from_result = True
            signature = session_signature(self.files, self.ruleset.rules)
            if key == "main-rules-changed":
                signature = "changed"
            self.uncheck_memory = UncheckMemory(signature=signature, keys=(("kept-1",), ("kept-2",)))
            self.show("main")
            return

        many = key == "res-many"
        nohit = key in {"res-allnohit", "res-nohit"} and key == "res-allnohit"
        all_fail = key == "res-allfail"
        self.scans = demo_scans(many=many, nohit=key == "res-allnohit", all_fail=all_fail)
        self.selected = 0

        if key.startswith("res-"):
            self.output_display = r"D:\脱敏输出\20261017-2140"
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
            elif key == "res-backhint":
                self.selected = 0
                if self.scans[0].hits:
                    self.scans[0].hits[3].enabled = False
                self.show("result")
                from doc_redactor.backpath import back_prompt_body

                self._overlay(
                    "返回修改？",
                    back_prompt_body(unchecked_count(self.scans)),
                    [("留在这里", False), ("返回修改", True)],
                )
                return
            elif key == "res-backwritten":
                self.selected = 0
                self.written = True
            elif key == "res-restore":
                self.selected = 0
                self.selected_cat = "项目号"
                if len(self.scans[0].hits) > 3:
                    self.scans[0].hits[1].enabled = False
                    self.scans[0].hits[3].enabled = False
            elif key == "res-partial":
                self.selected = 2
                self.written = True
                self.write_errors[str(self.scans[2].path)] = "输出文件夹里这个文件正被别的程序占用"
            elif key == "res-many":
                self.selected = 1
            elif key == "res-skipped":
                self.selected = 0
                self.written = True
                self.write_notes[str(self.scans[0].path)] = MSG_ALREADY_WRITTEN
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
                MSG_EXPORT_BODY,
                [("取消", False), ("导出…", True)],
                choices=list(_EXPORT_CHOICES),
                choice_default="all",
            )
            return
        if key == "rules-more":
            self.show_rules_page("list")
            self.root.update_idletasks()
            self.rules_view.post_more_menu()
            return
        if key == "rules-more-closed":
            self.show_rules_page("list")
            self.root.update_idletasks()
            self.rules_view.post_more_menu()
            self.root.update()
            self.rules_view._more_menu.unpost()
            self.rules_view._close_menu_gap()
            self.root.update()
            return
        if key == "rules-reset":
            self.ruleset.add(
                Rule(name="内部编号", kind=KIND_PATTERN, pattern=r"ZX-\d+", examples=["ZX-001"], enabled=True)
            )
            self.ruleset.add(
                Rule(
                    name="手机号（中国大陆）",
                    kind=KIND_PATTERN,
                    pattern=r"1[3-9]\d{9}",
                    examples=["138 0000 0000"],
                    enabled=True,
                )
            )
            nct = self.ruleset.find_index("NCT 试验登记号")
            if nct is not None:
                self.ruleset.rules[nct].enabled = False
            n, x = changed_from_defaults(self.ruleset.rules)
            self.show_rules_page("list")
            self._overlay(
                MSG_RESET_TITLE,
                reset_body(n, x),
                [("取消", False), (MSG_RESET_CONFIRM, False)],
                danger=MSG_RESET_CONFIRM,
                focus="取消",
            )
            return
        if key == "rules-reset-same":
            self.show_rules_page("list")
            self._overlay(
                MSG_RESET_TITLE,
                MSG_RESET_SAME,
                [("知道了", False)],
                focus="知道了",
            )
            return
        if key == "rules-reset-done":
            self.ruleset.restore_defaults(label=MSG_RESET_DONE)
            self.show_rules_page("list")
            return
        if key == "rules-savefail":
            self.save_failed = True
            self.show_rules_page("list")
            return
        if key == "rules-opened":
            self.settings_open_msg = MSG_OPENED
            self.show_rules_page("list")
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

    def _overlay(
        self,
        title: str,
        body: str,
        buttons: list[tuple[str, bool]],
        *,
        extra: str | None = None,
        danger: str | None = None,
        focus: str | None = None,
        choices: list[tuple[str, str]] | None = None,
        choice_default: str | None = None,
    ) -> None:
        from doc_redactor.gui.theme import BAD, BG_INSET, BG_SURFACE, INK, INK2, LINE2

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
        tk.Label(box, text=body, bg=BG_SURFACE, fg=INK, justify="left", wraplength=620, anchor="w").pack(
            fill="x", padx=14, pady=(12, 4 if (extra or choices) else 12)
        )
        if choices:
            picked = choice_default if choice_default else choices[0][1]
            if picked not in {value for _label, value in choices}:
                picked = choices[0][1]
            var = tk.StringVar(value=picked)
            self._overlay_choice = var
            radios = tk.Frame(box, bg=BG_SURFACE)
            radios.pack(fill="x", padx=14, pady=(0, 12))
            for label, value in choices:
                ttk.Radiobutton(radios, text=label, value=value, variable=var).pack(anchor="w", pady=1)
        if extra:
            tk.Label(box, text=extra, bg=BG_SURFACE, fg=INK2, justify="left", wraplength=620, anchor="w").pack(
                fill="x", padx=14, pady=(0, 12)
            )
        foot = tk.Frame(box, bg=BG_SURFACE, highlightthickness=1, highlightbackground=LINE2)
        foot.pack(fill="x")
        inner = tk.Frame(foot, bg=BG_SURFACE)
        inner.pack(fill="x", padx=14, pady=8)
        made = []
        for label, primary in reversed(buttons):
            if danger and label == danger:
                btn = tk.Button(
                    inner,
                    text=label,
                    fg=BAD,
                    bg=BG_SURFACE,
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    padx=10,
                    pady=4,
                )
            elif focus and label == focus:
                btn = tk.Button(
                    inner,
                    text=label,
                    fg=INK,
                    bg=BG_INSET,
                    relief="solid",
                    bd=1,
                    highlightthickness=2,
                    highlightbackground=INK,
                    highlightcolor=INK,
                    padx=10,
                    pady=2,
                )
            else:
                btn = ttk.Button(inner, text=label, style="Primary.TButton" if primary else "TButton")
            btn.pack(side="right", padx=(8, 0))
            made.append((btn, label))
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
        "replacement": "",
    }


def run(*, shoot: bool = False) -> None:
    root, dnd = create_root()
    App(root, dnd, shoot=shoot)
    root.mainloop()
