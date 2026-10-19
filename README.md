# doc-redactor

离线脱敏小工具。它按你自己的规则，处理 Word（`.docx`）、PDF，以及 `png` / `jpg` / `jpeg`。正文、表格（含嵌套表格）、页眉页脚里的命中会换成该类的固定占位符（同一类不编号，例如药名一律 `[STUDY DRUG]`）；PDF 文本层里能定位到的命中会先真正删掉，再在原位置写入同一个占位符（放不下会先缩小字号，还放不下就换成短码，并在文末加一页说明）；图片里的命中用黑块盖住。

规则、词表和 `samples/` 里只有虚构数据，例如项目号 `ABC-2026-001`、申办方 `Acme Pharma`、药名 `Fictizumab`。不要把真实项目号、申办方或药名写进仓库。

运行时不下载模型，也不发起网络请求。

## 安装

需要 Python 3.10 或更高版本，以及本机的 Tesseract（语言数据至少要有 `eng` 和 `chi_sim`）。没有图片时可以不装 Tesseract；文件里一旦有图片，就会要求能找到 Tesseract，找不到时这个文件不写出并提示原因，不会跳过图片照写。

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
    pattern: '(?<![A-Za-z0-9])ABC-\d{4}-\d{3}(?![A-Za-z0-9])'
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
- 编号两边的边界请照上面的写法用 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])`。Python 的 `\b` 把汉字也算作单词字符，编号紧挨中文（如「受试者ABC-2026-001」）时不算边界，会整串漏掉。为防照网上的写法踩坑，工具会把规则里的 `\b` 自动换成「两边不同时是英文字母、数字或下划线」，`[\b]` 和 `\\b` 不动。
- 图片和扫描件里的字由 tesseract 识别。识别偶尔会把一个字认成形近字，所以只在识别出的文字上，词表里 4 个汉字以上、或不含汉字且 6 个字符以上的词允许错一个字。这条也管词表里的编号（如 `HT-2026-0815` 会连带遮住 `HT-2026-0816`）；不到 4 个汉字的词、含汉字但不到 4 个的词、正则规则（含编号规则）不放宽，按原样匹配。代价是和词表词只差一个字的别的词（同类药名、另一家医院）也会被遮。这类近似命中在结果页「内容」栏前面标「近似」，后面是 OCR 实际读出的字；这几个字只在本机窗口里临时显示，不写进任何文件，也不进命中清单。近似命中和其他命中一样默认勾选，可以取消。
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

整份文件一处命中都没有时，不写出这个文件，也不写报告，并在错误输出里提示「整份文件没有找到要遮的内容，没有写出……」，退出码为 1，免得把原件当成脱敏件交出去。

写出后还有一道复核：换几种读法（Word 的全部 XML 文字和属性、内层压缩包；PDF 的整页文字、链接、页标签、图层名和全部字符串对象）把输出再读一遍，任何一种读法还能读出命中，就删掉这个输出、提示「写出后复核：换一种读法仍能读出 N 处」，不放行。用户在界面上取消勾选的那几处不算。

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

**PDF 文本。** 对每一页用 Presidio 看 `page.get_text()` 的文本，再用 `page.search_for()` 找矩形，然后 `add_redact_annot` 和 `apply_redactions` 把文字从内容流里删掉，不是只盖一个黑框。删完后在原来的框里写入该类占位符；框太窄会先缩小字号（最小 6 磅），还放不下就换成该类短码，用过短码时在文末加一页替换说明，再放不下就留空白。不会写成半截，也不会留下原文。扫描整页，落在页面外或被裁切框裁掉的文字也算；行尾用连字符断开的词（如「Fictizu-」接「mab」）会拼起来再找。扫描和写出前先去掉标签结构（结构树）和标记内容上的「实际文字」（ActualText）、替代文字（Alt）、缩写展开（E），这样找的是页面上真正画出的字，写出的文件里也不留这些副本。写出前会清空文档属性（标题、作者、主题、关键词、创建者、制作者，以及自定义属性）和 XMP，删除全部批注（含表单控件）、链接、书签、页标签和嵌入附件，图层名改成「Layer 1」这类中性名字。保存时使用 `garbage` 和 `clean`。命令行和窗口走同一个清理函数。

**PDF 图片。** 对有 xref 的位图取出像素（带透明蒙版的先合成到白底），走和独立图片一样的 OCR 与涂黑，再用 `page.replace_image` 写回。写在内容流里的内联图片用 `get_image_info` 找出来，OCR 后用 MuPDF 的像素涂改把命中处涂黑，写完读回核对；涂不黑、或内联图是旋转/斜放的，这个文件不写出。图片解码失败时同样不写出。

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

覆盖规则加载、词表大小写、docx（正文、表格、嵌套表格、页眉页脚、跨 run、文本框、简单域、内容控件、超链接、修订插入与删除，断言直接读输出 XML）、PDF 文本删除（含文档属性、XMP、批注、书签、表单、附件里不再留下原词）并写入占位符（放不下会缩小或换成短码，不再截成半截）、报告不含原文也不含占位符、脱敏后再次 OCR 确认原词不可读，以及规则新增/导入（重名跳过、旧 yaml 保留大小写、缺省不区分、无 replacement 字段走默认表）、撤销 10 分钟、常用库 7 条（方案号清单 + 字母加数字门控）、照样写、命中过多阈值（测试框 20/21、单文件 500/501）、词表重复词、取消勾选后写出数一致。

出厂门禁（`tests/test_ai_read_gate.py`，CI 的 `ai-read-gate` 作业里必过）：`tools/ai_gate/make_samples.py` 生成一批编造样本（PDF 的花样文字、隐藏处、增量保存、带标签、整页图片的扫描件；Word 的正文和隐藏处、替代文字、嵌入对象、不显眼处），经命令行和界面两条路写出，再用 AI 程序最常用的读法读输出：PDF 用 PyMuPDF、pymupdf4llm（遇到扫描图用 RapidOCR 看图）和 pdftotext，Word 用 python-docx（含 core 属性）和 pandoc。任何一种读法读出 `tools/ai_gate/words.txt` 里的原词，或解析器出错，测试失败。CI 作业装齐这五种并设 `AI_GATE_STRICT=1`，缺一种也算失败；本地和 Windows 的测试步骤里没装的读法跳过。门禁要的额外包在 `requirements-gate.txt`（软件本身不依赖，也不打进包），pdftotext 来自 poppler-utils。本地单独跑：`python tools/ai_gate/make_samples.py in && python -m doc_redactor in/* -r tools/ai_gate/rules.yaml -o out && python tools/ai_gate/read_outputs.py out --strict`。Word 文档属性的清理在另一个改动里，样本暂不含。

## 限制

- Word 的正文、表格、页眉页脚里，文本框（新旧两种存法都改）、简单域的显示结果、内容控件（含包着表格行和单元格的）、超链接、修订插入的文字都会扫描和替换。修订删除的文字（`w:delText`）也还在文件里：按修订前的读法（保留的文字加删除的文字，不含插入的）扫描，碰到删除文字的命中会单独列出（位置写作「第 N 段 · 修订删除」），写出时把删除文字里的那一段换成占位符，删除标记保留。一个词一半在保留文字、一半在删除文字里，也能扫到；写出时只改删除的那一半。
- Word 的脚注、尾注正文，图片替代文字（`descr`、`title`、`name`，VML 的 `o:title`），域代码（`w:instr`、`w:instrText`，含 HYPERLINK 的地址），外部超链接地址，超链接提示文字，内容控件的别名和标签，修订的作者，样式名，都按同一套规则扫描和替换；位置写作「脚注 第 N 段」「图片替代文字 第 N 处」等。外部超链接地址（关系里的链接目标和 HYPERLINK 域代码里的地址）写出时不管有没有命中，一律整条换成 `https://redacted.invalid/`，链接的显示文字和 `\o` 提示等开关保留；地址里的写法（如 `acme-pharma.example`）规则不一定匹配得上，所以不在地址里逐词找。修订作者（含 people.xml 里的作者）写出时不管有没有命中，一律改成 `Author`，people.xml 里的账号信息（presenceInfo）删除。书签名和自建样式的内部 id 一律改成中性名字（`bm1`、`UserStyle1`），内部跳转和 REF 域跟着改；Word 自动生成的 `_Toc`、`_Ref` 这类书签不动。嵌入的对象（Excel 表、OLE `.bin`）写出时删除，只留预览图；altChunk（打开时才合进正文的 HTML 等）、文档变量（docVars）和模板路径也删除。只差下划线或连字符的变体（如 `Acme_Pharma`、`acme-pharma`）规则找不到，和正文一样。批注写出时整条删除（批注正文、回复、批注人清单一起删），结果页写明删了几条。修订的时间不清。文档属性（docProps）不在这里处理。
- Word 写出时清掉文档属性：core 里的作者、标题、主题、关键词、备注、最后修改者、类别、状态、标识、语言、版本；app.xml 里的公司、经理、超链接基础、超链接清单（`HLinks`）、模板名，以及 Word 存盘时抄进去的标题（`TitlesOfParts`，连同配套的 `HeadingPairs`）；custom.xml 里的全部自定义属性；首页缩略图；`settings.xml` 里挂的模板路径（`attachedTemplate`，路径里常带用户名）和 rsid；`customXml/item*.xml` 数据项里的内容（SharePoint 负责人之类，只留空的根元素，部件本身保留）。不清：创建/修改时间、修订号、总编辑时长、应用程序名和页数字数统计，以及修订记录本身和修订时间（修订作者名一律改成 `Author`）。
- PDF 写出时会去掉文档属性（含自定义属性）、XMP、批注（含表单）、链接、书签、页标签、嵌入附件、页缩略图（`/Thumb`）、`/PieceInfo`、标签结构和标记内容上的实际文字/替代文字，图层名改成中性名字。直接写在标记内容资源里（不是单独对象）的属性列表不逐个清理，只是不再被引用。
- PDF 里被拆开、写成矢量路径，或和 `get_text()` 对不上的词，`search_for` 可能找不到，文字就还在。
- 独立图片只处理 `png` / `jpg` / `jpeg`。docx 里的 gif、bmp、tiff 照常识别涂黑（动图和多页 tiff 只留第一帧）；带位图备份的 SVG 删掉 SVG、留位图；只有 SVG，或有 emf、wmf 等矢量图时，这个文件不写出。
- OCR 会漏字，也会把框画大。涂黑不能当成所有扫描件都已经不可读。
- 不做 `.doc`、`.xlsx`、`.pptx`，不做自动人名识别，不做备份或回滚。
- 界面不做「项目范围 / 适用范围」，规则一律作用于全部文件。
- 删除规则的撤销只存在本次运行的内存里，超过 10 分钟或关掉窗口就没了。
- 没有 `tkinterdnd2` 时不能把文件拖进窗口，请用「选择文件…」。
- 界面字体按 微软雅黑 → Noto Sans CJK SC → 回退。Windows 用微软雅黑；Linux 本机若只有 Noto/WenQuanYi，会走这一档。
- PDF 预览是抽出的文字，不是原版式页面图。
- 测试框命中超过 20 处、单文件命中超过 500 处只提醒，不拦住保存或写出；界面只写实际命中数，不写这两个阈值。
- 写出后会按扫描同样的规则重扫输出（PDF 用和扫描同一种抽字方式，Word 用同一套遍历，修订里每处命中只算一次）：命中完整地落在占位符、本文件用到的短码或「替换说明」页的说明行里才不算残留。原文里没有的命中，只有既不包含原文里的任何命中、也不是其中一部分时才不算（删词后前后文字拼成的新命中）；其余照样算残留。有残留或核对本身出错，就删掉这份输出、只拦这个文件；删不掉会提示手动删除并给出文件名，命令行退出码非 0。PDF 结构标签（`StructTreeRoot`）和替代文字（`ActualText`、`Alt`）在扫描和写出前就去掉，带标签的 PDF 不会因此被拦。已知查不出或会误拦的情况：遮罩只盖住半个词（比如留下 `e Pharma`）；同一个词一处勾选、一处取消，写出时又打错了位置（取消勾选按全文字数抵扣，不按段落或页）；原文本来就有和本文件所用短码一样的 `[SP]` 之类文字，而词表里正好有 `SP`；删词后前后文字拼成的命中正好在原文别处出现过，写对了的文件也会被拦。

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
