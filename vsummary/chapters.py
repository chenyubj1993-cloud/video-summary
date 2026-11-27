"""生成讲义章节（ch*.md）：把一门课的转写组织成"章"，而不是逐P 摘要拼盘。

用法：
  python cli.py chapters BV1E9U8BBEvZ                  # 自动分章，全部生成
  python cli.py chapters BV1E9U8BBEvZ --plan           # 只打印分章计划，不调接口
  python cli.py chapters BV1E9U8BBEvZ --only 3         # 只重写第 3 章
  python cli.py chapters BV1E9U8BBEvZ --chapters "1:1,2:2-6,3:7-13"
  python cli.py chapters BV1E9U8BBEvZ --force          # 覆盖已存在的章

难点与做法：让模型"读 5 万字写 2 千字"会退化成摘要，所以一章超过 MAX_CHARS 时
先做 map（每块压成带时间码的要点），再做 reduce（按要点组织章节）。时间码由程序
拼进正文，模型只负责复制，减少编号错乱。
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor

from vsummary.core import OUT, fmt, load_segments, seg_text, write_atomic
from vsummary.llm import LLM, LLMError

MAX_CHARS = 12000          # 单次请求塞多少字转写（中文约 1 字 1 token）
MAX_MAP_NOTES = 40         # reduce 时最多喂多少条要点

SYSTEM = (
    "你是中文视频内容编辑，正在把一段视频的录音转写整理成讲义正文。规则：\n"
    "① 只使用给定材料里的信息，不补充外部知识；材料没讲的不要写。\n"
    "② 数字、名称、公式、参数、命令行必须与材料一致，拿不准就不写具体数字。\n"
    "③ 材料里明显听错的地方，按你的判断写对，但必须在『存疑与勘误』里说明。\n"
    "④ 每个论断后面用 [Pxx mm:ss] 标注出处，时间码必须从材料里复制，不许自己编。\n"
    "⑤ 不写「本期视频讲解了……」这类空话，直接讲内容本身。\n"
)

TALK = """这是《{title}》第 {n} 章，覆盖 {span}（{dur}）。

{material}

请写成**一段纪实/评论类内容的结构化笔记**，严格用下面这个骨架（Markdown）：

# 第 {n} 章 <标题，8–16 字，概括这段的主线>

> 覆盖 {span}（{dur}）

## 这一段讲了什么
2–4 句，把这段的起因、经过、结论说清楚。不要复述语气词。

## 关键信息
- 4–8 条，人物、事件、数字、时间、因果关系；每条后面带 [Pxx mm:ss]

## 值得留意的说法
- 1–3 条，直接引语（照抄原话，不要改写），说明它为什么值得留意

## 存疑与勘误
- 逐条列出转写听错、前后矛盾或没讲清的地方，格式「- 原文现象 → 应为」；没有就写「无」
"""

WRITE = """这是《{title}》第 {n} 章，覆盖 {span}（{dur}）。

{material}

请写成讲义的一章，严格用下面这个骨架（Markdown）：

# 第 {n} 章 <章标题，8–16 字，概括本章主线>

> 覆盖 {span}（{dur}）

## 学习目标
- 3–5 条，每条以动词开头（能解释/能写出/能判断……），说明读完这一章能做什么

## <小节标题>
（4–8 个小节，按知识依赖排序，不要按分P 顺序流水账；公式用 $...$ 或 $$...$$；
代码只写关键行，用 ``` 围起来；每个论断后面带 [Pxx mm:ss]）

## 本章小结
（一张 3 列的对照表，把本章的概念/方法/代价并排；再写 2–3 句收尾）

## 存疑与勘误
（逐条列出材料里听错、前后不一致或没讲清的地方，格式「- 原文现象 → 应为」；没有就写「无」）
"""

TEXTBOOK = """这是《{title}》第 {n} 章，覆盖 {span}（{dur}）。

{material}

目标篇幅约 {target} 字。请写成**教科书的一章**，严格用下面这个骨架（Markdown）：

# 第 {n} 章 <章标题，8–16 字，概括本章主线>

> 覆盖 {span}（{dur}）

## 学习目标
- 3–5 条，每条以动词开头（能解释/能推出/能判断/能写代码……）

## <小节标题>
每个小节必须按这个顺序写：
1. **为什么需要它**：接上一节留下的问题，一句话说清这一节要解决什么。不要写"接下来我们讲"。
2. **它是怎么做的**：方法本身，公式用 $...$；材料给了推导就写推导，没给就写直觉解释。
3. **设计上的取舍**：为什么是这个参数/这个结构而不是别的（比如为什么 mask 75%、为什么用队列）。
   材料没讲的，写「材料未展开」，**不许自己补**。
4. **代码**（这一节有代码时）：代码块前一句"这段在做什么"，块后一句"跑出来/看到什么"；
   代码只留关键行，用 ``` 围起来，不要整段复制。
每个论断后面带 [Pxx mm:ss]，时间码从材料里复制。

## 本章小结
一张 3 列对照表（方法 / 核心做法 / 代价），再写 2–3 句收尾，点明这一章把读者带到哪一步。

## 存疑与勘误
逐条列出材料里听错、前后不一致或没讲清的地方，格式「- 原文现象 → 应为」；没有就写「无」。
"""

MAP = """下面是一段课程录音的转写，每行开头是 [Pxx mm:ss] 出处。

{text}

请把它压缩成要点清单：8–15 条，每条 20–50 字，必须是可检索的具体内容
（方法名、公式、参数、结论、命令）；行首的 [Pxx mm:ss] 复制到条目末尾；
丢掉寒暄、口头禅、重复。只输出 Markdown 无序列表，不要别的话。
"""

REDUCE_NOTE = "下面不是原文，是这一章按时间顺序整理的要点清单（每行末尾是出处）。\n" \
               "请据此写讲义正文，出处沿用清单里的，不要另编时间码。\n\n"


# ------------------------------------------------------------------ 分章
def parse_spec(spec: str, n_parts: int | None = None) -> dict[int, list[int]]:
    """'1:1,2:2-6' → {1: [1], 2: [2,3,4,5,6]}

    宽松但不容错到"静默丢分P"：范围逆序、章号重复、页码超出分P 数都直接报错。
    """
    out: dict[int, list[int]] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        head, _, tail = item.partition(":")
        try:
            n = int(head)
        except ValueError:
            raise SystemExit(f"章号不是数字：{item}（应形如 2:2-6）")
        if n in out:
            raise SystemExit(f"章号重复：第 {n} 章写了两遍")
        nums = []
        for part in tail.split("-"):
            part = part.strip()
            if part:
                try:
                    nums.append(int(part))
                except ValueError:
                    raise SystemExit(f"分P 号不是数字：{item}")
        if not nums:
            raise SystemExit(f"分章写法看不懂：{item}（应形如 2:2-6）")
        if len(nums) > 1 and nums[0] > nums[1]:
            raise SystemExit(f"分P 范围逆序：{item}（应为 小-大）")
        if n_parts:
            bad = [x for x in nums if x < 1 or x > n_parts]
            if bad:
                raise SystemExit(f"分P 号超出范围：{item}（这个视频只有 {n_parts} 个分P）")
        out[n] = list(range(nums[0], nums[-1] + 1)) if len(nums) > 1 else nums
    if not out:
        raise SystemExit("--chapters 解析出 0 章，检查写法（形如 \"1:1,2:2-6\"）")
    return out


def slice_part(part: dict, t0: float, t1: float) -> dict:
    """从某个分P 里切出一段时间，做成一个"伪分P"，后面的素材组装完全不用改。"""
    segs = [s for s in part["segments"] if s["end"] > t0 and s["start"] < t1]
    return {"page": part["page"], "part": part["part"],
            "duration": max(0.0, min(t1, part["duration"]) - t0),
            "segments": segs, "t0": t0}


def chapters_from_pages(spec: dict[int, list[int]], parts: list[dict]) -> dict[int, list[dict]]:
    by_page = {p["page"]: p for p in parts}
    return {n: [by_page[x] for x in pages if x in by_page] for n, pages in spec.items()}


def time_split(parts: list[dict], target: int = 1500) -> dict[int, list[dict]]:
    """没有分P、或分P 标题没有编号时，按音频时长切块（单块不超过 1.6×target）。

    长视频（讲座、纪录片、直播回放）都走这条路，所以它是"通用"的兜底。
    """
    pieces: list[dict] = []
    for p in parts:
        dur = p["duration"] or 0
        if dur <= target * 1.6 or not p["segments"]:
            pieces.append(p)
            continue
        k = max(2, round(dur / target))
        step = dur / k
        for i in range(k):
            t0, t1 = step * i, step * (i + 1) if i < k - 1 else dur
            piece = slice_part(p, t0, t1)
            if piece["segments"]:
                pieces.append(piece)

    out: dict[int, list[dict]] = {}
    cur: list[dict] = []
    acc = 0.0
    for piece in pieces:
        if cur and acc + piece["duration"] > target * 1.4:
            out[len(out) + 1] = cur
            cur, acc = [], 0.0
        cur.append(piece)
        acc += piece["duration"]
    if cur:
        out[len(out) + 1] = cur
    return out


def auto_split(parts: list[dict], target: int = 1500) -> dict[int, list[dict]]:
    """分章顺序：分P 标题自带编号 → 按编号；否则按音频时长切。"""
    chapters: dict[int, list[int]] = {}
    for p in parts:
        m = re.match(r"^\s*(\d+)\s*[\.\-、,，]\s*\S", p["part"])
        if m:
            chapters.setdefault(int(m.group(1)), []).append(p["page"])
    if len(chapters) >= 2:
        return chapters_from_pages(dict(sorted(chapters.items())), parts)
    return time_split(parts, target)


def describe(ch: dict[int, list[dict]]) -> str:
    lines = []
    for n, pieces in ch.items():
        dur = sum(p["duration"] for p in pieces)
        pages = sorted({p["page"] for p in pieces})
        span = (f"P{pages[0]:02d}" if len(pages) == 1
                else f"P{pages[0]:02d}–P{pages[-1]:02d}")
        head = pieces[0]["part"][:26]
        lines.append(f"  第 {n} 章  {span}  {dur/60:5.1f} 分钟  {head}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 素材
def lines_of(part: dict) -> list[str]:
    return [f"[P{part['page']:02d} {fmt(s['start'])}] {seg_text(s)}" for s in part["segments"]]


def chunks(parts: list[dict], budget: int = MAX_CHARS) -> list[str]:
    """按分P 装车；单个分P 超预算时按句子切。"""
    out, cur, size = [], [], 0
    for p in parts:
        block = "\n".join(lines_of(p))
        if size + len(block) > budget and cur:
            out.append("\n\n".join(cur))
            cur, size = [], 0
        if len(block) > budget:
            buf = []
            for line in block.split("\n"):
                if sum(len(x) + 1 for x in buf) + len(line) > budget and buf:
                    out.append("\n".join(buf))
                    buf = []
                buf.append(line)
            if buf:
                out.append("\n".join(buf))
            continue
        cur.append(block)
        size += len(block) + 2
    if cur:
        out.append("\n\n".join(cur))
    return out


def make_material(llm: LLM, ch_parts: list[dict], workers: int = 4,
                  bv: str = "", pages: list[int] | None = None) -> str:
    blocks = chunks(ch_parts)
    base = blocks[0] if len(blocks) == 1 else _reduce(llm, blocks, workers)

    # 有抽帧结果就把"画面里写了什么"接在后面——口播念不出的公式、代码、表格都在这里
    if bv and pages:
        try:
            from vsummary.frames import board_text
            board = board_text(bv, pages)
        except Exception:
            board = ""
        if board:
            base = (base + "\n\n" + board)[: MAX_CHARS + 4000]
    return base


def strip_fence(text: str) -> str:
    """模型有时把整章包在 ```markdown 里，这会毁掉标题与后续 section 判定。"""
    t = text.strip()
    m = re.match(r"^```[A-Za-z]*\s*\n(.*?)\n```\s*$", t, flags=re.S)
    if m and m.group(1).lstrip().startswith("#"):
        return m.group(1).strip()
    return t


def _reduce(llm: LLM, blocks: list[str], workers: int) -> str:
    def one(b: str) -> str:
        try:
            return llm.chat(MAP.format(text=b), system=SYSTEM, temperature=0.1)
        except LLMError as e:
            print(f"[warn] map 失败（{e}），退回原始转写", flush=True)
            return b

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        notes = list(ex.map(one, blocks))

    extra = notes[MAX_MAP_NOTES:]
    if extra:                       # 别静默丢：块数超上限时明确告警
        print(f"[warn] 素材 {len(notes)} 块，只用前 {MAX_MAP_NOTES} 块（每块已含 8–15 条要点）", flush=True)
    notes = notes[:MAX_MAP_NOTES]
    joined = "\n\n".join(notes)
    if len(joined) > MAX_CHARS:     # 超长就分两半再 map 一次，而不是硬截断
        half = max(1, len(notes) // 2)
        joined = "\n\n".join(notes[:half])
        second = _reduce(llm, notes[half:], workers) if len(notes) > 1 else ""
        joined = (joined + "\n\n" + second.split("\n\n", 1)[-1])[:MAX_CHARS]
    return REDUCE_NOTE + joined


# ------------------------------------------------------------------ 主流程
def span_of(ch_parts: list[dict]) -> str:
    pages = sorted({p["page"] for p in ch_parts})
    t0 = ch_parts[0].get("t0")
    if len(pages) > 1:
        return f"P{pages[0]:02d}–P{pages[-1]:02d}"
    if t0:                                    # 单集里切出来的一段
        return f"P{pages[0]:02d} {fmt(t0)}–{fmt(t0 + sum(p['duration'] for p in ch_parts))}"
    return f"P{pages[0]:02d}"


DERIVE = """这是《{title}》第 {n} 章，覆盖 {span}（{dur}）。

{material}

目标篇幅约 {target} 字。请写成**教科书的一章**，重点不是罗列知识点，而是把每一步"为什么这么走"推出来。
严格用下面这个骨架（Markdown）：

# 第 {n} 章 <章标题，8–16 字，概括本章主线>

> 覆盖 {span}（{dur}）

## 学习目标
- 3–5 条，每条以动词开头（能解释/能推出/能判断/能写代码……）

## <小节标题>
每个小节按这个顺序写，像在讲台上推着听众往前走：
1. **上一节留下什么**：具体到那个还没解决的问题（第一小节写"这一章要从哪出发"）。
2. **它为什么难**：难在哪一步、卡在哪个取舍上。这里要写出矛盾（成本 vs 效果、通用 vs 专用），
   不要写成"接下来我们要学习"。
3. **这一节的办法**：怎么绕过那个矛盾。公式用 $...$；材料给了推导就照材料写，
   材料只给了结论就先写直觉、再给结论。
4. **代价**：用了它要付出什么（算力、精度、工程复杂度）。
5. **代码**（有代码时）：块前一句"这段在做什么"，块后一句"跑出来/看到什么"，只留关键行。

硬规则：
- 每个论断后面带 [Pxx mm:ss]，时间码从材料里复制，不许自造。
- 材料没讲的地方，允许写「材料未展开，按上下文推断：…」，但**必须**带「推断」二字；
  不许把推断写成和原文一样的口气。
- 不要写"本节将介绍""综上所述"这类空话。句子要有信息。

## 本章小结
一张 3 列对照表（方法 / 核心做法 / 代价），再写 2–3 句收尾：这一章把读者从哪个问题带到了哪里，
留下什么未解问题给下一章。

## 存疑与勘误
逐条列出材料里听错、前后不一致或没讲清的地方，格式「- 原文现象 → 应为」；没有就写「无」。
"""


def write_chapter(llm: LLM, bv: str, n: int, ch_parts: list[dict],
                  title: str, workers: int, style: str = "lecture",
                  density: int = 90, derive: bool = False,
                  out_dir: str = "handout") -> bool:
    if not ch_parts:
        print(f"[skip] 第 {n} 章没有可用素材", flush=True)
        return False
    dur = sum(p["duration"] for p in ch_parts)
    pages = sorted({p["page"] for p in ch_parts})
    material = make_material(llm, ch_parts, workers, bv=bv, pages=pages)
    span = span_of(ch_parts)
    if style == "textbook" and derive:
        prompt = DERIVE.format(title=title, n=n, span=span,
                               dur=f"{dur/60:.0f} 分钟", material=material,
                               target=min(int(density * dur / 60), 10000))
    elif style == "textbook":
        prompt = TEXTBOOK.format(title=title, n=n, span=span,
                                 dur=f"{dur/60:.0f} 分钟", material=material,
                                 target=min(int(density * dur / 60), 10000))
    elif style == "talk":
        prompt = TALK.format(title=title, n=n, span=span,
                             dur=f"{dur/60:.0f} 分钟", material=material)
    else:
        prompt = WRITE.format(title=title, n=n, span=span,
                              dur=f"{dur/60:.0f} 分钟", material=material)
    try:
        text = llm.chat(prompt, system=SYSTEM, temperature=0.3)
    except LLMError as e:
        print(f"[err] 第 {n} 章：{e}", flush=True)
        return False

    text = strip_fence(text)
    if not text.lstrip().startswith("#"):
        text = f"# 第 {n} 章\n\n" + text
    if "## " not in text:
        print(f"[err] 第 {n} 章：模型没有输出任何 `## ` 小节，判为失败，不落盘", flush=True)
        return False
    path = OUT / bv / out_dir / f"ch{n}.md"
    write_atomic(path, text.rstrip() + "\n")
    try:                     # 时间码是模型抄的，抄错要当场看见
        from vsummary import cite
        ok, tot = cite.stats(cite.verify(bv, text))
        chips = tot
        extra = f"（{ok}/{tot} 核对到原文）" if tot else "（没有时间码，不合规）"
    except Exception as e:
        chips = len(cite.parse(text))
        extra = f"（校验失败：{e}）"
    print(f"[ok] 第 {n} 章 {span} → {len(text)} 字，"
          f"{chips} 处时间码{extra} → {path.name}", flush=True)
    return True


def _sig(pieces: list[dict]) -> str:
    """一章的内容签名：只跟转写文字有关，跟注解/配图无关。"""
    import hashlib
    h = hashlib.md5()
    for p in pieces:
        for s in p["segments"]:
            h.update(seg_text(s).encode("utf-8"))
    return h.hexdigest()[:16]


def pick_style(bv: str, title: str, owner: str = "") -> str:
    """标题里有"课/教程/入门"这类词 → 教科书式；否则纪实笔记式。

    领域判定只作参考：它靠关键词投票，遇到跨领域内容（比如政治吐槽里提到编程）会误判，
    所以只在标题没有明显信号、且领域置信度足够高时才采信。
    """
    course = re.search(r"教程|课程|入门|从零|详解|实战|训练营|公开课|考研|讲透|精讲|全套|"
                       r"第\s*\d+\s*[讲讲节]|保姆级|速成", title)
    if course:
        return "textbook"
    dense = ("人工智能", "计算机视觉", "编程与软件工程", "数学")
    try:
        import json
        from vsummary.core import OUT
        p = OUT / bv / "domain.json"
        d = json.load(open(p, encoding="utf-8"))
        top = (d.get("top3") or [{}])[0]
        if (top.get("domain") in dense and float(top.get("score", 0) or 0) >= 3.0
                and float(d.get("conf", 0) or 0) >= 1.25):
            return "textbook"
    except Exception:
        pass
    return "talk"


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py chapters")
    ap.add_argument("bv")
    ap.add_argument("--chapters", default="", help='手工分章，如 "1:1,2:2-6,3:7-13"')
    ap.add_argument("--only", default="", help="只重写这些章，如 3,4")
    ap.add_argument("--plan", action="store_true", help="只打印分章计划")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的章")
    ap.add_argument("--workers", type=int, default=4, help="map 阶段的并发数")
    ap.add_argument("--style", choices=["auto", "lecture", "textbook", "talk"], default="auto",
                    help="auto=按领域自动选（技术知识→textbook，其它→talk）；"
                         "lecture=通用讲义；textbook=教科书式；talk=纪实/评论笔记")
    ap.add_argument("--density", type=int, default=90, help="textbook 风格的目标密度（字/音频分钟）")
    ap.add_argument("--derive", action="store_true",
                    help="推演链版：每节写清从哪个问题出发、卡在哪、怎么绕")
    ap.add_argument("--out", default="handout", help="输出到 out/<bv>/<dir>，默认 handout")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--temperature", type=float, default=0.3)
    args = ap.parse_args()

    seg = load_segments(args.bv)
    parts = seg["parts"]
    ch = (chapters_from_pages(parse_spec(args.chapters), parts) if args.chapters
          else auto_split(parts))

    style = args.style
    if style == "auto":
        style = pick_style(args.bv, seg.get("title", ""), seg.get("owner", ""))
    (OUT / args.bv).mkdir(parents=True, exist_ok=True)
    (OUT / args.bv / "style.json").write_text(json.dumps({"style": style}, ensure_ascii=False),
                                              encoding="utf-8")

    print(f"[plan] {args.bv}《{seg['title'][:40]}》{len(parts)} 个分P → {len(ch)} 章，"
          f"风格 {style}")
    print(describe(ch), flush=True)
    if args.plan:
        return

    llm = LLM(base=args.api_base, key=args.api_key, model=args.model,
              temperature=args.temperature)
    if not llm.ready:
        raise SystemExit(
            "没有 API key。设 OPENAI_API_KEY 再跑，或指定本地服务：\n"
            "  $env:OPENAI_API_KEY=\"sk-...\"\n"
            "  python cli.py chapters BV1E9U8BBEvZ --api-base https://api.deepseek.com/v1\n"
            "  # ollama：--api-base http://127.0.0.1:11434/v1 --model qwen2.5:14b --api-key ollama")
    print(f"[llm] {llm.describe()}", flush=True)

    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    ref = 0.0
    for p in (OUT / args.bv / "segments_fixed.json", OUT / args.bv / "segments.json"):
        if p.exists():
            ref = max(ref, p.stat().st_mtime)

    # 内容签名：转写改了才重写这一章，没改的章直接跳过（想全量重跑用 --force）
    state_path = OUT / args.bv / "chapters_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    done = 0
    for n, pieces in ch.items():
        if only is not None and n not in only:
            continue
        path = OUT / args.bv / args.out / f"ch{n}.md"
        sig = _sig(pieces)
        key = f"{args.out}/ch{n}"
        if (path.exists() and not args.force
                and (state.get(key) == sig or path.stat().st_mtime >= ref)):
            why = "内容没变" if state.get(key) == sig else "比转写新"
            print(f"[skip] 第 {n} 章{why}，跳过（--force 覆盖）", flush=True)
            state[key] = sig          # 就算跳过也要记下签名，否则下次 mtime 一变就全量重写
            continue
        ok = write_chapter(llm, args.bv, n, pieces, seg["title"], args.workers,
                           style=style, density=args.density,
                           derive=args.derive, out_dir=args.out)
        done += ok
        if ok:                      # 只有真写成功了才记签名，失败的下次还会重试
            state[key] = sig

    write_atomic(state_path, json.dumps(state, ensure_ascii=False, indent=1))

    print(f"\n[done] {done} 章 → {OUT / args.bv / args.out}　调用统计：{llm.stats()}")


if __name__ == "__main__":
    main()
