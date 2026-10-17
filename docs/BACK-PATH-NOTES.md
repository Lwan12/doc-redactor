# 回头路径验收

界面以 `brief.md` 第 20 条、`方案.md` 2.9、`prototype.html` 为准。下面 9 条是 brief 新增验收。逻辑在 `src/doc_redactor/backpath.py`，界面在 `gui/steps.py`、`gui/main_view.py`、`gui/result_view.py`、`gui/app.py`。

1. 步骤条：走过的步骤带下划线，可点并出小提示；当前步和未走到的步不可点；扫描中不可点。
   通过。依据：`step_clickable`（`test_leave_and_step_and_close_share_the_result_rule`）；`StepBar` 对可点步使用下划线字体，绑定 `<Button-1>` 到 `App.on_step`，`<Enter>` 显示 `step_tip`。当前步、未走到的步和 `running` 时不绑定。

2. 结果页（未写出）点「返回修改」或点前面的步骤，弹「返回修改？」，文案逐字对得上；按「留在这里」什么都不变。
   通过。依据：`decide_leave` 与 `back_prompt_body`（`test_leave_and_step_and_close_share_the_result_rule`、`test_prompt_copy_and_rescan_hint`）。`App.request_leave` 是步骤条和「返回修改」的同一入口；确认结果不是 `leave` 时直接返回，不调用 `_clear_preview`。

3. 返回后选文件页的文件、选规则页的规则勾选、规则里改过的占位符、输出文件夹都还在；返回绝不清空这些（页面 Frame 不销毁，只切换显示）。
   通过。依据：`MainView` 在初始化时 `place` 选文件页和选规则页，`refresh` 只清当前页的子控件再 `tkraise()`。`_clear_preview` 只清 `scans` 和预览，不改 `files`、`ruleset`、`output_var`。占位符在规则内容里，签名测试覆盖占位符变化：`test_signature_tracks_files_and_checked_rule_content`。编辑页草稿：`retain_editor_draft`（`test_editor_draft_is_not_cleared`）。

4. 取消勾选一处→返回→不动文件和规则→重新扫描：仍是取消 1 处；中间改了规则或增删文件：取消勾选清零，结果页顶部有说明。
   通过。依据：`session_signature`、`capture_unchecks`、`apply_unchecks`（`test_unchecks_kept_only_when_signature_matches`）。签名用文件列表加已勾规则内容。对上则重新关掉同一处；对不上则保持全选，`App._scan_done` 把 `note_cleared` 设上，结果页顶部用 `MSG_CLEARED_TOP`。选规则页提示：`rescan_hint`（`test_prompt_copy_and_rescan_hint`）。

5. 已写出后「返回修改」「改规则再出一份」可用；重新出的文件进新的带时间的文件夹，同名加序号，原文件和上一次输出都不被改动；同名弹窗里默认高亮「都另存，名字后加序号」。
   通过。依据：已写出时 `decide_leave` 不弹窗但仍清预览（同一测试）。`allocate_batch_dir` 与两次 `write_scans`（`test_batch_dir_sequence_does_not_reuse`、`test_two_writes_do_not_overwrite_previous_or_source`）。拒绝写出到原路径：`test_refuses_to_overwrite_the_source_path`。`App.write_files` 每次新建批次目录；冲突对话框三个按钮里「都另存，名字后加序号」是主按钮。两个返回按钮都走 `back_to_rules` → `request_leave`。已写出文件经 `protect_written` 保留（`test_written_pdf_is_not_restored_by_going_back`）。

6. 部分写出失败：其他文件照常写出、已写出的不删，失败行标「✕ 没写出」并写原因，「重试失败的」只重试失败的。
   通过。依据：`is_write_failure`、`merge_kept` 和只重写失败文件（`test_retry_does_not_delete_successful_files`）。`App.write_files(retry=True)` 只用 `write_errors` 里的文件、沿用同一批次目录。结果页 `_scan_status` 在写出错误时返回 `nowrite`（✕ 没写出），顶部用 `partial_banner`。

7. 右栏「恢复全部（已取消 N 处）」只在有取消时出现，点后全部恢复勾选。
   通过。依据：结果页右栏用 `unchecked_count`，大于 0 且未写出才放按钮。`App.restore_all_hits` 把每条命中的 `enabled` 设回真。N 按全部文件计，不随类别筛选变。

8. 选文件页每行「移除」、「移除所选（N）」可用，清空只清文件，不删电脑上的文件，规则和输出文件夹保留。
   通过。依据：`without_paths`、`clear_file_list`（`test_remove_and_clear_keep_rules_and_output`）。`App.remove_paths` 只改列表；`clear_files` 只清空 `files` / `file_sizes` / `file_status` / `file_selected`。界面文案 `MSG_CLEAR_FILES`、`MSG_REMOVE_SELECTED`。

9. 中途关窗：只在结果页未写出时弹提示；规则已即时保存，文件列表和扫描结果不恢复。
   通过。依据：`decide_close`（`test_leave_and_step_and_close_share_the_result_rule`）。`App.on_close` 只在该判断为真时弹窗。规则只留在本次进程的 `RuleSet` 里，没有把文件列表或扫描结果写盘；下次启动不会读回。命中清单无原文：`test_hit_list_has_no_source_text`。
