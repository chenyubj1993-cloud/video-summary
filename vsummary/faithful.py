"""忠实度抽检：讲义里的每一句带出处的话，原文真的支持它吗？

`gate` 只验"时间码落在某句转写上"，不验"那句话支持这个论断"。这个命令补的就是这一层：
把带 `[Pxx mm:ss]` 的句子和它指的那几句原文一起交给模型，判 支持 / 部分支持 / 不支持。

用法：
  python cli.py faithful BV1E9U8BBEvZ --sample 100
  python cli.py faithful BV1E9U8BBEvZ --only 3 --sample 20
  python cli.py faithful BV1E9U8BBEvZ --all          # 全量（上千句，慢且贵）
"""
from __future__ import annotations

import argparse
import json
import random
import re

from . import cite
from .core import OUT, fmt, link, load_segments, seg_text
from .llm import LLM, LLMError

SENT = re.compile(r"[^。！？\n]{8,200}[。！？]")
SYS = ("你在核对一份讲义有没有编造。给定一句讲义原文和它能引用的转写原文，"
       "判断转写原文是否支持讲义这句话。只输出一行 JSON："
       '{"verdict":"支持|部分支持|不支持","why":"不超过20字"}。'
       "拿不准就算部分支持；转写里根本没有相关内容就算不支持。")

OUT_MD = "faithful.md"


def load(bv: str) -> tuple[list[dict], dict]:
    segs = load_segments(bv, fixed=True)
    idx: dict[int, list[dict]] = {}
    for part in segs["parts"]:
        idx[part["page"]] = part["segments"]
    return segs["parts"], idx


def pick_sentences(bv: str) -> list[dict]:
    """从章节里挑出"带出处"的句子，连同它引用到的原句一起返回。"""
    rows: list[dict] = []
    d = OUT / bv / "handout"
    for p in sorted(d.glob("ch*.md"), key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        n = int(re.sub(r"\D", "", p.stem) or 0)
        text = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)
        for sent in SENT.finditer(text):
            s = sent.group(0).strip()
            if "OCR" in s or "画面" in s or "板书" in s:
                continue                      # 这类断言的依据在画面里，转写里查不到
            refs = cite.parse(s)
            if not refs:
                continue
            rows.append({"chapter": n, "file": p.name, "text": s, "refs": refs})
    return rows


def evidence(idx: dict, refs: list[tuple[int, float, str]]) -> tuple[str, list[dict]]:
    """取引用点附近最贴近的几句原话。按"离时间码最近"挑，而不是按时间先后取前几句。"""
    blocks: list[dict] = []
    for page, t, _raw in refs[:2]:
        segs = idx.get(page) or []
        near = sorted((x for x in segs if abs(x["start"] - t) <= 25),
                      key=lambda x: abs(x["start"] - t))[:6]
        if not near:
            continue
        near.sort(key=lambda x: x["start"])
        blocks.append({"page": page, "t": round(near[0]["start"], 1),
                       "text": "".join(seg_text(x) for x in near)})
    body = "\n".join(f"(P{b['page']:02d} {fmt(b['t'])}) {b['text']}" for b in blocks)
    return body, blocks


def judge(llm: LLM, sent: str, body: str) -> dict:
    try:
        raw = llm.chat(f"讲义句子：{sent}\n\n转写原文：\n{body}", system=SYS, temperature=0.0)
    except LLMError as e:
        return {"verdict": "跳过", "why": str(e)[:30]}
    m = re.search(r'\{.*?"verdict"\s*:\s*"([^"]+)".*?"why"\s*:\s*"([^"]*)"', raw, re.S)
    if not m:
        return {"verdict": "跳过", "why": raw[:30]}
    v = m.group(1)
    if v not in ("支持", "部分支持", "不支持"):
        v = "跳过"          # 模型答了别的词，别折成"部分支持"把支持率抬高
    return {"verdict": v, "why": m.group(2)[:40]}


def run(bv: str, sample: int = 100, only: set[int] | None = None,
        every: bool = False, seed: int = 7) -> dict:
    rows = [r for r in pick_sentences(bv) if not only or r["chapter"] in only]
    if not rows:
        raise SystemExit("没有带出处的句子——先跑 chapters")
    if not every and len(rows) > sample:
        random.seed(seed)
        rows = random.sample(rows, sample)
    llm = LLM()
    if not llm.ready:
        raise SystemExit("忠实度判定要模型，先设 OPENAI_API_KEY")

    _parts, idx = load(bv)
    out: list[dict] = []
    for i, r in enumerate(rows, 1):
        body, ev = evidence(idx, r["refs"])
        if not body:
            out.append({**r, "verdict": "无原文", "why": "引用落不到原句", "ev": []})
            continue
        j = judge(llm, r["text"], body)
        out.append({**r, "verdict": j["verdict"], "why": j["why"], "ev": ev})
        if i % 25 == 0:
            print(f"  ...{i}/{len(rows)}", flush=True)

    n = len(out)
    cnt = {k: sum(1 for x in out if x["verdict"] == k)
           for k in ("支持", "部分支持", "不支持", "无原文", "跳过")}
    verdict_cn = (f"{n} 条抽样：支持 {cnt['支持']}、部分支持 {cnt['部分支持']}、"
                  f"不支持 {cnt['不支持']}、引用落空 {cnt['无原文']}、跳过 {cnt['跳过']}")
    ok = cnt["支持"] + cnt["部分支持"]
    judged = n - cnt["跳过"] - cnt["无原文"]
    rate = ok / judged if judged else 0.0
    if judged == 0:
        verdict_cn += "（可核样本为 0，无法评估——不是「通过」）"

    lines = [f"# {bv} 忠实度抽检", "",
             f"{verdict_cn}", "",
             (f"**可核样本里 {ok} 条有原文支持（{rate * 100:.0f}%），"
              f"{cnt['不支持']} 条找不到支撑。**" if judged else
              "**没有可核样本，这一项不算通过也不算失败。**"), "",
             "判定方式：把讲义句子和它引用的转写原句一起交给模型，判 支持/部分支持/不支持。"
             "这只是抽检，不是逐句盖章。", ""]
    bad = [x for x in out if x["verdict"] in ("不支持", "无原文")]
    if bad:
        lines += ["## 需要人看一眼的", ""]
        for x in bad:
            ref = x["refs"][0]
            lines += [f"- **第 {x['chapter']} 章**（{x['verdict']}，{x['why']}）",
                      f"  - 讲义：{x['text'][:120]}",
                      f"  - 出处：{link(bv, ref[0], ref[1])}", ""]
    (OUT / bv / OUT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")
    json.dump(out, open(OUT / bv / "faithful.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(f"\n{verdict_cn}")
    print(f"可核样本支持率 {rate * 100:.0f}%")
    print(f"→ {OUT / bv / OUT_MD}")
    return {"n": n, "cnt": cnt, "rate": rate}


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py faithful")
    ap.add_argument("bv")
    ap.add_argument("--sample", type=int, default=100)
    ap.add_argument("--only", default="", help="只查某几章，如 3 或 2,5")
    ap.add_argument("--all", action="store_true", help="不抽样，全查")
    args = ap.parse_args()
    only = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
    run(args.bv, args.sample, only or None, args.all)


if __name__ == "__main__":
    main()
