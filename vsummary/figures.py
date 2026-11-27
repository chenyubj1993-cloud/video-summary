"""配图：从已经抽好的画面帧里挑几张，插到讲对应内容的那一段旁边。

不追求"每页都配图"，只解决一件事：**有些东西念不出来**——架构图、对比表、
代码在跑的样子。grep 不出来的东西，得让读者看见。

挑选逻辑（都能解释）：
  1. 只在有内容的分P 里挑：OCR 行数 < 4 的不算；
  2. 近重复帧去掉：OCR 字符 3-gram 余弦 > 0.75 视作同一页，只留最早那张；
  3. 按"信息量"排序：代码符号（=(){}[]#_/*+-<>）越多、字数越多，越可能是板书/代码；
  4. 每章最多 per_chapter 张，总上限 total，尽量摊到不同分P 上；
  5. 落位：找同分P、时间差 ≤30 秒的最近一条 `[Pxx mm:ss]`，插在那段话后面；
     找不到出处的帧不插（宁可少，不要乱放）。

产物：
  out/<bv>/figures/fig{章}-{序}_P{分P}_{时间}.jpg   （缩到宽 900，控制体积）
  out/<bv>/figures.json
小节里写的是相对路径 `figures/...`，讲义打包成单文件 HTML 时会转成内嵌图。

用法：
  python cli.py figures BV1E9U8BBEvZ               # 挑图并插入章节
  python cli.py figures BV1E9U8BBEvZ --dry         # 只看会插哪些
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from .core import OUT, fmt

CITE = re.compile(r"\[P\s*(\d{1,2})[·:：]?\s*(\d{1,2}):(\d{2})\]")
HEAD = re.compile(r"^#{2,3}\s+(.+)$", re.M)
CODEY = set("=(){}[]#_/*+-<>:")
FORBID = ("存疑与勘误", "本章小结", "学习目标", "自测题")
FIGBLOCK = re.compile(r"\n{2,}!\[图 [^\]]*\]\(figures/[^)]*\)\n\*[^\n]*\*\n")


def ocr_lines(f: dict) -> list[str]:
    """帧的 OCR 行。老结果存 `_items`（带坐标），重建的 frames.json 只有 `ocr` 文本。"""
    items = f.get("_items")
    if items:
        return [t for _x, _y, t in items]
    return [ln for ln in (f.get("ocr") or "").split("\n") if ln.strip()]


def ocr_text(f: dict) -> str:
    return "\n".join(ocr_lines(f))


def grams(s: str, n: int = 3) -> Counter:
    s = re.sub(r"\s+", "", s.lower())
    return Counter(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def informative(f: dict) -> float:
    items = ocr_lines(f)
    text = "".join(items)
    if len(items) < 4 or len(text) < 12:
        return 0.0
    codey = sum(1 for ch in text if ch in CODEY)
    digits = sum(1 for ch in text if ch.isdigit())
    return len(text) * 0.1 + codey * 3 + digits * 0.5


def dedupe(frames: list[dict]) -> list[dict]:
    kept: list[tuple[Counter, dict]] = []
    for f in frames:
        g = grams(ocr_text(f))
        if any(cosine(g, k) > 0.75 for k, _ in kept):
            continue
        kept.append((g, f))
    return [f for _, f in kept]


def save(frame: dict, dest: Path, width: int = 900) -> bool:
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        im = Image.open(frame["path"]).convert("RGB")
    except (FileNotFoundError, OSError) as e:
        print(f"[warn] 缺帧文件 {frame['path']}（{e}），跳过这张图", flush=True)
        return False
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(dest, "JPEG", quality=80, optimize=True)
    return True


def nearest_cite(text: str, page: int, t: float, tol: float = 30.0):
    """同分P、时间最近的引用 → 插入位置（该引用所在段落的段尾）；找不到返回 None。"""
    best = None
    for m in CITE.finditer(text):
        p, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if p != page:
            continue
        d = abs(mm * 60 + ss - t)
        if d <= tol and (best is None or d < best[0]):
            best = (d, m.end())
    if not best:
        return None
    end_para = text.find("\n\n", best[1])
    return best[1] if end_para < 0 else end_para


def run(bv: str, per_chapter: int = 3, total: int = 18, dry: bool = False) -> dict:
    fp = OUT / bv / "frames.json"
    if not fp.exists():
        print(f"[skip] {bv} 没有抽过帧（frames.json 不存在），跳过配图。"
              f"要配图先跑：python cli.py frames {bv} --pages 1,2", flush=True)
        return {"figures": 0, "skipped": True}
    frames = dedupe(json.load(open(fp, encoding="utf-8")))

    chapters = []
    for p in sorted((OUT / bv / "handout").glob("ch*.md"),
                    key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        chapters.append((int(re.sub(r"\D", "", p.stem) or 0), p, p.read_text(encoding="utf-8")))

    figdir = OUT / bv / "figures"
    rows = []
    used = 0
    taken: set[tuple[int, float]] = set()
    for num, path, text in chapters:
        text = FIGBLOCK.sub("", text).rstrip() + "\n"
        pages = {int(m.group(1)) for m in CITE.finditer(text)}
        cand = []
        for f in frames:
            if f["page"] not in pages or (f["page"], f["t"]) in taken:
                continue
            pos = nearest_cite(text, f["page"], f["t"])
            if pos is None:
                continue
            sc = informative(f)
            if sc <= 0:
                continue
            head = ""
            for m in HEAD.finditer(text, 0, pos):
                if m.group(1).strip() and not any(x in m.group(1) for x in FORBID):
                    head = m.group(1).strip()
            if not head:                       # 落在「学习目标」或章首的图，没有小节可挂，跳过
                continue
            cand.append((sc, pos, f, head))
        cand.sort(key=lambda x: -x[0])
        picks, per_page, seen_head = [], Counter(), set()
        for sc, pos, f, head in cand:
            if head in seen_head:              # 同一小节只放一张，避免三张同一段代码
                continue
            if per_page[f["page"]] >= 2:
                continue
            picks.append((pos, f, head))
            seen_head.add(head)
            per_page[f["page"]] += 1
            if len(picks) >= per_chapter:
                break
        if not picks:
            continue
        inserts, k = [], 0
        for pos, f, head in picks:
            if used >= total:
                break
            k += 1
            taken.add((f["page"], f["t"]))
            name = f"fig{num}-{k}_P{f['page']:02d}_{int(f['t'])//60:02d}m{int(f['t'])%60:02d}s.jpg"
            dest = figdir / name
            if not dry:
                if not save(f, dest):
                    continue
            stem = re.sub(r"^\d+(\.\d+)?\s*", "", head)[:28]
            cap = f"图 {num}-{k}　{stem}（P{f['page']:02d} {fmt(f['t'])} 画面）"
            block = f"\n\n![{cap}](figures/{name})\n*{cap}*\n"
            inserts.append((pos, block))
            rows.append({"page": f["page"], "t": f["t"], "chapter": num, "file": name,
                         "caption": cap})
            used += 1
        if inserts and not dry:
            for pos, block in sorted(inserts, reverse=True):
                text = text[:pos] + block + text[pos:]
            path.write_text(text, encoding="utf-8")
        if inserts:
            print(f"  ch{num}: 插 {len(inserts)} 张")

    if not dry:
        (OUT / bv / "figures.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n共 {used} 张{'（演练，未写入）' if dry else ''} → {figdir}")
    return {"n": used, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py figures")
    ap.add_argument("bv")
    ap.add_argument("--per-chapter", type=int, default=3)
    ap.add_argument("--total", type=int, default=18)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    run(args.bv, args.per_chapter, args.total, dry=args.dry)


if __name__ == "__main__":
    main()
