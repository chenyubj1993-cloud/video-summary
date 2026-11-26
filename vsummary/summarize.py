"""逐P 摘要（需要任意 OpenAI 兼容 /chat/completions 接口）。

用法：
  set OPENAI_API_KEY=sk-xxx
  python cli.py summarize BV1E9U8BBEvZ --api-base https://api.deepseek.com/v1 --model deepseek-chat
  python cli.py summarize BV1E9U8BBEvZ --only 3,4 --workers 4

产物：out/<bv>/summaries/P01.md …（格式与 cli.py overview 兼容）
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor

from vsummary.core import OUT, load_segments, seg_text
from vsummary.llm import LLM

SYSTEM = ("你是中文技术课程编辑。把一段课程录音的转写整理成笔记。"
          "只依据给定文本，不要补充外部知识；宁可少写，不要编造数字。")

TEMPLATE = """分P 标题：P{page:02d} {part}
转写全文（{n} 字，{dur}）：

{body}

请严格按下面格式输出，不要写别的内容：

## P{page:02d} {part}
**一句话**：30 字以内说清这P 讲了什么
**要点**：
- （3–5 条，每条 15–40 字，必须是可检索的具体内容：方法名、公式、参数、命令、实验结论；禁止写"讲解了xx的使用"这类空话）
**关键术语**：3–6 个容易被语音识别听错的专有名词（英文保留原样），格式 `正确写法（转写为"错误写法"）`，没有就写"无"
**存疑**：只写明显听错或前后矛盾之处，没有就写"无"
"""


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py summarize")
    ap.add_argument("bv")
    ap.add_argument("--api-base", default=None, help="OpenAI 兼容接口，默认读 SUMMARY_API_BASE / OPENAI_BASE_URL")
    ap.add_argument("--api-key", default=None, help="默认读 OPENAI_API_KEY")
    ap.add_argument("--model", default=None, help="默认读 SUMMARY_MODEL / OPENAI_MODEL")
    ap.add_argument("--only", default="", help="只做这些分P，如 1,2,3")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="已存在的摘要也重写")
    args = ap.parse_args()

    llm = LLM(base=args.api_base, key=args.api_key, model=args.model)
    if not llm.ready:
        raise SystemExit(
            "没有 API key。设一个 OpenAI 兼容接口的 key 再跑，例如：\n"
            '  $env:OPENAI_API_KEY="sk-..."\n'
            f"  python cli.py summarize {args.bv} --api-base https://api.deepseek.com/v1\n"
            "  本地 ollama：--api-base http://127.0.0.1:11434/v1 --model qwen2.5:14b --api-key ollama\n"
            "（本机现有的 40 篇摘要当初由 agent 直接读转写写成，不依赖本接口。）")
    print(f"[llm] {llm.describe()}", flush=True)

    out = OUT / args.bv
    (out / "summaries").mkdir(parents=True, exist_ok=True)
    seg = load_segments(args.bv)
    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None

    todo = []
    for p in seg["parts"]:
        if only is not None and p["page"] not in only:
            continue
        if not args.force and (out / "summaries" / f"P{p['page']:02d}.md").exists():
            print(f"[skip] P{p['page']:02d} 已有摘要", flush=True)
            continue
        todo.append(p)

    def work(p: dict) -> tuple[int, bool]:
        body = "\n".join(seg_text(s) for s in p["segments"])
        prompt = TEMPLATE.format(page=p["page"], part=p["part"], n=len(body),
                                 dur=f"{p['duration']/60:.1f} 分钟", body=body)
        try:
            text = llm.chat(prompt, system=SYSTEM)
        except Exception as e:
            print(f"[err] P{p['page']:02d}: {e}", flush=True)
            return p["page"], False
        (out / "summaries" / f"P{p['page']:02d}.md").write_text(text + "\n", encoding="utf-8")
        print(f"[ok] P{p['page']:02d} {p['part'][:24]} {len(text)}字", flush=True)
        return p["page"], True

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        res = list(ex.map(work, todo))
    ok = sum(1 for _, good in res if good)
    print(f"\n[done] {ok}/{len(todo)} 篇 → {out / 'summaries'}")
    if ok < len(todo):
        print("有失败的分P，修好 key/网络后用 --only 重跑失败的即可")
    print(f"[llm] {llm.stats()}")


if __name__ == "__main__":
    main()
