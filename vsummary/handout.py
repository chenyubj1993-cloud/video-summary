"""把 handout/ch*.md 拼成一份完整讲义（单文件 HTML + Markdown）。

用法：python cli.py handout BV1E9U8BBEvZ
产物：out/<bv>/讲义.html（双击即读，可打印）、out/<bv>/讲义.md
"""
from __future__ import annotations

import argparse
import html as H
import json
import re
from pathlib import Path

from vsummary.core import GLOSSARY, OUT, fmt, link, load_segments, seg_text
from vsummary import cite

CSS = """
:root{--ink:#1f2328;--muted:#6b7280;--line:#e5e7eb;--bg:#fdfdfb;--accent:#8b3a2e;
--code:#f4f1ec;--warn:#fff7e6;--warn-line:#e0b44a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.85 "Source Han Serif SC","Noto Serif SC","Songti SC",Georgia,serif;}
.wrap{display:grid;grid-template-columns:250px minmax(0,1fr);gap:56px;
max-width:1180px;margin:0 auto;padding:0 32px 120px;}
nav.toc{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;
padding:32px 0;font:14px/1.7 "Source Han Sans SC","Noto Sans SC",system-ui,sans-serif;}
nav.toc .t{font-weight:700;letter-spacing:.08em;color:var(--muted);font-size:12px;margin-bottom:12px}
nav.toc a{display:block;color:var(--ink);text-decoration:none;padding:3px 10px;border-left:2px solid transparent}
nav.toc a:hover{border-left-color:var(--accent);background:#f6f3ee}
nav.toc a.h1{margin-top:10px;font-weight:600}
nav.toc a.h2{color:var(--muted);padding-left:22px;font-size:13px}
main{padding-top:40px;min-width:0}
.cover{border-bottom:3px double var(--line);padding-bottom:28px;margin-bottom:44px}
.cover h1{font-size:34px;line-height:1.35;margin:0 0 10px;letter-spacing:-.01em}
.cover .orig{color:#8a8175;font:13px/1.7 "Source Han Sans SC",system-ui,sans-serif;margin:0 0 10px}
.cover .sub{color:var(--muted);font:15px/1.8 "Source Han Sans SC",system-ui,sans-serif;margin:0}
.cover .badges{margin-top:16px;display:flex;flex-wrap:wrap;gap:8px}
.badge{font:12px/1 "Source Han Sans SC",system-ui,sans-serif;color:var(--muted);
border:1px solid var(--line);border-radius:999px;padding:6px 12px;background:#fff}
h1.ch{font:700 26px/1.4 "Source Han Sans SC","Noto Sans SC",system-ui,sans-serif;
margin:64px 0 8px;padding-top:22px;border-top:1px solid var(--line)}
main h1{font:700 26px/1.4 "Source Han Sans SC","Noto Sans SC",system-ui,sans-serif;
margin:64px 0 8px;padding-top:22px;border-top:1px solid var(--line)}
main h1:first-of-type{border-top:0;margin-top:8px;padding-top:0}
h2{font:600 20px/1.5 "Source Han Sans SC","Noto Sans SC",system-ui,sans-serif;margin:38px 0 12px}
h3{font:600 17px/1.5 "Source Han Sans SC",system-ui,sans-serif;margin:26px 0 8px}
p{margin:12px 0}
ul,ol{padding-left:1.5em}li{margin:6px 0}
blockquote{margin:18px 0;padding:10px 18px;border-left:3px solid var(--accent);
background:#faf6f1;color:#4b4238;font-size:15px}
code{font:13.5px/1.5 "JetBrains Mono",Consolas,monospace;background:var(--code);
padding:1.5px 5px;border-radius:4px}
pre{background:var(--code);padding:14px 16px;border-radius:8px;overflow:auto;border:1px solid var(--line)}
pre code{background:none;padding:0;font-size:13px}
table{border-collapse:collapse;width:100%;margin:18px 0;font:14.5px/1.7 "Source Han Sans SC",system-ui,sans-serif}
th,td{border:1px solid var(--line);padding:8px 12px;text-align:left;vertical-align:top}
th{background:#f6f3ee;font-weight:600}
tbody tr:nth-child(even){background:#fbfaf7}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid #d9c3bd}
a:hover{background:#f7ece8}
a.cite{font:11.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);
text-decoration:none;border-bottom:1px dotted var(--accent);white-space:nowrap;padding:0 1px}
a.cite.bad{color:#b42318;background:#fff1f0;border-bottom-color:#b42318}
@media print{a.cite{color:#8b3a2e}a.cite.bad{color:#b42318}}
.math-block{margin:20px 0;text-align:center;overflow-x:auto;padding:6px 0}
main img{display:block;max-width:100%;margin:16px auto 4px;border:1px solid var(--line);
border-radius:6px;background:#fff}
main p > img + em, main em{color:#6b6258}
main img + em{display:block;text-align:center;font-size:12.5px;color:#6b6258;margin:0 0 18px}
@media print{main img{page-break-inside:avoid;max-height:78vh}}
.boarditem{margin:14px 0 18px;page-break-inside:avoid}
.boarditem .near{color:#7a736a;font-size:12.5px;margin-left:10px}
.boarditem pre{background:#f6f3ee;border:1px solid var(--line);border-radius:6px;
padding:10px 12px;font:12px/1.6 ui-monospace,Menlo,monospace;margin:6px 0 0;
white-space:pre-wrap;color:#3d3831}
.print-only{display:none}
@media print{.print-only{display:block}
.toc-print{page-break-before:always;page-break-after:always;margin-bottom:24px}
.toc-print h2{font:700 22px/1.4 "Source Han Sans SC",system-ui,sans-serif;margin:0 0 16px}
.toc-print .cols{columns:2;column-gap:40px;font:9.5pt/1.55 "Source Han Sans SC",system-ui,sans-serif}
.toc-print a{display:block;color:var(--ink);text-decoration:none;border:0}
.toc-print .pt-h1{font-weight:700;margin-top:9px}
.toc-print .pt-h2{padding-left:14px;color:#4b4238;font-size:9pt}}
.math-inline{white-space:nowrap}
math{font-size:1.02em}
hr{border:0;border-top:1px solid var(--line);margin:44px 0}
.callout{background:var(--warn);border:1px solid var(--warn-line);border-radius:8px;
padding:12px 16px;margin:18px 0;font-size:14.5px}
footer{margin-top:80px;padding-top:24px;border-top:1px solid var(--line);
color:var(--muted);font:13px/1.8 "Source Han Sans SC",system-ui,sans-serif}
.appx h1{font:700 24px/1.4 "Source Han Sans SC",system-ui,sans-serif;margin:56px 0 6px}
@media (max-width:900px){.wrap{grid-template-columns:1fr;gap:0}nav.toc{position:static;
max-height:none;border-bottom:1px solid var(--line);padding-bottom:20px}}
@media print{nav.toc{display:none}
.wrap{display:block;max-width:none;padding:0}
body{font-size:11.5pt;background:#fff}
main h1{page-break-before:always}
main h1:first-of-type{page-break-before:avoid}
pre,table,blockquote,.math-block{page-break-inside:avoid}
table.terms{page-break-inside:auto}
table.terms tr{page-break-inside:avoid}
table.terms thead{display:table-header-group}
table.terms td:first-child{width:17%}
table.terms td:nth-child(2){width:13%;white-space:nowrap}
td.ctx{color:#6b6258;font-size:12.5px}
a{color:var(--ink);border-bottom:0}}
"""


class Slug:
    """保留中文的锚点生成器，并处理重名。

    可以传入外部字典，让整篇文档共用一份计数（否则每章都从 0 开始，锚点重复）。
    """

    def __init__(self, seen: dict | None = None) -> None:
        self.seen: dict[str, int] = seen if seen is not None else {}

    def __call__(self, value: str, separator: str) -> str:
        s = re.sub(r"[^\w\u4e00-\u9fa5]+", separator, value).strip(separator).lower() or "sec"
        n = self.seen.get(s, 0) + 1
        self.seen[s] = n
        return s if n == 1 else f"{s}-{n}"


def _mathify(text: str) -> str:
    """把 $...$ / $$...$$ 在构建期转成 MathML，离线也能看，打印也正常。"""
    try:
        import latex2mathml.converter as conv
    except ImportError:
        return text

    def block(m):
        try:
            return f'\n\n<div class="math-block">{conv.convert(m.group(1).strip())}</div>\n\n'
        except Exception:
            return m.group(0)

    def inline(m):
        try:
            return f'<span class="math-inline">{conv.convert(m.group(1).strip())}</span>'
        except Exception:
            return m.group(0)

    text = re.sub(r"\$\$(.+?)\$\$", block, text, flags=re.S)
    return re.sub(r"\$(.+?)\$", inline, text)


def _math_outside_code(text: str) -> str:
    """行内 `code` 里的 $ 不是公式（`` `$HOME` `` 会被 mathify 吃掉）。"""
    bits = re.split(r"(`[^`\n]*`)", text)
    return "".join(b if b.startswith("`") else _mathify(b) for b in bits)


def md_to_html(md: str, slug_seen: dict | None = None) -> tuple[str, list[tuple[str, str, str]]]:
    """用 markdown 的 toc 扩展生成锚点，代码块里的 # 不会被误当成标题。

    `slug_seen` 由调用方在整篇文档内共享，否则每章都新建一个 Slug，
    `id="学习目标"` 会在 7 章里重复，目录全跳回第 1 章。
    """
    import markdown

    # 代码块里的 $ 不动；`~~~` 围栏与缩进代码块同样保护，行内 `code` 也不当公式
    parts = re.split(r"(^\s*```.*?^\s*```\s*$|^\s*~~~.*?^\s*~~~\s*$|^(?: {4,}|\t).*(?:\n|$))",
                     md, flags=re.S | re.M)
    md = "".join(p if p.lstrip().startswith(("```", "~~~")) or p[:1] in (" ", "\t")
                 else _math_outside_code(p) for p in parts)

    slug = slug_seen if slug_seen is not None else {}
    m = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"],
                          extension_configs={"toc": {"slugify": Slug(slug), "toc_depth": "1-2"}},
                          output_format="html5")
    body = m.convert(md)

    toc: list[tuple[str, str, str]] = []

    def walk(tokens):
        for t in tokens:
            toc.append((f"h{t['level']}", t["id"], t["name"]))
            walk(t.get("children", []))

    walk(m.toc_tokens)
    return body, toc


def inline_images(html: str, base: Path) -> str:
    """把 ![](figures/x.jpg) 换成 base64 内嵌，讲义就是一个能离线发的单文件。"""
    import base64

    def rep(m):
        src = m.group(1)
        if src.startswith(("data:", "http")):
            return m.group(0)
        p = (base / src).resolve()
        if not p.is_file():
            return m.group(0)
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        data = base64.b64encode(p.read_bytes()).decode()
        return f'src="data:{mime};base64,{data}"'

    return re.sub(r'src="([^"]+)"', rep, html)


def build_frames_appendix(bv: str) -> tuple[str, str]:
    """附录 E：画面文字（抽帧 + OCR）。读 `板书.md`，没有就整段跳过。

    不读 frames.json：OCR 结果已经由 `frames` 写成可读的 板书.md，
    原始帧和图都是中间物，删掉也不该让附录消失。
    """
    p = OUT / bv / "板书.md"
    if not p.exists():
        return "", ""
    from vsummary.frames import clean

    items: list[list] = []
    page = 0
    for line in p.read_text(encoding="utf-8").split("\n"):
        pm = re.match(r"^## P(\d+)", line)
        if pm:
            page = int(pm.group(1))
            continue
        hm = re.match(r"^### (?:(\d{1,2}):)?(\d{1,2}):(\d{2})[　 ]?(.*)$", line)
        if hm:
            h = int(hm.group(1) or 0)
            items.append([page, h * 3600 + int(hm.group(2)) * 60 + int(hm.group(3)),
                          hm.group(4).strip().strip("　"), []])
            continue
        if items and line.strip() and not line.startswith((">", "#", "```")):
            items[-1][3].append(line)

    rows = []
    seen: dict[int, list[set]] = {}
    per_page = 12
    total = 0
    for page, t, near, body_lines in items:
        if total >= 60:
            break
        body = clean("\n".join(body_lines))
        if len(body) < 12:
            continue
        grams = {body[i:i + 3] for i in range(max(1, len(body) - 2))}
        bucket = seen.setdefault(page, [])
        if len(bucket) >= per_page:          # 同一P 只留前 12 张有内容的
            continue
        if any(len(grams & g) / max(1, len(grams | g)) > 0.6 for g in bucket):
            continue                          # 同一页翻动产生的近似帧，只留第一张
        bucket.append(grams)
        total += 1
        rows.append((page, t, body, near))
    if not rows:
        return "", ""

    h = ['<h1 id="appx-frames">附录 E　画面文字（抽帧 + OCR）</h1>',
         "<p>课堂里最关键的几样东西常常念不出来：PPT 的公式、代码、架构图。"
         "这一节是本地抽帧 + OCR 的结果，每段都标了时间和它附近的讲法。"
         "<b>OCR 会认错字</b>，尤其代码里的 l/1、rn/m，只当线索用，不要当原文引用。</p>",
         "<div class=board>"]
    m = ["", "## 附录 E　画面文字（抽帧 + OCR）", "",
         "OCR 结果，可能有识别错误；每条都标了时间。", ""]
    cur = None
    for page, t, body, near in rows:
        if page != cur:
            cur = page
            h.append(f"<h3>P{page:02d}</h3>")
            m.append(f"### P{page:02d}")
            m.append("")
        lines = body.splitlines()[:10]
        h.append(f'<div class=boarditem><a class=cite href="{link(bv, page, t)}" '
                 f'title="跳到这一秒">P{page:02d} {fmt(t)}</a>'
                 f'<span class=near>{H.escape(near[:36])}</span>'
                 f"<pre>{H.escape(chr(10).join(lines))}</pre></div>")
        m.append(f"**P{page:02d} {fmt(t)}**　{near[:36]}")
        m.append("")
        m.append("```")
        m += lines
        m.append("```")
        m.append("")
    h.append("</div>")
    return "\n".join(h), "\n".join(m)


def build_appendix(bv: str, info: dict) -> tuple[str, str]:
    """返回 (html, md) 两部分附录。"""
    seg = load_segments(bv)
    parts = seg["parts"]

    h = ['<section class="appx"><h1 id="appx-index">附录 A　分P 索引</h1>',
         f"<p>共 {len(parts)} 个分P，音频 "
         f"{sum(p['duration'] for p in parts)/3600:.2f} 小时。点标题直接跳到那一P。</p>",
         "<table><thead><tr><th>#</th><th>分P</th><th>时长</th><th>字数</th></tr></thead><tbody>"]
    m = ["## 附录 A　分P 索引", "", "| # | 分P | 时长 | 字数 |", "|---|---|---|---|"]
    for p in parts:
        chars = sum(len(seg_text(s)) for s in p["segments"])
        h.append(f'<tr><td>P{p["page"]:02d}</td><td>'
                 f'<a href="{link(bv, p["page"])}">{H.escape(p["part"])}</a></td>'
                 f"<td>{fmt(p['duration'])}</td><td>{chars:,}</td></tr>")
        m.append(f"| P{p['page']:02d} | [{p['part']}]({link(bv, p['page'])}) "
                 f"| {fmt(p['duration'])} | {chars:,} |")
    h.append("</tbody></table>")

    rules = []
    for f in sorted(GLOSSARY.glob("*.tsv")) + sorted(GLOSSARY.glob("series/*.tsv")):
        for line in open(f, encoding="utf-8"):
            if line.strip() and not line.lstrip().startswith("#"):
                c = line.rstrip("\n").split("\t")
                if len(c) >= 4 and c[3].strip() == "obs":
                    ctx = c[2].strip() if len(c) > 2 else ""
                    rules.append((c[0], c[1], ctx, f.name))
    h += ['<h1 id="appx-terms">附录 B　术语纠错表</h1>',
          "<p>下表是转写后实际生效的替换规则（只列 <code>obs</code> 行）。"
          "「限定上下文」为空表示无条件替换；有值时只有紧挨着的那个词对得上才换——"
          "比如 <code>论文 → 任务</code> 只在前面是「下游/前置」时才生效，否则 <code>论文</code> 是合法词。</p>",
          "<table class=\"terms\"><thead><tr><th>规范写法</th><th>模型听成的</th><th>限定上下文</th>"
          "<th>来源</th></tr></thead><tbody>"]
    m += ["", "## 附录 B　术语纠错表", "",
          "「限定上下文」为空表示无条件替换；有值时只有紧挨着的那个词对得上才换。", "",
          "| 规范写法 | 模型听成的 | 限定上下文 | 来源 |", "|---|---|---|---|"]
    for good, bad, ctx, src in rules:
        shown = ctx.replace("_", "…") if ctx else "—"
        h.append(f"<tr><td>{H.escape(good)}</td><td>{H.escape(bad)}</td>"
                 f"<td class=ctx>{H.escape(shown)}</td><td>{src}</td></tr>")
        m.append(f"| {good} | {bad} | {shown} | {src} |")
    h.append("</tbody></table>")

    ch = OUT / bv / "changes.md"
    n_fix = 0
    if ch.exists():
        n_fix = sum(1 for line in open(ch, encoding="utf-8") if line.startswith("- **P"))
    h += ['<h1 id="appx-quality">附录 C　这份转写是怎么来的</h1>',
          '<div class="callout"><b>请你先知道三件事，再相信正文里的任何一句话。</b></div>',
          "<ul>",
          "<li><b>B 站自带的 AI 字幕不可用。</b>抽查两个站内课程（共 80 个分P）发现大量串台："
          "一集多模态课程的字幕是英雄联盟解说，一集强化学习课程的字幕是电视剧台词/行车 vlog/英文歌词，"
          "且存在多个分P 字幕完全相同的现象。因此本讲义<b>全部来自本地 ASR 转写，没有用平台字幕</b>。</li>",
          "<li><b>转写模型</b>：faster-whisper large-v3-turbo（fp16，RTX 5060 Laptop）。"
          "中文字级准确率约 99.9%，6.9 小时音频耗时约 13 分钟。</li>",
          f"<li><b>自动纠错 {n_fix} 处</b>（全部列在 <code>changes.md</code>）。"
          "纠错只处理<b>字符层明显不成立</b>的专名误听；像「清华河资源研究院」（实为智源研究院）"
          "这种读音接近、字符合法的错误，词表治不了，只能靠人读出来。</li>",
          "</ul>",
          "<h3>置信度这个坑</h3>",
          "<p>原计划用 <code>avg_logprob &lt; -0.6</code> 找出可疑句子再复核。实测 10,800 句里"
          "最小只有 -0.266，<b>没有一句低于阈值</b>——VAD 已经把噪声音频切干净，剩下的句子模型都很自信，"
          "而「自信」和「正确」是两件事。所以低置信筛选在本项目上是空转的，纠错只能靠词表 + 搭配约束。</p>",
          ]
    m += ["", "## 附录 C　这份转写是怎么来的", "",
          "> 请你先知道三件事，再相信正文里的任何一句话。", "",
          "- **B 站自带的 AI 字幕不可用。** 抽查两个站内课程（共 80 个分P）发现大量串台："
          "一集多模态课程的字幕是英雄联盟解说，一集强化学习课程的字幕是电视剧台词/行车 vlog/英文歌词，"
          "且存在多个分P 字幕完全相同的现象。因此本讲义**全部来自本地 ASR 转写，没有用平台字幕**。",
          "- **转写模型**：faster-whisper large-v3-turbo（fp16，RTX 5060 Laptop）。"
          "中文字级准确率约 99.9%，6.9 小时音频耗时约 13 分钟。",
          f"- **自动纠错 {n_fix} 处**（全部列在 `changes.md`）。纠错只处理**字符层明显不成立**的专名误听；"
          "像「清华河资源研究院」（实为智源研究院）这种读音接近、字符合法的错误，词表治不了，只能靠人读出来。",
          "",
          "### 置信度这个坑",
          "",
          "原计划用 `avg_logprob < -0.6` 找出可疑句子再复核。实测 10,800 句里最小只有 -0.266，"
          "**没有一句低于阈值**——VAD 已经把噪声音频切干净，剩下的句子模型都很自信，"
          "而「自信」和「正确」是两件事。所以低置信筛选在本项目上是空转的，纠错只能靠词表 + 搭配约束。",
          ""]

    # ---------------- 附录 D 前后不一致
    try:
        from . import consistency
        _, rows = consistency.run(bv, write=True)
    except Exception as e:                                    # 不给附录挂住整个构建
        rows, err = [], str(e)
        print(f"[warn] 一致性检测失败：{err}")

    h += ['<h1 id="appx-consist">附录 D　前后不一致清单</h1>',
          "<p>把全片带数字的断言抽出来，比「同一分P、两分钟内、同一属性」的两次说法。"
          "它只指出<b>哪里说了两遍不一样的话</b>，不判断谁对谁错。</p>",
          "<blockquote><p>局限写在前面：它只认<b>紧挨着属性词</b>的数字（端口是 2400、12 个 block），"
          "所以会漏掉「相隔几十分钟的两次口误」和「不带数字的概念矛盾」；"
          "形状组合（64×4096 vs 64×4097）只在第一个维度相同、第二个只差一且数字很大时才报，"
          "否则全是噪声。</p></blockquote>"]
    m += ["", "## 附录 D　前后不一致清单", "",
          "把全片带数字的断言抽出来，比「同一分P、两分钟内、同一属性」的两次说法。"
          "它只指出**哪里说了两遍不一样的话**，不判断谁对谁错。", "",
          "> 局限：只认紧挨着属性词的数字，会漏掉相隔很久的两次口误和不带数字的概念矛盾。", ""]
    if not rows:
        h.append("<p>没有发现数值冲突。</p>")
        m.append("没有发现数值冲突。")
    else:
        h += ["<table><thead><tr><th>#</th><th>属性</th><th>说法 A</th><th>说法 B</th>"
              "<th>像什么</th></tr></thead><tbody>"]
        m += ["| # | 属性 | 说法 A | 说法 B | 像什么 |", "|---|---|---|---|---|"]
        for i, c in enumerate(rows, 1):
            vs = c["values"]
            a, b = c["items"][vs[0]][0], c["items"][vs[1]][0]
            ha = (f'<a href="{link(bv, a["page"], a["t"])}">{H.escape(vs[0])}</a>'
                  f' <span class=ctx>P{a["page"]:02d} {fmt(a["t"])}</span>')
            hb = (f'<a href="{link(bv, b["page"], b["t"])}">{H.escape(vs[1])}</a>'
                  f' <span class=ctx>P{b["page"]:02d} {fmt(b["t"])}</span>')
            h.append(f"<tr><td>{i}</td><td>{H.escape(c['entity'])}</td>"
                     f"<td>{ha}</td><td>{hb}</td><td>{H.escape(c.get('note') or '—')}</td></tr>")
            m.append(f"| {i} | {c['entity']} | [{vs[0]}]({link(bv, a['page'], a['t'])})"
                     f"（P{a['page']:02d} {fmt(a['t'])}） | [{vs[1]}]({link(bv, b['page'], b['t'])})"
                     f"（P{b['page']:02d} {fmt(b['t'])}） | {c.get('note') or '—'} |")
        h.append("</tbody></table>")

    # ---------------- 附录 D.2 概念层面的矛盾（模型抽 + 判）
    cp = OUT / bv / "claims.json"
    crs, ncl = [], 0
    if cp.exists():
        try:
            cj = json.loads(cp.read_text(encoding="utf-8"))
            crs = cj.get("conflicts") or []
            ncl = len(cj.get("claims") or [])
        except (OSError, json.JSONDecodeError):
            crs, ncl = [], 0
    if crs:
        h += ["<h2>D.2 概念层面的矛盾</h2>",
              f"<p>上面那张表只看得见数字。这一节从 {ncl} 条断言（是/不是/有 N 个/比…更…）里"
              f"先圈出可疑，再让模型逐组判定「能不能同时成立」，留下 {len(crs)} 组。</p>",
              "<blockquote><p>这类判断模型也会错：它看不见你没说出口的前提，"
              "也可能把「不同实验的不同参数」当成矛盾。请点出处听一遍再采信。</p></blockquote>",
              "<table><thead><tr><th>#</th><th>主体</th><th>说法 A</th><th>说法 B</th>"
              "<th>为什么可疑</th></tr></thead><tbody>"]
        m += ["", "### D.2 概念层面的矛盾", "",
              f"从 {ncl} 条断言里先圈出可疑，再逐组判定「能不能同时成立」，留下 {len(crs)} 组。"
              "模型也会错，请点出处核一遍。", "",
              "| # | 主体 | 说法 A | 说法 B | 为什么可疑 |", "|---|---|---|---|---|"]
        for i, r in enumerate(crs, 1):
            a, b = r["a"], r["b"]
            ha = (f'<a href="{link(bv, a["page"], a["t"])}">{H.escape(str(a["value"]))}</a>'
                  f' <span class=ctx>P{a["page"]:02d} {fmt(a["t"])}</span>')
            hb = (f'<a href="{link(bv, b["page"], b["t"])}">{H.escape(str(b["value"]))}</a>'
                  f' <span class=ctx>P{b["page"]:02d} {fmt(b["t"])}</span>')
            h.append(f"<tr><td>{i}</td><td>{H.escape(str(r['subject']))}</td>"
                     f"<td>{ha}</td><td>{hb}</td><td>{H.escape(str(r.get('why') or '—'))}</td></tr>")
            m.append(f"| {i} | {r['subject']} | {a['value']}（P{a['page']:02d} {fmt(a['t'])}） "
                     f"| {b['value']}（P{b['page']:02d} {fmt(b['t'])}） | {r.get('why') or '—'} |")
        h.append("</tbody></table>")

    # ---------------- 附录 F 名词速查
    tpath = OUT / bv / "termsheet.json"
    trows = []
    if tpath.exists():
        try:
            trows = json.loads(tpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            trows = []
    if trows:
        defined = [r for r in trows if r.get("def") and r["def"] != "—"]
        missing = [r for r in trows if not (r.get("def") and r["def"] != "—")]
        h += ['<h1 id="appx-glossary">附录 F　名词速查</h1>',
              f"<p>讲义里出现过的 {len(trows)} 个术语，按首次出现的位置排。"
              "一句话定义优先取原文里的定义句，原文没定义的就按上下文改写一句"
              "（标「据上下文改写」），再没有的留空——<b>空白的那些就是正文没讲清楚的</b>，"
              f"共 {len(missing)} 个，完整清单见 <code>prereq.md</code>。</p>",
              '<table class="terms"><thead><tr><th>术语</th><th>首见</th>'
              '<th>一句话</th></tr></thead><tbody>']
        m += ["", "## 附录 F　名词速查", "",
              f"讲义里出现过的 {len(trows)} 个术语。一句话定义优先取原文，"
              f"其次按上下文改写（标「据上下文改写」），都没有的留空（共 {len(missing)} 个）。", "",
              "| 术语 | 首见 | 一句话 |", "|---|---|---|"]
        for r in defined:
            ch = f'第 {r["ch"]} 章'
            src = r.get("src") or ""
            if "改写" in src:
                ch += " <span class=ctx>改写</span>"
            h.append(f"<tr><td><b>{H.escape(r['term'])}</b></td><td>{ch}</td>"
                     f"<td>{H.escape(r['def'])}</td></tr>")
            m.append(f"| **{r['term']}** | {ch.replace('<br>', ' ')} | {r['def']} |")
        if missing:
            names = "、".join(r["term"] for r in missing)
            h += ["</tbody></table>",
                  f"<p class=ctx>正文里没解释的 {len(missing)} 个：{H.escape(names)}</p>"]
            m += ["", f"正文里没解释的 {len(missing)} 个：{names}"]
        else:
            h.append("</tbody></table>")

    h.append("</section>")
    return "\n".join(h), "\n".join(m)


def _title_file(out) -> str:
    """out/<bv>/title.txt 里写了标题就用它——B 站原标题常常是营销号文案。"""
    p = out / "title.txt"
    if p.exists():
        t = p.read_text(encoding="utf-8-sig").strip()
        if t:
            return t
    return ""


def conf_ok(out) -> bool:
    """领域置信比太低时不展示——分类器是关键词投票，跨领域内容会瞎猜。"""
    try:
        d = json.load(open(out / "domain.json", encoding="utf-8"))
        return float(d.get("conf", 0) or 0) >= 1.2
    except Exception:
        return True


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py handout")
    ap.add_argument("bv")
    ap.add_argument("--title", default="", help="默认用 B 站原标题")
    ap.add_argument("--subtitle", default="", help="副行，默认留空")
    args = ap.parse_args()

    bv = args.bv
    out = OUT / bv
    chs = sorted((out / "handout").glob("ch*.md"),
                 key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not chs:
        raise SystemExit(f"{out/'handout'} 里没有 ch*.md 章节文件")

    seg = load_segments(bv)
    total_sec = sum(p["duration"] for p in seg["parts"])
    chars = sum(len(seg_text(s)) for p in seg["parts"] for s in p["segments"])
    dom = []
    dp = out / "domain.json"
    if dp.exists():
        dom = [d["domain"] for d in json.load(open(dp, encoding="utf-8"))["top3"]]

    bodies, ctoc, mdparts = [], [], []
    cite_ok = cite_n = 0
    slug_seen: dict = {}
    for f in chs:
        text = open(f, encoding="utf-8").read()
        body, toc = md_to_html(text, slug_seen)
        body, rows = cite.annotate_html(bv, body)
        ok, n = cite.stats(rows)
        cite_ok, cite_n = cite_ok + ok, cite_n + n
        bodies.append(body)
        ctoc += toc
        mdparts.append(cite.annotate_md(bv, text.strip()))

    appx_h, appx_m = build_appendix(bv, seg)
    frames_h, frames_m = build_frames_appendix(bv)
    # 附录 E（画面文字）要排在 D 与 F 之间；F 不存在时（没跑 termsheet）就接在 D 之后，
    # 不能因为找不到标记位就整节丢掉。
    if frames_h:
        mk_h, mk_m = '<h1 id="appx-glossary">', "## 附录 F　名词速查"
        if mk_h in appx_h:
            pre, post = appx_h.split(mk_h, 1)
            appx_h = pre + frames_h + "\n" + mk_h + post
        else:
            appx_h = appx_h.rstrip() + "\n" + frames_h
        if mk_m in appx_m:
            pre, post = appx_m.split(mk_m, 1)
            appx_m = pre + frames_m + "\n" + mk_m + post
        else:
            appx_m = appx_m.rstrip() + "\n" + frames_m

    toc_html = ['<nav class="toc"><div class="t">目录</div>']
    print_toc = ['<section class="toc-print print-only"><h2>目录</h2><div class="cols">']
    for lvl, i, text in ctoc:
        if lvl in ("h1", "h2"):
            cls = "pt-h1" if lvl == "h1" else "pt-h2"
            toc_html.append(f'<a class="{lvl}" href="#{i}">{H.escape(text)}</a>')
            print_toc.append(f'<a class="{cls}" href="#{i}">{H.escape(text)}</a>')
    toc_html.append("</nav>")
    print_toc.append("</div></section>")

    title = args.title or _title_file(out) or seg["title"]
    subtitle = args.subtitle or ("" if not args.title else f"原视频：{seg['title']}")
    n_part = len(seg["parts"])
    unit = "个分P" if n_part > 1 else "集"
    n_ch = len(sorted((out / "handout").glob("ch*.md"))) if (out / "handout").exists() else 0
    meta = [f"UP：{seg['owner']}", f"{bv}", f"{n_part} {unit}",
            f"音频 {total_sec/3600:.2f} 小时", f"转写 {chars:,} 字"]
    if dom and conf_ok(out):
        meta.append("领域：" + "、".join(dom[:2]))

    if n_ch >= 3:
        reading = ("只想先看全貌：读第 1 章，它通常是全局地图；"
                   "要动手或看细节：直接跳到后面的章节。")
    else:
        reading = "篇幅不长，建议顺着读；需要核对原话时点句末的时间码。"
    intro = f"""<div class="callout"><b>怎么读这份讲义</b><br>
本讲义由 {n_part} {unit}（{total_sec/3600:.2f} 小时）的本地转写整理，不是字幕的复制。
{reading}
每章末尾的「存疑与勘误」逐条列出了转写听错或讲解可疑之处——和正文同样重要。</div>"""

    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{H.escape(title)} · 讲义</title><style>{CSS}</style>
</head><body>
<div class="wrap">
{''.join(toc_html)}
<main>
<header class="cover">
<h1>{H.escape(title)}</h1>
<p class="orig">{H.escape(subtitle)}</p>
<p class="sub">视频讲义　·　由 {bv} 的 {n_part} {unit} 转写整理而成</p>
<div class="badges">{''.join(f'<span class="badge">{H.escape(x)}</span>' for x in meta)}</div>
</header>
{intro}
{''.join(print_toc)}
{''.join(bodies)}
{appx_h}
<footer>
<p>本讲义由本地转写 + 自动纠错 + 分章整理生成，不依赖 B 站字幕。每章末尾的「存疑与勘误」列出了
转写或讲解中可疑之处，阅读时请保留自己的判断。</p>
<p>正文共 {cite_n} 处出处，{cite_ok} 处已核对到原文
{'' if cite_n and cite_ok == cite_n else '；标红的表示那一秒附近找不到对应转写，可能是记错了位置'}
　|　原视频：<a href="{link(bv)}">{link(bv)}</a></p>
</footer>
</main></div></body></html>"""

    doc = inline_images(doc, out)
    hp = out / "讲义.html"
    hp.write_text(doc, encoding="utf-8")

    md = [f"# {title} 视频讲义", "",
          f"> UP：{seg['owner']}　|　{bv}　|　{len(seg['parts'])} {unit}　|　"
          f"音频 {total_sec/3600:.2f}h　|　转写 {chars:,} 字", "",
          "---", "", "\n\n---\n\n".join(mdparts), "", "---", "", appx_m,
          ""]
    mp = out / "讲义.md"
    mp.write_text("\n".join(md), encoding="utf-8")

    print(f"已写 {hp}  ({hp.stat().st_size/1024:.0f} KB)")
    print(f"已写 {mp}  ({mp.stat().st_size/1024:.0f} KB)")
    print(f"章节 {len(chs)} 个，目录项 {sum(1 for l, _, _ in ctoc if l in ('h1', 'h2'))} 条")
    if cite_n:
        print(f"出处 {cite_n} 处，{cite_ok} 处核对到原文" +
              ("" if cite_ok == cite_n else f"，{cite_n - cite_ok} 处标红"))


if __name__ == "__main__":
    main()
