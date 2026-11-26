"""跨期整合：把一个合集里的多期当成一门课来读。

三件事（其余都先从简）：
  1. 课程地图：这一系列按什么顺序讲，每期解决什么问题、留下什么；
  2. 去重：同一个概念在第 3 期讲过、第 11 期又讲一遍 → 只保留第一次，后面标「详见 E03」；
  3. 修正：后一期和前一期的说法不一致 → 单独列表，不替人判断谁对。

用法：
  python cli.py series BV1qqHZ6UEJy --max 3          # 从某一集所属的合集取前 3 期
  python cli.py series BV1qqHZ6UEJy --ids 9266904 --max 5
  python cli.py series BV1qqHZ6UEJy --bvs BV1..,BV2  # 直接给几集（顺序即讲述顺序）

产物：out/_series/<season_id>/series.md + points.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import claims as claims_mod
from .core import OUT, api, fmt, link, load_segments, seg_text
from .llm import LLM, LLMError
from .source import parse

SYS = ("你是课程编辑。只依据给的材料写，不许补充材料之外的知识；"
       "数字和术语原样保留；拿不准就不写。")

POINT = """下面是一集视频的转写，每行开头是 [Pxx mm:ss] 出处。

{text}

请输出这一集的要点，格式严格如下（每行一条，不要别的话）：
要点|一句话说清这期讲了什么、结论是什么|Pxx mm:ss
术语|概念名（只要名字，不要解释）|这一期给的定义（≤40 字）|Pxx mm:ss

要求：要点 4–8 条，术语 0–6 个，时间码从材料里抄，不许自造。"""


def season_of(bv: str) -> dict:
    """一集如果属于某个合集，返回 {id, title, mid, episodes:[{bvid,title,duration}]}。"""
    r = api(f"/x/web-interface/view?bvid={bv}")
    d = r.get("data") or {}
    sea = d.get("ugc_season") or {}
    if not sea:
        return {}
    eps = []
    for sec in sea.get("sections") or []:
        for ep in sec.get("episodes") or []:
            arc = ep.get("arc") or {}
            eps.append({"bvid": arc.get("bvid") or ep.get("bvid"),
                        "title": arc.get("title") or ep.get("title", ""),
                        "duration": arc.get("duration", 0)})
    return {"id": sea.get("id"), "title": sea.get("title", ""),
            "mid": (sea.get("mid") or d.get("owner", {}).get("mid")), "episodes": eps}


def fetch_season(mid: int, season_id: int, limit: int = 20) -> list[dict]:
    r = api(f"/x/polymer/web-space/seasons_archives_list?mid={mid}"
            f"&season_id={season_id}&page_num=1&page_size={limit}")
    d = r.get("data") or {}
    out = []
    for it in (d.get("archives") or [])[:limit]:
        out.append({"bvid": it.get("bvid"), "title": it.get("title", ""),
                    "duration": it.get("duration", 0)})
    return out


def points_of(bv: str, llm: LLM, pages: int = 0) -> list[dict]:
    seg = load_segments(bv, fixed=True)
    if pages:
        seg = {"title": seg.get("title", ""), "parts": seg["parts"][:pages]}
    lines = []
    for part in seg["parts"]:
        for s in part["segments"]:
            t = seg_text(s).strip()
            if t:
                lines.append(f"[P{part['page']:02d} {fmt(s['start'])}] {t}")
    text = "\n".join(lines)
    if len(text) > 24000:                     # 太长就均匀抽，保住首尾
        step = len(text) // 24000 + 1
        text = "\n".join(lines[::step])
    raw = llm.chat(POINT.format(text=text), system=SYS, max_tokens=1200)
    rows = []
    for line in raw.splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) >= 3 and c[0] == "要点":
            rows.append({"kind": "要点", "text": c[1], "at": c[2], "bv": bv,
                         "title": seg.get("title", "")})
        elif len(c) >= 4 and c[0] == "术语":
            rows.append({"kind": "术语", "text": c[1], "def": c[2], "at": c[3],
                         "bv": bv, "title": seg.get("title", "")})
    return rows


def concept_key(term: str) -> str:
    t = re.sub(r"\s+", "", term).lower()
    return t


def render(bv0: str, info: dict, eps: list[dict], rows: dict[str, list[dict]],
           fixed: list[dict], pages: int = 0) -> Path:
    base = OUT / "_series" / str(info.get("id") or bv0)
    base.mkdir(parents=True, exist_ok=True)
    L = [f"# 《{info.get('title') or '系列'}》跨期整合", "",
         f"> 共 {len(eps)} 期，按合集顺序。每期先用本地 ASR 转写，再抽要点与术语，"
         f"然后跨期比对：哪些概念重复讲过、哪些说法前后不一致。"]
    if pages:
        L.append(f"> 抽样说明：每期只用了前 {pages} 个分P，结论以这一部分为准。")
    L += ["", "## 一、课程地图", ""]
    for i, e in enumerate(eps, 1):
        mine = rows.get(e["bvid"], [])
        pts = [r for r in mine if r["kind"] == "要点"]
        term = [r for r in mine if r["kind"] == "术语"]
        L += [f"### E{i:02d}　{e['title'][:44]}", "",
              f"`{e['bvid']}`　{fmt(e.get('duration', 0))}"
              f"　[看原片]({link(e['bvid'], 1, 0).rsplit('&t=', 1)[0]})", ""]
        for r in pts[:6]:
            L.append(f"- {r['text']} `{r['at']}`")
        if term:
            L += [""]
            for r in term:
                L.append(f"- **{r['text']}**：{r.get('def', '')} `{r['at']}`")
        L.append("")

    first: dict[str, int] = {}
    dupes: list[tuple[str, int, int]] = []
    for i, e in enumerate(eps, 1):
        for r in rows.get(e["bvid"], []):
            if r["kind"] != "术语":
                continue
            k = concept_key(r["text"])
            if k in first and first[k] != i:
                dupes.append((r["text"], first[k], i))
            else:
                first.setdefault(k, i)
    L += ["## 二、重复讲过的概念", ""]
    if dupes:
        L += ["| 概念 | 第一次讲 | 又讲一次 | 建议 |", "|---|---|---|---|"]
        for t, a, b in dupes:
            L.append(f"| {t} | E{a:02d} | E{b:02d} | 只看 E{a:02d}；E{b:02d} 扫一眼是否加了新东西 |")
        L += ["", f"共 {len(dupes)} 个概念被重复解释，占全部术语的 "
                  f"{len(dupes) / max(1, len(first)) * 100:.0f}%。"]
    else:
        L.append("没有发现重复解释的概念。")
    L.append("")

    L += ["## 三、前后不一致", ""]
    if fixed:
        L += ["| 主体 | 说法 A | 说法 B | 为何可疑 |", "|---|---|---|---|"]
        for r in fixed:
            a, b = r["a"], r["b"]
            L.append(f"| {r['subject']} | {a['value']}（E{a.get('ep', '?')}） "
                     f"| {b['value']}（E{b.get('ep', '?')}） | {r.get('why', '')} |")
        L += ["", "这些是同一主体在两期里被说出两个不同的值。"
                  "可能是口误，也可能是不同实验的设定——点进去听再判断。"]
    else:
        L.append("没有发现跨期数值冲突。")
    L.append("")
    p = base / "series.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p


def run(bv0: str, llm: LLM, max_eps: int = 5, ids: str = "",
        bvs: str = "", pages: int = 0) -> Path:
    bv0, _ = parse(bv0) if bv0 else ("", 0)
    info = {"id": 0, "title": "", "mid": 0, "episodes": []}
    if bvs:
        eps = [{"bvid": b.strip(), "title": "", "duration": 0} for b in bvs.split(",") if b.strip()]
        info = season_of(bv0) if bv0 else {}
        if not info:
            info = {"id": 0, "title": "自定义序列", "mid": 0, "episodes": []}
        for e in eps:
            e["title"] = load_segments(e["bvid"], fixed=True).get("title", e["bvid"])
    else:
        info = season_of(bv0)
        if not info:
            raise SystemExit("这一集不属于任何合集。用 --bvs BV1,BV2 直接指定几期。")
        eps = info["episodes"]
        if ids:
            eps = fetch_season(info["mid"], int(ids), max_eps)
    keep = {b.strip() for b in bvs.split(",")} if bvs else None
    if keep:
        eps = [e for e in eps if e["bvid"] in keep]
    eps = eps[:max_eps]
    print(f"[series] 《{info.get('title', '')}》{len(eps)} 期")

    rows: dict[str, list[dict]] = {}
    all_claims = []
    for i, e in enumerate(eps, 1):
        bv = e["bvid"]
        print(f"  E{i:02d} {bv} {e.get('title', '')[:36]} …", flush=True)
        try:
            get = points_of(bv, llm, pages=pages)
        except (LLMError, KeyError) as ex:
            print(f"    [skip] {ex}")
            continue
        rows[bv] = get
        for r in get:
            r["ep"] = i
        seg = load_segments(bv, fixed=True)
        for c in claims_mod.extract(seg)[:80]:
            c["ep"] = i
            all_claims.append(c)

    fixed = []
    if len(eps) > 1:
        by: dict[str, list[dict]] = {}
        for c in all_claims:
            by.setdefault(c["subject"], []).append(c)
        for subj, items in by.items():
            eps_of = {c["ep"] for c in items}
            vals = {c["value"] for c in items}
            if len(eps_of) > 1 and len(vals) > 1:
                a = items[0]
                b = next((c for c in items if c["value"] != a["value"]), None)
                if b:
                    fixed.append({"subject": subj, "a": a, "b": b,
                                  "why": "同一主体在两期里给了不同数值"})
        fixed = fixed[:20]

    p = render(bv0, info, eps, rows, fixed, pages=pages)
    (p.parent / "points.json").write_text(
        json.dumps({"info": info, "rows": rows, "fixed": fixed},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {p}（{len(eps)} 期，{len(fixed)} 组跨期冲突）")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py series")
    ap.add_argument("bv")
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--ids", default="", help="合集 id（默认从这一集自动取）")
    ap.add_argument("--bvs", default="", help="直接给几集，逗号分隔，顺序即讲述顺序")
    ap.add_argument("--pages", type=int, default=0, help="每集只看前 N 个分P（抽样试跑用）")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    llm = LLM(base=args.api_base, key=args.api_key, model=args.model)
    if not llm.ready:
        raise SystemExit("没有 API key。设 OPENAI_API_KEY 再跑。")
    run(args.bv, llm, max_eps=args.max, ids=args.ids, bvs=args.bvs, pages=args.pages)
