"""自测题：读完一章，拿它检查自己是不是真懂了。

只出**能在本章找到答案**的题（答案必须带 `[Pxx mm:ss]`），题型固定四种：
  1. 解释类：为什么是这个设计
  2. 计算/推导类：把公式或维度算一遍
  3. 代码类：这段代码在干什么 / 改一个参数会怎样
  4. 判断类：给一个错误说法，指出错在哪

产物写进 `out/<bv>/handout/chN.md` 的「## 自测题」小节（在「存疑与勘误」之前），
答案折叠在 `<details>` 里，打印时也占版面，所以答案写得短。

用法：
  python cli.py exercises BV1E9U8BBEvZ            # 每章 4 题
  python cli.py exercises BV1E9U8BBEvZ --only 3   # 只出第 3 章
  python cli.py exercises BV1E9U8BBEvZ --dry
"""
from __future__ import annotations

import argparse
import re

from . import cite
from .core import OUT
from .llm import LLM

SYS = """你是这门课的助教，负责出**自测题**，帮读者检查自己是不是真读懂了。

硬要求：
1. 每道题的答案必须能在给定正文里找到，并在答案末尾带 `[Pxx mm:ss]` 出处（从正文里原样抄，不要编）。
2. 四种题各出一道：解释设计动机、算一遍维度或公式、读一段代码、判断一个错误说法。
3. 题目要具体，不要问"谈谈你对…的理解"。判断题的错误说法要像真的（把两个概念张冠李戴）。
4. 答案 1–3 句，别超过 80 字。**只写结论和一句为什么，不要写"让我想想""不对，应该是…"这类推理过程**；算不出来或正文没说清的，就换一道题，不要硬答。
5. 题目里要用到的数字必须是正文明确写过的；正文没给具体数值的，出概念题或判断题，别让人猜。

输出格式（严格，每行一条，不要别的话）：
Q|题目
A|答案（含 [Pxx mm:ss]）
Q|题目
A|答案
（共 {n} 组）
"""

TITLE = "## 自测题"


def render(pairs: list[tuple[str, str]], n_ch: int) -> str:
    lines = [TITLE, "",
             "> 做完再展开答案。如果你能不看答案做完，这一章就算读进去了。", ""]
    for i, (q, a) in enumerate(pairs, 1):
        lines.append(f"**{n_ch}.{i}**　{q}")
        lines.append("")
        lines.append(f"<details><summary>答案</summary>\n\n{a}\n\n</details>")
        lines.append("")
    return "\n".join(lines)


def parse(text: str) -> list[tuple[str, str]]:
    q = a = ""
    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Q|") or line.startswith("Q：") or line.startswith("Q:"):
            if q and a:
                pairs.append((q, a))
            q, a = line[2:].strip(), ""
        elif line.startswith("A|") or line.startswith("A：") or line.startswith("A:"):
            a = line[2:].strip()
    if q and a:
        pairs.append((q, a))
    return pairs


DOUBT = re.compile(r"不成立|不对|让我|可能是|大概|似乎|我不确定|自相矛盾")   # 只看答案；题目本来带问号


def ask(llm: LLM, body: str, n: int, n_ch: int, avoid: str = "") -> list[tuple[str, str]]:
    """出题 → 剔掉"答案里带推理过程"的题 → 不够就再要一轮。"""
    out: list[tuple[str, str]] = []
    for rnd in range(3):
        want = n - len(out)
        if want <= 0:
            break
        extra = ""
        if rnd:
            extra = (f"\n\n注意：上一轮有 {n - len(out)} 道题的答案写成了推理过程"
                     f"（含「不对」「不成立」等字样），已被剔除。这次只给结论。")
        raw = llm.chat(f"这是第 {n_ch} 章的正文：\n\n{body}\n\n"
                       f"请出 {want} 组题。{extra}{avoid}", system=SYS)
        got = [(q, a) for q, a in parse(raw) if q and a]
        fresh = []
        for q, a in got:
            if DOUBT.search(a) or "？" in a:
                continue
            if any(q[:20] == x[0][:20] for x in out):
                continue
            fresh.append((q, a))
        out.extend(fresh[:want])
    return out[:n]


def run(bv: str, llm: LLM, per_chapter: int = 4, only: set[int] | None = None,
        dry: bool = False, force: bool = False) -> int:
    total = 0
    for path in sorted((OUT / bv / "handout").glob("ch*.md"),
                       key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        n = int(re.sub(r"\D", "", path.stem) or 0)
        if only and n not in only:
            continue
        text = path.read_text(encoding="utf-8")
        if TITLE in text and not force:
            print(f"[skip] ch{n} 已有自测题（--force 覆盖）", flush=True)
            continue
        body = re.split(r"^##\s*存疑与勘误", text, flags=re.M)[0]
        body = re.sub(r"^## 自测题.*?(?=^## |\Z)", "", body, flags=re.S | re.M).strip()
        if len(body) < 800:
            print(f"[skip] ch{n} 正文太短，不出题", flush=True)
            continue
        pairs = ask(llm, body, per_chapter, n)
        if not pairs:
            print(f"[warn] ch{n} 没出成题，跳过", flush=True)
            continue
        ok, tot = cite.stats(cite.verify(bv, "\n".join(a for _q, a in pairs)))
        block = render(pairs, n)
        if not dry:
            text = re.sub(r"^## 自测题.*?(?=^## |\Z)", "", text, flags=re.S | re.M)
            if "## 存疑与勘误" in text:
                text = text.replace("## 存疑与勘误", block + "\n## 存疑与勘误", 1)
            else:
                text = text.rstrip() + "\n\n" + block + "\n"
            path.write_text(text, encoding="utf-8")
        total += len(pairs)
        print(f"  ch{n}: {len(pairs)} 题，{ok}/{tot} 个答案找得到出处", flush=True)
    print(f"\n共 {total} 题{'（演练，未写入）' if dry else ''}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py exercises")
    ap.add_argument("bv")
    ap.add_argument("--per-chapter", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    llm = LLM(base=args.api_base, key=args.api_key, model=args.model, temperature=0.4)
    if not llm.ready:
        raise SystemExit("没有 API key，先设 OPENAI_API_KEY")
    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    run(args.bv, llm, args.per_chapter, only, args.dry, args.force)
    print(f"[llm] {llm.stats()}")


if __name__ == "__main__":
    main()
