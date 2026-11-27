"""断言抽取：把"这句话在主张什么"抽出来，用来找前后矛盾。

之前的 consistency 只看得见"挨着属性词的数字"，跨几十分钟的口误、不带数字的
概念矛盾全漏。这一层补上：从每句里抽 (主体, 类型, 值)，再按主体分组找冲突。

四种断言（全部靠句式，不需要大模型）：
  定义   X 是/就是/指的是/叫做 Y
  否定   X 不是/并非/不属于 Y
  数量   X 有/分为/包含 N 个
  比较   X 比 Y 好/快/大/准确

产物：out/<bv>/claims.md + claims.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import defaultdict

from .core import GLOSSARY, load_segments, OUT, read_lines, seg_text

CJK = r"\u4e00-\u9fff"
LEAD = re.compile(r"^(?:但是|所以|那么|然后|因为|而且|就是|其实|这个|那个|我们|大家|"
                  r"这里|那里|现在|接下来|比如说|也就是说|可以说|还有|另外)+")
TAIL = re.compile(r"(?:的|了|呢|啊|吧|嘛|呀|的话|这个|那个)+$")
# 主体允许空格（batch size 不能被截成 size），但不许以「不/没/也/都」结尾——
# 否则「MoCo不是自监督方法」会同时产出「MoCo不」和「MoCo」两个主体，肯定/否定永远打不起来。
SUBJ = rf"(?P<s>[{CJK}A-Za-z0-9](?:[{CJK}A-Za-z0-9\- ]{{0,18}}?)(?<![不没也都就]))"

DEF = re.compile(SUBJ + rf"(?:其实|就|大概|基本上?)?(?:是|指的是|叫做|称为|等于)"
                 rf"(?P<v>[^{CJK}，。；！？]*[{CJK}A-Za-z0-9\-][^。；！？]{{0,38}})")
NEG = re.compile(SUBJ + r"(?:不是|并非|不属于|不等于)(?P<v>[^。；！？]{2,38})")
CNT = re.compile(SUBJ + r"(?:一共|总共)?(?:有|包含|分为|分成|用了)"
                 r"(?P<n>\d{1,4})\s*(?P<u>个|种|类|层|块|张|步)")
# 比较句的值要截在形容词处：`MoCo比SimCLR快` 的值是 SimCLR，不是「SimCLR快」
CMP = re.compile(SUBJ + rf"(?:比|优于|好于|快于|大于|高于)"
                 rf"(?P<v>[{CJK}A-Za-z0-9\-]{{2,14}})")
CMP_CUT = re.compile(r"[好快大高准强低慢小差多]")

KINDS = [("定义", DEF), ("否定", NEG), ("数量", CNT), ("比较", CMP)]

# 主体必须是"像一个术语"，否则"这个模型""它的好处"这类会灌满误报
TERM_SUFFIX = ("模型", "网络", "方法", "算法", "编码器", "解码器", "损失", "函数",
               "模块", "结构", "数据集", "任务", "向量", "矩阵", "参数", "机制",
               "策略", "层", "头", "框架", "系统", "任务", "注意力", "掩码", "队列",
               "投影", "增广", "预训练", "微调", "分类器", "梯度", "优化器", "权重")
BAD_SUBJ = {"这", "那", "它", "他", "我们", "大家", "这个", "那个", "这里", "那里",
            "这个问题", "这种做法", "好处", "目的", "原因", "结果", "第一", "第二"}


def termish(s: str) -> bool:
    if s in BAD_SUBJ or len(s) < 2:
        return False
    if re.search(r"[A-Za-z]", s):
        return True
    return any(s.endswith(x) for x in TERM_SUFFIX)


QUES = re.compile(r"^(?:什么|啥|多少|多长|多大|多远|哪儿|哪里|哪个|谁|怎么样|怎么|为什么|在哪|哪|如何)")
Q_SENT = re.compile(r"吗|呢[，。？?]|[?？]|是不是|有没有|能不能|要不要|是多少|是什么|是啥|什么意思")
# 定义的值必须像个名词短语，否则抓到的多是「head 是需要让它有梯度」这种半句话
BAD_VAL_HEAD = ("说", "要", "把", "用", "可以", "会", "能", "让", "需要", "我们", "你",
                "他", "它", "这", "那", "因为", "如果", "然后", "在", "有", "不")
NOUNISH = re.compile(r"(模型|网络|方法|算法|编码器|解码器|损失|函数|模块|结构|数据集|任务|"
                     r"向量|矩阵|维度|参数|机制|策略|框架|系统|层|头|注意力|掩码|分类器|"
                     r"优化器|梯度|权重|表示|空间|范式|流程|过程)$")


def good_value(v: str) -> bool:
    if len(v) < 2 or len(v) > 18:
        return False
    if QUES.match(v) or v.startswith(("的", "了", "呢", "吧")):
        return False
    if v.startswith(BAD_VAL_HEAD):
        return False
    # 要么含有英文（专名），要么以名词性词尾结束，否则多半是半句话
    if re.search(r"[A-Za-z]", v) or NOUNISH.search(v):
        return True
    return len(v) <= 8 and not re.search(r"[，、]", v)


def clean(s: str, is_subject: bool) -> str:
    s = s.strip("，、 　")
    s = LEAD.sub("", s)
    s = TAIL.sub("", s)
    return s.strip()


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


LLM_SYS = """你在给一份中文技术课转写做「断言抽取」。只做抽取，不做总结。

对每一句，如果它在断言某事，输出一条 JSON（每行一条，不要数组、不要解释）：
{"page":7,"t":754,"subject":"DINO","rel":"定义","value":"自监督方法"}
rel 只能是：定义（X 是/指的是 Y）、否定（X 不是 Y）、数量（X 有 N 个）、比较（X 比 Y 好/快/大）。

规则：
1. subject 写成术语本身（英文专名保留原写法），不要写成「这个模型」「它的好处」这类。
2. value 写成名词短语，不超过 20 字；比较类写被比较的对象。
3. 没有断言的句子直接跳过，不要编。
4. 每句最多一条。"""


def extract_llm(segs: dict, llm, batch: int = 80, stats: dict | None = None) -> list[dict]:
    """让模型抽断言。规则抽取的 subject 经常抓歪，这一步明显更准。"""
    lines: list[str] = []
    texts: dict[tuple[int, int], str] = {}
    for part in segs["parts"]:
        for seg in part["segments"]:
            text = seg_text(seg)
            if len(text) >= 6:
                key = (int(part["page"]), int(seg["start"]))
                lines.append(f'[P{part["page"]:02d} {int(seg["start"])}] {text}')
                texts[key] = text
    stats = stats if stats is not None else {}
    stats.setdefault("bad", 0)

    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for i in range(0, len(lines), batch):
        chunk = "\n".join(lines[i: i + batch])
        resp = llm.chat(f"下面是转写片段：\n\n{chunk}", system=LLM_SYS)
        for ln in resp.splitlines():
            ln = ln.strip().strip(",")
            if not ln.startswith("{"):
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                stats["bad"] += 1
                continue
            kind = str(d.get("rel", "")).strip()
            subj = clean(str(d.get("subject", "")), True)
            val = clean(str(d.get("value", "")), False)
            if kind not in ("定义", "否定", "数量", "比较") or not subj or not val:
                stats["bad"] += 1
                continue
            key = (kind, subj, val)
            if key in seen:
                seen[key]["n"] += 1
                continue
            page = int(d.get("page") or 0)
            t = float(d.get("t") or 0)
            # 原句回填：模型只给 page/t，早期版本把 text 写成空串，
            # 于是 judge 的 prompt 里"原句"永远是空的，判定等于瞎猜。
            orig = texts.get((page, int(t)))
            if orig is None:
                near = [(abs(k[1] - t), v) for k, v in texts.items() if k[0] == page]
                orig = min(near)[1] if near else ""
            row = {"kind": kind, "subject": subj, "value": val,
                   "text": orig, "page": page, "t": t, "n": 1}
            seen[key] = row
            out.append(row)
    return out


def extract(segs: dict) -> list[dict]:
    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for part in segs["parts"]:
        page = part["page"]
        for seg in part["segments"]:
            text = seg_text(seg)
            if len(text) < 6:
                continue
            if Q_SENT.search(text):      # 疑问句不是断言
                continue
            for kind, pat in KINDS:
                m = pat.search(text)
                if not m:
                    continue
                subj = clean(m.group("s"), True)
                val = clean(m.group("v"), False) if "v" in m.groupdict() else ""
                if kind == "数量":
                    val = m.group("n") + m.group("u")
                if kind == "比较" and val:
                    val = CMP_CUT.split(val)[0].strip()   # SimCLR快 → SimCLR
                if not subj or len(subj) < 2 or not val:
                    continue
                if not termish(subj):
                    continue
                if kind in ("定义", "否定") and not good_value(val):
                    continue
                key = (kind, subj, val)
                if key in seen:
                    seen[key]["n"] += 1
                    continue
                row = {"kind": kind, "subject": subj, "value": val, "text": text,
                       "page": page, "t": round(seg["start"], 1), "n": 1}
                seen[key] = row
                out.append(row)
    return out


CHANGE_WORDS = ("原先", "原来", "改成", "改为", "以前", "后来", "之前是", "现在是",
                "调成", "换成", "改一下", "调整成")

# 这些"主体"是代码里的通用词，抓到的多是半句话，不是概念
GENERIC_SUBJ = {"返回值", "模块", "模型定义", "参数", "结构", "数据", "方法", "步骤",
                "结果", "输入", "输出", "维度", "大小", "数量", "值", "代码", "函数",
                "变量", "这个", "那个", "它", "东西", "部分", "过程", "方式"}


def is_change(a: str, b: str) -> bool:
    """「原先 64，改成 4」——是同一个东西被改了，不是两处说法冲突。"""
    s = f"{a}|{b}"
    return any(w in s for w in CHANGE_WORDS)


def junk_value(v: str) -> bool:
    """值本身不像话（转写残渣、纯数字），配对比毫无意义。"""
    v = v.strip()
    if len(v) < 2:
        return True
    if re.fullmatch(r"[0-9.,:%×xX*\s]+", v):
        return True
    if re.fullmatch(r"[a-z]{1,3}", v):        # gt / gs / ab 这类多半是听错
        return True
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", v):
        return True
    return False


def conflicts(claims: list[dict], min_ratio: float = 0.55) -> list[dict]:
    """按主体分组找冲突：定义互相打架、定义与否定打架、数量不一致、比较反向。"""
    by_subj: dict[str, list[dict]] = defaultdict(list)
    for c in claims:
        if junk_value(c.get("value", "")) or c.get("subject", "") in GENERIC_SUBJ:
            continue
        by_subj[c["subject"]].append(c)

    rows = []
    for subj, items in by_subj.items():
        defs = [c for c in items if c["kind"] == "定义"]
        negs = [c for c in items if c["kind"] == "否定"]
        cnts = [c for c in items if c["kind"] == "数量"]
        cmps = [c for c in items if c["kind"] == "比较"]

        # ① 两个"定义"差别很大
        for i in range(len(defs)):
            for j in range(i + 1, len(defs)):
                a, b = defs[i], defs[j]
                if a["value"] == b["value"]:
                    continue
                if is_change(a["value"], b["value"]):
                    continue          # 「原先 64，改成 4」是改口，不是矛盾
                if a["value"] in b["value"] or b["value"] in a["value"]:
                    continue          # 「4096」和「4096 维度的向量」是同一件事
                if similar(a["value"], b["value"]) >= min_ratio:
                    continue
                if abs(len(a["value"]) - len(b["value"])) > 14:
                    continue          # 一句话说全、一句话说半个，多半不是同一件事
                rows.append({"kind": "定义打架", "subject": subj, "a": a, "b": b,
                             "why": "两处对同一主体的定义不同"})

        # ② 说"是 Y"又说"不是 Y"
        for a in defs:
            for b in negs:
                if similar(a["value"], b["value"]) >= 0.7:
                    rows.append({"kind": "肯定/否定打架", "subject": subj, "a": a, "b": b,
                                 "why": "一处说是，一处说不是"})

        # ③ 数量不一致
        nums = {c["value"] for c in cnts}
        if len(nums) > 1:
            rows.append({"kind": "数量打架", "subject": subj,
                         "a": cnts[0], "b": next(c for c in cnts[1:] if c["value"] != cnts[0]["value"]),
                         "why": "同一主体的数量说法不同：" + "、".join(sorted(nums))})

        # ④ A 比 B 和 B 比 A
        for a in cmps:
            for b in cmps:
                if a["value"] == b["subject"] and b["value"] == a["subject"]:
                    rows.append({"kind": "比较方向打架", "subject": a["subject"], "a": a, "b": b,
                                 "why": f"{a['subject']} 比 {a['value']}，另一处反过来说"})

    rows.sort(key=lambda r: (-(r["a"]["n"] + r["b"]["n"]), r["subject"]))
    return rows


def render_md(bv: str, claims: list[dict], rows: list[dict], n_raw: int | None = None) -> str:
    head = f"从转写里抽出 **{len(claims)}** 条断言（定义 / 否定 / 数量 / 比较）"
    if n_raw and n_raw != len(rows):
        head += (f"，规则先圈出 **{n_raw}** 组可疑，再逐组判过一遍，"
                 f"留下 **{len(rows)}** 组真的互相矛盾。")
    else:
        head += f"，按主体分组后报出 **{len(rows)}** 组矛盾。"
    L = [f"# {bv} 断言与前后矛盾", "", head + "", "",
         "> 只报「同一主体说了两遍不能同时成立的话」，不判断谁对谁错，"
         "请点出处自己听一遍。", ""]
    if rows:
        L += ["| # | 类型 | 主体 | 说法 A | 说法 B | 为什么可疑 |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(rows, 1):
            a, b = r["a"], r["b"]
            L.append(f"| {i} | {r['kind']} | {r['subject']} | {a['value']} "
                     f"<sub>P{a['page']:02d} {int(a['t'])//60:02d}:{int(a['t'])%60:02d}</sub> | "
                     f"{b['value']} <sub>P{b['page']:02d} {int(b['t'])//60:02d}:{int(b['t'])%60:02d}</sub> | "
                     f"{r['why']} |")
    else:
        L.append(f"没有发现矛盾——从 {len(claims)} 条断言里比出 0 组冲突。"
                 "断言是机械抽的（是/不是/有 N 个/比…），抽得少时别当成「查过且干净」。")
    return "\n".join(L) + "\n"


JUDGE_SYS = """你在核对一份中文课程讲义里「前后矛盾」的候选。每组给出同一主体在两处的说法（含原句和出处）。

分两步判断，两个都为 true 才算数：
1. same_attr：这两句话说的是**同一个属性、同一个方面**吗？
   如果一句讲架构、一句讲训练方式（"SAM 有 encoder 和 decoder" / "SAM 是有监督训练"），
   或者一句讲 token 的编号、一句讲用到的向量（"用数字表示" / "4096 维向量"），
   或者讲的是两个不同角色（"Q 和 K 是一对正样本" / "新数据当负样本"），
   那它们描述的就不是同一个属性，same_attr=false。
2. conflict：在**同一个属性**上，两句话不能同时成立吗？只能补充、只能同时为真的，conflict=false。

另外这些都判 false：
- 一句是补充信息（A 说"是 linear 层"，B 说"权重可调"）；
- 一句笼统一句具体、用不同说法描述同一件事；
- 是问答、举例、假设；
- 两处在不同分P，讲的是不同模型/不同实验（一集 MoCo 的 batch 64，另一集 VPT 的 batch 4）。

例子：
A：MAE 是 VIT    B：MAE 是掩码重建方法        → same_attr=false, conflict=false
A：MAE 是 VIT    B：MAE 不是 VIT              → same_attr=true, conflict=true
A：端口是 2400   B：端口是 24000              → same_attr=true, conflict=true
A：学习方法分三种 B：学习方法分两类            → same_attr=true, conflict=true
A：batch 是 64（P05 MoCo）  B：batch 是 4（P38 VPT） → same_attr=false, conflict=false

输出每行一条 JSON，不要数组，不要解释。attr_a / attr_b 必须是**同一个短属性名**才算同一件事，
写不出来（两句话根本不是一件事）就填 "-"：
{"id":0,"attr_a":"学习方法的分类数","attr_b":"学习方法的分类数","same_attr":true,"conflict":true,"why":"一处说分三种，另一处说分两类"}"""


def same_attr(a: str, b: str) -> bool:
    """模型两次写出的属性名必须一致（否则说明它自己也没认为在说同一件事）。"""
    def norm(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"[\s\-_·、,，。.:：()（）【】\[\]]", "", s)
        for w in ("的", "数量", "值", "大小"):
            s = s.replace(w, "")
        return s

    x, y = norm(a), norm(b)
    if not x or not y or x == "-" or y == "-":
        return False
    if x == y or x in y or y in x:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.75


def judge(rows: list[dict], llm, batch: int = 12) -> tuple[list[dict], int]:
    """候选太多时让模型判一遍：抽取可以松，判定必须严（先问是不是同一属性）。

    返回 (留下的, 模型没判的组数)。没判的不能悄悄丢掉也不能默认保留——
    丢掉会让结果看起来"干净"，保留会灌进假冲突，所以两个数都要报出来。
    """
    kept: list[dict] = []
    missing = 0
    for i in range(0, len(rows), batch):
        part = rows[i: i + batch]
        body = []
        for j, r in enumerate(part):
            body.append(f'{j}. 主体「{r["subject"]}」\n'
                        f'   A：{r["a"]["value"]}'
                        f'（P{r["a"]["page"]:02d} {r["a"].get("part", "")}｜原句：{r["a"]["text"][:100]}）\n'
                        f'   B：{r["b"]["value"]}'
                        f'（P{r["b"]["page"]:02d} {r["b"].get("part", "")}｜原句：{r["b"]["text"][:100]}）')
        resp = llm.chat("\n\n".join(body), system=JUDGE_SYS)
        verdict: dict[int, tuple[bool, str]] = {}
        for ln in resp.splitlines():
            ln = ln.strip().strip(",")
            if not ln.startswith("{"):
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ok = bool(d.get("same_attr")) and bool(d.get("conflict"))
            if ok:
                ok = same_attr(d.get("attr_a", ""), d.get("attr_b", ""))
            try:
                idx = int(d.get("id", -1))
            except (TypeError, ValueError):
                continue
            verdict[idx] = (ok, str(d.get("why", ""))[:80])
        for j, r in enumerate(part):
            ok, why = verdict.get(j, (False, ""))
            if j not in verdict:
                missing += 1
            if ok:
                r2 = dict(r)
                if why:
                    r2["why"] = why
                kept.append(r2)
        print(f"[judge] {min(i + batch, len(rows))}/{len(rows)} → 留 {len(kept)}", flush=True)
    return kept, missing


CONFIRM_SYS = """给你两句话，问一个反向问题：**它们能不能同时为真？**

能同时为真（只是不同侧面、不同细节、笼统与具体、都可以成立）→ 输出 {"id":N,"both_true":true}
确实互相冲突、不可能同时为真 → 输出 {"id":N,"both_true":false,"why":"…"}
每行一条 JSON，不要解释。"""


def confirm(rows: list[dict], llm, batch: int = 10) -> tuple[list[dict], int]:
    """留下的再反向问一遍：能不能同时为真？答"能"的剔除。

    单次判定容易被"看起来像矛盾"骗到，反问一次能把大部分误报挡掉。
    """
    kept: list[dict] = []
    missing = 0
    for i in range(0, len(rows), batch):
        part = rows[i: i + batch]
        body = []
        for j, r in enumerate(part):
            body.append(f'{j}. 「{r["subject"]}」\n'
                        f'   A：{r["a"]["value"]}（P{r["a"]["page"]:02d} 原句：{r["a"]["text"][:80]}）\n'
                        f'   B：{r["b"]["value"]}（P{r["b"]["page"]:02d} 原句：{r["b"]["text"][:80]}）')
        resp = llm.chat("\n\n".join(body), system=CONFIRM_SYS)
        verdict: dict[int, tuple[bool, str]] = {}
        for ln in resp.splitlines():
            ln = ln.strip().strip(",")
            if not ln.startswith("{"):
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if "both_true" not in d:      # 模型没答这一项：不能当成"确实冲突"
                continue
            try:
                idx = int(d.get("id", -1))
            except (TypeError, ValueError):
                continue
            verdict[idx] = (not bool(d.get("both_true")), str(d.get("why", ""))[:80])
        for j, r in enumerate(part):
            still, why = verdict.get(j, (False, ""))
            if j not in verdict:
                missing += 1
            if still:
                r2 = dict(r)
                if why:
                    r2["why"] = why
                kept.append(r2)
    print(f"[confirm] {len(rows)} → 留 {len(kept)}", flush=True)
    return kept, missing


def dedupe(rows: list[dict]) -> list[dict]:
    """同一个主体的一堆"定义打架"折叠成一条：最孤立的说法 vs 最主流的说法。

    否则「CLIP 是有监督方法」这一句会和另外十几句各配一次，把表刷满。
    """
    out = []
    defs: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["kind"] == "定义打架":
            defs[r["subject"]].append(r)
        else:
            out.append(r)
    for subj, rs in defs.items():
        best = min(rs, key=lambda r: (r["a"]["n"], r["b"]["n"]))
        a, b = best["a"], best["b"]
        if a["n"] > b["n"]:
            a, b = b, a
        count = len({r["a"]["value"] for r in rs} | {r["b"]["value"] for r in rs})
        r2 = dict(best)
        r2["a"], r2["b"] = a, b
        base = (r2.get("why") or "").strip()
        if count > 2:
            base += f"（同一主体另有 {count - 1} 种说法，例如「{b['value']}」）"
        r2["why"] = base or f"「{a['value']}」与「{b['value']}」不能同时成立"
        out.append(r2)
    out.sort(key=lambda r: (-(r["a"]["n"] + r["b"]["n"]), r["subject"]))
    return out


# ---------------------------------------------------------------- 回流血表
DEF_SUFFIX = ("数据集", "模型", "网络", "方法", "算法", "框架", "层", "任务", "函数")


def norm_term(v: str) -> str:
    v = (v or "").strip()
    for s in DEF_SUFFIX:
        if v.endswith(s) and len(v) > len(s):
            v = v[: -len(s)]
            break
    return re.sub(r"[\s\-_·、,，。.（）()]", "", v)


def glossary_candidates(rows: list[dict], min_ratio: float = 0.5) -> list[dict]:
    """矛盾里有一类不是矛盾：同一个词被听成了两种写法（CIFAR-10 / sipa10）。

    拿词典当锚，把「一边是已知专名、另一边像它的错拼」的挑出来，回流成 hyp 候选。
    """
    from .coldstart import load_lexicon          # 避免循环导入

    alias, known = load_lexicon()
    if not known:
        return []
    out = []
    for r in rows:
        for good, bad in ((r["a"], r["b"]), (r["b"], r["a"])):
            g, b = norm_term(good["value"]), norm_term(bad["value"])
            if not g or not b or g.lower() == b.lower():
                continue
            if g.lower() not in alias or b.lower() in known:
                continue
            score = difflib.SequenceMatcher(None, g.lower(), b.lower()).ratio()
            if score < min_ratio:
                continue
            out.append({"good": alias[g.lower()], "bad": bad["value"].strip(),
                        "score": round(score, 2), "page": bad["page"], "t": bad["t"],
                        "text": bad["text"]})
    return out


def write_glossary(bv: str, rows: list[dict]) -> list[dict]:
    """把候选追加进 glossary/_discovered.tsv（hyp，不会被 fix 自动应用）。"""
    cands = glossary_candidates(rows)
    if not cands:
        return []
    p = GLOSSARY / "_discovered.tsv"
    seen: set[tuple[str, str]] = set()
    if p.exists():
        for line in read_lines(p):
            if not line.strip() or line.startswith("#"):
                continue
            c = line.split("\t")
            if len(c) >= 2:
                seen.add((c[0].strip(), c[1].strip()))
    new = [c for c in cands if (c["good"], c["bad"]) not in seen]
    if not new:
        return []
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# 冷启动候选：全部是 hyp，不会被 fix 自动应用\n"
                     "# 规范词\t误听形\t上下文\t状态\t证据\n", encoding="utf-8")
    with open(p, "a", encoding="utf-8") as f:
        for c in new:
            f.write(f"{c['good']}\t{c['bad']}\t\thyp\t"
                    f"来自断言冲突 P{c['page']:02d} {int(c['t'])//60:02d}:{int(c['t'])%60:02d}；"
                    f"相似度 {c['score']}；例：{c['text'][:40]}\n")
    print(f"[glossary] 回流 {len(new)} 条候选 → {p.name}")
    return new


def run(bv: str, write: bool = True, llm=None, rejudge: bool = False) -> tuple[list[dict], list[dict]]:
    d = OUT / bv
    cache = d / "claims.json"
    claims = None
    if rejudge and cache.exists():
        try:
            claims = json.loads(cache.read_text(encoding="utf-8"))["claims"]
            print(f"[rejudge] 复用手里的 {len(claims)} 条断言，只重判冲突")
        except (KeyError, json.JSONDecodeError):
            claims = None
    if claims is None:
        segs = load_segments(bv, fixed=True)
        claims = extract_llm(segs, llm) if llm else extract(segs)
    titles = {p["page"]: p.get("part", "") for p in load_segments(bv, fixed=True)["parts"]}
    for c in claims:
        c.setdefault("part", titles.get(c["page"], ""))
    raw_rows = conflicts(claims)
    n_raw = len(raw_rows)
    rows = raw_rows
    undecided = 0
    if llm and rows:
        judged, miss1 = judge(rows, llm)
        rows, miss2 = confirm(judged, llm)
        undecided = miss1 + miss2
        if undecided:
            print(f"[claims] 有 {undecided} 组模型没给出可用判定，已丢弃（不当作矛盾）", flush=True)
    rows = dedupe(rows)
    if write:
        d.mkdir(parents=True, exist_ok=True)
        (d / "claims.md").write_text(render_md(bv, claims, rows, n_raw), encoding="utf-8")
        (d / "claims.json").write_text(
            json.dumps({"claims": claims, "conflicts": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"→ {d / 'claims.md'}（{len(claims)} 条断言，{n_raw} 组候选，"
              f"{len(rows)} 组判定成立，{'模型抽取' if llm else '句式抽取'}）")
        # 词表回流用"全部候选"，不只判定留下的——被判定剔除的经常正是听错同一个词
        write_glossary(bv, rows + raw_rows)
    return claims, rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py claims")
    ap.add_argument("bv")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--llm", action="store_true", help="用模型抽断言（更准，要 key）")
    ap.add_argument("--rejudge", action="store_true", help="复用已有断言，只重判冲突")
    ap.add_argument("--glossary-only", action="store_true",
                    help="只从已有断言里挖词表候选（不调模型、不改 claims.md）")
    ap.add_argument("--refilter", action="store_true",
                    help="按通用主体过滤已有冲突，不调模型")
    ap.add_argument("--api-base")
    ap.add_argument("--api-key")
    ap.add_argument("--model")
    args = ap.parse_args()

    if args.glossary_only:
        cache = OUT / args.bv / "claims.json"
        if not cache.exists():
            raise SystemExit(f"没有 {cache}，先跑一次 python cli.py claims {args.bv} --llm")
        data = json.loads(cache.read_text(encoding="utf-8"))
        raw = conflicts(data["claims"])
        cands = write_glossary(args.bv, raw + data.get("conflicts", []))
        for c in cands[: args.top]:
            print(f"  {c['good']:<16} ← {c['bad']:<18} 相似度 {c['score']} "
                  f"P{c['page']:02d} {int(c['t'])//60:02d}:{int(c['t'])%60:02d}")
        return

    if args.refilter:
        cache = OUT / args.bv / "claims.json"
        data = json.loads(cache.read_text(encoding="utf-8"))
        before = data.get("conflicts", [])
        rows = [r for r in before if r.get("subject") not in GENERIC_SUBJ]
        (OUT / args.bv / "claims.md").write_text(
            render_md(args.bv, data["claims"], rows, len(before)), encoding="utf-8")
        data["conflicts"] = rows
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"过滤通用主体：{len(before)} → {len(rows)} 组")
        for r in rows[: args.top]:
            print(f"[{r['kind']}] {r['subject']}: {r['a']['value']} vs {r['b']['value']}")
        return

    llm = None
    if args.llm:
        from .llm import LLM

        llm = LLM(base=args.api_base, key=args.api_key, model=args.model)
        if not llm.ready:
            raise SystemExit("没找到 key：设 OPENAI_API_KEY，或加 --api-key，"
                             "或去掉 --llm 用句式抽取（精度低）")
    claims, rows = run(args.bv, llm=llm, rejudge=args.rejudge)
    for r in rows[: args.top]:
        a, b = r["a"], r["b"]
        print(f"[{r['kind']}] {r['subject']}: {a['value']} (P{a['page']:02d} "
              f"{int(a['t'])//60:02d}:{int(a['t'])%60:02d}) vs {b['value']} "
              f"(P{b['page']:02d} {int(b['t'])//60:02d}:{int(b['t'])%60:02d})")
    if llm:
        print("[llm]", llm.stats())
