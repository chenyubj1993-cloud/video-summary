"""按标题 + 分P 标题 + 转写开头判定视频所属行业（多标签、可解释、零依赖）。

用法：python cli.py classify BV1E9U8BBEvZ
"""
from __future__ import annotations

import argparse
import json
import math
import os

from vsummary.core import OUT, ROOT, video_info

W_PARTS, W_TEXT, W_TITLE = 1.0, 0.8, 0.6


def score(text: str, kws: list[str]) -> float:
    t = text.lower()
    n = len(t) or 1
    hit = sum(t.count(k.lower()) for k in kws)
    return math.log1p(hit * 1000.0 / n) if hit else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py classify")
    ap.add_argument("bv")
    ap.add_argument("--json", default="", help="默认写 out/<bv>/domain.json")
    args = ap.parse_args()

    doms = json.load(open(ROOT / "domains.json", encoding="utf-8"))
    info = video_info(args.bv)
    title = info["title"]
    part_titles = " ".join(p["part"] for p in info["pages"])

    text = ""
    seg = OUT / args.bv / "segments.json"
    if seg.exists():
        d = json.load(open(seg, encoding="utf-8"))
        text = "".join("".join(s["text"] for s in p["segments"]) for p in d["parts"])[:4000]

    res = []
    for name, cfg in doms.items():
        s = W_PARTS * score(part_titles, cfg["kw"]) + W_TITLE * score(title, cfg["kw"])
        if text:
            s += W_TEXT * score(text, cfg["kw"])
        if s > 0:
            res.append({"domain": name, "score": round(s, 2), "template": cfg["template"]})
    res.sort(key=lambda x: -x["score"])

    top = res[:3]
    conf = round(top[0]["score"] / top[1]["score"], 2) if len(top) > 1 and top[1]["score"] else 99.0
    out = {"bv": args.bv, "title": title[:80], "top3": top, "confidence": conf,
           "needs_llm_check": conf < 1.2, "note": "标题与正文不一致时以正文为准"}

    print(f"视频: {title[:60]}")
    print(f"转写样本: {len(text)} 字已纳入判定" if text else "转写样本: 无（仅用标题判定）")
    for i, r in enumerate(top, 1):
        print(f"  {i}. {r['domain']:<12} {r['score']:>5}  {r['template'][:30]}")
    print(f"置信比 top1/top2 = {conf}" + ("  ⚠ 需 LLM 复核" if conf < 1.2 else ""))

    path = args.json or str(OUT / args.bv / "domain.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写", path)


if __name__ == "__main__":
    main()
