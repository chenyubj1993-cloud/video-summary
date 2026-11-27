"""从逐P 摘要的「关键术语」行自动挖候选误听对，并在全量转写里核对出现次数。

用法：python cli.py harvest BV1E9U8BBEvZ
产物：glossary/_discovered.tsv（全部 hyp，人工确认后改成 obs 才生效）
"""
from __future__ import annotations

import argparse
import glob
import re
from collections import Counter

from vsummary.core import GLOSSARY, load_segments, OUT, read_lines, seg_text

PAT = re.compile(r'([A-Za-z0-9\+\-\.\u4e00-\u9fa5]{1,24})\s*[（(]\s*转写(?:为|成|成了|为是)?'
                 r'\s*[“"\']([^”"\']{1,24})[”"\']\s*[)）]')


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py harvest")
    ap.add_argument("bv")
    args = ap.parse_args()
    out = OUT / args.bv

    pairs: Counter[str] = Counter()
    for f in sorted(glob.glob(str(out / "summaries" / "*.md"))):
        for line in read_lines(f):
            if "关键术语" not in line:
                continue
            for good, bad in PAT.findall(line):
                good, bad = good.strip(), bad.strip()
                if good and bad and good != bad:
                    pairs[f"{good}\t{bad}"] += 1

    body = "".join(seg_text(s) for p in load_segments(args.bv)["parts"] for s in p["segments"])
    rows = []
    for k, votes in pairs.items():
        good, bad = k.split("\t")
        rows.append((body.count(bad), votes, good, bad))
    rows.sort(key=lambda r: (-r[0], -r[1]))

    path = GLOSSARY / "_discovered.tsv"
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 自动发现候选（hyp，不生效）：规范词\t误听形\t上下文约束\t命中次数\t被投票次数\n")
        for cnt, votes, good, bad in rows:
            f.write(f"{good}\t{bad}\t\t{cnt}\t{votes}\n")

    print(f"候选 {len(rows)} 对，按「在转写里实际出现次数」排序：\n")
    print(f"{'命中':>4} {'票':>3}  {'误听形':<18} → 规范词")
    for cnt, votes, good, bad in rows[:25]:
        print(f"{cnt:>4} {votes:>3}  {bad:<18} → {good}")
    print(f"\n已写 {path}（确认后把状态改成 obs 才会生效）")


if __name__ == "__main__":
    main()
