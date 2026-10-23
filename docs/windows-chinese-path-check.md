# 在 Windows 上检查中文路径

你要在 Windows 上跑这次 Actions 打出的免安装包。同一份带图片的虚构文档，只改目录和文件名。扫描要通过，写出的文件里三个原词都不在，原来的文件名要留下来。

Mac 上的 pytest 通过不能代替这份检查。这份检查没做完之前，不要把 Windows 上的问题写成已经修好。

用这三个虚构词，不要用真实病历。

- `ABC-2026-001`
- `Acme Pharma`
- `Fictizumab`

## 确认压缩包是这次提交

1. 打开这个 pull request 的 Actions 运行，不要打开 run `39380802965`。
2. 运行页上的提交必须不是 `94e430f`。`94e430f` 是上一包，里面没有这次的图片暂存。
3. 下载产物 `doc-redactor-windows`。里面有 `doc-redactor-windows.zip` 和 `SHA256SUMS.txt`。
4. 在放着这两个文件的目录里运行下面的命令。`Hash` 要和 `SHA256SUMS.txt` 里的值一致，大小写不论。

```powershell
Get-FileHash -Algorithm SHA256 .\doc-redactor-windows.zip
Get-Content .\SHA256SUMS.txt
```

5. 解压到带中文的用户目录。不要放进 `C:\Program Files`。

```powershell
Expand-Archive .\doc-redactor-windows.zip -DestinationPath "$env:USERPROFILE\中文 路径"
```

解压后使用 `$env:USERPROFILE\中文 路径\doc-redactor\doc-redactor-gui.exe`。不要把 exe 单独拷走。不要设置 `TESSERACT_CMD`。识别用包里的 `tesseract\tesseract.exe`。

## 准备同一份文件

先做一张图，再做一份文档，然后只复制。四次检查必须是同一批字节。

1. 打开画图。白底，黑字，三行。

```text
Project ABC-2026-001
Sponsor Acme Pharma
Drug Fictizumab
```

2. 把图存成 `card.png`。
3. 打开 Word。新建文档，正文写下同样三行。插入 `card.png`。另存为 `master.docx`。
4. 再把这份 Word 另存为 `card.pdf`。正文和图都要在。
5. 复制文档，不要另存时改内容。

```powershell
$root = "$env:USERPROFILE\ocr-check"
New-Item -ItemType Directory -Force -Path "$root\english","$root\中文目录","$root\中文 目录" | Out-Null
Copy-Item .\master.docx "$root\english\CAPA回复1.docx"
Copy-Item .\master.docx "$root\中文目录\report.docx"
Copy-Item .\master.docx "$root\中文 目录\CAPA回复1.docx"
Copy-Item .\card.png "$root\card.png"
Copy-Item .\card.pdf "$root\card.pdf"
```

`CAPA回复1.docx` 这个名字要保留。检查失败时不要把它改成英文名再试。

## 准备规则

常用规则库里的「项目号」匹配的是字面的 `AAA-####-###`，匹配不到 `ABC-2026-001`。申办方和药名可以走常用库。

1. 打开 `doc-redactor-gui.exe`。
2. 进入规则设置。点「从常用规则库添加」。勾上「申办方」和「药名」。点添加。
3. 点「新增规则」。规则名填 `项目号`。例子框里只填 `ABC-2026-001`。例子下面那一行留空。点「确定」。
4. 回到文件页之前，这三条规则都是启用的。

## 扫描并写出

每一条都用新的输出目录，避免和上一条混在一起。窗口里点「添加文件…」，选这一条的文件。点「开始扫描」。扫描通过后，点「写出 N 个脱敏文件」。

通过的标准有四条。

- 扫描页没有失败句。
- 输出目录里有脱敏文件。docx 是 `CAPA回复1.redacted.docx` 或 `report.redacted.docx`。pdf 是 `card.redacted.pdf`。png 是 `card.redacted.png`。
- 原来的输入文件还在，名字没变。
- 写出的文件里三个原词都不在。正文用 Word 或浏览器打开后搜索。图片用包里的 tesseract 认，下面有命令。

认图片时忽略空格和换行。认出来的字里仍然能拼出这三个词，就算没过。

```powershell
$tess = "$env:USERPROFILE\中文 路径\doc-redactor\tesseract\tesseract.exe"
& $tess .\picture.png stdout -l chi_sim+eng
```

docx 里的图要先拿出来。把 `CAPA回复1.redacted.docx` 复制成 zip，解压，对 `word\media` 里的图运行上面的命令。

按这个顺序做。

1. 英文目录加中文文件名。输入是 `english\CAPA回复1.docx`。
2. 中文目录加英文文件名。输入是 `中文目录\report.docx`。
3. 中文目录加中文文件名。输入是 `中文 目录\CAPA回复1.docx`。
4. 中文临时目录。先关掉已经打开的窗口。在同一个 PowerShell 窗口里设置临时目录，再从这里启动程序，然后重复第 3 条。从开始菜单打开的窗口看不到这两个变量。

```powershell
$env:TEMP = "$env:USERPROFILE\临时 目录"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& "$env:USERPROFILE\中文 路径\doc-redactor\doc-redactor-gui.exe"
```

第 4 条如果仍然失败，句子必须是「有 1 张图片识别文字时出错，没法检查，未写出」。输出目录里不能有这份文件的 `.redacted.docx`。这句话如果写成「打不开」，这次修复没有生效。

第 4 条如果扫描并写出成功，你不会看到识别失败那句。这是预期。不要为了看到那句去改文件名。

## 检查打不开的图片

用第 3 条那份能打开的 docx 做一份坏副本。不要改 `中文 目录\CAPA回复1.docx` 本身。

```powershell
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$src = "$env:USERPROFILE\ocr-check\中文 目录\CAPA回复1.docx"
$bad = "$env:USERPROFILE\ocr-check\broken"
New-Item -ItemType Directory -Force -Path $bad | Out-Null
Copy-Item -LiteralPath $src -Destination "$bad\CAPA回复1.docx"
$zip = [System.IO.Compression.ZipFile]::Open("$bad\CAPA回复1.docx", "Update")
$names = @($zip.Entries | Where-Object { $_.FullName -like "word/media/*" } | ForEach-Object { $_.FullName })
if ($names.Count -lt 1) { $zip.Dispose(); throw "word/media 里没有图片，这份坏样本不能用" }
foreach ($name in $names) {
    $zip.GetEntry($name).Delete()
    $entry = $zip.CreateEntry($name)
    $stream = $entry.Open()
    $bytes = [Text.Encoding]::ASCII.GetBytes("not-an-image")
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Close()
}
$zip.Dispose()
```

扫描 `broken\CAPA回复1.docx`。必须看到这句。

```text
有 1 张图片打不开，没法检查，未写出
```

输出目录里不能出现 `CAPA回复1.redacted.docx`。不能是「识别文字时出错」。

## 检查 PDF 和 PNG

对 `card.pdf` 和 `card.png` 做和 docx 相同的扫描、写出、核对。三个原词在正文和图里都不在。PNG 直接对 `card.redacted.png` 运行上面的 tesseract 命令。PDF 先在浏览器里搜索正文。图如果能另存出来，再用 tesseract 认一遍。

PDF 某一页识别失败时，程序可以留下部分脱敏文件，并写明是哪一页哪一张。这和 Word 不同。Word 只要有一张图打不开或识别失败，整份都不写出。

## 记下结果

每一条记下输入路径、屏幕上的原句、输出文件名、三个原词还在不在。真实的 `CAPA回复1.docx` 可以另测一次，但不要把它的内容写进这份记录以外的地方。那份原件如果是坏图、EMF 或 WMF，正确结果仍是「打不开」，并且不写出。

把记录和 zip 的 SHA256 一起留下。SHA256 对上、四条路径都写出、坏图被拦住，这份 Windows 检查才算过。
