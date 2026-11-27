"""名词速查表：给讲义里出现的术语各配一句定义。

定义**只从原文里摘**，不自己编：
  1. 讲义正文里 `X 是/指/即/称为/全称/表示 …` 这种定义句，截取定义部分；
  2. 退一步用 `claims.json` 里 kind=定义 的说法（取出现最多的那个）；
  3. 都没有就标「正文未解释」，留给 `prereq` 的清单去处理，绝不凭空写。

用法：
  python cli.py termsheet BV1E9U8BBEvZ          # 只列有定义的
  python cli.py termsheet BV1E9U8BBEvZ --all    # 连没有定义的也列出来
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter

from .core import GLOSSARY, OUT
from .prereq import load_terms, read_chapters

OVERRIDE = GLOSSARY / "terms_override.tsv"

CUE = r"(?:是指|指的是|定义为|定义是|全称为|全称是|称为|叫做|即|所谓)"
BAD_HEAD = ("我们", "这个", "那个", "大家", "下面", "接下来", "然后", "所以", "它")
MAXLEN = 48


def core_pat(term: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9\-\.\+/ ]+", term):
        body = re.escape(term).replace(r"\ ", r"\s*")
        return r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"
    return re.escape(term)


def any_pat(aliases: list[str]) -> re.Pattern:
    # 英文术语大小写不固定（patch embedding / Embedding 都算出现），中文不受影响
    return re.compile("|".join(core_pat(a) for a in aliases if len(a) >= 2), re.IGNORECASE)


def clean(v: str) -> str:
    v = v.strip().strip("，,。；;：:")
    v = re.sub(r"\s+", " ", v)
    # 之前注入的注解会被下一轮当成"定义"再采一次，这里把 A（A） 折回 A
    for _ in range(3):
        m = re.fullmatch(r"(.+?)[（(](.+)[）)]", v)
        if not m or m.group(1).strip() != m.group(2).strip():
            break
        v = m.group(1).strip()
    if len(v) > MAXLEN:
        cut = v[:MAXLEN]
        for sep in ("，", "、", "；"):
            if sep in cut[12:]:
                cut = cut[: cut.rindex(sep)]
        v = cut
    if v.count("（") > v.count("）"):          # 截断留下的半个括号会让注解无法成对
        v = v[: v.index("（")] if "（" in v else v
    return v.strip("，, ")


SENT_START = r"(?:^|[\n。！？；])\s*"


def find_def(text: str, aliases: list[str]) -> str | None:
    """只认"句首就是这个词，后面紧跟判断词"的句子。

    为什么要这么严：中文里"X 是 …"的 X 很容易不是主语（
    `Transformer 的全连接结构不知道 patch 位置` 会被错当成 Transformer 的定义）。
    宁可摘不到、交给模型按上下文写，也不要给人一句错的定义。
    """
    for term in aliases:
        p = core_pat(term)
        pats = [
            # 句首：术语（它）是/指/即/称为 …
            SENT_START + r"(?:所谓|什么是|什么叫)?\s*" + p
            + r"\s*(?:，|,)?\s*(?:它)?\s*" + CUE + r"\s*([^。；\n]{4,70})",
            # 句首：术语 的核心/思想/做法/流程/目标 (就)是 …
            SENT_START + r"(?:所谓|什么是|什么叫)?\s*" + p
            + r"\s*的\s*(?:核心|核心思想|基本思想|思想|做法|流程|定义|目标|作用|思路|"
              r"基本思路|关键|本质)\s*(?:就)?(?:是|指)\s*([^。；\n]{4,70})",
            # 任何位置的括号解释：术语（一句话说明）
            p + r"\s*[（(]([^）)]{6,60})[）)]",
        ]
        for pat in pats:
            for m in re.finditer(pat, text):
                v = clean(m.group(1))
                if len(v) >= 4 and not v.startswith(BAD_HEAD):
                    return v
    return None


def claims_defs(bv: str) -> dict[str, str]:
    p = OUT / bv / "claims.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    claims = data.get("claims", []) if isinstance(data, dict) else data
    by: dict[str, Counter] = {}
    for c in claims:
        if c.get("kind") != "定义" or not c.get("value"):
            continue
        by.setdefault(str(c.get("subject", "")).lower(), Counter())[c["value"]] += 1
    return {k: v.most_common(1)[0][0] for k, v in by.items()}


def load_override() -> dict[str, str]:
    """人工修订：{小写术语: 定义}。定义为空串表示"这词不是概念，删掉"。"""
    out: dict[str, str] = {}
    if not OVERRIDE.exists():
        return out
    for line in OVERRIDE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        c = line.split("\t")
        out[c[0].strip().lower()] = c[1].strip() if len(c) > 1 else ""
    return out


def build(bv: str, only_defined: bool = True, use_claims: bool = False) -> list[dict]:
    terms = load_terms()
    overrides = load_override()
    chapters = read_chapters(bv)          # [(标题, 章号, 屏蔽后的正文)]
    cdefs = claims_defs(bv) if use_claims else {}
    rows = []

    for key, info in terms.items():
        aliases = info["aliases"]
        pat = any_pat(aliases)
        first_ch = None
        for _title, num, text in chapters:
            if pat.search(text):
                first_ch = num
                break
        if first_ch is None:
            continue                          # 讲义里根本没出现，不列

        defn, src = None, ""
        for title, num, text in chapters:
            if num < first_ch:
                continue
            d = find_def(text, aliases)
            if d:
                defn, src = d, f"第 {num} 章"
                break
        if not defn and use_claims:
            cand = cdefs.get(key)
            if cand and cand.strip():
                defn, src = clean(cand), "claims.json"
        if not defn and (info.get("note") or "").strip():
            note = info["note"].strip()
            # 备注只当兜底：中文的更可信；纯英文的只有够长（像个完整说法）才用，
            # 否则会出现「jigsaw puzzle」「infoNCE」这种等于没解释的行
            if len(note) >= 4 and (re.search(r"[\u4e00-\u9fff]", note) or len(note) >= 15):
                defn, src = note, "词表备注"
        # 人工修订优先于"没定义"：override 里写了一句定义，就不该被 only_defined 过滤掉
        ovd = overrides.get(key)
        if ovd:
            defn, src = ovd, "人工修订"
        if not defn and only_defined:
            continue
        rows.append({"term": info["name"], "def": defn or "—",
                     "src": src or "正文未解释", "ch": first_ch,
                     "ctx": first_context(chapters, pat)})
    rows.sort(key=lambda r: (r["ch"], r["term"].lower()))
    return apply_override(rows)


def apply_override(rows: list[dict]) -> list[dict]:
    ov = load_override()
    out = []
    for r in rows:
        key = r["term"].lower()
        if key in ov:
            if not ov[key]:
                continue                       # 人工判定不是概念，删
            r = dict(r)
            r["def"] = ov[key]
            r["src"] = "人工修订"
        out.append(r)
    return out


def first_context(chapters, pat: re.Pattern, span: int = 130) -> str:
    """第一次出现的上下文，给模型当抓手用（也方便人核对）。"""
    for title, num, text in chapters:
        m = pat.search(text)
        if m:
            a, b = max(0, m.start() - 40), min(len(text), m.end() + span)
            return re.sub(r"\s+", " ", text[a:b]).strip()
    return ""


def render_md(bv: str, rows: list[dict], n_terms: int) -> str:
    miss = sum(1 for r in rows if r["def"] == "—")
    out = [f"# {bv} 名词速查", "",
           f"术语表里 {n_terms} 个词，讲义中出现过 {len(rows)} 个："
           f"{len(rows) - miss} 个能摘到原文定义，{miss} 个从头到尾没解释。", "",
           "定义一律从讲义原句里截，截不到就写「—」，**不替它编**。", "",
           "| 术语 | 一句话 | 首次出现 | 定义出处 |", "|---|---|---|---|"]
    for r in rows:
        out.append(f"| **{r['term']}** | {r['def']} | 第 {r['ch']} 章 | {r['src']} |")
    return "\n".join(out) + "\n"


FILL_SYS = """你在给一份视频讲义编"名词速查表"。
我给你若干术语和它们在讲义里首次出现的那段原文。你只许依据这段原文写一句**中文**定义：
- 不超过 30 字，直接说它是什么，不要复述原句，不要加"在本课中"这类废话；
- 不许补充原文以外的知识，不许猜，宁可写 NA；
- 原文只提到名字、没给解释的，写 NA。
每行输出：术语<TAB>定义"""


def fill_llm(rows: list[dict], llm, batch: int = 20) -> int:
    todo = [r for r in rows if r["def"] == "—" and r["ctx"]]
    filled = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        prompt = "\n\n".join(f"术语：{r['term']}\n上下文：{r['ctx'][:240]}" for r in chunk)
        ans = llm.chat(prompt, system=FILL_SYS, temperature=0.1)
        for line in ans.splitlines():
            line = line.strip()
            if "\t" not in line:
                continue
            t, d = line.split("\t", 1)
            t, d = t.strip().strip("：:* "), d.strip()
            if not d or d.upper().startswith("NA"):
                continue
            for r in chunk:
                if r["term"].lower() == t.lower():
                    r["def"] = clean(d)
                    r["src"] = f"第 {r['ch']} 章（据上下文改写）"
                    filled += 1
    return filled


def run(bv: str, only_defined: bool = True, use_claims: bool = False,
        llm=None, write: bool = True) -> list[dict]:
    n_terms = len(load_terms())
    rows = build(bv, only_defined=only_defined, use_claims=use_claims)
    if llm is not None:
        n = fill_llm(rows, llm)
        if n:
            print(f"[llm] 补了 {n} 个定义（只用上下文，写不出的保持原样）")
        if only_defined:
            rows = [r for r in rows if r["def"] != "—"]
    if write:
        p = OUT / bv / "termsheet.md"
        p.write_text(render_md(bv, rows, n_terms), encoding="utf-8")
        (OUT / bv / "termsheet.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        miss = sum(1 for r in rows if r["def"] == "—")
        print(f"→ {p}（出现过的词 {len(rows)} 个，有定义 {len(rows)-miss}，没定义 {miss}）")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py termsheet")
    ap.add_argument("bv")
    ap.add_argument("--all", action="store_true", help="连没有定义的也列出来")
    ap.add_argument("--llm", action="store_true", help="缺定义的用模型按上下文补（可能不准）")
    ap.add_argument("--claims", action="store_true", help="退而用 claims.json 的说法凑数")
    ap.add_argument("--api-base")
    ap.add_argument("--api-key")
    ap.add_argument("--model", default=None)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    llm = None
    if args.llm:
        from .llm import LLM
        llm = LLM(base=args.api_base, key=args.api_key, model=args.model)
        if not llm.ready:
            raise SystemExit("开了 --llm 但没有 key：设 OPENAI_API_KEY 或传 --api-key")
    rows = run(args.bv, only_defined=not args.all, use_claims=args.claims, llm=llm)
    for r in rows[: args.top]:
        print(f"  {r['term']:<16} [{r['src']:<10}] {r['def'][:46]}")
    if llm is not None:
        print("[llm]", llm.stats())
