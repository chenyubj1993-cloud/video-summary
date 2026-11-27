"""前置顺序体检：讲义里有没有「先用了、后解释」的术语。

只看读者实际读到的那份文本（`out/<bv>/handout/ch*.md`），不碰讲义本身。
判定两条：
  首次出现（use）  —— 术语第一次出现在正文/标题/学习目标里
  首次定义（define）—— 术语第一次出现在"是/指/即/称为/全称/（英文全称）"这类定义语境里

use 早于 define，或者压根没有 define，就列进清单。

用法：
  python cli.py prereq BV1E9U8BBEvZ
  python cli.py prereq BV1E9U8BBEvZ --all      # 连"定义正常"的也列出来
"""
from __future__ import annotations

import argparse
import re

from .core import GLOSSARY, OUT, read_lines

CODE = re.compile(r"```.*?```", re.S)
CITE = re.compile(r"\[P\s*\d{1,2}[^\]]*\]")

CUE_AFTER = re.compile(r"^\s{0,2}[（(]?\s*(?:全称|是|指|即|表示|称为|叫做|就是|为一个|"
                       r"是一种|是一个|一份|一篇|一词|源于|出自|源自|来自|：|:)")
CUE_BEFORE = re.compile(r"(?:所谓|什么是|什么叫|称为|叫做|即|统称)\s{0,2}$")
CUE_PAREN = re.compile(r"^\s*[（(](?:[^（）()]|[（(][^（）()]*[）)]){5,}[）)]")
# 讲义里明说"假定你会"的段落，里面的词不算课程欠你的定义
BACKGROUND = ("默认学习者", "已具备", "前置知识", "先学基础", "基础知识", "都学过", "假设你")
MIN_GAP = 1500          # 同一章里隔不到 1500 字才定义，算正常行文，不报

# 这些词太通用，列出来只会淹没真问题
SKIP = {"模型", "网络", "方法", "数据", "数据量", "任务", "训练", "测试", "参数", "特征",
        "图像", "视频", "文本", "语言", "分类", "检测", "分割", "损失", "函数", "算法",
        "架构", "结构", "输入", "输出", "结果", "过程", "方式", "概念", "部分", "模块",
        "自监督", "有监督", "无监督", "全零", "随机"}      # 缩写/代码里的取值，不当概念


def mask(text: str) -> str:
    """把代码块、引用标记、图注、表格行替换成等长空白，位置不变。"""
    def blank(m):
        return "".join("\n" if ch == "\n" else " " for ch in m.group(0))

    text = CITE.sub(blank, CODE.sub(blank, text))
    # 图注与表格行不是正文：`![图 …]`、`*图 3-1　…*`、`| … |`
    lines = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("![") or s.startswith("*图") or s.startswith("|"):
            lines.append(" " * len(line))
        else:
            lines.append(line)
    return "\n".join(lines)


def load_terms() -> dict[str, dict]:
    """术语表 → {小写键: {"name": 展示名, "aliases": [...]}}，忽略纯数字。

    `terms_override.tsv` 里"定义留空"的行表示"这不是概念"，不能被当成术语源。
    """
    terms: dict[str, dict] = {}
    # 先在 override 里收集"删除名单"（定义留空的那些），后面统一排除——
    # 否则别的词表文件里同名的词还会被当术语（残差 就漏回来过）。
    deleted: set[str] = set()
    ov = GLOSSARY / "terms_override.tsv"
    if ov.exists():
        for line in read_lines(ov, "utf-8-sig"):
            if not line.strip() or line.startswith("#"):
                continue
            c = line.rstrip("\n").split("\t")
            if c[0].strip() and not (len(c) > 1 and c[1].strip()):
                deleted.add(c[0].strip().lower())
    for f in [GLOSSARY / "terms" / "ai.tsv"] + sorted(GLOSSARY.glob("*.tsv")) \
            + sorted(GLOSSARY.glob("series/*.tsv")):
        if not f.exists():
            continue
        override = f.name == "terms_override.tsv"
        for line in read_lines(f, "utf-8-sig"):
            if not line.strip() or line.startswith("#"):
                continue
            c = line.rstrip("\n").split("\t")
            canon = c[0].strip()
            if not canon or canon in SKIP or len(canon) < 2:
                continue
            if override and not (len(c) > 1 and c[1].strip()):
                continue                  # 空定义 = 删掉这个词
            if canon.lower() in deleted:
                continue
            if re.fullmatch(r"[\d\s.,%×x*]+", canon):
                continue                      # 768、64×4096 这类不是概念
            key = canon.lower()
            slot = terms.setdefault(key, {"name": canon, "aliases": [canon], "note": ""})
            if len(c) > 1 and f.parent.name == "terms":
                if len(c) > 2 and c[2].strip() and not slot.get("note"):
                    slot["note"] = c[2].strip()      # 词表备注，速查表兜底用
                for a in c[1].split("|"):
                    a = a.strip()
                    if a and len(a) >= 2 and a.lower() not in {x.lower() for x in slot["aliases"]}:
                        slot["aliases"].append(a)
    return terms


def pattern(alias: str) -> re.Pattern:
    """拉丁词按大小写不敏感 + 空格宽松；中文按字面。"""
    esc = re.escape(alias).replace(r"\ ", r"\s*")
    head = r"(?<![A-Za-z0-9])" if alias[:1].isascii() else ""
    tail = r"(?![A-Za-z0-9])" if alias[-1:].isascii() else ""
    return re.compile(head + esc + tail, re.I)


def read_chapters(bv: str) -> list[tuple[str, int, str]]:
    """[(标题, 章号, 屏蔽后的正文)]，按章号排序。"""
    out = []
    for p in (OUT / bv / "handout").glob("ch*.md"):
        num = int(re.sub(r"\D", "", p.stem) or 0)
        raw = p.read_text(encoding="utf-8")
        m = re.search(r"^#\s+(.+)$", raw, re.M)
        out.append((m.group(1).strip() if m else p.stem, num, mask(raw)))
    return sorted(out, key=lambda x: x[1])


def goal_span(text: str) -> tuple[int, int]:
    """「## 学习目标」那一段的范围，用来标记"读者最先看到却没解释"的词。"""
    m = re.search(r"^##\s*学习目标.*$", text, re.M)
    if not m:
        return (-1, -1)
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return (m.start(), end)


def find(text: str, pats: list[re.Pattern]) -> list[tuple[int, bool]]:
    """返回 [(位置, 是否定义语境)]，已按位置排序。"""
    hits = []
    for pat in pats:
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            after = text[e:e + 24]
            after_wide = text[e:e + 90]      # 括号解释可能很长，单独放宽
            before = text[max(0, s - 16):s]
            is_def = bool(CUE_AFTER.match(after) or CUE_BEFORE.search(before)
                          or CUE_PAREN.match(after_wide))
            hits.append((s, is_def))
    return sorted(hits)


def line_of(text: str, pos: int, width: int = 46) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    line = text[start:end if end >= 0 else len(text)].strip()
    line = re.sub(r"^[#>\-*|:\s]+", "", line)
    return line[:width] + ("…" if len(line) > width else "")


def para_of(text: str, pos: int) -> str:
    s = text.rfind("\n\n", 0, pos)
    e = text.find("\n\n", pos)
    return text[s + 2 if s >= 0 else 0: e if e >= 0 else len(text)]


def scan(bv: str) -> list[dict]:
    terms = load_terms()
    chapters = read_chapters(bv)
    rows = []
    for _key, slot in terms.items():
        canon, aliases = slot["name"], slot["aliases"]
        pats = [pattern(a) for a in aliases]
        use = define = None
        n = 0
        first_text = ""
        flagged = assumed = False
        # 统计必须扫完所有章：早先一找到 define 就 break，导致 n 只算到首次定义那一章
        # （ViT 报 1 次、实际 106 次），A/D 两类按 n 排序全乱。
        for idx, (_title, num, text) in enumerate(chapters):
            hits = find(text, pats)
            n += len(hits)
            if not hits:
                continue
            if use is None:
                use = (idx, hits[0][0])
                first_text = line_of(text, hits[0][0])
                g0, g1 = goal_span(text)
                ls = text.rfind("\n", 0, hits[0][0]) + 1
                le = text.find("\n", hits[0][0])
                line = text[ls:le if le >= 0 else len(text)]
                flagged = line.lstrip().startswith("#") or (g0 <= hits[0][0] < g1)
                para = para_of(text, hits[0][0])
                assumed = any(k in para for k in BACKGROUND)
            if define is None:
                for pos, d in hits:
                    if d:
                        define = (idx, pos)
                        break
        if use is None:
            continue
        rows.append({"term": canon, "use": use, "define": define, "n": n,
                     "ch": f"第{chapters[use[0]][1]}章", "flag": flagged,
                     "assumed": assumed, "snippet": first_text})
    return rows


def render_md(bv: str, rows: list[dict], show_all: bool = False) -> str:
    assumed = [r for r in rows if r["assumed"]]
    core = [r for r in rows if not r["assumed"]]
    late, never, ok = [], [], []
    for r in core:
        if not r["define"]:
            never.append(r)
        elif r["define"] > r["use"] and (r["define"][0] != r["use"][0]
                                         or r["define"][1] - r["use"][1] >= MIN_GAP):
            late.append(r)
        else:
            ok.append(r)

    out = [f"# {bv} 前置顺序体检", ""]
    out.append(f"扫了 {len(rows)} 个术语（术语词典 + 系列专名表）。"
               f"其中 {len(assumed)} 个首次出现在「假定你已会」的段落里，单独列在 D。")
    out.append("")
    out.append(f"- 首次出现就带定义：**{len(ok)}** 个")
    out.append(f"- 先出现、后定义：**{len(late)}** 个")
    out.append(f"- 全文没有定义：**{len(never)}** 个")
    out.append("")
    out.append("> 「定义语境」＝术语后面紧跟 是/指/即/称为/全称/出自/冒号，或前面有 "
               "所谓/什么是/什么叫。标题、学习目标、表格都算「使用」。"
               f"同一章里隔不到 {MIN_GAP} 字才解释的，按正常行文处理，不报。")
    out.append("")

    if never:
        out.append("## A. 全程没解释就用了")
        out.append("")
        out.append("| 术语 | 首次出现 | 次数 | 读者第一眼看到的句子 |")
        out.append("|---|---|---|---|")
        for r in sorted(never, key=lambda r: (-r["n"], r["use"])):
            flag = " ⚠" if r["flag"] else ""
            out.append(f"| {r['term']} | {r['ch']}{flag} | {r['n']} | {r['snippet']} |")
        out.append("")

    if late:
        out.append("## B. 先用了，后解释")
        out.append("")
        out.append("| 术语 | 首次使用 | 首次定义 | 晚 | 读者第一眼看到的句子 |")
        out.append("|---|---|---|---|---|")
        for r in sorted(late, key=lambda r: (-(r["define"][0] - r["use"][0]), -r["n"])):
            if r["define"][0] != r["use"][0]:
                gap = f"{r['define'][0] - r['use'][0]} 章"
            else:
                gap = f"{r['define'][1] - r['use'][1]} 字"
            out.append(f"| {r['term']} | 第{r['use'][0] + 1}章 | "
                       f"第{r['define'][0] + 1}章 | {gap} | {r['snippet']} |")
        out.append("")

    if show_all and ok:
        out.append("## C. 顺序正常")
        out.append("")
        out.append("| 术语 | 章 | 次数 |")
        out.append("|---|---|---|")
        for r in sorted(ok, key=lambda r: -r["n"]):
            out.append(f"| {r['term']} | {r['ch']} | {r['n']} |")
        out.append("")

    if assumed:
        out.append("## D. 假定你已会（讲义里明说「默认学习者已具备」的那段）")
        out.append("")
        out.append("这些词不算课程欠你定义，但如果读者真不会，第一章就会卡住。"
                   "要照顾零基础读者，可以在这一节加一段「名词速查」。")
        out.append("")
        out.append("| 术语 | 首次出现 | 次数 |")
        out.append("|---|---|---|")
        for r in sorted(assumed, key=lambda r: -r["n"]):
            out.append(f"| {r['term']} | {r['ch']} | {r['n']} |")
        out.append("")

    out.append("## 怎么看这份清单")
    out.append("")
    out.append("- **A 类**最该管：在术语第一次出现处补一句定义或括号解释，成本最低。")
    out.append("- **B 类**多半是章节顺序问题：内容在第 N 章用到，第 N+1 章才讲。")
    out.append("- **⚠** 表示这个词第一次出现在小节标题或学习目标里——读者最先看到它，"
               "却最晚得到解释，观感最差。")
    out.append("- **D 类**是取舍问题，不是错误：教材默认读者有底子很常见，但要自己心里有数。")
    out.append("")
    return "\n".join(out)


def run(bv: str, write: bool = True, show_all: bool = False) -> list[dict]:
    rows = scan(bv)
    if write:
        p = OUT / bv / "prereq.md"
        p.write_text(render_md(bv, rows, show_all), encoding="utf-8")
        print(f"→ {p}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py prereq")
    ap.add_argument("bv")
    ap.add_argument("--all", action="store_true", help="连顺序正常的也列出来")
    ap.add_argument("--top", type=int, default=18)
    args = ap.parse_args()
    rows = run(args.bv, show_all=args.all)
    assumed = [r for r in rows if r["assumed"]]
    core = [r for r in rows if not r["assumed"]]
    late = [r for r in core if r["define"] and
            (r["define"][0] != r["use"][0] or r["define"][1] - r["use"][1] >= MIN_GAP)]
    never = [r for r in core if not r["define"]]
    print(f"术语 {len(rows)}｜没定义 {len(never)}｜先使用后定义 {len(late)}｜"
          f"假定已会 {len(assumed)}")
    for r in sorted(never, key=lambda r: (-r["n"], r["use"]))[: args.top]:
        mark = "标题/目标" if r["flag"] else "正文"
        print(f"  [没定义] {r['term']:<16} {r['ch']:<4}{mark} ×{r['n']:<3} {r['snippet'][:34]}")
    for r in sorted(late, key=lambda r: (-(r["define"][0] - r["use"][0]), -r["n"]))[: args.top]:
        print(f"  [晚解释] {r['term']:<16} 用 第{r['use'][0] + 1}章 → 定义 第{r['define'][0] + 1}章")


if __name__ == "__main__":
    main()
