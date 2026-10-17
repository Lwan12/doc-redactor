# PR 说明草稿

分支 `fix/pdf-hidden-text`，基于 `main`（`6813c43`，界面改造已合并）。不推远程。

## 这次改什么

品审打回的主项：PDF 写出以前只删正文。文档属性、XMP、批注（含表单控件）、书签、嵌入附件里的原文会留在输出文件里。

命令行 `redact_pdf` 和窗口 `scan._write_pdf` 在保存前走同一个 `clear_pdf_hidden`：

- 文档属性 title / author / subject / keywords / creator / producer 写成空
- `set_xml_metadata("")`，再 `del_xml_metadata()`
- 删掉全部批注，含表单 widget 和附件批注
- `set_toc([])` 清书签
- 删嵌入文件，并去掉目录里的 AcroForm / EmbeddedFiles

正文仍用 `apply_redactions` 真删，不是盖黑框。回归测试用虚构词复现「元数据 + 文本批注 + 书签 + 表单域 + XMP + 附件」，断言这些位置和原始字节都没有原词；正文里未命中的词还在，命中的词被删掉。

MuPDF 保存时会自己写上 `/Producer (MuPDF …)`。原来的制作者字符串已经去掉，阅读器里的 producer 字段也是空的。这是库在写出时盖上的标记，不是原文件的属性。

另外：

- 用户能看到的报错不再写「正则」，改成「匹配写法」。`config.py` 的标记/规则解析和 `engine.py` 编译失败都会走到。窗口导入规则文件时，未知标记走 `parse_flags`，这条界面能触发。
- README 的规则文件一节改标题为「给开发者 / 命令行高级用法」。键名仍是文件格式，窗口里不出现。
- 方案号样例统一为 `AAA0000`、`AA-K0000`。`ABC-2026-001`、`Acme Pharma`、`Fictizumab` 不动。
- 960 宽结果页两列加宽，文件名和位置文字不再被裁。PDF 翻页单独一行，避免和「原文高亮」叠在一起。写出按钮固定在窗口底部。图片预览按中间栏缩放。规则页提示条按所在栏的宽度折行，不用固定换行宽度。
- 命中清单维持现状：含文件名，不含原文。文件名本身敏感时请自行注意。

## 命中清单

导出文件是 `{文件名: {类别: 次数}}`。清单含文件名，不含原文。文件名本身敏感时请自行注意。这次没有改导出内容。

## pyproject.toml / requirements.txt / doc-redactor.spec

这三份文件的改动在已合并的 `6813c43`（相对上一提交 `9c8ce4f`），不是本分支新改的。下面按 `git diff 9c8ce4f..6813c43` 写，没有另编版本号。

### 版本

`pyproject.toml` 的 `version` 在 `9c8ce4f` 里已经是 `1.0.0`。`6813c43` 没有改版本号。

### 依赖

`6813c43` 把 `tkinterdnd2==0.6.4` 加进三处：

- `[project].dependencies`（`pip install` 这个包时的直接依赖）
- `[project.optional-dependencies].dev`（和 pytest、pyinstaller、reportlab 列在一起，又写了一次）
- `requirements.txt`

窗口用它把文件拖进列表。`create_root` 里如果 import 失败，就退回普通 Tk，只留「选择文件…」「选择文件夹…」。写进依赖是为了按 `requirements.txt` 或安装包时默认带上拖放；没装时窗口仍能开。

### spec 的 collect

`6813c43` 在 `doc-redactor.spec` 的 `hiddenimports` 里加了界面模块和 `tkinterdnd2`：

- `doc_redactor.gui.app`
- `doc_redactor.gui.theme`
- `doc_redactor.gui.main_view`
- `doc_redactor.gui.result_view`
- `doc_redactor.gui.rules_view`
- `doc_redactor.gui.dialogs`
- `doc_redactor.gui.demo`
- `tkinterdnd2`

`collect_all("pymupdf")` 之后，用 `try/except` 包住 `collect_all("tkinterdnd2")`。收集成功就把 datas、binaries、hiddenimports 并进包；收集失败（没装，或 hook 报错）就跳过，spec 继续往下走。这是打包时可选收集。窗口运行时仍看 import 成不成功，决定能不能拖文件。

两个 exe 还是同一个 `COLLECT` 收到 `dist/doc-redactor/`。没有改 `MERGE`，也没有改成 onefile。

---

## PR #4：按类别固定占位符

命中的内容不再写成 `[REDACTED:类别]` 加编号，同一类的所有命中换成同一个固定占位符。

默认表：

| 类别 | 占位符 |
|---|---|
| 药名 | `[STUDY DRUG]` |
| 申办方 | `[SPONSOR]` |
| 项目号 | `[PROTOCOL]` |
| NCT 试验登记号 | `[NCT ID]` |
| 方案号清单、「字母加数字」 | `[PROTOCOL NO]` |
| 邮箱 | `[EMAIL]` |
| 手机号 | `[PHONE]` |
| 其他规则 | `[REDACTED:名称]` |

- **可改**：规则设置页里每条规则、词表有「替换成」；常用库添加时带上默认值。`rules.yaml` 的 `regex` 和 `wordlists` 条目都可写 `replacement` 字段。旧 yaml 没有该字段时走默认表，不报错。
- **Word**：跨 run 命中写入占位符，保留首个 run 的格式。
- **PDF**：先 `apply_redactions` 真删文字，再在被遮的框里写入占位符。放不下先缩字号，缩到最小仍放不下就截断显示。原文已经删掉，不会残留。
- **图片**：仍然涂黑，不写占位符。
- **不变**：`report.json` 和命中清单仍只有类别和次数；预览高亮、统计、取消勾选同源逻辑不变；结果页「替换后」预览显示的就是这些占位符。
- **验证**：56 条 pytest 全过（含 tesseract 的图片测试）；对样例 docx、pdf、png 和含内嵌图的 docx 跑命令行，三个原词不残留，输出里有对应占位符；Actions 构建与非管理员运行验证为绿。

限制：

- PDF 框太小时占位符会缩小或截断。
- 图片只涂黑，不写占位符。
- Word 的文本框、脚注、批注仍不覆盖。
- PDF 里文字搜不到的词（比如被拆开的）仍可能留着。
- 结果页中间预览栏比上一版窄，长句折行较多。
