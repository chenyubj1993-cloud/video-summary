"""按行业词表纠正转写，产出可审计的改动报告。

用法：
  python cli.py fix BV1E9U8BBEvZ            # 只统计，不落盘
  python cli.py fix BV1E9U8BBEvZ --emit     # 写出全课转写_已纠错.md、changes.md、segments_fixed.json

注意：不带 --emit 时只打印统计，不写任何文件（下游要新的转写才加 --emit）。
"""
from __future__ import annotations

import argparse
import json
import os
import re

from vsummary.core import GLOSSARY, OUT, domain_of, load_segments, write_atomic

DOMAIN_FILE = {"人工智能与机器学习": "ai", "计算机视觉": "cv", "编程与软件工程": "dev",
               "数学": "math", "医学与健康": "med", "法律": "law",
               "金融与投资": "finance", "历史人文": "history", "美食与生活": "life",
               "游戏与电竞": "game"}
LOW_LP = -0.6
ASCII_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+#-]*$")


def _is_ascii_word(s: str) -> bool:
    """拉丁词与纯数字都要加边界：718 不能命中 1718，clip 不能命中 clip_embedding。"""
    return bool(ASCII_WORD.match(s))


def load_rules(paths: list[str]) -> list[dict]:
    rules = []
    for p in paths:
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8-sig"):     # 词表可能被记事本存成 BOM
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            good, bads = cols[0].strip().lstrip("\ufeff"), cols[1].strip()
            ctx = cols[2].strip() if len(cols) > 2 else ""
            status = cols[3].strip() if len(cols) > 3 else "hyp"
            alts = [b.strip() for b in bads.split("|") if b.strip()]
            if not alts:
                continue
            # 纯拉丁/数字的误听形要加词边界，否则 768←718 会打到 1718、
            # CLIP←clip 会打到 clip_embedding
            body = "(?P<bad>"
            for i, a in enumerate(alts):
                if i:
                    body += "|"
                if _is_ascii_word(a.rstrip("._+#-")):
                    body += r"(?<![A-Za-z0-9_])" + re.escape(a) + r"(?![A-Za-z0-9_])"
                else:
                    body += re.escape(a)
            body += ")"
            left = right = ""
            if ctx:
                left, right = ctx.split("_", 1) if "_" in ctx else (ctx, "")
            l = "(?:" + "|".join(map(re.escape, [x for x in left.split("|") if x])) + ")" if left else ""
            r = "(?:" + "|".join(map(re.escape, [x for x in right.split("|") if x])) + ")" if right else ""
            pat = f"({l})({body})({r})" if l and r else (f"({l})({body})" if l else f"({body})({r})")
            flags = re.I if all(_is_ascii_word(a.rstrip("._+#-")) for a in alts) else 0
            rules.append({"good": good, "bads": alts, "ctx": ctx, "status": status,
                          "re": re.compile(pat, flags), "src": os.path.basename(p)})
    return rules


def rule_paths(bv: str) -> list[str]:
    """fix 与 gate 共用的词表路径：通用 + 本项目系列 + 各领域。"""
    paths = [str(GLOSSARY / "common.tsv"), str(GLOSSARY / "series" / f"{bv}.tsv")]
    for d in domain_of(bv):
        f = DOMAIN_FILE.get(d)
        if f:
            paths.append(str(GLOSSARY / (f + ".tsv")))
    return [p for p in paths if os.path.exists(p)]


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py fix")
    ap.add_argument("bv")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--show-low", type=int, default=15)
    args = ap.parse_args()

    out = OUT / args.bv
    seg = load_segments(args.bv, fixed=False)
    doms = domain_of(args.bv)

    paths = rule_paths(args.bv)
    rules = load_rules(paths)
    print(f"行业: {', '.join(doms) or '(未分类)'}")
    print(f"词表: {', '.join(os.path.basename(p) for p in paths if os.path.exists(p))}")
    print(f"规则 {len(rules)} 条（生效 obs={sum(r['status'] == 'obs' for r in rules)}，"
          f"候选 hyp={sum(r['status'] != 'obs' for r in rules)}）\n")

    changes, hyp_hits = [], []
    for part in seg["parts"]:
        for s in part["segments"]:
            text = s["text"]
            for r in rules:
                m = r["re"].search(text)
                if not m:
                    continue
                if r["status"] != "obs":
                    hyp_hits.append((part["page"], s["start"], r["good"], m.group(0)))
                    continue
                # 用命名组定位误听形本身：大小写不敏感时 group(0) 里的大小写和词表未必一致，
                # 早先按"词表里的写法去 group(0) 里找"会 StopIteration
                new = r["re"].sub(lambda mm: mm.group(0).replace(mm.group("bad"), r["good"]), text)
                if new != text:
                    changes.append({"page": part["page"], "start": s["start"], "before": text,
                                    "after": new, "rule": r["good"], "src": r["src"]})
                    text = new
            s["text_fixed"] = text

    print(f"已纠正 {len(changes)} 处")
    if hyp_hits:
        print(f"候选规则命中 {len(hyp_hits)} 处（未改动，供验证）：")
        for p, st, good, hit in hyp_hits[:8]:
            print(f"  P{p:02d} {int(st//60):02d}:{int(st%60):02d} {hit} → {good}")

    lows, n_lp, allp = [], 0, []
    for part in seg["parts"]:
        for s in part["segments"]:
            if "lp" in s:
                n_lp += 1
                allp.append(s["lp"])
                if s["lp"] < LOW_LP:
                    lows.append((part["page"], s["start"], s["lp"], s["text"]))
    lows.sort(key=lambda x: x[2])
    if n_lp == 0:
        print("\n没有 lp 字段（重新跑一次 transcribe 才能拿到置信度）")
    elif lows:
        print(f"\n低置信句 {len(lows)} 条（avg_logprob < {LOW_LP}）：")
        for p, st, lp, t in lows[: args.show_low]:
            print(f"  P{p:02d} {int(st//60):02d}:{int(st%60):02d} lp={lp:+.2f}  {t[:50]}")
    else:
        print(f"\n低置信句 0 条：{n_lp} 句的 avg_logprob 最小 {min(allp):+.3f}，全部高于 {LOW_LP}")
        print("  → VAD 已切掉噪声音频，剩余句子模型都很自信，avg_logprob 在此场景不是有效信号；"
              "纠错只能靠词表 + 搭配规则。")

    # 纠错结果必须落盘：下游（chapters / termsheet / gate / ask）读的都是这份
    if not args.emit:
        print("\n（未加 --emit，只统计，不写任何文件）")
        return
    write_atomic(out / "segments_fixed.json",
                 json.dumps(seg, ensure_ascii=False, indent=0))
    print(f"已写出 segments_fixed.json（{len(changes)} 处）")
    lines = [f"# {seg['title']}", f"UP：{seg['owner']}　{seg['bv']}　{len(seg['parts'])}P",
             f"行业：{'、'.join(doms)}　已纠正 {len(changes)} 处", ""]
    for part in seg["parts"]:
        lines.append(f"\n## P{part['page']:02d} {part['part']}\n")
        for s in part["segments"]:
            t = f"{int(s['start']//60):02d}:{int(s['start']%60):02d}"
            lines.append(f"[{t}](https://www.bilibili.com/video/{seg['bv']}"
                         f"?p={part['page']}&t={int(s['start'])}) {s['text_fixed']}")
    open(out / "全课转写_已纠错.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")

    with open(out / "changes.md", "w", encoding="utf-8") as f:
        f.write(f"# 术语纠错报告 {args.bv}\n\n行业：{'、'.join(doms)}\n\n"
                f"共 {len(changes)} 处改动。格式：位置 · 规则 · 改前 → 改后\n\n")
        for c in changes:
            t = f"{int(c['start']//60):02d}:{int(c['start']%60):02d}"
            f.write(f"- **P{c['page']:02d} {t}** `{c['rule']}`（{c['src']}）\n")
            f.write(f"  - 前：{c['before']}\n  - 后：{c['after']}\n")
    print("\n已写出 全课转写_已纠错.md / changes.md")


if __name__ == "__main__":
    main()
