"""给术语首次出现处补一句话注解，让讲义能直接读下去。

重写章节时，原来手工加的括号注解会一起没掉；这个命令把它变成可重复的一步：
用 `termsheet.json` 里那一句话，补在**全书第一次出现**的位置上（已经解释过的不动）。

幂等：同一个术语只补一次；后面紧跟「（」的视为已经注解过。

用法：
  python cli.py annotate BV1E9U8BBEvZ           # 补注解（会改 ch*.md）
  python cli.py annotate BV1E9U8BBEvZ --dry     # 只看会补哪些
"""
from __future__ import annotations

import argparse
import json
import re

from . import prereq
from .core import OUT

MAXLEN = 46          # 太长的注解反而打断阅读


def load_defs(bv: str) -> dict[str, str]:
    p = OUT / bv / "termsheet.json"
    if not p.exists():
        raise SystemExit(f"没有 {p}，先跑 python cli.py termsheet {bv} --all")
    rows = json.load(open(p, encoding="utf-8"))
    return {r["term"]: r["def"].strip() for r in rows
            if r.get("def") and r["def"].strip() not in ("", "—")}


def strip(bv: str, dry: bool = False) -> int:
    """把上次插入的 `术语（定义）` 收回去，避免"注解被当成定义"再被采一次。"""
    defs = load_defs(bv)
    n = 0
    for p in sorted((OUT / bv / "handout").glob("ch*.md"),
                    key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        text = raw = p.read_text(encoding="utf-8")
        for term, d in defs.items():
            for t in (term,):
                raw = raw.replace(f"{t}（{d}）", t)
        if raw != text:
            n += text.count("（") - raw.count("（")
            if not dry:
                p.write_text(raw, encoding="utf-8")
    if n:
        print(f"收回 {n} 处旧注解{'（演练）' if dry else ''}")
    return n


def annotate(bv: str, dry: bool = False) -> int:
    defs = load_defs(bv)
    if not dry:
        strip(bv)                        # 先收回上次插入的，保证可重复执行
    terms = prereq.load_terms()          # 规范化：{小写: {name, aliases}}
    aliases: set[str] = set()
    for slot in terms.values():
        aliases.update(a for a in slot["aliases"] if a)
    chapters = []
    for p in sorted((OUT / bv / "handout").glob("ch*.md"),
                    key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        chapters.append([p, p.read_text(encoding="utf-8")])

    done: set[str] = set()
    total = 0
    for path, raw in chapters:
        masked = prereq.mask(raw)
        inserts: list[tuple[int, str]] = []
        occupied: list[tuple[int, int]] = []      # 已决定插入的区间，避免「掩码」插进「掩码重建」里
        # 长词优先：先安排「掩码重建」，短词「掩码」就避开这段
        for term, d in sorted(defs.items(), key=lambda kv: -len(kv[0])):
            key = term.lower()
            if key in done:
                continue
            slot = terms.get(key) or {"aliases": [term]}
            pats = [prereq.pattern(a) for a in slot["aliases"]]
            hits = prereq.find(masked, pats)
            if not hits:
                continue                     # 这章还没出现这个词
            picked = None
            for pos, is_def in hits:
                m = next((m for pat in pats for m in [pat.search(masked, pos)] if m), None)
                if m is None:
                    continue
                if any(not (m.end() <= s or m.start() >= e) for s, e in occupied):
                    continue                 # 落在别的术语的注解区间里，跳过这次出现
                nxt = masked[m.end():m.end() + 1]
                if nxt and "\u4e00" <= nxt <= "\u9fff" and any(
                        a.startswith(m.group(0) + nxt) for a in aliases if len(a) > len(m.group(0))):
                    continue                 # 「残差」插进「残差网络」里会把词切开
                line_start = masked.rfind("\n", 0, m.start()) + 1
                if masked[line_start:line_start + 1] == "#":
                    continue                 # 别往标题里塞定义
                picked = (pos, is_def, m)
                break
            if picked is None:
                continue
            pos, is_def, m = picked
            end = m.end()
            tail = raw[end:end + 2]
            if is_def or tail.startswith("（"):
                done.add(key)                # 第一次出现就在解释，或已经有括号
                continue
            if len(d) > MAXLEN:
                done.add(key)
                continue
            if term.lower() in d.lower() and len(d) - len(term) < 8:
                done.add(key)                # 「PyTorch 框架」这种等于没说
                continue
            inserts.append((end, f"（{d}）"))
            occupied.append((m.start(), end))
            done.add(key)

        if not inserts:
            continue
        for end, text in sorted(inserts, reverse=True):
            raw = raw[:end] + text + raw[end:]
        if not dry:
            path.write_text(raw, encoding="utf-8")
        total += len(inserts)
        print(f"  {path.name}: 补 {len(inserts)} 处 → {', '.join(t.strip('（）')[:16] for _, t in inserts)}")
    if not dry:
        stamp = OUT / bv / "annotate.json"
        stamp.write_text(json.dumps({"count": total}, ensure_ascii=False), encoding="utf-8")
    print(f"\n共补 {total} 处注解{'（演练，未写入）' if dry else ''}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py annotate")
    ap.add_argument("bv")
    ap.add_argument("--dry", action="store_true", help="只看会补哪些，不写文件")
    args = ap.parse_args()
    annotate(args.bv, dry=args.dry)


if __name__ == "__main__":
    main()
