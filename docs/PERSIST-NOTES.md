# 规则自动保存验收

界面以 `persist-flow.md`、`brief.md` 第 21/22 条、`方案.md` 2.10、截图 57–65 为准。逻辑在 `src/doc_redactor/persist.py`，规则比较与恢复默认在 `ruleset.py`，界面在 `gui/app.py`、`gui/main_view.py`、`gui/rules_view.py`、`gui/dialogs.py`。

1. 第一次打开（保存文件不存在）：选文件页顶部有「已载入默认规则，之后修改自动保存。」，点「知道了」才消失，再次打开不再出现；默认规则已落盘。
   通过。依据：`load_session` 在文件不存在时 `first_run=True`，写入默认规则（`test_first_launch_writes_defaults_and_sets_banner`）。`App` 把该标志交给 `MainView` 顶部条；`dismiss_first_run` 只关当次提示。再次启动文件已在，不再出条。

2. 改规则、词表、替换文字、启用状态、输出文件夹后关程序再开，全部原样恢复；文件列表、扫描结果、原文都不恢复。
   通过。依据：`save_session` / `load_session` 往返（`test_second_launch_restores_rules_words_replacement_enabled_library_output`）。九个时机走 `App.persist_now`：确定新增/修改、删除、启停、词表确定、撤销、恢复默认、导入确认、改输出文件夹。载荷只有 `version`、`output_dir`、`library_checked`、`rules`。

3. 保存文件里查不到任何被处理文件的文件名或原文。
   通过。依据：`session_payload` 允许键只有 `ALLOWED_TOP` / `ALLOWED_RULE`（`test_settings_payload_has_no_source_filenames_or_document_text`）。不含 files、scans、原文、`source_path`。

4. 规则页底部有「规则保存在你这台电脑上，改动会自动保存。导出只用来备份或发给同事。」和「打开保存位置」，点后在资源管理器里打开（`os.startfile` 之类）。
   通过。依据：`MSG_PERSIST_FOOT`、`MSG_OPEN_DIR`；`reveal_directory` 在 Windows 调 `os.startfile`，其他平台复制路径（`test_reveal_windows_opens_others_copy`）。`App.open_settings_location` 接剪贴板。截图 65。

5. 「更多 ▾」里有「恢复默认规则…」，点后必弹确认框；「取消」是默认焦点，点取消或关框什么都不改；「恢复默认规则」是红字、不是蓝色主按钮；没有改动时只出「现在的规则和默认规则一样，不需要恢复。」+「知道了」。
   通过。依据：`RulesView` 菜单项 `MSG_RESET_MENU`；`App.ask_restore_defaults` 用 `prompt_dialog`，N=0 走 `MSG_RESET_SAME`，有改动时 `focus="cancel"`、红字 `danger`。Esc / 关闭返回 None。截图 58、59、60。

6. 恢复后顶部出「已恢复默认规则。10 分钟内可点「撤销」找回你原来的规则。」，点撤销全部回来；恢复和撤销都立即保存；输出文件夹不动；关程序后撤销失效。
   通过。依据：`RuleSet.restore_defaults` 写入 `MSG_RESET_DONE`，TTL `UNDO_TTL_SECONDS`（`test_restore_defaults_counts_and_ten_minute_undo_saves`）。N/X 由 `changed_from_defaults` 现算，X 为默认条数（现 5，不是常用库 7）。撤销栈只在内存，关程序即清；磁盘已是恢复后的规则。

7. 保存文件损坏：旧文件改名留存（加「.损坏」和日期时间，不删），载入默认规则并存成新文件，弹窗「规则文件有问题」只出一次，点「知道了」才关，「打开保存位置」可用。
   通过。依据：`load_session` 改名为 `settings.损坏.YYYYMMDD-HHMM.json`（`test_corrupt_file_is_renamed_defaults_loaded_prompt_once`）。第二次加载新文件不再带 `corrupt_name`。弹窗 `prompt_dialog`，`打开保存位置` 不关窗。截图 62。

8. 保存失败：规则页顶部常驻橙条，不弹窗；当次修改仍生效；之后一次保存成功条自动消失；条里有「打开保存位置」「导出…」。
   通过。依据：`save_session` 捕获 `OSError` 返回 False，内存规则保留（`test_save_failure_keeps_memory_and_clears_on_success`）。`App.save_failed` 驱动 `RulesView` 橙条；下次 `persist_now` 成功则清。截图 63。

9. 选文件页输出文件夹那行有「下次打开还会记住这个文件夹。」；记住的文件夹不存在时红字就地提示、「下一步」灰着、不自己换路径。
   通过。依据：`MSG_OUTPUT_REMEMBER` 接在 `MSG_OUTPUT_PARENT` 后。`check_output_dir` 不建目录、不改路径，缺失返回 `MSG_OUTPUT_MISSING`（`test_missing_output_folder_reports_and_keeps_path`）。`can_next` 在有 `output_error` 时为假。截图 02、64。

10. 导出说明和导入预览底部的新增说明逐字对得上。
    通过。依据：导出对话框 `MSG_EXPORT_BODY`（「用来备份或换电脑」）；导入底栏 `MSG_IMPORT_FOOT`（「确认后会自动保存。」）。按钮仍叫「导入…」「导出…」。截图 35、37。
