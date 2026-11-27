"""把 docs/技术方案.md 转成 Word（含插图、表格、代码块、页码、目录域）。

用法：python tools/build_docx.py
输出：docs/技术方案.docx
"""

from __future__ import annotations

import pathlib
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = pathlib.Path(r"D:\video_summary")
SRC = ROOT / "docs" / "技术方案.md"
DST = ROOT / "docs" / "技术方案.docx"

BODY_FONT = "宋体"
HEAD_FONT = "微软雅黑"
MONO_FONT = "Consolas"

INK = RGBColor(0x1C, 0x1A, 0x18)
MUTED = RGBColor(0x6D, 0x63, 0x5A)
ACCENT = RGBColor(0x8C, 0x3B, 0x2E)


# --------------------------------------------------------------------- 小工具
def set_cjk(run, font: str):
    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)


def shade(paragraph, hex_fill: str):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    pPr.append(shd)


def field(paragraph, instr: str, placeholder: str = ""):
    run = paragraph.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    run._r.append(fld)

    run2 = paragraph.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    run2._r.append(it)

    run3 = paragraph.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run3._r.append(sep)

    if placeholder:
        paragraph.add_run(placeholder)

    run5 = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run5._r.append(end)


INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def add_inline(par, text: str, size: float = 10.5, bold: bool = False,
               color: RGBColor = INK):
    """处理 **粗体**、`代码`、[文字](链接)。"""
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            r = par.add_run(piece[2:-2])
            r.bold = True
            set_cjk(r, BODY_FONT)
            r.font.size = Pt(size)
            r.font.color.rgb = color
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            r = par.add_run(piece[1:-1])
            set_cjk(r, MONO_FONT)
            r.font.size = Pt(size - 1)
            r.font.color.rgb = RGBColor(0x7A, 0x3E, 0x2E)
        elif piece.startswith("[") and "](" in piece:
            label, url = piece[1:-1].split("](", 1)
            r = par.add_run(label)
            set_cjk(r, BODY_FONT)
            r.font.size = Pt(size)
            r.font.color.rgb = ACCENT
            r.font.underline = True
            if url.startswith("http"):
                r2 = par.add_run(f" ({url})")
                r2.font.size = Pt(size - 1.5)
                r2.font.color.rgb = MUTED
                set_cjk(r2, BODY_FONT)
        else:
            r = par.add_run(piece)
            r.bold = bold
            set_cjk(r, BODY_FONT)
            r.font.size = Pt(size)
            r.font.color.rgb = color
    return par


# --------------------------------------------------------------------- 文档骨架
def new_document() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = Cm(2.2)
    sec.bottom_margin = Cm(2.0)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.line_spacing = 1.45
    normal.paragraph_format.space_after = Pt(4)

    for name, size, color, before, after in (
        ("Heading 1", 17, ACCENT, 20, 8),
        ("Heading 2", 13.5, INK, 14, 5),
        ("Heading 3", 11.5, INK, 10, 4),
    ):
        st = doc.styles[name]
        st.font.name = HEAD_FONT
        st.element.rPr.rFonts.set(qn("w:eastAsia"), HEAD_FONT)
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True
    return doc


def add_footer(doc):
    p = doc.sections[0].footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("视频总结工具 · 技术方案　|　第 ")
    set_cjk(r, BODY_FONT)
    r.font.size = Pt(8.5)
    r.font.color.rgb = MUTED
    field(p, "PAGE", "1")
    r = p.add_run(" 页 / 共 ")
    set_cjk(r, BODY_FONT)
    r.font.size = Pt(8.5)
    r.font.color.rgb = MUTED
    field(p, "NUMPAGES", "1")
    r = p.add_run(" 页")
    set_cjk(r, BODY_FONT)
    r.font.size = Pt(8.5)
    r.font.color.rgb = MUTED


# --------------------------------------------------------------------- 转换
def emit_table(doc, rows: list[list[str]]):
    header, body = rows[0], rows[1:]
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    tblPr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    # 表头跨页重复
    trPr = table.rows[0]._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:tblHeader"))

    hdr = table.rows[0].cells
    for i, cell_text in enumerate(header):
        p = hdr[i].paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        add_inline(p, cell_text, size=9.5, bold=True)
        shade(p, "F2EDE4")

    for row in body:
        cells = table.add_row().cells
        for i, cell_text in enumerate(row[: len(header)]):
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.3
            add_inline(p, cell_text, size=9)

    # 按内容长度分配列宽（总宽 16.4cm，单列 1.5–6.5cm）
    total = 16.4
    weights = []
    for c in range(len(header)):
        cells_c = [re.sub(r"`|\*\*", "", r[c]) for r in rows if c < len(r)]
        longest = max([max((len(tok) for tok in cell.split()), default=3) + 2 for cell in cells_c] + [5])
        weights.append(min(max(longest, 5), 30))
    scale = total / sum(weights)
    widths = [Cm(max(2.0, min(6.4, w * scale))) for w in weights]
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for cell, w in zip(row.cells, widths):
            cell.width = w
    # gridCol 也要给宽度，否则 Word 仍按内容自适应
    grid = table._tbl.find(qn("w:tblGrid"))
    if grid is not None:
        for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
            gc.set(qn("w:w"), str(int(w.twips)))

    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def emit_code(doc, lines: list[str]):
    for line in lines:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.left_indent = Cm(0.4)
        shade(p, "F7F5F1")
        r = p.add_run(line if line.strip() else " ")
        set_cjk(r, MONO_FONT)
        r.font.size = Pt(8.5)
        r.font.color.rgb = RGBColor(0x2E, 0x2A, 0x26)


def emit_image(doc, path: pathlib.Path, caption: str, number: str):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(3)
    p.add_run().add_picture(str(path), width=Cm(16.4))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(10)
    r = cap.add_run(f"图 {number}　{caption}")
    set_cjk(r, HEAD_FONT)
    r.font.size = Pt(9)
    r.font.color.rgb = MUTED


def convert(md: str) -> Document:
    doc = new_document()
    add_footer(doc)
    lines = md.splitlines()
    i = 0
    fig_no = 0
    first_h1 = True

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # 代码块
        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i].rstrip())
                i += 1
            i += 1
            emit_code(doc, buf)
            continue

        # 图片
        m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if m:
            alt, rel = m.group(1), m.group(2)
            path = (SRC.parent / rel).resolve()
            fig_no += 1
            emit_image(doc, path, alt, str(fig_no))
            i += 1
            continue

        # 表格
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not re.match(r"^[\s:|-]+$", "".join(cells)):
                    rows.append(cells)
                i += 1
            if rows:
                emit_table(doc, rows)
            continue

        # 标题
        if line.startswith("# "):
            title = line[2:].strip()
            if first_h1:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(60)
                p.paragraph_format.space_after = Pt(6)
                r = p.add_run(title)
                set_cjk(r, HEAD_FONT)
                r.font.size = Pt(26)
                r.font.bold = True
                r.font.color.rgb = INK
                first_h1 = False
            else:
                doc.add_heading(title, level=1)
            i += 1
            continue
        for level, prefix in ((2, "## "), (3, "### "), (4, "#### ")):
            if line.startswith(prefix):
                doc.add_heading(line[len(prefix):].strip(), level=level - 1)
                break
        else:
            if line.startswith("#### "):
                pass
        if line.startswith("#"):
            i += 1
            continue

        # 分隔线 → 空一行
        if re.match(r"^---+\s*$", line):
            i += 1
            continue

        # 引用
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(6)
            shade(p, "F7F5F1")
            add_inline(p, " ".join(buf), size=9.5, color=RGBColor(0x4B, 0x42, 0x38))
            continue

        # 列表
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            indent = len(m.group(1)) // 2
            ordered = m.group(2) not in "-*+"
            style = "List Bullet" if not ordered else None
            p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75 + 0.6 * indent)
            p.paragraph_format.first_line_indent = Cm(-0.55) if ordered else None
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.4
            if ordered:
                # 手写编号，避免 Word 列表编号跨小节继续累加
                r = p.add_run(f"{m.group(2)} ")
                set_cjk(r, BODY_FONT)
                r.font.size = Pt(10.5)
                r.bold = True
                r.font.color.rgb = ACCENT
            add_inline(p, m.group(3), size=10.5)
            i += 1
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 普通段落（连续非空行合并）
        buf = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i].rstrip()
            if (not nxt.strip() or nxt.startswith(("#", "|", ">", "```", "---"))
                    or re.match(r"^\s*([-*+]|\d+\.)\s+", nxt) or nxt.startswith("![")):
                break
            buf.append(nxt)
            i += 1
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.first_line_indent = Cm(0)
        add_inline(p, " ".join(buf), size=10.5)

    return doc


def add_meta_and_toc(doc: Document):
    """在标题后插入副标题、目录页（域，打开时按 F9 更新）。"""
    from docx.text.paragraph import Paragraph

    def append_after(anchor_p, parent):
        el = OxmlElement("w:p")
        anchor_p.addnext(el)
        return Paragraph(el, parent)

    lead = doc.paragraphs[0]
    parent = lead._parent
    anchor = lead._p

    p_info = append_after(anchor, parent)
    p_info.paragraph_format.space_before = Pt(10)
    p_info.paragraph_format.space_after = Pt(18)
    r = p_info.add_run("版本 v1.0.1　·　仓库 github.com/chenyubj1993-cloud/video-summary　·　"
                       "代码 6,645 行 / 29 个模块 / 63 个测试")
    set_cjk(r, BODY_FONT)
    r.font.size = Pt(9)
    r.font.color.rgb = MUTED

    p_brk = append_after(p_info._p, parent)
    p_brk.add_run().add_break(WD_BREAK.PAGE)

    toc_head = append_after(p_brk._p, parent)
    r = toc_head.add_run("目　录")
    set_cjk(r, HEAD_FONT)
    r.font.size = Pt(15)
    r.font.bold = True
    toc_head.paragraph_format.space_after = Pt(10)

    toc = append_after(toc_head._p, parent)
    field(toc, r'TOC \o "1-2" \h \z \u', "（在 Word 里按 Ctrl+A 然后 F9 更新目录）")
    toc.paragraph_format.space_after = Pt(14)

    p_stat = append_after(toc._p, parent)
    r = p_stat.add_run("全文 13 节 · 5 张框图 · 18 张表 · 21 页　|　"
                       "先看第 2 节看链路，第 5 节看取舍，第 8 节看问题，第 9 节看要做什么")
    set_cjk(r, BODY_FONT)
    r.font.size = Pt(9)
    r.font.color.rgb = MUTED
    p_stat.paragraph_format.space_after = Pt(6)

    page_break = append_after(p_stat._p, parent)
    page_break.add_run().add_break(WD_BREAK.PAGE)


def main():
    md = SRC.read_text(encoding="utf-8")
    doc = convert(md)
    add_meta_and_toc(doc)
    settings = doc.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)

    doc.core_properties.title = "视频总结工具 · 技术方案与代码架构"
    doc.core_properties.subject = "架构、关键决策、已知缺陷与优化清单"
    doc.core_properties.author = "chenyubj1993-cloud"
    doc.save(DST)

    reopen = Document(DST)
    print(f"已写 {DST}　段落 {len(reopen.paragraphs)}　表格 {len(reopen.tables)}　"
          f"图片 {len(reopen.inline_shapes)}　{Cm(21) and DST.stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main()
