"""精简版讲义：把 7 章正文压成一份"复习用"的短稿，不动原始章节。

做法：逐章让模型按固定骨架重写（这一章在回答什么 / 核心结论 / 概念关系 /
关键数字 / 代码要点 / 易错点），每条结论后必须保留原文的时间码；再拼一页地图
和十条总纲。产物 `out/<bv>/精简版.md` + `精简版.html`（复用讲义的 CSS）。

用法：
  python cli.py brief BV1E9U8BBEvZ            # 生成精简版
  python cli.py brief BV1E9U8BBEvZ --only 1,3 # 只重做某几章（调试用）
"""
from __future__ import annotations

import argparse
import re
import time

from . import cite
from .core import OUT, write_atomic
from .handout import CSS, md_to_html
from .llm import LLM

CHAPTER_SYS = """你是教材编辑，负责把一章讲义压成"复习用精简版"。

保留：这一章要回答的问题、核心结论、概念之间的关系、关键数字与公式、
代码到底在证明什么、读者最容易错的地方。
删掉：推导过程、举例铺陈、重复表述、修饰性说明、"材料未展开"这类编者注。

硬规则：
1. 只写原文有的事实，不许补外部知识，不许编数字。
2. 每条结论、每个数字后面保留原文的时间码（形如 [P07 12:34]），原样抄，不要改。
3. 全文 600–900 个汉字，宁短不长。
4. 严格按给定骨架输出 Markdown，不要另加标题层级。
5. 某一项原文确实没有，就整行省略（不要写"无"以外的解释）。"""

MAP_SYS = """你是教材编辑。下面是一门课的七章精简稿摘要，请写两样东西：

一页地图：三到五句话，说清这门课的整体结构（先干什么、再干什么、章与章怎么衔接），
不要罗列章节标题，要写成连贯的判断。

十条总纲：全课最值得记住的十条结论，每条一行，20–40 字，可执行、可检验，
不要写成"本章介绍了…"这种空话。"""

SKELETON = """请把下面这一章压成精简稿，严格用这个骨架（不要加别的标题）：

**这一章在回答**：一句话（不超过 40 字）

**核心结论**
- 三条到六条，每条一行，句尾保留时间码

**概念关系**
| 概念 | 一句话 | 在这门课里的作用 |
|---|---|---|
| … | … | … |

**关键数字**
| 数字 | 含义 |
|---|---|
| … | … |

**代码要点**
- 两到四条，讲"这段代码在证明什么"，不要贴代码本身

**易错点**
- 两到四条，必须是读者真会错的地方

"""


def chapters(bv: str) -> list[tuple[int, str, str]]:
    d = OUT / bv / "handout"
    out = []
    for p in sorted(d.glob("ch*.md"), key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        n = int(re.sub(r"\D", "", p.stem) or 0)
        t = p.read_text(encoding="utf-8")
        title = t.split("\n", 1)[0].lstrip("# ").strip()
        title = re.sub(r"^第\s*\d+\s*章[\s　:：]*", "", title)     # 去掉重复的"第 N 章"
        title = title.split("（")[0].strip()                        # 标题里不保留注解
        out.append((n, title, t))
    return out


def squash(text: str) -> str:
    """去掉图、附录式长表和非正文部分，减少输入 token。"""
    text = re.sub(r"^!\[图 [^\n]*\n\*[^\n]*\*\n?", "", text, flags=re.M)
    text = re.split(r"^## 自测题", text, flags=re.M)[0]
    text = re.split(r"^## 存疑与勘误", text, flags=re.M)[0]
    return text.strip()


def one_chapter(llm: LLM, n: int, title: str, body: str) -> str:
    prompt = f"{SKELETON}\n这一章（第 {n} 章 {title}）的正文如下：\n\n{squash(body)}"
    md = llm.chat(prompt, system=CHAPTER_SYS)
    md = md.strip()
    if not md.startswith("**这一章在回答**"):
        md = "**这一章在回答**：" + md.split("**这一章在回答**：")[-1].lstrip("：: ")
    head = f"## 第 {n} 章　{title}\n\n"
    return head + md


def _block_type(line: str) -> str:
    s = line.lstrip()
    if s.startswith("```"):
        return "fence"
    if s.startswith("|"):
        return "table"
    if s.startswith(("- ", "* ", "+ ")) or re.match(r"\d+\.\s", s):
        return "list"
    return ""


def normalize(md: str) -> str:
    """模型输出常把列表/表格紧贴上一行，Markdown 会当成段落续行——补空行。"""
    out: list[str] = []
    for line in md.split("\n"):
        kind = _block_type(line)
        if out and out[-1].strip():
            prev = _block_type(out[-1])
            if kind and kind != prev:
                out.append("")
        out.append(line)
    return "\n".join(out)


def build(bv: str, llm: LLM, only: set[int] | None = None) -> str:
    chs = chapters(bv)
    # --only 只重做指定章：其余章从现有 精简版.md 里原样搬回来，不能整体覆盖（否则其它章被删）
    prev = ""
    p_old = OUT / bv / "精简版.md"
    if only and p_old.exists():
        prev = p_old.read_text(encoding="utf-8")
    blocks: dict[int, str] = {}
    if prev:
        for m in re.finditer(r"(?m)^## 第 (\d+) 章[^\n]*\n(.*?)(?=^## 第 \d+ 章|\Z)", prev, flags=re.S):
            blocks[int(m.group(1))] = m.group(0).rstrip()
    parts, digests = [], []
    for n, title, body in chs:
        if only and n not in only:
            if n in blocks:
                parts.append(blocks[n])
                digests.append(title)
            continue
        t0 = time.time()
        sec = one_chapter(llm, n, title, body)
        ok, tot = cite.stats(cite.verify(bv, sec))
        han = len(re.findall(r"[\u4e00-\u9fff]", sec))
        print(f"  ch{n}: {han} 汉字，{tot} 处时间码（{ok}/{tot} 核对到原文）"
              f"，{time.time() - t0:.0f}s", flush=True)
        parts.append(sec)
        digests.append(re.sub(r"\*\*这一章在回答\*\*[：:]\s*", "", sec).split("\n")[0][:60]
                       if "这一章在回答" in sec else title)

    omni = ""
    m_old = re.search(r"## 一页地图\s*\n(.*?)(?=\n## 十条总纲|\n---)", prev, flags=re.S) if prev else None
    if only and m_old:
        omni = prev[m_old.start():].split("\n---")[0].strip()   # 只重做单章时保留旧的地图与总纲
        print("  （保留原有的「一页地图」与「十条总纲」）", flush=True)
    if not omni:
        omni = llm.chat(f"{len(digests)}章摘要如下：\n\n" + "\n\n".join(d[:400] for d in digests),
                        system=MAP_SYS)
    body_md = "\n\n".join(parts)
    han = len(re.findall(r"[\u4e00-\u9fff]", body_md))
    full = OUT / bv / "讲义.md"
    full_txt = full.read_text(encoding="utf-8") if full.exists() else ""
    full_han = len(re.findall(r"[\u4e00-\u9fff]", full_txt))
    n_fig = full_txt.count("![图 ")
    n_q = full_txt.count("答案</summary>") or full_txt.count("<summary>答案")
    omni_txt = omni.strip()
    han_omni = len(re.findall(r"[\u4e00-\u9fff]", omni_txt))
    total = han + han_omni
    md = (f"# 精简版\n\n"
          f"> {len(chs)} 章正文 {han} 个汉字；连「一页地图」与「十条总纲」共 {total} 个汉字，约 {total / 300:.0f} 分钟读完"
          f"（完整讲义 {full_han:,} 字、{n_fig} 张画面图、{n_q} 道自测题，要细节回那里查）。\n>\n"
          f"> 建议顺序：先读「一页地图」和「十条总纲」（3 分钟建立框架），"
          f"再挑你关心的章看核心结论；每条的 `[Pxx mm:ss]` 都能跳回原视频那一秒。\n\n"
          f"{omni_txt}\n\n---\n\n{body_md}\n")
    md = normalize(md)
    write_atomic(p_old, md)
    print(f"→ {p_old}")
    return md


def render(bv: str, md: str, title: str) -> str:
    body, _ = md_to_html(md)
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 精简版</title><style>{CSS}</style>
<style>main h1{{font-size:26px}}.wrap{{display:block;max-width:820px;margin:0 auto;padding:28px 22px}}</style>
</head><body><div class="wrap"><main>{body}</main></div></body></html>"""
    p = OUT / bv / "精简版.html"
    write_atomic(p, html)
    print(f"→ {p}（{len(html) // 1024} KB）")
    return html


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py brief")
    ap.add_argument("bv")
    ap.add_argument("--only", default="", help="只重做某几章，如 1,3")
    ap.add_argument("--title", default="精简版")
    args = ap.parse_args()
    only = {int(x) for x in re.findall(r"\d+", args.only)} if args.only else None
    llm = LLM()
    md = build(args.bv, llm, only)
    render(args.bv, md, args.title)
    han = len(re.findall(r"[\u4e00-\u9fff]", md))
    print(f"\n精简版 {han} 汉字，约 {han / 300:.0f} 分钟读完（按 300 字/分）；"
          f"{llm.stats()}")
