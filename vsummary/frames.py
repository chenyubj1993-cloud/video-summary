"""视觉通道：把课件里的板书/PPT/代码截图抓出来，挂到时间轴上。

整条流水线此前只用音频，而技术课最关键的信息往往是"念不出来"的：
PPT 上的公式、代码、架构图、超参表。这里补上这一路。

做法（不做无脑抽帧）：
  1. 只下载 480p 视频——够 OCR，文件小；
  2. 逐帧解码但只看缩略灰度图的帧间差，翻页/切代码时差分会跳起来，此时抓帧；
  3. 抓到的帧算 dHash 去重（同一页停留会产生多帧）；
  4. 对留下的帧跑 RapidOCR（onnxruntime，CPU 就行）；
  5. 每帧挂到它所在的时间点，方便和转写对照。

产物：
  out/<bv>/frames/P05_0041_03m07s.jpg
  out/<bv>/frames.json        [{page, t, hash, ocr, near(附近在讲什么)}]
  out/<bv>/板书.md            按分P 汇总，只留有内容的行

用法：
  python cli.py frames BV1E9U8BBEvZ --pages 5,14
  python cli.py frames BV1E9U8BBEvZ --pages 5 --no-download   # 已经有视频时
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from .core import OUT, WORK, fmt, load_segments, seg_text

THR = 2.5          # 帧间灰度平均差：实测翻页 3–7、人物微动 0–0.1，取 2.5 较稳
MIN_GAP = 1.0      # 两次抓帧的最小间隔（秒）
SMALL = (48, 27)   # 缩略图尺寸，算差分用
MAX_LINES = 14     # 每帧最多保留几行 OCR 文本


# ------------------------------------------------------------------ 下载
def download(bv: str, page: int, out_dir: Path, quality: int = 720) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bv}?p={page}"
    fmt_sel = (f"bv*[height<={quality}][ext=mp4]/bv*[height<={quality}]/"
               f"b[height<={quality}]/bv*[height<=720]/b")
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings",
           "-f", fmt_sel, "-o", str(out_dir / f"P{page:02d}.%(ext)s"),
           "--print", "after_move:filepath", url]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(f"[err] P{page:02d} 下载失败：{(p.stderr or '').strip().splitlines()[-1:]}",
              flush=True)
        return None
    path = Path(p.stdout.strip().splitlines()[-1])
    if not path.exists():
        print(f"[err] P{page:02d} 没找到下载后的文件", flush=True)
        return None
    print(f"[dl] P{page:02d} {path.stat().st_size/1048576:.0f} MB "
          f"（{time.time()-t0:.0f}s）", flush=True)
    return path


# ------------------------------------------------------------------ 抓帧
def dhash(gray_small: np.ndarray) -> int:
    """16x16 灰度图的水平梯度哈希，返回 64 位整数。"""
    img = gray_small[:: max(1, gray_small.shape[0] // 16),
                     :: max(1, gray_small.shape[1] // 16)][:16, :16]
    if img.shape != (16, 16):
        img = np.resize(img, (16, 16))
    bits = (img[:, 1:] > img[:, :-1]).flatten()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def grab(video: Path, out_dir: Path, page: int, thr: float = THR,
         min_gap: float = MIN_GAP, max_frames: int = 400) -> list[dict]:
    """解码整个视频，只在画面变化处落盘。返回 [{t, path, hash}]。"""
    import av
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    container = av.open(str(video))
    stream = container.streams.video[0]
    tb = stream.time_base
    prev = None
    last_t = -1e9
    kept: list[dict] = []
    hashes: list[int] = []
    t0 = time.time()
    n = 0

    for frame in container.decode(stream):
        if frame.pts is None:
            continue
        t = float(frame.pts * tb)
        n += 1
        arr = frame.to_ndarray(format="gray")
        h, w = arr.shape
        small = arr[:: max(1, h // SMALL[1]), :: max(1, w // SMALL[0])].astype(np.int16)
        diff = 99.0 if prev is None or prev.shape != small.shape else \
            float(np.abs(small - prev).mean())
        prev = small
        if diff < thr or t - last_t < min_gap or len(kept) >= max_frames:
            continue

        h64 = dhash(small.astype(np.uint8))
        if any(hamming(h64, x) <= 6 for x in hashes[-25:]):   # 同一页的邻近帧
            last_t = t
            continue

        rgb = frame.to_ndarray(format="rgb24")
        name = f"P{page:02d}_{len(kept):03d}_{int(t)//60:02d}m{int(t)%60:02d}s.jpg"
        path = out_dir / name
        Image.fromarray(rgb).save(path, quality=82)
        kept.append({"t": round(t, 2), "path": str(path), "hash": h64})
        hashes.append(h64)
        last_t = t

    container.close()
    print(f"[grab] P{page:02d} 解码 {n} 帧（{time.time()-t0:.0f}s）→ 抓 {len(kept)} 张",
          flush=True)
    return kept


# ------------------------------------------------------------------ OCR
def ocr_frames(frames: list[dict]) -> None:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    t0 = time.time()
    for i, f in enumerate(frames, 1):
        try:
            res, _ = engine(f["path"])
        except Exception as e:
            f.setdefault("_items", [])
            print(f"[warn] OCR 失败 {f['path']}：{e}", flush=True)
            continue
        try:
            from PIL import Image
            im = Image.open(f["path"])
            f["_w"], f["_h"] = im.width, im.height
        except Exception:
            f["_w"] = f["_h"] = 0
        items = []
        for r in res or []:
            if len(r) < 2:
                continue
            box, text = r[0], str(r[1]).strip()
            try:
                x0 = min(p[0] for p in box)
                y0 = min(p[1] for p in box)
            except Exception:
                x0 = y0 = 0.0
            if text:
                items.append((x0, y0, text))
        f["_items"] = items
        if i % 10 == 0:
            print(f"[ocr] {i}/{len(frames)}", flush=True)
    print(f"[ocr] 完成 {len(frames)} 张（{time.time()-t0:.0f}s）", flush=True)
    refine(frames)


def refine(frames: list[dict]) -> None:
    """按位置去掉界面噪声，而不是按词频。

    早期版本用"某行在六成帧里出现就丢掉"，结果把**静止的代码/幻灯片**也当噪声删了——
    代码停在一屏上就是每帧都一样。改成只看几何位置：
      · 左边 8% 以内 = 工程树/工具栏
      · 上边 4.5% 以内 = 菜单栏 / 窗口标题
      · 下边 4% 以内 = 状态栏
    剩下的都当内容。
    """
    for f in frames:
        w, h = f.get("_w") or 0, f.get("_h") or 0
        kept: list[tuple[int, str]] = []
        for x0, y0, t in f.get("_items", []):
            if len(t) < 2:
                continue
            if w and x0 < 0.08 * w:
                continue
            if h and (y0 < 0.025 * h or y0 > 0.96 * h):   # 0.025 而不是 0.045：
                continue                                  # 幻灯片标题常常就在 3% 处，别当菜单栏删掉
            kept.append((len(kept), t))
        if len(kept) > MAX_LINES:                       # 有代码符号的排前面，其余按序补
            scored = sorted(
                kept, key=lambda it: -(2 * bool(re.search(r"[=(){}<>\[\]#_/\\]", it[1]))
                                       + min(len(it[1]), 40) / 40))
            kept = sorted(scored[:MAX_LINES])
        f["ocr"] = "\n".join(t for _, t in kept)


def clean(text: str) -> str:
    """去掉 OCR 常见的纯符号噪声行，只留有信息量的。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 2:
            continue
        alnum = sum(c.isalnum() for c in s)
        if alnum / max(1, len(s)) < 0.5:
            continue
        out.append(s)
    return "\n".join(out)


# ------------------------------------------------------------------ 对齐
def attach_context(frames: list[dict], parts: list[dict]) -> None:
    """每帧挂上"这一刻在讲什么"（就近的转写句）。"""
    by_page = {p["page"]: p for p in parts}
    for f in frames:
        part = by_page.get(f["page"])
        if not part:
            f["near"] = ""
            continue
        best, gap = "", 1e9
        for seg in part["segments"]:
            d = abs(seg["start"] - f["t"])
            if d < gap:
                gap, best = d, seg_text(seg)
            if seg["start"] > f["t"] + 20:
                break
        f["near"] = best if gap < 30 else ""


# ------------------------------------------------------------------ 汇总
def write_md(bv: str, frames: list[dict]) -> Path:
    p = OUT / bv / "板书.md"
    pages: dict[int, list[dict]] = {}
    for f in frames:
        pages.setdefault(f["page"], []).append(f)
    lines = [f"# {bv} 板书与画面文字", "",
             "> 由本地抽帧 + OCR 生成，只是「画面里有什么」，不一定和口播一致；",
             "> 识别错的行保留了，方便你怀疑它。", ""]
    for page in sorted(pages):
        lines.append(f"## P{page:02d}")
        lines.append("")
        for f in pages[page]:
            body = clean(f.get("ocr", ""))
            if not body:
                continue
            lines.append(f"### {fmt(f['t'])}　{f.get('near','')[:40]}")
            lines.append("")
            lines.append("```")
            lines.append(body)
            lines.append("```")
            lines.append("")
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return p


def board_text(bv: str, pages: list[int], limit: int = 4000) -> str:
    """给章节生成用的"画面文字"素材：只挑有内容的分P，控制长度。"""
    p = OUT / bv / "frames.json"
    if not p.exists():
        return ""
    try:
        frames = json.load(open(p, encoding="utf-8"))
    except Exception:
        return ""
    picked = [f for f in frames if f.get("page") in pages and clean(f.get("ocr", ""))]
    if not picked:
        return ""
    out, used = [], 0
    for f in picked:
        body = clean(f.get("ocr", ""))
        if len(body) < 12:
            continue
        block = f"[P{f['page']:02d} {fmt(f['t'])} 画面]\n{body}"
        if used + len(block) > limit:
            break
        out.append(block)
        used += len(block)
    if not out:
        return ""
    return ("\n\n下面是画面里识别出来的文字（PPT/代码/板书，OCR 结果，可能有识别错误）：\n\n"
            + "\n\n".join(out))


def run(bv: str, pages: list[int] | None = None, no_download: bool = False,
        quality: int = 720, thr: float = THR) -> list[dict]:
    segs = load_segments(bv)
    parts = {p["page"]: p for p in segs["parts"]}
    pages = pages or [min(parts)]          # 默认先挑第一个分P 试
    vdir = WORK / "video"
    fdir = OUT / bv / "frames"

    prev_json = OUT / bv / "frames.json"    # 已有结果先读进来，按分P 替换而不是整体覆盖
    existing: list[dict] = []
    if prev_json.exists():
        try:
            existing = json.load(open(prev_json, encoding="utf-8"))
        except Exception:
            existing = []
    old = [f for f in existing if f.get("page") not in pages]
    new_frames: list[dict] = []

    for page in pages:
        if page not in parts:
            print(f"[skip] 没有 P{page:02d}")
            continue
        video = None
        if not no_download:
            video = download(bv, page, vdir, quality)
        if video is None:
            cand = sorted(vdir.glob(f"P{page:02d}.*"))
            video = cand[0] if cand else None
        if video is None:
            # 下载失败时保住这个分P 的旧帧：早先先把旧帧剔除再抓，
            # 一失败旧帧就被永久丢掉（板书.md 会静默少内容）。
            keep = [f for f in existing if f.get("page") == page]
            if keep:
                print(f"[warn] P{page:02d} 没有视频文件，保留已有 {len(keep)} 帧", flush=True)
                new_frames += keep
            else:
                print(f"[skip] P{page:02d} 没有视频文件")
            continue
        frames = grab(video, fdir, page, thr=thr)
        if not frames:
            keep = [f for f in existing if f.get("page") == page]
            if keep:
                print(f"[warn] P{page:02d} 解码出 0 帧，保留已有 {len(keep)} 帧", flush=True)
                new_frames += keep
                continue
        for f in frames:
            f["page"] = page
        ocr_frames(frames)
        new_frames += frames

    all_frames = sorted(old + new_frames, key=lambda f: (f["page"], f["t"]))
    attach_context(all_frames, segs["parts"])
    out = OUT / bv / "frames.json"
    out.write_text(json.dumps(all_frames, ensure_ascii=False, indent=1), encoding="utf-8")
    md = write_md(bv, all_frames)
    print(f"\n→ 本次 {len(new_frames)} 帧，累计 {len(all_frames)} 帧 → {md}")
    return all_frames


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py frames")
    ap.add_argument("bv")
    ap.add_argument("--pages", default="", help="分P 号，逗号分隔；默认第一个")
    ap.add_argument("--no-download", action="store_true", help="用 work/video 下已有的视频")
    ap.add_argument("--quality", type=int, default=720)
    args = ap.parse_args()
    pages = [int(x) for x in args.pages.replace("，", ",").split(",") if x.strip()]
    run(args.bv, pages or None, args.no_download, args.quality)
