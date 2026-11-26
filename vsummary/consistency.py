"""一致性检测：把转写里的"数字断言"抽出来，找同一件事前后说法不一致的地方。

思路（不需要大模型）：
  1. 只认紧邻结构——「属性词 + 数字」（端口是 2400）或「数字 + 单位」（12 个 block）；
  2. 用属性词前面最近的领域名词做实体，得到 (实体, 属性, 值)；
  3. 同一 (实体, 属性) 出现两个以上不同值 → 报冲突。

它能抓住"前面说 4096、后面说 4097"这类数值矛盾，抓不住不带数字的概念矛盾。
课里数字很密，靠"离得最近的数字"会大量误抓，所以宁可漏、不可错。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import OUT, fmt, link, load_segments, seg_text

# ---------------------------------------------------------------- 抽取规则
# (属性名, 正则, 取值组号, 是否低基数属性)
RULES = [
    ("端口", r"(?:localhost|本地|端口号?|port)[^0-9]{0,4}(\d{3,5})", 1, True),
    ("端口", r"(\d{3,5})\s*的?(?:这个|这样一个)?端口", 1, True),
    ("学习率", r"(?:学习率|learning\s*rate|lr)\s*(?:呢|是|为|取|设置成|设成|=|：|:)?\s*(\d*\.?\d+)", 1, True),
    ("batch", r"batch\s*(?:size|大小|批大小)?\s*(?:呢|是|为|取|设置成|设成|=)?\s*(\d{1,6})", 1, True),
    ("epoch", r"epoch\s*(?:呢|是|为|取|设置成)?\s*(\d{1,6})", 1, True),
    ("温度系数", r"(?:温度|τ|tau)\s*(?:系数)?\s*(?:呢|是|为|取|=)?\s*(0?\.\d+|\d+)", 1, True),
    ("维度", r"(?:维度|维数|dim)\s*(?:呢|是|为|取|就是|=)?\s*的?\s*(\d{2,5})", 1, False),
    ("维度", r"(\d{2,5})\s*的?\s*(?:这个|一个|这样一个)?(?:维度|维)", 1, False),
    ("层数", r"(\d{1,3})\s*个?\s*(?:这个|这样一个)?\s*(?:block|Block|层|layer|Layer)", 1, False),
    ("头数", r"(\d{1,3})\s*个?\s*(?:head|头)", 1, False),
    ("类别数", r"(?:类别数|分类数|class\s*num)\D{0,4}(\d{1,6})", 1, True),
]
DIM = re.compile(r"(?<![\d.])(\d{1,5})\s*[×xX*乘]\s*(\d{1,6})(?![\d.])")

CUT = "。！？；\n，,、：:"
NOUNS = ["memory bank", "batch size", "输入序列", "序列长度", "特征", "向量", "logits",
         "encoder", "decoder", "队列", "block", "patch", "词表", "图像块", "图像", "序列",
         "模型", "样本", "类别", "维度", "head", "token", "tokens", "损失", "label", "标签"]
LOW_CARD = {a for a, _, _, low in RULES if low} | {"维度对"}
ATTR_OF_RULE = {r[0] for r in RULES}


@dataclass
class Fact:
    entity: str
    attr: str
    value: str
    text: str
    page: int
    t: float
    raw: str


def _entity(text: str, pos: int, attr: str) -> str:
    """取属性词前面最近的领域名词当实体；没有就只留属性名。"""
    head = text[max(0, pos - 16):pos]
    low = head.lower()
    best, best_i = "", -1
    for n in NOUNS:
        i = low.rfind(n.lower())
        if i > best_i:
            best, best_i = n, i
    return f"{best}·{attr}" if best and best != attr else attr


def _norm(v: str) -> str:
    v = v.strip()
    if re.fullmatch(r"\d+\.0+", v):
        v = v.split(".")[0]
    return v


def extract(segs: dict) -> list[Fact]:
    facts: list[Fact] = []
    for part in segs["parts"]:
        page = part["page"]
        for seg in part["segments"]:
            text = seg_text(seg)
            t = seg["start"]
            seen: set[tuple[str, str]] = set()

            # 维度对：64乘4097。只按"第一个维度相同、第二个只差一两位"报冲突——
            # 课里形状组合太多（64×1024、64×4097 往往说的是不同对象），全比会全是噪声。
            for m in DIM.finditer(text):
                a, b = m.group(1), m.group(2)
                ent = _entity(text, m.start(), "维度")
                key = (ent, "维度对", m.group(0))
                if key not in seen:
                    seen.add(key)
                    facts.append(Fact(ent, "维度对", f"{a}×{b}", text, page, t, m.group(0)))

            for attr, pat, grp, _ in RULES:
                for m in re.finditer(pat, text, re.I):
                    val = _norm(m.group(grp))
                    if not re.fullmatch(r"\d+(?:\.\d+)?", val):
                        continue
                    ent = _entity(text, m.start(), attr)
                    key = (ent, attr, val)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append(Fact(ent, attr, val, text, page, t, m.group(0)))
    return facts


def _near(a: str, b: str) -> tuple[bool, str]:
    """两个值像不像"同一件事被说了两遍"：差一位数 / 差一个数量级 / 一个是另一个的后缀。"""
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False, ""
    if x == y:
        return False, ""
    lo, hi = min(x, y), max(x, y)
    if lo == 0:                      # 0 与任何非 0 值都不算"说重了"，也不能拿它做除数
        return False, ""
    if hi - lo == 1 and lo >= 1000:
        return True, "只差一"
    if hi / lo >= 9.5 and abs(hi / lo - round(hi / lo)) < 0.3:
        return True, f"差 {round(hi / lo)} 倍"
    sa, sb = str(int(lo)), str(int(hi))
    if len(sa) >= 3 and sb.endswith(sa):
        return True, "像是多打了一位"
    return False, ""


def _dim_near(a: str, b: str) -> bool:
    """两个形状 A×B 是否值得怀疑：第一个维度一样，第二个维度只差一（且都不是小数字）。"""
    if "×" not in a or "×" not in b:
        return False
    a1, _, a2 = a.partition("×")
    b1, _, b2 = b.partition("×")
    if a1 != b1:
        return False
    try:
        x, y = int(a2), int(b2)
    except ValueError:
        return False
    return abs(x - y) == 1 and min(x, y) >= 1000


def _mk(ent: str, attr: str, va: list[Fact], vb: list[Fact],
        conf: float, note: str = "") -> dict:
    def pack(fs):
        return [{"text": f.text, "page": f.page, "t": f.t, "raw": f.raw} for f in fs[:2]]
    return {"entity": ent, "attr": attr, "values": [va[0].value, vb[0].value],
            "counts": {va[0].value: len(va), vb[0].value: len(vb)},
            "items": {va[0].value: pack(va), vb[0].value: pack(vb)},
            "confidence": round(min(conf, 0.95), 2), "note": note}


def conflicts(facts: list[Fact], window: float = 180) -> list[dict]:
    """只在"同一分P、两分钟内"做比对，跨大段比较噪声太高。"""
    out: list[dict] = []
    seen: set[tuple] = set()

    by_page: dict[int, list[Fact]] = defaultdict(list)
    for f in facts:
        by_page[f.page].append(f)

    for page, fs in by_page.items():
        fs.sort(key=lambda f: f.t)
        for i, a in enumerate(fs):
            for b in fs[i + 1:]:
                if b.t - a.t > window:
                    break
                if b.t - a.t < 3:      # 同一句话里说的两个数字，不算前后矛盾
                    continue
                if a.attr != b.attr or a.value == b.value:
                    continue
                if a.attr == "维度对":
                    ok = _dim_near(a.value, b.value)
                    note = "大数字只差一"
                    conf = 0.75
                else:
                    ok, note = _near(a.value, b.value)
                    conf = 0.6 + (0.15 if a.attr in LOW_CARD else 0)
                key = (page, a.entity, b.entity, a.attr,
                       tuple(sorted((a.value, b.value))))
                if not ok or key in seen:
                    continue
                seen.add(key)
                out.append(_mk(a.entity if a.entity == b.entity else f"{a.entity} / {b.entity}",
                               a.attr, [a], [b], conf, note))

    # 低基数属性（端口、学习率……）允许跨分P，但要求两个值都反复出现
    low: dict[tuple[str, str], dict[str, list[Fact]]] = defaultdict(lambda: defaultdict(list))
    for f in facts:
        if f.attr in LOW_CARD and f.attr != "维度对":
            low[(f.entity, f.attr)][f.value].append(f)
    for (ent, attr), by_val in low.items():
        vals = [v for v, fs in by_val.items() if len(fs) >= 2]
        # 三个以上值也要报：取出现最多的当"主流"，其余逐个跟它配（原来只处理恰好 2 个）
        if len(vals) < 2:
            continue
        vals.sort(key=lambda v: -len(by_val[v]))
        main = vals[0]
        for other in vals[1:]:
            key = (ent, attr, tuple(sorted((main, other))))
            if key in seen:
                continue
            seen.add(key)
            out.append(_mk(ent, attr, by_val[main], by_val[other], 0.7, "两边都反复出现"))

    out.sort(key=lambda c: (-c["confidence"], c["entity"]))
    return out


# ---------------------------------------------------------------- 输出
def render_md(bv: str, rows: list[dict], n_facts: int, n_segs: int) -> str:
    h = [f"# 前后不一致清单 · {bv}", "",
         f"从 {n_segs:,} 句里抽出 {n_facts:,} 条带数字的断言，其中 {len(rows)} 组存在"
         f"「同一实体、同一属性、值不同」的情况。", "",
         "> 这份清单只做**机械比对**：它不判断谁对谁错，只指出「你这里说了两遍不一样的话」。",
         "> 置信度反映「值得看一眼」的程度，不反映「确实有错」。", ""]
    if not rows:
        h.append("没有发现数值冲突。")
        return "\n".join(h) + "\n"

    h += ["| # | 实体·属性 | 说法 A | 说法 B | 像什么 | 置信 |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(rows, 1):
        vs = c["values"]
        a = c["items"][vs[0]][0]
        b = c["items"][vs[1]][0]
        ca = f"**{vs[0]}** ×{c['counts'][vs[0]]}（P{a['page']:02d}·{fmt(a['t'])}）"
        cb = f"**{vs[1]}** ×{c['counts'][vs[1]]}（P{b['page']:02d}·{fmt(b['t'])}）"
        h.append(f"| {i} | {c['entity']} · {c['attr']} | {ca} | {cb} | "
                 f"{c.get('note') or '—'} | {c['confidence']:.2f} |")

    h += ["", "## 逐条原文", ""]
    for i, c in enumerate(rows, 1):
        h.append(f"### {i}. {c['entity']} · {c['attr']}")
        for v in c["values"]:
            for it in c["items"][v]:
                h.append(f"- **{v}** · [{it['page']:02d} {fmt(it['t'])}]"
                         f"({link(bv, it['page'], it['t'])})　{it['text']}")
        h.append("")
    return "\n".join(h) + "\n"


def run(bv: str, write: bool = True) -> tuple[Path, list[dict]]:
    segs = load_segments(bv)
    facts = extract(segs)
    rows = conflicts(facts)
    n_segs = sum(len(p["segments"]) for p in segs["parts"])
    md = render_md(bv, rows, len(facts), n_segs)
    p = OUT / bv / "consistency.md"
    if write:
        p.write_text(md, encoding="utf-8")
        (OUT / bv / "consistency.json").write_text(
            json.dumps({"facts": [asdict(f) for f in facts], "conflicts": rows},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    return p, rows


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="cli.py consistency")
    ap.add_argument("bv")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    p, rows = run(args.bv)
    if args.quiet:
        print(f"{len(rows)} 组冲突 → {p}")
        return
    segs = load_segments(args.bv)
    n = sum(len(x["segments"]) for x in segs["parts"])
    print(f"{n:,} 句 → {len(rows)} 组「同一属性说了两个值」→ {p}")
    for c in rows:
        vs = c["values"]
        a, b = c["items"][vs[0]][0], c["items"][vs[1]][0]
        print(f"  [{c['confidence']:.2f}] {c['entity']}·{c['attr']}: "
              f"{vs[0]}（P{a['page']:02d} {fmt(a['t'])}） vs "
              f"{vs[1]}（P{b['page']:02d} {fmt(b['t'])}）  {c.get('note') or ''}")
        print(f"        {a['text'][:60]}")
        print(f"        {b['text'][:60]}")
