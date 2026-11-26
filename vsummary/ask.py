"""问一句，从转写里找答案——不需要向量库。

10,800 句的规模，BM25 在内存里就是毫秒级；命中的句子本来就带分P 和秒数，
所以答案天然可回溯。加 `--llm` 时只把命中的那几句交给模型，它不许自己补知识。

用法：
  python cli.py ask BV1E9U8BBEvZ "MoCo 的动量系数取多少"
  python cli.py ask BV1E9U8BBEvZ "负样本队列多大" --no-llm     # 只看原文，不问模型
  python cli.py ask BV1E9U8BBEvZ "SAM 怎么做分割" --top 8 --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter

from .core import GLOSSARY, OUT, fmt, link, load_segments, seg_text

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")
STOP = set("的了是在和就都而及与这那你我他它们有会要可以不过而且对于所以因为如果就是没有"
           "一个 什么 怎么 为什么 多少 哪些".split())

SYS = ("你是这门课的资料员。只能依据给定的原文回答，不许补充原文以外的知识。"
       "每句话后面用 [1][2] 这样的编号标注它来自第几条。原文不足以回答时，"
       "直接说「材料里没有讲」，并列出最接近的几条。回答控制在 200 字以内。")


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in TOKEN.finditer(text):
        t = m.group(0)
        if "\u4e00" <= t[0] <= "\u9fff":
            if len(t) == 1:
                out.append(t)
            else:
                out.extend(t[i:i + 2] for i in range(len(t) - 1))
        else:
            t = t.lower()
            if t not in STOP:
                out.append(t)
    return out


class BM25:
    """标准 BM25，k1=1.5 / b=0.75。文档是单句，语料一万量级。"""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.n = len(docs)
        self.avg = sum(len(d) for d in docs) / max(1, self.n)
        df: Counter[str] = Counter()
        for d in docs:
            for t in set(d):
                df[t] += 1
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in docs]

    def search(self, query: str, k: int = 8) -> list[tuple[float, int]]:
        q = [t for t in tokens(query) if t in self.idf]
        if not q:
            return []
        scores: list[tuple[float, int]] = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            dl = len(self.docs[i]) or 1
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return scores[:k]


def build(bv: str) -> tuple[list[dict], BM25]:
    """按窗口建索引：单句常常只有四五个字，检索会全是噪声；两三句合成一条更像"一段话"。"""
    segs = load_segments(bv, fixed=(OUT / bv / "segments_fixed.json").exists())
    lines: list[dict] = []
    for part in segs["parts"]:
        cur: list[dict] = []
        for seg in part["segments"]:
            text = seg_text(seg).strip()
            if len(text) < 2:
                continue
            gap = seg["start"] - cur[-1]["end"] if cur else 0
            long = len("".join(x["text"] for x in cur)) >= 48
            if cur and (gap > 1.5 or long):
                lines.append(_mk(part, cur))
                cur = []
            cur.append({"text": text, "start": seg["start"], "end": seg["end"]})
        if cur:
            lines.append(_mk(part, cur))
    return lines, BM25([tokens(x["text"]) for x in lines])


def _mk(part: dict, cur: list[dict]) -> dict:
    return {"page": part["page"], "part": part["part"],
            "start": cur[0]["start"], "end": cur[-1]["end"],
            "text": "".join(x["text"] for x in cur)}


def alias_map() -> dict[str, set[str]]:
    """中英互查：问「动量系数」时也去搜「momentum」「0.99」那几句。"""
    amap: dict[str, set[str]] = {}
    p = GLOSSARY / "terms" / "ai.tsv"
    if not p.exists():
        return amap
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        c = line.split("\t")
        forms = [c[0]] + (c[1].split("|") if len(c) > 1 else [])
        forms = [f.strip() for f in forms if f and f.strip()]
        for f in forms:
            amap.setdefault(f.lower(), set()).update(forms)
    return amap


def expand(query: str, amap: dict[str, set[str]]) -> str:
    extra: list[str] = []
    low = query.lower()
    for form, forms in amap.items():
        if form and form in low:
            extra.extend(x for x in forms if x.lower() not in low)
    return query + " " + " ".join(sorted(set(extra))) if extra else query


def context(lines: list[dict], idx: int, span: int = 1) -> list[int]:
    """把命中句前后各 span 句一起带上，避免断章取义。"""
    same = [j for j in range(max(0, idx - span), min(len(lines), idx + span + 1))
            if lines[j]["page"] == lines[idx]["page"]]
    return same


def answer(bv: str, question: str, top: int = 6, use_llm: bool = True) -> dict:
    lines, index = build(bv)
    hits = index.search(expand(question, alias_map()), top)
    if not hits:
        return {"question": question, "answer": "转写里没有出现这些词。", "evidence": []}

    # 「多少/多大/系数」这类问题，答案往往是一个数字：含数字的段优先
    if re.search(r"多少|多大|多久|几个|几层|系数|比例|尺寸|长度|维度|几次", question):
        hits = sorted(((s + (0.9 if re.search(r"\d", lines[i]["text"]) else 0.0), i)
                       for s, i in hits), reverse=True)

    picked = sorted({i for _s, i in hits}, key=lambda j: (lines[j]["page"], lines[j]["start"]))
    # 命中的窗口往往只说了半句，把左右邻居一起交给模型，避免"材料里没有讲"的误判
    wide: list[int] = []
    for i in picked:
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(lines) and lines[j]["page"] == lines[i]["page"] and j not in wide:
                wide.append(j)
    wide.sort(key=lambda j: (lines[j]["page"], lines[j]["start"]))
    picked = wide[: max(top * 2, 10)]
    evidence = [{"n": n + 1, "page": lines[j]["page"], "part": lines[j]["part"],
                 "t": round(lines[j]["start"], 1), "text": lines[j]["text"],
                 "url": link(bv, lines[j]["page"], lines[j]["start"])}
                for n, j in enumerate(picked)]

    body = "\n".join(f"[{e['n']}] (P{e['page']:02d} {fmt(e['t'])}) {e['text']}" for e in evidence)
    text = ""
    if use_llm:
        from .llm import LLM, LLMError
        llm = LLM()
        if llm.ready:
            try:
                text = llm.chat(f"问题：{question}\n\n原文：\n{body}", system=SYS)
            except LLMError as e:
                text = f"（模型调用失败，下面只给原文：{e}）"
        else:
            text = "（没有配置 key，下面只给原文）"
    return {"question": question, "answer": text or "（--no-llm：只给原文）",
            "evidence": evidence}


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py ask")
    ap.add_argument("bv")
    ap.add_argument("question", nargs="+")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    q = " ".join(args.question)
    r = answer(args.bv, q, args.top, not args.no_llm)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    print(f"问：{q}\n")
    print(r["answer"].strip() or "（无）")
    if r["evidence"]:
        print("\n原文：")
        for e in r["evidence"]:
            print(f"  [{e['n']}] P{e['page']:02d} {fmt(e['t'])}  {e['text'][:80]}")
            print(f"      {e['url']}")


if __name__ == "__main__":
    main()
