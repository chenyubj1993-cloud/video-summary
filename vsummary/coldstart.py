"""术语表冷启动：从转写里自动猜"哪些词是听错的专名"，不用人一条条手填。

三路信号：
  1. 英数串（Deno、lessnets50、GVT）——与词典里的专名做编辑距离，够近就提候选；
  2. 数字字母混淆（L1M 的 1 其实是 l）——归一化后再比一次；
  3. 中文重复片段——只在给定词典里找得到对应英文词的才提（剩下交给 LLM 复核）。

输出 `glossary/_discovered.tsv`，全部是 hyp：**不会被 fix 自动应用**，
人看一眼、把确认的改成 obs 才生效。

用法：
  python cli.py coldstart BV1E9U8BBEvZ            # 从原始转写（还没纠错的）猜
  python cli.py coldstart BV1E9U8BBEvZ --fixed    # 从已纠错转写里找漏网的
  python cli.py coldstart BV1E9U8BBEvZ --eval     # 拿现有词表当标准答案，看召回
"""
from __future__ import annotations

import argparse
import difflib
import re
from collections import Counter

from .core import GLOSSARY, load_segments, read_lines, seg_text, write_atomic

TERMS = GLOSSARY / "terms"          # 一个行业一个文件，全读进来做词典
LEXICON = TERMS / "ai.tsv"          # 兼容老路径（本机只有 ai.tsv 时也走这里）
FENCE = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9\-]{2,}(?![A-Za-z])")
CONFUSE = str.maketrans({"1": "l", "0": "o", "5": "s", "2": "z", "|": "l"})
STOP = {"the", "and", "for", "that", "this", "with", "from", "you", "are", "not",
        "can", "all", "our", "but", "has", "have", "its", "out", "one", "two", "we",
        "ok", "yes", "no", "so", "to", "of", "in", "on", "is", "it", "as", "at", "by"}

# 这些是"正常英文/常见术语"，被听错的可能性低于被当成专名的可能性——
# 不过滤掉的话，"batch→BatchNorm""patch→PyTorch" 这类噪声会淹掉真候选。
BIG_STOP = STOP | {
    "model", "models", "data", "dataset", "datasets", "batch", "batches", "size",
    "patch", "patches", "image", "images", "load", "tune", "tuning", "adapt",
    "adapter", "tensor", "vision", "caption", "value", "values", "main", "scaling",
    "speed", "agent", "train", "training", "test", "testing", "loss", "losses",
    "layer", "layers", "token", "tokens", "input", "inputs", "output", "outputs",
    "head", "heads", "block", "blocks", "dim", "size", "epoch", "epochs", "rate",
    "learning", "accuracy", "class", "classes", "feature", "features", "encoder",
    "decoder", "attention", "weight", "weights", "matrix", "vector", "vectors",
    "query", "queries", "key", "keys", "sample", "samples", "label", "labels",
    "prompt", "prompts", "fine", "pretrain", "inference", "network", "networks",
    "deep", "large", "small", "base", "best", "new", "old", "multi", "single",
    "cross", "self", "super", "hyper", "macro", "micro", "selfsupervised",
    "python", "windows", "linux", "git", "docker", "json", "csv", "http",
    "mlp", "cnn", "rnn", "gan", "vae", "ocr", "asr", "api", "gpu", "cpu", "ram",
    "vram", "cuda", "nlp", "gpt", "bert", "clip", "sam", "mae", "vit", "dino",
    "pip", "conda", "bash", "shell", "cmd", "node", "npm", "yaml", "tiff", "png",
}

# 这些词本身就是正常英文，不该被"纠正"成专名。
REAL_WORDS = {
    "male", "female", "reset", "rest", "transform", "transforms", "adaptor",
    "adaptive", "form", "fill", "format", "simple", "light", "linear", "little",
    "local", "global", "normal", "random", "total", "final", "first", "second",
    "third", "next", "last", "same", "other", "each", "both", "more", "most",
    "less", "very", "much", "many", "some", "any", "every", "whole", "part",
    "point", "points", "step", "steps", "stage", "phase", "result", "results",
}


def load_lexicon() -> tuple[dict[str, str], set[str]]:
    """返回 (别名→规范词, 全部写法集合)。

    词典是 glossary/terms/*.tsv：医学课就用 med.tsv、金融课用 finance.tsv，
    没有对应文件也不会空转——把现有的一起读，总比只认 ai.tsv 强。
    """
    alias: dict[str, str] = {}
    files = sorted(TERMS.glob("*.tsv")) if TERMS.exists() else []
    if not files and LEXICON.exists():
        files = [LEXICON]
    for path in files:
        for line in read_lines(path):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            c = line.split("\t")
            canon = c[0].strip()
            if not canon:
                continue
            alias[canon.lower()] = canon
            for a in (c[1].split("|") if len(c) > 1 else []):
                if a.strip():
                    alias[a.strip().lower()] = canon
    return alias, set(alias)


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def best_match(tok: str, names: list[str]) -> tuple[str, float]:
    low = tok.lower()
    norm = low.translate(CONFUSE)
    best, score = "", 0.0
    for name in names:
        n = name.lower()
        s = max(ratio(low, n), ratio(norm, n.translate(CONFUSE)) if name else 0.0)
        if s > score:
            best, score = name, s
    return best, score


def acceptable(tok: str, good: str, score: float, count: int = 1) -> bool:
    """候选收不收：挡住常见英文词、包含关系、短词低相似度。"""
    low = tok.lower()
    g = good.lower().replace(" ", "")
    if low in BIG_STOP or low in REAL_WORDS or len(low) < 3:
        return False
    bare = low.translate(CONFUSE)
    if bare in g or g in bare:          # adapt / Adapter 这类包含关系是同一词的不同写法
        return False
    if any(c.isdigit() for c in low):
        return score >= 0.62
    if len(low) <= 3:
        return score >= 0.85
    if len(low) <= 5:                   # 短专名差一个字母就是另一个词，要求重复出现才收
        return score >= (0.75 if count >= 2 else 0.82)
    return score >= 0.72


def scan(segs: dict) -> tuple[list[dict], int]:
    alias, known = load_lexicon()
    names = sorted({v for v in alias.values()})
    counts: Counter[str] = Counter()
    example: dict[str, str] = {}
    where: dict[str, tuple[int, float]] = {}

    for part in segs["parts"]:
        for seg in part["segments"]:
            text = seg_text(seg)
            if not FENCE.search(text):        # 纯英文句子不猜
                continue
            for m in LATIN.finditer(text):
                tok = m.group(0)
                low = tok.lower()
                if low in known or low in STOP or len(low) < 3:
                    continue
                counts[tok] += 1
                example.setdefault(tok, text)
                where.setdefault(tok, (part["page"], seg["start"]))

    rows = []
    for tok, n in counts.most_common():
        good, score = best_match(tok, names)
        if not good or good.lower() == tok.lower():
            continue
        if not acceptable(tok, good, score, n):
            continue
        page, t = where[tok]
        rows.append({"good": good, "bad": tok, "score": round(score, 2), "count": n,
                     "page": page, "t": t, "example": example[tok]})
    rows.sort(key=lambda r: (-r["count"], -r["score"]))
    rows += num_scan(segs)
    return rows, sum(counts.values())


def num_scan(segs: dict) -> list[dict]:
    """数字类规则：试过两条路，都不可靠，所以这里返回空。

    ① 按"数字长得像"猜（等长、只差一位）：实测把「196 维拼成 197 维」这种正常表述
       判成错，误报率太高；
    ② 借 consistency 的冲突结果、拿出现次数多的当规范值：consistency 的属性抽取
       看不见「维度就是 718」这类语序，找不出可比的一对。
    结论：数字类的错听得靠回听音频或让模型看一眼上下文，不适合自动生成规则。
    """
    return []


def existing_rules() -> set[tuple[str, str]]:
    """现有词表里已生效（obs）的 (规范词, 误听形)。"""
    out = set()
    for f in list(GLOSSARY.glob("*.tsv")) + list(GLOSSARY.glob("series/*.tsv")):
        for line in read_lines(f):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            c = line.rstrip("\n").split("\t")
            if len(c) >= 4 and c[3].strip() == "obs":
                for bad in re.split(r"[|]", c[1]):
                    if bad.strip():
                        out.add((c[0].strip(), bad.strip()))
    return out


def evaluate(rows: list[dict]) -> None:
    truth = existing_rules()
    if not truth:
        print("现有词表没有 obs 规则，无法评测")
        return
    got = {(r["good"], r["bad"]) for r in rows}
    hit = truth & got
    latin = {(g, b) for g, b in truth
             if b.isascii() and sum(c.isalpha() for c in b) >= 2}
    hit_latin = latin & got
    miss = truth - got
    extra = got - truth
    print(f"\n[评测] 现有 obs 规则 {len(truth)} 条")
    print(f"  全部找回 {len(hit)} 条（{len(hit)/len(truth)*100:.0f}%）")
    if latin:
        print(f"  其中英文专名类 {len(latin)} 条，找回 {len(hit_latin)} 条"
              f"（{len(hit_latin)/len(latin)*100:.0f}%）")
        print(f"  中文同音 / 纯数字类 {len(truth)-len(latin)} 条：这个方法不处理"
              f"（要 LLM 或人听）")
    if miss:
        show = [f"{g}←{b}" for g, b in sorted(miss)
                if b.isascii() and sum(c.isalpha() for c in b) >= 2][:12]
        if show:
            print("  英文类没找回：" + "、".join(show))
    print(f"  多提 {len(extra)} 条（可能是对的新规则，也可能是错的，需要人过一遍）")


def run(bv: str, fixed: bool = False, write: bool = True) -> tuple[list[dict], int]:
    segs = load_segments(bv, fixed=fixed)
    rows, n_tok = scan(segs)
    if write:
        p = GLOSSARY / "_discovered.tsv"
        head = ["# 冷启动候选：全部是 hyp，不会被 fix 自动应用",
                "# 规范词\t误听形\t上下文\t状态\t证据"]
        lines = [f"{r['good']}\t{r['bad']}\t\thyp\t命中 {r['count']} 次；"
                 f"P{r['page']:02d} {r['t']:.0f}s；相似度 {r['score']}；"
                 f"例：{r['example'][:40]}" for r in rows]
        write_atomic(p, "\n".join(head + lines) + "\n")
        print(f"→ {p}（{len(rows)} 条候选）")
    return rows, n_tok


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py coldstart")
    ap.add_argument("bv")
    ap.add_argument("--fixed", action="store_true", help="从已纠错转写里找漏网的")
    ap.add_argument("--eval", action="store_true", help="用现有 obs 规则评测召回")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    rows, n_tok = run(args.bv, fixed=args.fixed)
    print(f"扫描了 {n_tok} 个英数串，提出 {len(rows)} 条候选")
    for r in rows[: args.top]:
        print(f"  {r['good']:<16} ← {r['bad']:<18} ×{r['count']:<3} 相似度 {r['score']}"
              f"  P{r['page']:02d} {r['t']:.0f}s")
    if args.eval:
        evaluate(rows)
