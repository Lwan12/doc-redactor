# doc-redactor

离线脱敏小工具。它按你自己的规则，处理 Word（`.docx`）、PDF，以及 `png` / `jpg` / `jpeg`。正文、表格（含嵌套表格）、页眉页脚里的命中会被换成 `[REDACTED:类别]`；PDF 文本层里能定位到的命中会被真正删掉；图片里的命中用黑块盖住。

规则、词表和 `samples/` 里只有虚构数据，例如项目号 `ABC-2026-001`、申办方 `Acme Pharma`、药名 `Fictizumab`。不要把真实项目号、申办方或药名写进仓库。

运行时不下载模型，也不发起网络请求。

## 安装

需要 Python 3.10 或更高版本，以及本机的 Tesseract（语言数据至少要有 `eng` 和 `chi_sim`）。没有图片时可以不装 Tesseract；文件里一旦有受支持的图片，就会要求能找到 Tesseract。

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
```

不安装包也可以：

```bash
PYTHONPATH=src python -m doc_redactor --help
```

识别引擎用的是 Presidio 的 `AnalyzerEngine`，NLP 部分是 `NoOpNlpEngine`。它不加载 spaCy 语言模型，也不会下载 `en_core_web_lg`。默认的 `AnalyzerEngine()` 会去找 spaCy 模型，这里没有走那条路径。

## 规则

`rules.example.yaml`：

```yaml
regex:
  - name: PROJECT_ID
    pattern: '\bABC-\d{4}-\d{3}\b'
    flags: ""
wordlists:
  - path: wordlists/sponsors.example.txt
    case_sensitive: false
    category: SPONSOR
  - path: wordlists/drugs.example.csv
    case_sensitive: true
    category: DRUG
```

- `regex` 可增可删。每条有 `name`、`pattern`、`flags`。`name` 就是报告里的类别。`flags` 可以省略，也可以写成 `IGNORECASE`，或用 `|` / 列表组合 `MULTILINE`、`DOTALL`。
- `wordlists` 可增可删。每条有 `path`、`case_sensitive`、`category`。`case_sensitive: false` 时忽略大小写。
- 词表一行一个。空行忽略。`.csv` 取第一列，不要表头。
- 相对路径相对于规则文件所在目录，不相对于当前工作目录。

示例词表里，`Fictizumab` 区分大小写，所以 `fictizumab` 会留下来；`Acme Pharma` 不区分大小写。

## 命令行

```bash
python -m doc_redactor samples/sample.docx samples/sample.pdf samples/sample.png \
  -r rules.example.yaml -o out
```

每个输入得到两个新文件，不覆盖原文件：

- `out/<文件名>.redacted.<原扩展名>`
- `out/<文件名>.report.json`

报告只写「类别 → 次数」，例如 `{"DRUG": 1, "PROJECT_ID": 2, "SPONSOR": 1}`。里面没有命中的原文。同一输出目录里，主文件名相同的输入会共用 `report.json` 这个名字，后写的会盖住先写的。

图形界面：

```bash
python -m doc_redactor.gui
```

界面里可以选择文件、规则文件和输出目录。装了 `tkinterdnd2` 时可以把文件拖进列表；没装就只用按钮选文件。运行结束后，窗口里显示各类别的命中次数。Linux 上如果提示没有 tkinter，需要带 Tcl/Tk 的 Python（常见包名 `python3-tk`）。

## 样例

```bash
python tools/make_samples.py
```

会在 `samples/` 写下 `sample.docx`、`sample.pdf`、`sample.png`。docx 含正文、表格、嵌套表格、首页/奇数页/偶数页的页眉页脚、跨 run 的项目号，以及一张内嵌图片。pdf 含文本层和同一张图片。图片上是上面那三组虚构字样。

## 各格式怎么处理

**Word。** 遍历正文段落、表格（递归嵌套表格），以及每个 section 自己定义的页眉页脚（默认、首页、奇偶页）。命中如果跨了多个 run，占位符写在第一个涉及的 run 上，并保留这个 run 的格式；后面那些 run 里属于命中的文字删掉。`word/media/` 下的 `png` / `jpg` / `jpeg` 会脱敏后再写回同名文件。

**PDF 文本。** 对每一页用 Presidio 看 `page.get_text()` 的文本，再用 `page.search_for()` 找矩形，然后 `add_redact_annot` 和 `apply_redactions` 把文字从内容流里删掉，不是只盖一个黑框。保存时使用 `garbage` 和 `clean`。

**PDF 图片。** 对有 xref 的位图取出像素，走和独立图片一样的 OCR 与涂黑，再用 `page.replace_image` 写回。没有独立 xref 的内联图片替换不到。

**独立图片。** 使用 Presidio 的 `ImageAnalyzerEngine` 和 `ImageRedactorEngine`，注入上面的 `AnalyzerEngine`，OCR 用 `pytesseract`，语言 `chi_sim+eng`。命中词涂黑，并稍微外扩几个像素，避免笔画露在框外。这一路不会调用会去下载 spaCy 大模型的默认分析器。

## Tesseract

查找顺序：

1. 环境变量 `TESSERACT_CMD`
2. 打包后的程序：可执行文件同目录下的 `tesseract/tesseract.exe`（或 `tesseract`），然后是 PyInstaller 解压目录 `_MEIPASS/tesseract/` 里的同名文件
3. `PATH`

如果可执行文件旁边有 `tessdata/` 目录，程序会把 `TESSDATA_PREFIX` 指到那里，这样打包后的程序不用再联网下载语言数据。

本机示例：

```bash
TESSERACT_CMD=/path/to/tesseract pytest
```

找不到 Tesseract 时，图片相关测试会跳过并提示安装或设置 `TESSERACT_CMD`。

## 打包

不要为这个项目下载 spaCy 的 `en_core_web_lg` 或其他语言模型。PyInstaller 需要带上 Presidio 自带的配置数据：

```bash
pyinstaller --onefile --windowed --name doc-redactor-gui \
  --paths src \
  --collect-data presidio_analyzer \
  --collect-all pymupdf \
  --add-data "tesseract:tesseract" \
  tools/pyinstaller_entry_gui.py

pyinstaller --onefile --name doc-redactor \
  --paths src \
  --collect-data presidio_analyzer \
  --collect-all pymupdf \
  --add-data "tesseract:tesseract" \
  tools/pyinstaller_entry_cli.py
```

Windows 上 `--add-data` 的分隔符是分号：`tesseract;tesseract`。GUI 使用 `--windowed`，CLI 保留控制台。两个入口都会把 `tesseract.exe`、它的 DLL 和 `tessdata` 打进去。

体积会明显大于这几份脚本。主要来自 PyMuPDF、Presidio 带进来的 spaCy（即使不加载语言模型）、OpenCV、Pillow，以及 Tesseract 和 `eng` / `chi_sim` 语言数据。Tesseract 本身是 Apache-2.0；构建时用的 `eng.traineddata` 和 `chi_sim.traineddata` 来自 [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast)，许可证以该仓库的声明为准（Apache-2.0）。

## Windows 构建

`.github/workflows/build-windows.yml` 在 `windows-latest` 上使用 Python 3.12：

1. `choco install tesseract`
2. 只在构建机上，从 `github.com/tesseract-ocr/tessdata_fast` 下载 `eng.traineddata` 和 `chi_sim.traineddata`
3. `pip install -r requirements.txt`
4. `pytest`
5. PyInstaller 分别打出 GUI（`--onefile --windowed`）和 CLI 两个 exe，并用 `--add-data` 放进 Tesseract
6. `upload-artifact` 上传 `dist/doc-redactor.exe` 和 `dist/doc-redactor-gui.exe`

构建机需要联网安装依赖和语言数据。打好的 exe 在运行时不再下载模型或语言数据。

## 测试

```bash
TESSERACT_CMD=/path/to/tesseract PYTHONPATH=src pytest
```

覆盖规则加载、词表大小写、docx（正文、表格、嵌套表格、页眉页脚、跨 run）、PDF 文本删除、报告不含原文，以及脱敏后再次 OCR 确认原词不可读。

## 限制

- 文本框、脚注、批注不覆盖。
- PDF 里被拆开、写成矢量路径，或和 `get_text()` 对不上的词，`search_for` 可能找不到，文字就还在。
- 图片只处理 `png` / `jpg` / `jpeg`。docx 里的 emf、wmf 等不处理。
- PDF 里没有独立 xref 的内联图片可能无法写回。
- OCR 会漏字，也会把框画大。涂黑不能当成所有扫描件都已经不可读。
- 不做 `.doc`、`.xlsx`、`.pptx`，不做自动人名识别，不做备份或回滚。

## 许可

本仓库自有代码使用 MIT License，版权人 Lwan12，2026。全文见 `LICENSE`。

下面只列出依赖的许可证名称，方便对照，不是法律结论。分发前请自己确认义务，必要时请律师看过再发出 exe。

- PyMuPDF（`pymupdf`）是 AGPL-3.0，也可以走 Artifex 商业许可。分发含 PyMuPDF 的 exe 会使整体需要按 AGPL 提供源码。MIT 只覆盖本项目自有代码。
- Presidio（`presidio-analyzer`、`presidio-image-redactor`）是 MIT。
- Tesseract 是 Apache-2.0。
- `python-docx` 是 MIT。
- PyInstaller 是 GPLv2，并带有 bootloader 例外。
- Pillow 是 HPND。
- PyYAML 是 MIT。

spaCy、OpenCV、NumPy、pytesseract、reportlab 等传递依赖还有各自的许可证，以各项目原文为准。
