"""引用可追溯：校验并渲染正文里的 `[P07 12:34]` 时间码。

两条作用：
  1. 渲染——讲义里每个出处变成可点的深链，点开直接跳到视频那一秒；
  2. 校验——时间码是模型从素材里抄的，也可能抄错或编造。
     这里回头查 segments：分P 存不存在、时间点在不在时长内、
     那一秒附近（±15s）有没有对应的转写句子。查不到的不删，标成"存疑"。

原则：宁可标出来让人看见，也不要悄悄删掉——删掉等于替读者做了判断。
"""
from __future__ import annotations

import re

from .core import link, load_segments, read_text

PAT = re.compile(r"\[P\s*(\d{1,3})\s*[·:：]?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]")
TOL = 15.0          # 允许的秒级误差


def _hms(m: re.Match) -> int:
    """把匹配到的 (h)? m s 换成秒。单分P 超过 1 小时时 fmt() 会写成 1:22:07。"""
    h, mi, se = m.group(2), m.group(3), m.group(4)
    return (int(h) * 3600 if h else 0) + int(mi) * 60 + int(se)


def _label(m: re.Match) -> str:
    p, h, mi, se = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
    body = f"{int(h)}:{mi:02d}:{se:02d}" if h else f"{mi:02d}:{se:02d}"
    return f"P{p:02d} {body}"


def parse(text: str) -> list[tuple[int, int, str]]:
    """抽出所有引用，返回 [(page, 秒, 原始写法)]。"""
    return [(int(m.group(1)), _hms(m), m.group(0)) for m in PAT.finditer(text)]


def verify(bv: str, text: str) -> list[dict]:
    segs = load_segments(bv)
    parts = {p["page"]: p for p in segs["parts"]}
    rows = []
    for p, t, raw in parse(text):
        part = parts.get(p)
        if not part:
            rows.append({"page": p, "t": t, "raw": raw, "ok": False, "why": "没有这个分P"})
            continue
        if t > part["duration"] + 5:
            rows.append({"page": p, "t": t, "raw": raw, "ok": False, "why": "超出这一P 的时长"})
            continue
        near = any(s["start"] - TOL <= t <= s["end"] + TOL for s in part["segments"])
        rows.append({"page": p, "t": t, "raw": raw, "ok": near,
                     "why": "" if near else f"±{int(TOL)}s 内没有转写句子"})
    return rows


def stats(rows: list[dict]) -> tuple[int, int]:
    ok = sum(1 for r in rows if r["ok"])
    return ok, len(rows)


def annotate_html(bv: str, html: str) -> tuple[str, list[dict]]:
    """把 HTML 里的 [P07 12:34] 换成深链；查不到的标红。"""
    rows = verify(bv, html)

    def repl(m):
        p, t = int(m.group(1)), _hms(m)
        r = next((x for x in rows if x["page"] == p and x["t"] == t), None)
        ok = bool(r and r["ok"])
        cls = "cite" if ok else "cite bad"
        tip = "原文出处" if ok else f"存疑：{r['why'] if r else '未校验'}"
        return (f'<a class="{cls}" href="{link(bv, p, t)}" '
                f'title="{tip}">{_label(m)}</a>')

    return PAT.sub(repl, html), rows


def annotate_md(bv: str, md: str) -> str:
    """Markdown 版：换成标准链接，查不到的加 ⚠ 提示。"""
    rows = verify(bv, md)

    def repl(m):
        p, t = int(m.group(1)), _hms(m)
        r = next((x for x in rows if x["page"] == p and x["t"] == t), None)
        bang = "" if (r and r["ok"]) else " ⚠"
        return f"[{_label(m)}{bang}]({link(bv, p, t)})"

    return PAT.sub(repl, md)


def main() -> None:
    """对着目录里已有章节做一次体检：python cli.py cite BV号"""
    import argparse

    from .core import OUT

    ap = argparse.ArgumentParser(prog="cli.py cite")
    ap.add_argument("bv")
    ap.add_argument("--dir", default="handout", help="章节目录，默认 out/<bv>/handout")
    args = ap.parse_args()

    d = OUT / args.bv / args.dir
    files = sorted(d.glob("ch*.md"))
    if not files:
        raise SystemExit(f"{d} 里没有 ch*.md")
    total_ok = total = 0
    for f in files:
        text = read_text(f)
        rows = verify(args.bv, text)
        ok, n = stats(rows)
        total_ok += ok
        total += n
        bad = [r for r in rows if not r["ok"]]
        print(f"{f.name}: {ok}/{n} 通过" + (f"　{len(bad)} 处存疑" if bad else ""))
        for r in bad[:5]:
            print(f"    ✗ {r['raw']}  {r['why']}")
    if total:
        print(f"\n合计 {total_ok}/{total}（{total_ok/total*100:.0f}%）")
    else:
        print("\n正文里没有 [Pxx mm:ss] 形式的出处。")
