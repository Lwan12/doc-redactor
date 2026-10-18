# 规则自动保存与恢复

依据：`persist-flow.md`（最新）、`brief.md` 第 21/22 条与 10 条验收、`方案.md` 2.10、`prototype.html`、截图 57–65。Lee 已定：各 Windows 账号各存一份；换电脑靠导出导入、不上云；恢复默认 10 分钟可撤销。

## 逻辑（无 tk，`persist.py`）

- 目录：`%LOCALAPPDATA%\doc-redactor`；Linux/Mac 退 `~/.local/share/doc-redactor`；`DOC_REDACTOR_HOME` 覆盖整个目录。文件 `settings.json`，带 `version`。原子写：同目录临时文件 + `os.replace`。
- 存：规则（名称、种类、启用、替换文字、大小写、词表、例子、匹配写法、照样写占位）、常用库勾选、输出文件夹。不存：文件列表、扫描结果、取消勾选、原文、撤销栈、窗口、测试框、文件名。
- 文件不存在：载入默认规则（常用库 `default_checked`，现 5 条，X 取实际条数不写死；演示词 Acme Pharma / Fictizumab 即真规则），马上落盘，给首次提示。
- 文件在：完整恢复。损坏：改名为 `settings.损坏.YYYYMMDD-HHMM.json`（不删），载入默认并落盘，只提示一次。保存失败：内存照常改，返回失败，下次成功才清。
- 输出文件夹：每次启动检查是否存在。不存在只给红字，不改路径、不静默建目录。
- 恢复默认：N = 当前里不属于默认或内容不同的条数 + 缺了的默认条；X = `default_rules()` 条数。与默认相同则 N=0。恢复后 10 分钟可撤销（复用 `UNDO_TTL_SECONDS`），撤销后也保存。输出文件夹不动。
- 「打开保存位置」：Windows `os.startfile`；其他平台复制路径并提示。

## 界面

- 选文件页顶部首次条；输出行补「下次打开还会记住这个文件夹。」；找不到时用 persist-flow 红字。
- 规则页：右上「更多 ▾」→「恢复默认规则…」；底部小字 +「打开保存位置」；保存失败橙条；导入/导出按钮名不变，只改两处说明。
- 确认与损坏弹窗自绘 `Toplevel`，不用 `messagebox`。恢复确认「取消」默认焦点，「恢复默认规则」红字。

## 验证

- `tests/test_persist.py`：首次、恢复、损坏、保存失败、N/X 与 10 分钟撤销、原子写、环境变量、签名不含原文/文件名、输出夹缺失。
- 全量 pytest（含 tesseract）。
- `tools/shoot_gui.py` 增补 57–65，全部重渲到 `real-screenshots-r5/`。
