# 按类别固定占位符

分支 `feat/placeholders`，基于当前 `main`。YAGNI：不编号、不加新画面、图片仍涂黑。

## 行为

同一类所有命中换成同一个占位符，不编号。默认表：

| 类别（中文 / yaml 名） | 占位符 |
| --- | --- |
| 药名 / DRUG | `[STUDY DRUG]` |
| 申办方 / SPONSOR | `[SPONSOR]` |
| 项目号 / PROJECT_ID | `[PROTOCOL]` |
| NCT 试验登记号 / NCT | `[NCT ID]` |
| 方案号清单、字母加数字 | `[PROTOCOL NO]` |
| 邮箱 / EMAIL | `[EMAIL]` |
| 手机号（中国大陆）/ 手机号 / PHONE | `[PHONE]` |
| 其它 | `[REDACTED:类别]` |

用户自建规则：替换框默认仍是 `[REDACTED:名称]`，可改。常用库添加时带上表里的默认值。已有规则可在规则设置里改。

## 规则文件

`regex` 与 `wordlists` 条目都可写 `replacement`。缺省（含旧 yaml 无此字段）走默认表，不报错。`rules.example.yaml` 写上字段，便于对照。

## 写出

- Word：沿用跨 run 写入，占位符进首 run，保留格式。
- PDF：`apply_redactions` 真删后，在最大可用框里 `insert_textbox`。字号从框高往下缩到 4pt；仍放不下就截断。原文必须消失。
- 图片：仍黑块，不写占位符。
- `report.json`：只含类别和次数。无原文、无占位符。

## 界面

- 规则编辑「替换成」框沿用；词表编辑补同一框。
- 规则列表加「替换成」列；常用库在「默认 / 提醒」里带上占位符。
- 结果页「替换后」：Word 和 PDF 显示占位符；图片仍涂黑。高亮、统计、取消勾选不变。
- 主界面提示改为：命中的内容会换成该类的占位符(可在规则设置里改)；PDF 的占位符放不下会缩小或截断；图片会涂黑。

## 代码落点

- `engine.py`：默认表 + `redaction_label`；规则上的 `replacement` 收进引擎。
- `config.py` / `ruleset.py`：解析、导出 `replacement`；`Rule.replacement_text()` 走默认表。
- `docx_redactor.py`：用引擎/默认表的占位符。
- `pdf_redactor.py`：删字后写入；`scan._write_pdf` 同步。
- GUI：`rules_view` / `result_view` / `main_view` / `app.save_words`。
- README、检查脚本、现有断言 `[REDACTED:PROJECT_ID]` 的测试。

## 测试与自检

单测：默认表、自定义 replacement、旧 yaml、同类多处同一占位符且不编号、Word 跨 run、PDF 写入且原词不在、缩字号/截断、report 无原文无占位符。全量 pytest（含 tesseract）。对 samples 的 docx/pdf/png 与含内嵌图 docx 跑 CLI。截图 47 张到 `real-screenshots-r3/`。不 push。
