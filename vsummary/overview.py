"""把逐P 摘要拼成课程总览（目录表 + 逐P 摘要 + 统计）。

用法：python cli.py overview BV1E9U8BBEvZ
产物：out/<bv>/总览.md
"""
from __future__ import annotations

import argparse
import glob
import os
import re

from vsummary.core import OUT, fmt, link, load_segments, seg_text


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py overview")
    ap.add_argument("bv")
    args = ap.parse_args()
    out = OUT / args.bv

    seg = load_segments(args.bv)
    parts = {p["page"]: p for p in seg["parts"]}

    files = sorted(glob.glob(str(out / "summaries" / "*.md")))
    if not files:
        raise SystemExit("还没有 summaries/*.md，先跑 `cli.py summarize` 或人工撰写")
    raw = "\n\n".join(open(f, encoding="utf-8").read() for f in files)

    chunks = re.split(r"(?m)^##\s+(P\d{2,3})\s*(.*)$", raw)
    blocks = []
    for i in range(1, len(chunks), 3):
        pid, title, body = chunks[i], chunks[i + 1].strip(), chunks[i + 2].strip()
        blocks.append({"page": int(pid[1:]), "title": title, "body": body})
    blocks.sort(key=lambda b: b["page"])

    one = {}
    for b in blocks:
        m = re.search(r"\*\*一句话\*\*[：:]\s*(.+)", b["body"])
        one[b["page"]] = m.group(1).strip() if m else ""

    total_sec = sum(p.get("duration", 0) for p in parts.values())
    total_chars = sum(len(seg_text(s)) for p in parts.values() for s in p["segments"])

    L = [f"# {seg['title']}", "",
         f"UP：{seg['owner']}　|　{seg['bv']}　|　{len(parts)}P　|　"
         f"音频 {total_sec/3600:.2f}h　|　转写 {total_chars:,} 字", "",
         "## 目录", "", "| # | 分P | 时长 | 字数 | 一句话 |", "|---|---|---|---|---|"]
    for b in blocks:
        p = parts.get(b["page"], {})
        chars = sum(len(seg_text(s)) for s in p.get("segments", []))
        L.append(f"| P{b['page']:02d} | [{b['title']}]({link(seg['bv'], b['page'])}) "
                 f"| {fmt(p.get('duration', 0))} | {chars:,} | {one.get(b['page'], '')} |")

    L += ["", "---", "", "## 逐P 摘要", ""]
    for b in blocks:
        p = parts.get(b["page"], {})
        L += [f"## P{b['page']:02d} {b['title']}　`{fmt(p.get('duration', 0))}`", "", b["body"], ""]

    path = out / "总览.md"
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"已写 {path}")
    print(f"分P {len(blocks)} 个，音频 {total_sec/3600:.2f}h，{total_chars:,} 字，"
          f"总览 {os.path.getsize(path)/1024:.1f}KB")


if __name__ == "__main__":
    main()
