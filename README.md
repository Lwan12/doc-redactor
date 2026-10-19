# doc-redactor

离线脱敏小工具。它按你自己的规则，处理 Word（`.docx`）、PDF，以及 `png` / `jpg` / `jpeg`。正文、表格（含嵌套表格）、页眉页脚里的命中会换成该类的固定占位符（同一类不编号，例如药名一律 `[STUDY DRUG]`）；PDF 文本层里能定位到的命中会先真正删掉，再在原位置写入同一个占位符（放不下会先缩小字号，还放不下就换成短码，并在文末加一页说明）；图片里的命中用黑块盖住。

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

## Windows 免安装包

Actions 产物里有 `doc-redactor-windows.zip` 和 `SHA256SUMS.txt`。zip 解压就能用：不用安装，不用管理员权限，也不写注册表。两个 exe 的 manifest 里，`requestedExecutionLevel` 是 `asInvoker`，权限跟启动它的用户一样，不会弹出 UAC 要求提权。`doc-redactor.spec` 里 PyInstaller 的 `uac_admin` 保持关闭。

解压到当前用户能写的目录，例如 `%USERPROFILE%\apps`。不要放进 `C:\Program Files`。解压出来的 `doc-redactor` 目录里有：

- `doc-redactor.exe`，命令行
- `doc-redactor-gui.exe`，窗口
- `tesseract\`：`tesseract.exe`、DLL，以及 `tessdata` 里的 `chi_sim.traineddata` 和 `eng.traineddata`

两个 exe 共用这一目录里的依赖。不要把 exe 单独拷走。图片脱敏用包内的 Tesseract，不用另外安装，也不用设置 `TESSERACT_CMD`。

打包用 onedir，不用 onefile。onefile 每次启动都先把内容解压到临时目录，启动更慢，也更容易被杀毒软件拦住。onedir 把 exe 和依赖放在同一个目录里，启动更稳。

没有签名的 exe 可能被杀毒软件或公司策略拦住。可以把解压后的目录交给 IT 加白名单；用 `SHA256SUMS.txt` 核对 zip 的 SHA256，确认它就是 Actions 上的那一份；也可以不跑 exe，按上面的安装一节用源码运行。

使用步骤：

1. 在本仓库的 Actions 页面下载名为 `doc-redactor-windows` 的产物。里面是 `doc-redactor-windows.zip` 和 `SHA256SUMS.txt`。
2. 核对 SHA256。PowerShell：

```powershell
Get-FileHash -Algorithm SHA256 .\doc-redactor-windows.zip
```

`Hash` 和 `SHA256SUMS.txt` 里的值一致即可，不区分大小写。

3. 解压到用户目录：

```powershell
Expand-Archive .\doc-redactor-windows.zip -DestinationPath "$env:USERPROFILE\apps"
```

4. 命令行处理文件：

```powershell
& "$env:USERPROFILE\apps\doc-redactor\doc-redactor.exe" `
  D:\docs\a.docx D:\docs\a.pdf D:\docs\a.png `
  -r D:\docs\rules.yaml `
  -o D:\docs\out
```

也可以双击同一目录里的 `doc-redactor-gui.exe`，在窗口里选文件、规则和输出目录。规则文件不会打进 zip。窗口里直接设规则即可；自己改规则文件是给开发者 / 命令行的高级用法，格式见下一节。仓库里的 `rules.example.yaml` 只有虚构词。

## 规则文件格式（给开发者 / 命令行高级用法）

窗口里设规则，不用看这一节，界面也不出现下面这些键名。命令行和要自己改规则文件的同事用这个格式。`rules.example.yaml`：

```yaml
regex:
  - name: PROJECT_ID
    pattern: '\bABC-\d{4}-\d{3}\b'
    flags: ""
    replacement: "[PROTOCOL]"
wordlists:
  - path: wordlists/sponsors.example.txt
    case_sensitive: false
    category: SPONSOR
    replacement: "[SPONSOR]"
  - path: wordlists/drugs.example.csv
    case_sensitive: true
    category: DRUG
    replacement: "[STUDY DRUG]"
```

- 下面的键名只出现在规则文件里。`regex` 可增可删。每条有 `name`、`pattern`、`flags`，以及可选的 `replacement`。`name` 就是报告里的类别。`flags` 可以省略，也可以写成 `IGNORECASE`，或用 `|` / 列表组合 `MULTILINE`、`DOTALL`。
- `wordlists` 可增可删。每条有 `path`、`case_sensitive`、`category`，以及可选的 `replacement`。`case_sensitive: false` 时忽略大小写。
- `replacement` 是写入文件的占位符。同一类所有命中都换成这一串，不编号。缺省（旧文件没有这个字段也可以）按类别走默认表：`PROJECT_ID` → `[PROTOCOL]`，`SPONSOR` → `[SPONSOR]`，`DRUG` → `[STUDY DRUG]`，`NCT` → `[NCT ID]`，方案号清单和「字母加数字」→ `[PROTOCOL NO]`，邮箱 → `[EMAIL]`，手机号 → `[PHONE]`。其它类别 → `[REDACTED:类别]`。
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

窗口标题是「文档脱敏工具」。主流程分成选文件、选规则、查看结果；规则设置另开一页。

- 选文件页把 Word / PDF / 图片拖进列表，或点「选择文件…」「选择文件夹…」。装了 `tkinterdnd2` 时可以拖入；没装或没打进包时，界面只留按钮，并写明当前不能拖。每行可以移除，多选后可以「移除所选」。清空只清列表，不删电脑上的文件。
- 下一步到选规则页勾选要遮的内容。第一次打开会带上常用库里默认勾上的五条（项目号、方案号清单、申办方、药名、NCT 试验登记号），并马上写到本机（Windows 在 `%LOCALAPPDATA%\doc-redactor\settings.json`，其他系统在 `~/.local/share/doc-redactor`；可用环境变量 `DOC_REDACTOR_HOME` 改目录）。之后改规则、词表、替换文字、启用状态或输出文件夹都会自动保存，下次打开原样恢复。导入/导出只用来备份或换电脑，按钮名字不变。方案号清单是词表，要先导入一列号才会命中；「字母加数字」是它的备选子项，默认不勾。点「规则设置…」可以新增（贴例子或照样写 `#`/`@`）、编辑词表、从常用库添加、导入/导出。界面不出现「正则」字样。返回选文件不会丢掉已添加的文件。
- 「开始扫描」先在内存里找出命中位置，不改原文件。结果页高亮原文（可改「替换后」），按类别汇总，每处可取消勾选。取消后顶部总数、文件替换数、类别个数、位置清单和主按钮上的数字一起变。有取消时可以「恢复全部」。
- 原文只在窗口里临时显示，不会写入任何文件。勾「隐藏原文，只看位置」后，预览和清单里的原文都换成 ▇。
- 「导出命中清单」写的是每个文件名对应的「类别 → 次数」。清单含文件名，不含原文。文件名本身敏感时请自行注意。
- 确认后，在所选输出文件夹下新建 `YYYYMMDD-HHMM` 目录（同名则加 `-2`、`-3`），里面写出 `<原名>.redacted.<ext>` 和只含类别次数的 `report.json`。不覆盖原文件，也不覆盖上一次的输出。命中换成该类的占位符（可在规则设置里改）；PDF 的占位符放不下会先缩小，还放不下就换成短码并在文末加说明；图片涂黑。某个文件没写出时，其余照常留下，可以「重试失败的」。
- 未写出时，「返回修改」或点步骤条回到前面的步骤，会先确认：扫描结果会清掉，要重新扫描；文件、规则勾选和改过的占位符还在。已写出后再返回不弹这窗，磁盘上的文件保持不动。关掉窗口只在结果页还没写出时提示。这些确认用自绘对话框，不用系统 `messagebox`。删除规则或恢复默认后 10 分钟内可撤销（只留在本次运行的内存里，到点撤销按钮消失，关掉窗口即清）。规则已经即时写到本机；文件列表和扫描结果不会在下次打开时恢复。保存文件里没有被处理文件的文件名或原文。

Linux 上如果提示没有 tkinter，需要带 Tcl/Tk 的 Python（常见包名 `python3-tk`）。Windows 免安装包会尽量带上 `tkinterdnd2`；如果打包时没收集到，窗口里会说明只能用「选择文件…」。

## 样例

```bash
python tools/make_samples.py
```

会在 `samples/` 写下 `sample.docx`、`sample.pdf`、`sample.png`。docx 含正文、表格、嵌套表格、首页/奇数页/偶数页的页眉页脚、跨 run 的项目号、页眉文本框、简单域、内容控件、修订插入与删除，以及一张内嵌图片。pdf 含文本层和同一张图片。图片上是上面那三组虚构字样。

## 各格式怎么处理

**Word。** 遍历正文段落、表格（递归嵌套表格），以及每个 section 自己定义的页眉页脚（默认、首页、奇偶页）。命中如果跨了多个 run，占位符写在第一个涉及的 run 上，并保留这个 run 的格式；后面那些 run 里属于命中的文字删掉。`word/media/` 下的 `png` / `jpg` / `jpeg` 会脱敏后再写回同名文件。

**PDF 文本。** 对每一页用 Presidio 看 `page.get_text()` 的文本，再用 `page.search_for()` 找矩形，然后 `add_redact_annot` 和 `apply_redactions` 把文字从内容流里删掉，不是只盖一个黑框。删完后在原来的框里写入该类占位符；框太窄会先缩小字号（最小 6 磅），还放不下就换成该类短码，用过短码时在文末加一页替换说明，再放不下就留空白。不会写成半截，也不会留下原文。写出前会清空文档属性（标题、作者、主题、关键词、创建者、制作者）和 XMP，并删除全部批注（含表单控件）、书签和嵌入附件。保存时使用 `garbage` 和 `clean`。命令行和窗口走同一个清理函数。

**PDF 图片。** 对有 xref 的位图取出像素，走和独立图片一样的 OCR 与涂黑，再用 `page.replace_image` 写回。没有独立 xref 的内联图片替换不到。

**独立图片。** 使用 Presidio 的 `ImageAnalyzerEngine` 和 `ImageRedactorEngine`，注入上面的 `AnalyzerEngine`，OCR 用 `pytesseract`，语言 `chi_sim+eng`。命中词涂黑，并稍微外扩几个像素，避免笔画露在框外。这一路不会调用会去下载 spaCy 大模型的默认分析器。

## Tesseract

查找顺序：

1. 环境变量 `TESSERACT_CMD`
2. 打包后的程序：可执行文件同目录下的 `tesseract/tesseract.exe`（或 `tesseract`），然后是 PyInstaller 解压目录 `_MEIPASS/tesseract/` 里的同名文件
3. `PATH`

如果可执行文件旁边有 `tessdata/` 目录，程序会把 `TESSDATA_PREFIX` 指到那里，这样打包后的程序不用再联网下载语言数据。Windows 免安装包把 Tesseract 放在 exe 同目录的 `tesseract\` 下，走的就是上面第 2 步，不用再设 `TESSERACT_CMD`。

本机示例：

```bash
TESSERACT_CMD=/path/to/tesseract pytest
```

找不到 Tesseract 时，图片相关测试会跳过并提示安装或设置 `TESSERACT_CMD`。

## 打包

不要为这个项目下载 spaCy 的 `en_core_web_lg` 或其他语言模型。Windows 产物由 `doc-redactor.spec` 生成：一次 onedir 构建，里面两个 `EXE`（命令行 `doc-redactor`，窗口 `doc-redactor-gui`），再由一个 `COLLECT` 收进同一个目录。两个程序共用这个目录里的依赖。spec 不用 `MERGE`，避免每个 exe 再去临时目录解压共享文件。

`uac_admin=False`，manifest 指向 `packaging/asInvoker.manifest`，其中 `requestedExecutionLevel` 的 `level` 是 `asInvoker`。`contents_directory` 是 `.`，收集到的文件（包括 `tesseract/`）放在两个 exe 旁边。构建前要把 Tesseract 放到 `build/tesseract/`：`tesseract.exe`、DLL、`tessdata/eng.traineddata`、`tessdata/chi_sim.traineddata`。

```bash
pyinstaller --noconfirm --clean --distpath dist --workpath build/pyi doc-redactor.spec
```

体积会明显大于这几份脚本。主要来自 PyMuPDF、Presidio 带进来的 spaCy（即使不加载语言模型）、OpenCV、Pillow，以及 Tesseract 和 `eng` / `chi_sim` 语言数据。Tesseract 本身是 Apache-2.0；构建时用的 `eng.traineddata` 和 `chi_sim.traineddata` 来自 [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast)，许可证以该仓库的声明为准（Apache-2.0）。

## Windows 构建

`.github/workflows/build-windows.yml` 在 `windows-latest` 上使用 Python 3.12：

1. `choco install tesseract`
2. 只在构建机上，从 `github.com/tesseract-ocr/tessdata_fast` 下载 `eng.traineddata` 和 `chi_sim.traineddata`，连同 `tesseract.exe` 和 DLL 放到 `build/tesseract/`
3. `pip install -r requirements.txt`
4. `pytest`
5. 用 `doc-redactor.spec` 打 onedir，得到 `dist/doc-redactor/`（两个 exe 和 `tesseract/`）
6. `tools/make_windows_zip.py` 检查 manifest 是 `asInvoker`、没有 `requireAdministrator`，并确认 `tesseract.exe`、DLL 和两种语言数据都在，然后打成 `doc-redactor-windows.zip`，写出 `SHA256SUMS.txt`
7. `upload-artifact` 上传这两个文件，产物名是 `doc-redactor-windows`

另一个 job 下载这份产物，核对 SHA256，解压到 `%USERPROFILE%\apps`（不是 Program Files），清掉 PATH 里的 Tesseract 以及 `TESSERACT_CMD` 和 `PYTHONPATH`，只用包内的 `doc-redactor.exe` 对 `samples/` 里的 docx、pdf、png 脱敏。然后 `tools/check_redacted_output.py` 检查 docx 文本、PDF 文本，并用包内 Tesseract 对 png 做 OCR，确认 `ABC-2026-001`、`Acme Pharma`、`Fictizumab` 这些原词已经不在，`report.json` 里也没有原文。任何一步失败，job 就失败。

构建机需要联网安装依赖和语言数据。打好的程序在运行时不再下载模型或语言数据，也不写注册表。

## 测试

```bash
TESSERACT_CMD=/path/to/tesseract PYTHONPATH=src pytest
```

覆盖规则加载、词表大小写、docx（正文、表格、嵌套表格、页眉页脚、跨 run、文本框、简单域、内容控件、超链接、修订插入与删除，断言直接读输出 XML）、PDF 文本删除（含文档属性、XMP、批注、书签、表单、附件里不再留下原词）并写入占位符（放不下会缩小或换成短码，不再截成半截）、报告不含原文也不含占位符、脱敏后再次 OCR 确认原词不可读，以及规则新增/导入（重名跳过、旧 yaml 保留大小写、缺省不区分、无 replacement 字段走默认表）、撤销 10 分钟、常用库 7 条（方案号清单 + 字母加数字门控）、照样写、命中过多阈值（测试框 20/21、单文件 500/501）、词表重复词、取消勾选后写出数一致。同一类多处命中写成同一个占位符，不编号。

## 限制

- Word 的正文、表格、页眉页脚里，文本框（新旧两种存法都改）、简单域的显示结果、内容控件（含包着表格行和单元格的）、超链接、修订插入的文字都会扫描和替换。修订删除的文字（`w:delText`）也还在文件里：按修订前的读法（保留的文字加删除的文字，不含插入的）扫描，碰到删除文字的命中会单独列出（位置写作「第 N 段 · 修订删除」），写出时把删除文字里的那一段换成占位符，删除标记保留。一个词一半在保留文字、一半在删除文字里，也能扫到；写出时只改删除的那一半。
- Word 的脚注、尾注、批注不覆盖。域代码本身（`w:instr`、`w:instrText`，例如 DOCPROPERTY 或链接地址）不扫描；Word 更新域时，可能按文档属性重新填出原词。修订的作者名和时间不清。
- PDF 写出时会去掉文档属性、XMP、批注（含表单）、书签和嵌入附件。
- PDF 里被拆开、写成矢量路径，或和 `get_text()` 对不上的词，`search_for` 可能找不到，文字就还在。
- 图片只处理 `png` / `jpg` / `jpeg`。docx 里的 emf、wmf 等不处理。
- PDF 里没有独立 xref 的内联图片可能无法写回。
- OCR 会漏字，也会把框画大。涂黑不能当成所有扫描件都已经不可读。
- 不做 `.doc`、`.xlsx`、`.pptx`，不做自动人名识别，不做备份或回滚。
- 界面不做「项目范围 / 适用范围」，规则一律作用于全部文件。
- 删除规则的撤销只存在本次运行的内存里，超过 10 分钟或关掉窗口就没了。
- 没有 `tkinterdnd2` 时不能把文件拖进窗口，请用「选择文件…」。
- 界面字体按 微软雅黑 → Noto Sans CJK SC → 回退。Windows 用微软雅黑；Linux 本机若只有 Noto/WenQuanYi，会走这一档。
- PDF 预览是抽出的文字，不是原版式页面图。
- 测试框命中超过 20 处、单文件命中超过 500 处只提醒，不拦住保存或写出；界面只写实际命中数，不写这两个阈值。

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
