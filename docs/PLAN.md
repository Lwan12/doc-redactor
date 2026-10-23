# doc-redactor v1 计划

## 功能清单
1. Word(.docx)：python-docx；正文、表格（含嵌套）、页眉页脚；跨 run 命中合并处理，尽量保留首 run 格式。
2. PDF：PyMuPDF；search/识别命中 -> add_redact_annot -> apply_redactions，真删除文字（非黑框覆盖）。
3. 规则：rules.yaml。regex 规则（项目号等，可增减）；词表规则（txt/csv，一行一个，case_sensitive 可配）。
4. 引擎：Presidio AnalyzerEngine + PatternRecognizer（regex/deny-list），不加载 NLP 大模型（用 spaCy blank 或自定义轻量 NlpEngine，无需下载模型）。
5. 输出：`<原名>.redacted.<ext>` + `<原名>.report.json`（仅类别与次数）。不覆盖原文件。
6. CLI（argparse）+ tkinter 小界面（选文件/可拖拽，拖拽用可选 tkinterdnd2，缺失则降级为选文件）。
7. GitHub Actions + PyInstaller 构建 Windows exe（artifact）。
8. 测试：pytest + 虚构样例 docx/pdf（由脚本生成）。

## 边界 / 不做
- 图片/扫描件/OCR 不做（README 写后续用 Presidio image redactor + Tesseract）。
- 不做 NER/人名自动识别、备份、回滚、容灾、遥测、联网。
- 不做 .doc/.xlsm/.pptx。表格（.xlsx/.xls/.csv）替换单元格、批注、页眉页脚、工作表名和隐藏行列。公式原样写出。图表、文本框和文件属性不处理。加密文件和带图片的工作簿整份不写出。
- 文本框/脚注/批注 v1 不覆盖（README 已知限制）。
