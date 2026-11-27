"""统一入口。

  python cli.py transcribe --bv BV1E9U8BBEvZ
  python cli.py classify   BV1E9U8BBEvZ
  python cli.py fix        BV1E9U8BBEvZ --emit
  python cli.py harvest    BV1E9U8BBEvZ
  python cli.py summarize  BV1E9U8BBEvZ        (需要 OpenAI 兼容 key)
  python cli.py overview   BV1E9U8BBEvZ
  python cli.py handout    BV1E9U8BBEvZ
  python cli.py check                          (环境自检)
"""
from __future__ import annotations

from vsummary.core import read_lines

import importlib
import sys

CMDS = {
    "transcribe": "vsummary.transcribe",
    "classify": "vsummary.classify",
    "fix": "vsummary.fix",
    "harvest": "vsummary.harvest",
    "summarize": "vsummary.summarize",
    "overview": "vsummary.overview",
    "consistency": "vsummary.consistency",
    "claims": "vsummary.claims",
    "cite": "vsummary.cite",
    "coldstart": "vsummary.coldstart",
    "prereq": "vsummary.prereq",
    "termsheet": "vsummary.termsheet",
    "frames": "vsummary.frames",
    "chapters": "vsummary.chapters",
    "annotate": "vsummary.annotate",
    "figures": "vsummary.figures",
    "exercises": "vsummary.exercises",
    "handout": "vsummary.handout",
    "brief": "vsummary.brief",
    "series": "vsummary.series",
    "ask": "vsummary.ask",
    "faithful": "vsummary.faithful",
    "gate": "vsummary.gate",
    "all": "vsummary.pipeline",
}

USAGE = """用法：python cli.py <命令> [参数]

命令：
  all        BV号 [--dry-run] [--from 阶段] [--only a,b] [--no-llm] [--force]
                 阶段：download transcribe classify fix summarize overview consistency chapters
                       termsheet annotate exercises figures prereq handout gate
  transcribe --bv BV号 [--no-dl | --dl-only] [--parts 1,2] [--device auto|cuda|cpu]
  classify   BV号
  fix        BV号 [--emit]
  harvest    BV号
  summarize  BV号 [--api-base URL] [--model 名] [--only 1,2] [--workers 4]
  overview   BV号
  consistency BV号 [--quiet]
  claims     BV号 [--top 15]          (抽断言，找前后矛盾)
  cite       BV号 [--dir handout]    (体检章节里的 [Pxx mm:ss] 出处)
  coldstart  BV号 [--fixed] [--eval]  (自动猜听错的专名，只出 hyp 候选)
  prereq     BV号 [--all]            (体检讲义：哪些术语先用了后解释)
  termsheet  BV号 [--all] [--llm]    (名词速查：每个术语一句话定义)
  frames     BV号 [--pages 5,14] [--no-download]  (抽帧 + OCR，补画面里的板书)
  chapters   BV号 [--plan] [--chapters "1:1,2:2-6"] [--only 3] [--force] [--style textbook]
  annotate   BV号 [--dry]           (给术语首次出现处补一句话注解，幂等)
  figures    BV号 [--per-chapter 3] [--total 18] [--dry]  (从抽好的帧里挑图插进章节)
  exercises  BV号 [--per-chapter 4] [--only 3] [--dry]     (每章出生 4 道自测题，答案带出处)
  handout    BV号 [--title 标题]
  brief      BV号 [--only 1,3]      (精简版：把 7 章压成复习短稿，不动原稿)
  ask        BV号 "问题" [--top 6] [--no-llm] [--json]   (从转写里查答案，带出处)
  faithful   BV号 [--sample 100] [--only 3] [--all]      (抽检：讲义的话原文支持吗)
  series     BV号 [--max 5] [--bvs BV1,BV2]  (跨期整合：合集当一门课，去重+修正)
  gate       BV号 [--strict]        (交付前硬检查，不达标退出码 1)
  check

一条视频从零到讲义（推荐先看计划）：
  python cli.py all BV1E9U8BBEvZ --dry-run
  python cli.py all BV1E9U8BBEvZ

只要转写 + 纠错（不调大模型）：
  python cli.py all BV1E9U8BBEvZ --no-llm
"""


def check() -> None:
    from vsummary.core import GLOSSARY, MODELS, OUT, ROOT, WORK, enable_cuda

    print(f"项目根目录 : {ROOT}")
    for name, p in (("work", WORK), ("out", OUT), ("models", MODELS), ("glossary", GLOSSARY)):
        print(f"  {name:<9}: {'存在' if p.exists() else '不存在'}  {p}")

    m = sorted(x.name for x in MODELS.glob("faster-whisper-*")) if MODELS.exists() else []
    print(f"  模型      : {', '.join(m) if m else '没有，需要下载到 models/ 下'}")

    dirs = enable_cuda()
    print(f"  CUDA DLL  : {len(dirs)} 个 nvidia bin 目录")
    try:
        import ctranslate2
        n_gpu = ctranslate2.get_cuda_device_count()
        print(f"  ctranslate2: {ctranslate2.__version__}，可见 GPU {n_gpu}")
        if n_gpu and not dirs:
            print("              ⚠ 看得到 GPU 但没装 CUDA 运行库，转写会退回 CPU（慢 8 倍）。")
            print("                装一行就好：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-nvrtc-cu12")
    except Exception as e:
        print(f"  ctranslate2: 不可用（{e}）")

    for f in ("common.tsv", "ai.tsv"):
        p = GLOSSARY / f
        if p.exists():
            n = sum(1 for line in read_lines(p)
                    if line.strip() and not line.lstrip().startswith("#"))
            print(f"  词表 {f:<9}: {n} 条")

    bvs = sorted(x.name for x in OUT.glob("BV*")) if OUT.exists() else []
    print(f"  已处理    : {', '.join(bvs) if bvs else '（空）'}")


def tests() -> None:
    """跑单元测试（不需要网络 / GPU / key）。"""
    import unittest
    from vsummary.core import ROOT
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "check":
        check()
        return
    if cmd == "test":
        tests()
        return
    if cmd not in CMDS:
        sys.exit(f"未知命令 {cmd}\n\n{USAGE}")
    from vsummary.source import normalize_argv
    rest = normalize_argv([cmd] + rest)[1:]     # 链接 / av 号 / 短链 一律换成 BV
    sys.argv = [f"cli.py {cmd}"] + rest
    importlib.import_module(CMDS[cmd]).main()


if __name__ == "__main__":
    main()
