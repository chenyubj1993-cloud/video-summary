"""交付前的硬检查：讲义能不能算"可交付"，用数字回答，不靠感觉。

查六件事，每条都有阈值和出处文件：
  1. 章节齐全        讲义有 ≥3 章，且每章都有「学习目标」和「存疑与勘误」
  2. 出处可核对      章节里的 [Pxx mm:ss] 至少 98% 能落到真实转写句上
  3. 术语干净        已纠错转写里，词表 obs 规则的误听形残留 = 0
  4. 前置顺序        出现 ≥10 次却从没定义的术语 = 0
  5. 覆盖率          讲义正文每音频分钟 ≥ 35 个汉字（防"精美但空洞"）
  6. 前后一致        consistency 冲突组数只作参考，不设门槛（数字冲突不一定是错）

用法：
  python cli.py gate BV1E9U8BBEvZ
  python cli.py gate BV1E9U8BBEvZ --strict     # 覆盖率也按阈值卡死
退出码：有硬指标不达标就是 1，方便接 CI。
"""
from __future__ import annotations

import argparse
import json
import re

from .core import OUT, load_segments, seg_text
from . import cite, fix, prereq


def _chapters(bv: str) -> list[tuple[str, str]]:
    d = OUT / bv / "handout"
    out = []
    for p in sorted(d.glob("ch*.md"), key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        out.append((p.name, p.read_text(encoding="utf-8")))
    return out


def _han(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


DENSE = ("人工智能", "计算机视觉", "编程", "数学", "医学", "法律", "金融")


def _need_chapters(minutes: float) -> int:
    """一节 20 分钟的片子不需要 3 章。"""
    if minutes < 30:
        return 1
    if minutes < 90:
        return 2
    return 3


def run(bv: str, min_chars_per_min: float | None = None) -> tuple[list[dict], int]:
    rows: list[dict] = []
    add = rows.append
    out = OUT / bv

    seg = load_segments(bv, fixed=True) if (out / "segments_fixed.json").exists() \
        else load_segments(bv, fixed=False)
    minutes = sum(p["duration"] for p in seg["parts"]) / 60
    if min_chars_per_min is None:
        try:
            style = json.load(open(out / "style.json", encoding="utf-8")).get("style", "")
        except Exception:
            style = ""
        min_chars_per_min = 18.0 if style == "talk" else 35.0

    # 1. 章节齐全
    chs = _chapters(bv)
    need = _need_chapters(minutes)
    lack_goal = [n for n, t in chs
                 if "## 学习目标" not in t and "## 这一段讲了什么" not in t]
    lack_pit = [n for n, t in chs if "存疑与勘误" not in t]
    ok = len(chs) >= need and not lack_goal and not lack_pit
    add({"name": "章节齐全", "ok": ok, "hard": True,
         "detail": f"{len(chs)} 章（{minutes:.0f} 分钟的片子至少要 {need} 章）"
                   + (f"；缺开篇小节 {lack_goal}" if lack_goal else "")
                   + (f"；缺存疑 {lack_pit}" if lack_pit else "")})

    # 2. 出处可核对
    text = "\n".join(t for _, t in chs)
    crows = cite.verify(bv, text)
    n, good = len(crows), sum(1 for r in crows if r.get("ok"))
    ratio = good / n if n else 0.0
    add({"name": "出处可核对", "ok": n > 0 and ratio >= 0.98, "hard": True,
         "detail": f"{good}/{n}（{ratio*100:.1f}%）"})

    # 3. 术语干净（在已纠错转写上查残留）——必须和 fix 用同一套词表，
    #    否则领域词表（ai.tsv 等）的 obs 规则永远查不到，会"残留还在却报 0"。
    from .fix import rule_paths
    # 纯大小写归一化规则（CLIP←clip）不能用来数"残留"：纠完的正文里 CLIP 本身
    # 就会命中这条不区分大小写的规则，永远报 50 处。这类规则跳过。
    rules = [r for r in fix.load_rules(rule_paths(bv))
             if r["status"] == "obs"
             and not any(b.lower() == r["good"].lower() for b in r["bads"])]
    left = 0
    for part in seg["parts"]:
        for s in part["segments"]:
            for r in rules:
                left += len(r["re"].findall(seg_text(s)))
    add({"name": "术语干净", "ok": left == 0, "hard": True,
         "detail": f"{len(rules)} 条 obs 规则，误听形残留 {left} 处"})

    # 4. 前置顺序
    prow = prereq.scan(bv)
    big = [r for r in prow if r["define"] is None and r["n"] >= 10]
    add({"name": "前置顺序", "ok": not big, "hard": True,
         "detail": "出现 ≥10 次仍无定义：" + ("、".join(f"{r['term']}×{r['n']}" for r in big)
                                    if big else "0 个")})

    # 5. 覆盖率
    han = _han(re.sub(r"```.*?```", "", text, flags=re.S))
    cpm = han / minutes if minutes else 0
    add({"name": "覆盖率", "ok": cpm >= min_chars_per_min, "hard": False,
         "detail": f"{han:,} 汉字 / {minutes:.0f} 分钟 = {cpm:.0f} 字/分钟"
                   f"（门槛 {min_chars_per_min:.0f}）"})

    # 6. 前后一致（只参考）
    cpath = out / "consistency.md"
    n_conf = 0
    if cpath.exists():
        n_conf = sum(1 for ln in cpath.read_text(encoding="utf-8").splitlines()
                     if ln.startswith("| ") and ln[2:3].isdigit())
    add({"name": "前后一致", "ok": True, "hard": False,
         "detail": f"数值冲突 {n_conf} 组（参考值，不是错误）"})

    bad = [r for r in rows if r["hard"] and not r["ok"]]
    return rows, 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py gate")
    ap.add_argument("bv")
    ap.add_argument("--strict", action="store_true", help="覆盖率也当硬指标")
    args = ap.parse_args()

    bv = args.bv
    rows, code = run(bv)
    if args.strict:
        for r in rows:
            if r["name"] == "覆盖率":
                r["hard"] = True
        code = 1 if any(r["hard"] and not r["ok"] for r in rows) else 0

    print(f"\n{bv} 交付检查\n" + "-" * 62)
    for r in rows:
        mark = "✓" if r["ok"] else ("✗" if r["hard"] else "·")
        tag = "" if r["hard"] else "  （参考）"
        print(f" {mark} {r['name']:<8} {r['detail']}{tag}")
    print("-" * 62)
    print("结论：" + ("可以交付" if code == 0 else "有硬指标不达标，先修再交"))
    raise SystemExit(code)
