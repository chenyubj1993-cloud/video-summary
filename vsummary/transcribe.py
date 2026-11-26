"""批量转写 B 站视频（单视频/多分P 通用），GPU 优先、自动回落 CPU。

用法：
  python cli.py transcribe --bv BV1E9U8BBEvZ
  python cli.py transcribe --bv BV1E9U8BBEvZ --no-dl            # 复用 work/<bv>/ 里已有音频
  python cli.py transcribe --bv BV1E9U8BBEvZ --no-dl --parts 1,2

产物：out/<bv>/NN_<分P标题>.txt / .srt、segments.json、index.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

from vsummary.core import (AUDIO_EXT, MODELS, OUT, WORK, decode, enable_cuda,
                           safe, seg_text, ts, video_info)

DEFAULT_MODEL = "faster-whisper-large-v3-turbo"


def download(bv: str, dest) -> None:
    os.makedirs(dest, exist_ok=True)
    # 单集视频没有 playlist_index，yt_dlp 会存成 NA.m4a；番剧/需要登录的源格式名也不同，
    # 所以按 bestaudio → best → worst 依次退让，别一上来就因为格式不存在直接失败。
    url = f"https://www.bilibili.com/video/{bv}"
    tmpl = os.path.join(str(dest), "%(playlist_index)02d.%(ext)s")
    last = ""
    for fmt in ("bestaudio/best", "best", "worst"):
        cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--sleep-requests", "0.4",
               "-f", fmt, "-o", tmpl, url]
        print(f"[dl] 尝试格式 {fmt}", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return
        last = (r.stderr or "").strip().splitlines()
        last = last[-1] if last else "未知原因"
        print(f"[dl] 失败：{last}", flush=True)
        if "Requested format is not available" not in (r.stderr or ""):
            break
    raise SystemExit(
        f"下载失败：{bv}（{last}）\n"
        "  · 番剧/付费/会员专享常见（会被重定向到 bangumi 且要求登录），本工具不处理这类源\n"
        "  · 地区限制或风控也会这样，可稍后重试\n"
        "  · 手动下载音频放到 work/<BV号>/01.m4a 后，用 --no-dl 继续")


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py transcribe")
    ap.add_argument("--bv", required=True)
    ap.add_argument("--no-dl", action="store_true", help="不下载，直接用 work/<bv>/ 里已有音频")
    ap.add_argument("--parts", default="", help="只转写这些分P，如 1,2,3")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = ap.parse_args()

    bv = args.bv
    work = WORK / bv
    out = OUT / bv
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    info = video_info(bv)
    pages = info["pages"]
    print(f"[meta] {info['title'][:60]} … 共 {len(pages)}P", flush=True)

    if not args.no_dl:
        download(bv, work)

    files = {}
    loose = []
    for p in sorted(work.glob("*")):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        m = re.match(r"^(\d+)\.", p.name)
        if m:
            files[int(m.group(1))] = str(p)
        else:
            loose.append(str(p))          # 单集视频 yt-dlp 会存成 NA.m4a
    for i, path in enumerate(loose, start=1):
        files.setdefault(i, path)
    if not files:
        sys.exit(f"[err] {work} 里没有音频文件（先跑一次不带 --no-dl 的）")

    only = {int(x) for x in args.parts.split(",") if x.strip()} if args.parts else None
    # 校验编号：work 目录里残留的旧音频（换 BV 没清目录、手工补文件）可能超出分P 数，
    # 早先直接 pages[i-1] 会 IndexError 把整批转写打断。
    bad = [i for i in sorted(files) if i < 1 or i > len(pages)]
    for i in bad:
        print(f"[warn] 忽略 {files[i]}：编号 {i} 超出分P 数 {len(pages)}", flush=True)
        files.pop(i)
    todo = [(i, pages[i - 1]) for i in sorted(files) if only is None or i in only]
    if not todo:
        sys.exit(f"[err] 没有可转写的分P（音频编号与分P 对不上，清一下 {work} 重下）")
    print(f"[plan] 待转写 {len(todo)} 个分P", flush=True)

    enable_cuda()
    from faster_whisper import WhisperModel
    t2s = to_simplified_safe()

    def build(device: str, ctype: str):
        return WhisperModel(str(MODELS / args.model), device=device, compute_type=ctype,
                            cpu_threads=min(16, os.cpu_count() or 8))

    device, ctype = ("cpu", "int8") if args.device == "cpu" else ("cuda", "float16")
    try:
        model = build(device, ctype)
        if device == "cuda":
            import ctranslate2
            ctranslate2.get_cuda_device_count()  # 触发一次加载，暴露缺 DLL 的问题
    except Exception as e:
        print(f"[warn] {device}/{ctype} 不可用（{e}），回落 CPU int8", flush=True)
        device, ctype = "cpu", "int8"
        model = build(device, ctype)
    print(f"[model] {args.model} on {device}/{ctype}", flush=True)

    all_segments, rows, t_start = [], [], time.time()
    for n, (idx, page) in enumerate(todo, 1):
        title = page["part"]
        t0 = time.time()
        audio = decode(files[idx])
        dur = len(audio) / 16000
        segs, _ = model.transcribe(
            audio, language="zh", beam_size=5, condition_on_previous_text=False,
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400))
        out_segs, prev = [], ""
        for s in segs:
            text = t2s.convert(s.text.strip())
            if not text or text == prev:
                continue
            prev = text
            out_segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text,
                             "lp": round(s.avg_logprob, 3), "ns": round(s.no_speech_prob, 3),
                             "cr": round(s.compression_ratio, 2)})
        el = time.time() - t0
        base = f"{idx:02d}_{safe(title)}"
        full = "".join(x["text"] for x in out_segs)

        with open(out / (base + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(x["text"] for x in out_segs))
        with open(out / (base + ".srt"), "w", encoding="utf-8") as f:
            for k, x in enumerate(out_segs, 1):
                f.write(f"{k}\n{ts(x['start'])} --> {ts(x['end'])}\n{x['text']}\n\n")

        all_segments.append({"page": idx, "part": title, "duration": round(dur, 1),
                             "segments": out_segs})
        rows.append({"page": idx, "part": title, "dur": round(dur, 1), "chars": len(full),
                     "segs": len(out_segs), "elapsed": round(el, 1),
                     "speed": round(dur / el, 1) if el else 0})
        print(f"[{n}/{len(todo)}] P{idx:02d} {dur/60:5.1f}min → {len(full):5d}字 "
              f"{el:6.1f}s ({dur/el if el else 0:5.1f}x) {title[:24]}", flush=True)

    # --parts 只重做一部分时，把没重做的分P 从旧 segments.json 里接回来，
    # 否则会把整份转写覆盖成残缺版（这个坑踩过一次）。
    merged = {p["page"]: p for p in all_segments}
    old = out / "segments.json"
    if args.parts and old.exists():
        try:
            kept = 0
            for p in json.load(open(old, encoding="utf-8"))["parts"]:
                if p["page"] not in merged:
                    merged[p["page"]] = p
                    kept += 1
            if kept:
                print(f"[merge] 保留旧的 {kept} 个分P", flush=True)
        except Exception as e:
            print(f"[warn] 旧的 segments.json 读不了（{e}），按当前结果覆盖", flush=True)
    final_parts = [merged[k] for k in sorted(merged)]

    json.dump({"bv": bv, "title": info["title"], "owner": info["owner"]["name"],
               "parts": final_parts},
              open(out / "segments.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    total = sum(p["duration"] for p in final_parts)
    spent = time.time() - t_start
    speed = {}
    for r in rows:
        speed[r["page"]] = r["speed"]
    with open(out / "index.md", "w", encoding="utf-8") as f:
        f.write(f"# {info['title']}\n\nUP：{info['owner']['name']}　{bv}　"
                f"{len(final_parts)}/{len(pages)}P / {total/3600:.2f}h\n\n"
                f"本次转写 {len(rows)} 个分P，耗时 {spent/60:.1f} 分钟\n\n"
                "| P | 分P 标题 | 时长 | 字数 | 句数 | 速度 |\n|---|---|---|---|---|---|\n")
        for p in final_parts:
            n = len("".join(seg_text(s) for s in p["segments"]))
            sp = f"{speed[p['page']]}x" if p["page"] in speed else "—"
            f.write(f"| {p['page']} | {p['part']} | {p['duration']/60:.1f}min | {n} "
                    f"| {len(p['segments'])} | {sp} |\n")

    chars = sum(len(x["text"]) for p in final_parts for x in p["segments"])
    print(f"\n[done] {len(rows)} 个分P / {chars:,} 字（现有 {len(final_parts)} 个分P 汇总）/ "
          f"{spent/60:.1f} 分钟", flush=True)


def to_simplified_safe():
    try:
        from vsummary.core import to_simplified
        return to_simplified()
    except Exception:
        class _Nop:
            def convert(self, s):
                return s
        print("[warn] 没装 opencc，繁体不转简体", flush=True)
        return _Nop()


if __name__ == "__main__":
    main()
