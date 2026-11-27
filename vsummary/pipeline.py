"""一条命令跑完：下载 → 转写 → 行业判定 → 纠错 → 摘要 → 总览 → 章节 → 讲义。

断点续跑不靠状态文件，而是直接看产物新不新：
每个阶段声明自己的产物，产物齐全且比上游新就跳过。中途断了重跑同一条命令即可，
想强制重做某个阶段用 --from。

用法：
  python cli.py all BV1E9U8BBEvZ --dry-run        # 先看它打算干什么
  python cli.py all BV1E9U8BBEvZ                  # 该跑什么跑什么
  python cli.py all BV1E9U8BBEvZ --from fix       # 从纠错开始往后
  python cli.py all BV1E9U8BBEvZ --only transcribe,fix
  python cli.py all BV1E9U8BBEvZ --no-llm         # 跳过需要 API 的摘要与章节
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

from vsummary import cite
from vsummary.core import load_segments, OUT, read_lines, video_info, WORK

AUDIO_EXT = (".m4a", ".mp3", ".webm", ".aac", ".flac", ".ogg", ".wav")


# ------------------------------------------------------------------ 小工具
def newest(*paths) -> float:
    t = 0.0
    for p in paths:
        if isinstance(p, (str, Path)):
            p = [p]
        for q in p:
            q = Path(q)
            if q.is_file():
                t = max(t, q.stat().st_mtime)
            elif q.is_dir():
                t = max([t] + [f.stat().st_mtime for f in q.rglob("*") if f.is_file()])
    return t


# ------------------------------------------------------------------ 阶段
def ch_download(bv: str, ctx: dict) -> tuple[bool, str]:
    n = ctx["n_parts"]
    got = [p for p in (WORK / bv).glob("*") if p.suffix.lower() in AUDIO_EXT] \
        if (WORK / bv).exists() else []
    return len(got) >= n, f"{len(got)}/{n} 个音频"


def ch_transcribe(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "segments.json"
    if not p.exists():
        return False, "没有 segments.json"
    seg = load_segments(bv, fixed=False)
    n = len(seg["parts"])
    segs = sum(len(x["segments"]) for x in seg["parts"])
    return n >= ctx["n_parts"] and segs > 0, f"{n} 个分P / {segs} 句"


def ch_classify(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "domain.json"
    return p.exists(), "domain.json" if p.exists() else "没有 domain.json"


def ch_fix(bv: str, ctx: dict) -> tuple[bool, str]:
    seg, fixed = OUT / bv / "segments.json", OUT / bv / "segments_fixed.json"
    if not fixed.exists():
        return False, "没有 segments_fixed.json"
    if seg.exists() and fixed.stat().st_mtime < seg.stat().st_mtime:
        return False, "segments_fixed.json 比 segments.json 旧"
    return True, "segments_fixed.json"


def ch_summarize(bv: str, ctx: dict) -> tuple[bool, str]:
    d = OUT / bv / "summaries"
    if not d.exists():
        return False, "没有 summaries/"
    pages = set()
    for f in d.glob("*.md"):
        for line in read_lines(f):
            if line.startswith("## P"):
                pages.add(int(line[4:6]))
    return len(pages) >= ctx["n_parts"], f"{len(pages)}/{ctx['n_parts']} 篇"


def ch_chapters(bv: str, ctx: dict) -> tuple[bool, str]:
    d = OUT / bv / "handout"
    chs = sorted(d.glob("ch*.md")) if d.exists() else []
    if len(chs) < ctx["n_chapters"]:
        return False, f"{len(chs)}/{ctx['n_chapters']} 章"
    ref = newest(OUT / bv / "segments_fixed.json", OUT / bv / "segments.json")
    return newest(chs) >= ref, f"{len(chs)} 章"


def ch_overview(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "总览.md"
    if not p.exists():
        return False, "没有 总览.md"
    return p.stat().st_mtime >= newest(OUT / bv / "summaries"), "总览.md"


def ch_consistency(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "consistency.md"
    if not p.exists():
        return False, "没有 consistency.md"
    ref = newest(OUT / bv / "segments_fixed.json", OUT / bv / "segments.json")
    return p.stat().st_mtime >= ref, "consistency.md"


def ch_annotate(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "annotate.json"
    if not p.exists():
        return False, "还没补注解"
    # 只看上游（转写 / 章节正文）是否更新，不看 ch*.md 的 mtime——
    # 否则 figures 插图会把 mtime 顶新，导致 annotate 永远"过期"、每次 all 都重跑。
    ref = max((x.stat().st_mtime for x in (OUT / bv / "segments_fixed.json",
                                           OUT / bv / "chapters_state.json") if x.exists()),
              default=0)
    return p.stat().st_mtime >= ref, "术语注解"


def ch_exercises(bv: str, ctx: dict) -> tuple[bool, str]:
    d = OUT / bv / "handout"
    chs = sorted(d.glob("ch*.md")) if d.exists() else []
    if not chs:
        return False, "还没有章节"
    ok = [f for f in chs if "## 自测题" in f.read_text(encoding="utf-8")]
    if len(ok) < len(chs):
        return False, f"{len(ok)}/{len(chs)} 章有自测题"
    ref = newest(OUT / bv / "segments_fixed.json", OUT / bv / "segments.json")
    return newest(ok) >= ref, f"{len(ok)} 章有自测题"


def ch_frames(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "frames.json"
    if not p.exists():
        return False, "还没抽过帧"
    n = len(json.loads(p.read_text(encoding="utf-8")))
    return True, f"{n} 帧"


def ch_figures(bv: str, ctx: dict) -> tuple[bool, str]:
    d = OUT / bv / "handout"
    chs = sorted(d.glob("ch*.md")) if d.exists() else []
    fp = OUT / bv / "frames.json"
    if not fp.exists():
        return True, "没有抽帧，跳过配图（要图先跑 frames）"
    if not chs:
        return False, "还没有章节"
    frames = json.loads(fp.read_text(encoding="utf-8"))
    have = {f.get("page") for f in frames}
    withimg = [f for f in chs if "![图 " in f.read_text(encoding="utf-8")]
    n = sum(f.read_text(encoding="utf-8").count("![图 ") for f in chs)
    if len(withimg) < len(chs):
        # 某章引用的分P 一帧都没抽过，那这章本来就没图可用——不算没做完
        blank = []
        for f in chs:
            text = f.read_text(encoding="utf-8")
            if "![图 " in text:
                continue
            pages = {x[0] for x in cite.parse(text)}
            if pages & have:
                blank.append(f)
        if not blank:
            return True, f"{n} 张图（其余章没有可用画面）"
        return False, f"{len(withimg)}/{len(chs)} 章有图（{len(blank)} 章有素材但没插上）"
    return True, f"{n} 张图"


def ch_termsheet(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "termsheet.json"
    if not p.exists():
        return False, "没有 termsheet.json"
    # 比"转写"新即可：annotate 会改 ch*.md，若拿 handout/ 当基准，这一步会永远重跑
    ref = newest(OUT / bv / "segments_fixed.json", OUT / bv / "segments.json")
    return p.stat().st_mtime >= ref, "名词速查"


def ch_prereq(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "prereq.md"
    if not p.exists():
        return False, "没有 prereq.md"
    # 同理：只有章节正文被重写才算"过期"，自己插的注不影响
    ref = newest(OUT / bv / "segments_fixed.json", OUT / bv / "segments.json")
    chs = sorted((OUT / bv / "handout").glob("ch*.md"))
    if chs and max(f.stat().st_mtime for f in chs) > p.stat().st_mtime:
        return False, "章节正文比体检结果新"
    return p.stat().st_mtime >= ref, "前置顺序"


def ch_gate(bv: str, ctx: dict) -> tuple[bool, str]:
    p = OUT / bv / "讲义.html"
    d = OUT / bv / "handout"
    if not p.exists():
        if not (sorted(d.glob("ch*.md")) if d.exists() else []):
            return True, "没有讲义（--no-llm 不出），跳过体检"
        return False, "讲义还没生成"
    from . import gate
    rows, code = gate.run(bv)
    bad = [r["name"] for r in rows if r["hard"] and not r["ok"]]
    if bad:
        return False, "硬指标不达标：" + "、".join(bad)
    return newest(p, OUT / bv / "termsheet.json") >= newest(OUT / bv / "handout"), \
        f"{sum(1 for r in rows if r['ok'])}/{len(rows)} 项通过"


def ch_handout(bv: str, ctx: dict) -> tuple[bool, str]:
    d = OUT / bv / "handout"
    if not (sorted(d.glob("ch*.md")) if d.exists() else []):
        return True, "没有章节正文（--no-llm 不写正文），跳过讲义"
    p = OUT / bv / "讲义.html"
    if not p.exists():
        return False, "没有 讲义.html"
    ref = newest(OUT / bv / "handout", OUT / bv / "changes.md", OUT / bv / "consistency.md",
                 OUT / bv / "segments_fixed.json", OUT / bv / "domain.json",
                 OUT / bv / "板书.md", OUT / bv / "termsheet.json", OUT / bv / "claims.json")
    return p.stat().st_mtime >= ref, "讲义.html"


STAGES = [
    ("download",   ch_download,   False, ["transcribe", "--bv", "{bv}", "--dl-only"]),
    ("transcribe", ch_transcribe, False, ["transcribe", "--bv", "{bv}", "--no-dl"]),
    ("classify",   ch_classify,   False, ["classify", "{bv}"]),
    ("fix",        ch_fix,        False, ["fix", "{bv}", "--emit"]),
    ("summarize",  ch_summarize,  True,  ["summarize", "{bv}"]),
    ("overview",   ch_overview,   True,  ["overview", "{bv}"]),
    ("consistency", ch_consistency, False, ["consistency", "{bv}"]),
    ("chapters",   ch_chapters,   True,  ["chapters", "{bv}", "--style", "auto"]),
    ("termsheet",  ch_termsheet,  True,  ["termsheet", "{bv}", "--all"]),
    ("exercises",  ch_exercises,  True,  ["exercises", "{bv}"]),
    ("figures",    ch_figures,    False, ["figures", "{bv}"]),    # annotate 放最后：它会改每一章正文，放前面会让后面每一步的"产物新旧"判断失效
    ("annotate",   ch_annotate,   False, ["annotate", "{bv}"]),
    ("prereq",     ch_prereq,     False, ["prereq", "{bv}"]),
    ("handout",    ch_handout,    False, ["handout", "{bv}"]),
    ("gate",       ch_gate,       False, ["gate", "{bv}"]),
]
BY_NAME = {s[0]: s for s in STAGES}


def run_stage(cmd: list[str]) -> None:
    """在同一个进程里调用 cli.py 的子命令（等价于命令行再敲一次）。"""
    mod = importlib.import_module(f"vsummary.{cmd[0]}")
    argv_backup = sys.argv[:]
    sys.argv = [f"cli.py {cmd[0]}"] + cmd[1:]
    try:
        mod.main()
    finally:
        sys.argv = argv_backup


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py all")
    ap.add_argument("bv")
    ap.add_argument("--from", dest="frm", default="", help="从这个阶段开始（含）")
    ap.add_argument("--only", default="", help="只跑这些阶段，如 transcribe,fix")
    ap.add_argument("--force", action="store_true", help="忽略产物是否已存在，全部重做")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--no-llm", action="store_true", help="跳过 summarize / overview / chapters")
    ap.add_argument("--chapters", default="", help="手工分章，传给 chapters")
    ap.add_argument("--title", default="", help="讲义标题，传给 handout")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--parts", default="", help="只处理这些分P（链接带 ?p=N 时自动带上）")
    ap.add_argument("--frames", default="", help="顺带抽帧，如 5,14（默认不抽：很慢，且 figures 会自动跳过）")
    args = ap.parse_args()

    bv = args.bv
    t0 = time.time()
    try:
        info = video_info(bv)
        n_parts, title = len(info["pages"]), info["title"]
    except Exception as e:
        seg_p = OUT / bv / "segments.json"
        if not seg_p.exists():
            raise SystemExit(f"取不到 {bv} 的视频信息（{e}），本地也没有 segments.json")
        seg = load_segments(bv, fixed=False)
        n_parts, title = len(seg["parts"]), seg["title"]
        print(f"[warn] 联网取元信息失败（{e}），按本地 segments.json 推断", flush=True)

    ctx = {"n_parts": n_parts, "n_chapters": n_parts}
    seg_p = OUT / bv / "segments.json"
    if seg_p.exists():
        try:
            parts = load_segments(bv, fixed=False)["parts"]
            if args.chapters:
                from vsummary.chapters import parse_spec
                ctx["spec"] = parse_spec(args.chapters)
                ctx["n_chapters"] = len(ctx["spec"])
            else:
                from vsummary.chapters import auto_split
                ctx["n_chapters"] = len(auto_split(parts))
        except Exception as e:
            print(f"[warn] 分章数量推断失败（{e}），按分P 数当章数", flush=True)

    names = [s[0] for s in STAGES]
    stages = list(STAGES)
    if args.frames:
        # 抽帧很慢（每帧 4–7 秒 OCR），默认不跑；给了 --frames 才插到 figures 之前
        if args.frames == "all":
            pages = ",".join(str(p) for p in range(1, n_parts + 1))
        else:
            pages = args.frames
        idx = [s[0] for s in stages].index("figures")
        stages.insert(idx, ("frames", ch_frames, False,
                            ["frames", "{bv}", "--pages", pages]))
        names = [s[0] for s in stages]
    if args.only:
        picked = [x.strip() for x in args.only.split(",") if x.strip()]
        skip = set(names) - set(picked)
    elif args.frm:
        if args.frm not in names:
            raise SystemExit(f"没有这个阶段：{args.frm}（可选 {', '.join(names)}）")
        skip = set(names[: names.index(args.frm)])
    else:
        skip = set()

    print(f"[all] {bv}《{title[:40]}》{n_parts}P{args.dry_run and '（演练）' or ''}", flush=True)

    plan = []
    dirty = False
    for name, check, needs_llm, tmpl in STAGES:
        if name in skip:
            plan.append((name, "跳过", "不在这条路线上"))
            continue
        if needs_llm and args.no_llm:
            plan.append((name, "跳过", "--no-llm"))
            continue
        if args.force:
            plan.append((name, "重做", "--force"))
            dirty = True
            continue
        if name == "download":
            done, why = ch_transcribe(bv, ctx)
            if done:
                plan.append((name, "跳过", "已有完整转写，不必重下"))
                continue
        if dirty:
            # 上游刚更新：先问一句"这一步还需不需要做"——不需要就别跟着重跑
            ok, why = check(bv, ctx)
            if ok:
                plan.append((name, "已完成", why))
                continue
            plan.append((name, "将运行", "上游刚更新"))
            continue
        ok, why = check(bv, ctx)
        plan.append((name, "已完成" if ok else "将运行", why))
        if not ok:
            dirty = True

    width = max(len(x[0]) for x in plan)
    for name, state, why in plan:
        icon = {"已完成": "✓", "将运行": "▸", "跳过": "-", "重做": "↻"}[state]
        print(f"  {icon} {name.ljust(width)}  {state}  {why}")

    if args.dry_run:
        print("\n[plan] 去掉 --dry-run 就开跑。中途断了重跑同一条命令即可。")
        return

    for name, state, why in plan:
        if state not in ("将运行", "重做"):
            continue
        _, _, _, tmpl = BY_NAME[name]
        cmd = [x.format(bv=bv) for x in tmpl]
        if name in ("summarize", "chapters"):
            for flag, val in (("--api-base", args.api_base), ("--api-key", args.api_key),
                              ("--model", args.model)):
                if val:
                    cmd += [flag, val]
        if name == "transcribe" and args.device:
            cmd += ["--device", args.device]
        if name == "transcribe" and args.parts:
            cmd += ["--parts", args.parts]
        if name == "chapters" and args.chapters:
            cmd += ["--chapters", args.chapters]
        if name == "handout" and args.title:
            cmd += ["--title", args.title]
        print(f"\n{'='*66}\n[{name}] {' '.join(cmd[1:])}\n{'='*66}", flush=True)
        try:
            run_stage(cmd)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if code == 0:                 # gate 用 SystemExit(0) 表示"可以交付"
                continue
            print(f"\n[停止] {name} 失败（exit {code}）。修好问题后重跑：\n"
                  f"  python cli.py all {bv} --from {name}", flush=True)
            raise
        except Exception as e:
            print(f"\n[停止] {name} 抛异常：{type(e).__name__}: {e}\n"
                  f"修好后重跑：python cli.py all {bv} --from {name}", flush=True)
            raise

    print(f"\n[done] 用时 {(time.time()-t0)/60:.1f} 分钟 → {OUT / bv}")


if __name__ == "__main__":
    main()
