"""Fictional preview data for screenshot hooks. Nothing here is a real protocol."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from doc_redactor.scan import FileScan, KIND_IMAGE, KIND_PDF, KIND_WORD, LocatedHit, MSG_ENCRYPTED_PDF


def _hit(hid, cat, text, loc, unit, start, end, enabled=True, **extra) -> LocatedHit:
    return LocatedHit(
        hit_id=hid,
        category=cat,
        text=text,
        location=loc,
        enabled=enabled,
        unit=unit,
        start=start,
        end=end,
        **extra,
    )


def demo_word_paragraphs() -> list[str]:
    return [
        "访视记录表",
        "项目编号：PRJ-2026-017　　申办方：华曜医药有限公司",
        "受试者 012 于 2026 年 9 月 12 日完成第 3 次访视，研究药物瑞沃替尼片依从性良好。",
        "本次访视按 PRJ-2026-017 方案 V2.1 执行，未发现新的安全性事件。",
        "监查员已向华曜医药提交访视摘要，参见附件 PRJ-2026-017-A3。",
        "备注：瑞沃替尼的库存核对由研究药师完成，项目号 PRJ-2026-017。",
    ]


def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    found = []
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return found
        found.append((i, i + len(needle)))
        start = i + len(needle)


def demo_scans(*, many: bool = False, nohit: bool = False, all_fail: bool = False) -> list[FileScan]:
    word_path = Path("访视记录_受试者012.docx")
    pdf_path = Path("入组统计报告_V2.pdf")
    img_path = Path("现场照片_0912.png")
    enc_path = Path("旧版协议_加密.pdf")
    empty_path = Path("随访表_模板.docx")

    paras = demo_word_paragraphs()
    hid = 1
    word_hits: list[LocatedHit] = []
    mapping = [
        (1, "项目号", "PRJ-2026-017"),
        (1, "申办方", "华曜医药有限公司"),
        (2, "药名", "瑞沃替尼片"),
        (3, "项目号", "PRJ-2026-017"),
        (4, "申办方", "华曜医药"),
        (4, "项目号", "PRJ-2026-017-A3"),
        (5, "药名", "瑞沃替尼"),
        (5, "项目号", "PRJ-2026-017"),
    ]
    used: dict[tuple[int, str], int] = {}
    for para_i, cat, text in mapping:
        key = (para_i, text)
        nth = used.get(key, 0)
        spans = _spans(paras[para_i], text)
        start, end = spans[nth]
        used[key] = nth + 1
        word_hits.append(
            _hit(hid, cat, text, f"第 {para_i + 1} 段", f"p:{para_i}", start, end)
        )
        hid += 1

    pdf_pages = [
        [
            "入组统计报告（V2）",
            "PRJ-2026-017 中期统计分析",
            "申办方：华曜医药有限公司",
            "报告日期：2026 年 10 月 8 日",
            "本报告汇总截至数据截止日的入组情况。",
        ],
        [
            "1. 入组概况",
            "截至 9 月 30 日，PRJ-2026-017 共筛选 214 例，入组 168 例。",
            "其中使用瑞沃替尼片组 84 例，对照组 84 例。",
            "各中心入组例数见表 1。",
            "PRJ-2026-017-S02 中心入组速度最快。",
        ],
    ]
    pdf_hits: list[LocatedHit] = []
    pdf_map = [
        (0, 1, "项目号", "PRJ-2026-017", (40, 70, 220, 86)),
        (0, 2, "申办方", "华曜医药有限公司", (40, 100, 68, 116)),
        (1, 1, "项目号", "PRJ-2026-017", (40, 140, 220, 156)),
        (1, 2, "药名", "瑞沃替尼片", (40, 170, 64, 186)),
        (1, 4, "项目号", "PRJ-2026-017-S02", (40, 200, 48, 208)),
    ]
    for page, line, cat, text, rect in pdf_map:
        src = pdf_pages[page][line]
        start = src.find(text)
        pdf_hits.append(
            _hit(
                hid,
                cat,
                text,
                f"第 {page + 1} 页 · 第 {line + 1} 行",
                f"page:{page}",
                start,
                start + len(text),
                page=page,
                rects=[rect],
            )
        )
        hid += 1

    image = _sample_image()
    img_hits = [
        _hit(
            hid,
            "项目号",
            "PRJ-2026-017",
            "图内 第 1 行",
            "image",
            0,
            0,
            boxes=[(150, 28, 156, 26)],
        ),
        _hit(
            hid + 1,
            "申办方",
            "华曜医药有限公司",
            "图内 第 2 行",
            "image",
            0,
            0,
            boxes=[(150, 78, 160, 26)],
        ),
        _hit(
            hid + 2,
            "项目号",
            "PRJ-2026-017",
            "图内 第 4 行",
            "image",
            0,
            0,
            boxes=[(150, 178, 156, 26)],
        ),
    ]

    word = FileScan(path=word_path, kind=KIND_WORD, status="ok", hits=word_hits, paragraphs=paras, size_label="86 KB")
    pdf = FileScan(path=pdf_path, kind=KIND_PDF, status="ok", hits=pdf_hits, pages=pdf_pages, size_label="1.2 MB")
    img = FileScan(path=img_path, kind=KIND_IMAGE, status="ok", hits=img_hits, image=image, size_label="2.4 MB")
    enc = FileScan(path=enc_path, kind=KIND_PDF, status="fail", error=MSG_ENCRYPTED_PDF, size_label="640 KB")
    empty = FileScan(
        path=empty_path,
        kind=KIND_WORD,
        status="nohit",
        paragraphs=["随访表（模板）", "受试者编号：＿＿＿＿　　访视日期：＿＿＿＿"],
        size_label="41 KB",
    )

    if many:
        extra = []
        for i in range(501):
            extra.append(
                _hit(
                    1000 + i,
                    "项目号",
                    "PRJ-2026-017",
                    f"第 {i + 1} 页 · 第 1 行",
                    "page:0",
                    0,
                    12,
                    page=0,
                    rects=[(72, 80, 200, 100)],
                )
            )
        pdf.hits = extra
        pdf.too_many = True

    if nohit:
        for scan in (word, pdf, img, empty):
            scan.hits = []
            scan.status = "nohit"
            scan.too_many = False

    if all_fail:
        for scan in (word, pdf, img, enc, empty):
            scan.status = "fail"
            scan.error = scan.error or "打不开这个文件。"
            scan.hits = []

    return [word, pdf, img, enc, empty]


def _sample_image() -> Image.Image:
    image = Image.new("RGB", (480, 330), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 16, index=0
        )
        font_b = ImageFont.truetype(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 18, index=0
        )
    except OSError:
        font = ImageFont.load_default()
        font_b = font
    draw.text((30, 12), "样本交接单", fill="#1f2124", font=font_b)
    rows = [
        (44, "项目编号", "PRJ-2026-017"),
        (94, "申办方", "华曜医药有限公司"),
        (144, "受试者", "012"),
        (194, "样本袋", "PRJ-2026-017 / 03"),
        (244, "采样日期", "2026-09-12"),
    ]
    for y, label, value in rows:
        draw.text((30, y), label, fill="#62656b", font=font)
        draw.text((150, y), value, fill="#1f2124", font=font)
    return image


def demo_files() -> list[Path]:
    return [
        Path("访视记录_受试者012.docx"),
        Path("入组统计报告_V2.pdf"),
        Path("现场照片_0912.png"),
        Path("旧版协议_加密.pdf"),
        Path("随访表_模板.docx"),
    ]


# ---------------------------------------------------------------- 去掉页眉页脚
# Placeholders only: the signing envelope number is all zeros.
DEMO_SIGN = "DocuSign 信封号：00000000-0000-0000-0000-000000000000"
DEMO_PDF_HEAD = "PRJ-2026-017 中期统计分析　版本 2.0　2026-10-08"
DEMO_WORD_HEAD = "PRJ-2026-017　访视记录　版本 2.1"
DEMO_WORD_FOOT = "华曜医药 机密　第 1 页 / 共 3 页"


def _page_hits(lines, mapping, page, hid, roles):
    """Hits with page-text offsets, as the scanner stores them."""
    text = "\n".join(lines)
    hits = []
    used: dict[str, int] = {}
    for cat, needle, *line in mapping:
        floor = used.get(needle, 0)
        if line:
            floor = max(floor, len("\n".join(lines[: line[0]])) + (1 if line[0] else 0))
        start = text.find(needle, floor)
        if start < 0:
            continue
        used[needle] = start + len(needle)
        line_no = text[:start].count("\n")
        hits.append(
            _hit(
                hid,
                cat,
                needle,
                f"第 {page + 1} 页 · 第 {line_no + 1} 行",
                f"page:{page}",
                start,
                start + len(needle),
                page=page,
                rects=[(40, 60 + 30 * line_no, 220, 76 + 30 * line_no)],
                hf_role=roles.get(line_no, ""),
            )
        )
        hid += 1
    return hits, hid


def demo_hf_scans(mode: str = "tag") -> list[FileScan]:
    """demo_scans with headers/footers in Word file 1 and PDF file 2.

    mode: "tag" (PDF has tags), "auto" (found by repetition), "none"
    (nothing found). Other files are unchanged.
    """
    from doc_redactor import header_footer as hf

    scans = demo_scans()
    word, pdf = scans[0], scans[1]
    body = list(word.paragraphs)
    paras = body + [DEMO_WORD_HEAD, DEMO_WORD_FOOT]
    head_i, foot_i = len(body), len(body) + 1
    hid = max(h.hit_id for s in scans for h in s.hits) + 1
    extra = [
        _hit(hid, "项目号", "PRJ-2026-017", f"第 {head_i + 1} 段", f"p:{head_i}", 0, 12, hf_role=hf.ROLE_HEADER),
        _hit(hid + 1, "申办方", "华曜医药", f"第 {foot_i + 1} 段", f"p:{foot_i}", 0, 4, hf_role=hf.ROLE_FOOTER),
    ]
    hid += 2
    word.paragraphs = paras
    word.hits = list(word.hits) + extra
    word.hf_route = hf.ROUTE_WORD
    word.hf_pages = 3
    word.hf_watermark = True
    word.hf_watermark_text = "机密"
    word.hf_units = {f"p:{head_i}": hf.ROLE_HEADER, f"p:{foot_i}": hf.ROLE_FOOTER}

    body_pages = [
        [
            "入组统计报告（V2）",
            "PRJ-2026-017 中期统计分析",
            "申办方：华曜医药有限公司",
            "报告日期：2026 年 10 月 8 日",
            "本报告汇总截至数据截止日的入组情况。",
        ],
        [
            "1. 入组概况",
            "截至 9 月 30 日，PRJ-2026-017 共筛选 214 例，入组 168 例。",
            "其中使用瑞沃替尼片组 84 例，对照组 84 例。",
            "各中心入组例数见表 1。",
            "PRJ-2026-017-S02 中心入组速度最快。",
        ],
    ]
    body_map = [
        [("项目号", "PRJ-2026-017"), ("申办方", "华曜医药有限公司")],
        [("项目号", "PRJ-2026-017"), ("药名", "瑞沃替尼片"), ("项目号", "PRJ-2026-017-S02")],
    ]
    pages, hits, lines_role = [], [], {}
    for page, lines in enumerate(body_pages):
        footer = f"华曜医药 CONFIDENTIAL　Page {page + 1}/2"
        full = [DEMO_SIGN, DEMO_PDF_HEAD] + lines + [footer]
        roles = {0: hf.ROLE_SIGNATURE, 1: hf.ROLE_HEADER, len(full) - 1: hf.ROLE_FOOTER}
        if mode == "none":
            roles_for_hits = {}
        else:
            roles_for_hits = roles
            for line_no, role in roles.items():
                lines_role[(page, line_no)] = role
        mapping = [("项目号", "PRJ-2026-017")] + body_map[page] + [("申办方", "华曜医药", len(full) - 1)]
        found, hid = _page_hits(full, mapping, page, hid, roles_for_hits)
        hits.extend(found)
        pages.append(full)
    pdf.pages = pages
    pdf.hits = hits
    pdf.hf_lines = lines_role
    pdf.hf_route = {"tag": hf.ROUTE_TAG, "auto": hf.ROUTE_REPEAT, "none": hf.ROUTE_NONE}[mode]
    pdf.hf_pages = None if mode == "none" else 2
    return scans
