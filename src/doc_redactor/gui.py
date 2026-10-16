"""Small tkinter window. Drag and drop is used when tkinterdnd2 is installed."""

from __future__ import annotations


def main() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise SystemExit(
            "未找到 tkinter。请安装带 Tcl/Tk 的 Python（Linux 上常见包名是 python3-tk）。"
        ) from exc

    dnd_type = None
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD

        root = TkinterDnD.Tk()
        dnd_type = DND_FILES
    except Exception:
        root = tk.Tk()

    root.title("doc-redactor")
    root.geometry("760x580")

    files: list[str] = []
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="待处理文件（docx / pdf / png / jpg / jpeg）").pack(anchor="w")
    file_list = tk.Listbox(frame, height=8, selectmode=tk.EXTENDED)
    file_list.pack(fill="both", expand=True, pady=(4, 0))

    def add_files(paths) -> None:
        for path in paths:
            if path and path not in files:
                files.append(path)
                file_list.insert(tk.END, path)

    def browse_files() -> None:
        selected = filedialog.askopenfilenames(
            title="选择文件",
            filetypes=[
                ("支持的文件", "*.docx *.pdf *.png *.jpg *.jpeg"),
                ("所有文件", "*.*"),
            ],
        )
        add_files(selected)

    def remove_selected() -> None:
        for index in reversed(file_list.curselection()):
            file_list.delete(index)
            del files[index]

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=6)
    ttk.Button(buttons, text="添加文件", command=browse_files).pack(side="left")
    ttk.Button(buttons, text="移除所选", command=remove_selected).pack(side="left", padx=6)

    if dnd_type is not None:

        def on_drop(event) -> None:
            add_files(root.tk.splitlist(event.data))

        file_list.drop_target_register(dnd_type)
        file_list.dnd_bind("<<Drop>>", on_drop)
        ttk.Label(frame, text="可以把文件拖到上面的列表里。").pack(anchor="w")
    else:
        ttk.Label(frame, text="未安装 tkinterdnd2，请使用“添加文件”。拖拽已关闭。").pack(anchor="w")

    ttk.Label(frame, text="规则文件").pack(anchor="w", pady=(10, 0))
    rules_var = tk.StringVar()
    rules_row = ttk.Frame(frame)
    rules_row.pack(fill="x", pady=4)

    def browse_rules() -> None:
        selected = filedialog.askopenfilename(
            title="选择规则文件",
            filetypes=[("YAML", "*.yaml *.yml"), ("所有文件", "*.*")],
        )
        if selected:
            rules_var.set(selected)

    ttk.Entry(rules_row, textvariable=rules_var).pack(side="left", fill="x", expand=True)
    ttk.Button(rules_row, text="浏览", command=browse_rules).pack(side="left", padx=(6, 0))

    ttk.Label(frame, text="输出目录").pack(anchor="w", pady=(8, 0))
    output_var = tk.StringVar()
    output_row = ttk.Frame(frame)
    output_row.pack(fill="x", pady=4)

    def browse_output() -> None:
        selected = filedialog.askdirectory(title="选择输出目录")
        if selected:
            output_var.set(selected)

    ttk.Entry(output_row, textvariable=output_var).pack(side="left", fill="x", expand=True)
    ttk.Button(output_row, text="浏览", command=browse_output).pack(side="left", padx=(6, 0))

    ttk.Label(frame, text="命中类别次数").pack(anchor="w", pady=(8, 0))
    result = tk.Text(frame, height=10)
    result.pack(fill="both", expand=True, pady=4)

    def run() -> None:
        result.delete("1.0", tk.END)
        if not files:
            messagebox.showerror("doc-redactor", "请先选择文件")
            return
        if not rules_var.get().strip():
            messagebox.showerror("doc-redactor", "请选择规则文件")
            return
        if not output_var.get().strip():
            messagebox.showerror("doc-redactor", "请选择输出目录")
            return
        from doc_redactor.cli import format_summary, redact_paths

        try:
            summaries = redact_paths(files, rules_var.get().strip(), output_var.get().strip())
        except Exception as exc:
            messagebox.showerror("doc-redactor", str(exc))
            result.insert(tk.END, f"失败: {exc}\n")
            return
        blocks = [format_summary(name, counts) for name, counts in summaries]
        result.insert(tk.END, "\n\n".join(blocks) + "\n")

    ttk.Button(frame, text="开始脱敏", command=run).pack(anchor="e", pady=(4, 0))
    root.mainloop()


if __name__ == "__main__":
    main()
