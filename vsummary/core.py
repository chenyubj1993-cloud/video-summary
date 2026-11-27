"""共享底座：路径、B站 API、音频解码、GPU 初始化、分段读取。

所有模块都从这里取常量（ROOT 由本文件位置反推），不再各自写死绝对路径。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sysconfig
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUT = ROOT / "out"
MODELS = ROOT / "models"
GLOSSARY = ROOT / "glossary"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
AUDIO_EXT = (".m4a", ".mp3", ".webm", ".aac", ".flac", ".ogg", ".wav")


# ---------------------------------------------------------------- B 站接口
def api(path: str, retries: int = 3) -> dict:
    """打 B 站公开接口。412/5xx/断连都退避重试，最终失败给可读的中文错误。"""
    url = "https://api.bilibili.com" + path
    last = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": "https://www.bilibili.com/"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in (408, 412, 429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(min(2 ** attempt, 10))
    raise SystemExit(f"请求 B 站接口失败（{last}）：{url}\n"
                     "可能是风控或网络问题，稍后重试；需要登录的内容请手动下载音频后用 --no-dl。")


def video_info(bv: str) -> dict:
    d = api(f"/x/web-interface/view?bvid={bv}").get("data")
    if not d:
        raise SystemExit(f"查不到 {bv} 的信息：可能已删除、是番剧/付费内容，或需要登录。")
    return d


# ---------------------------------------------------------------- 小工具
def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()[:80]


def ts(sec: float, sep: str = ",") -> str:
    """SRT 时间码 00:00:00,000"""
    h, rem = divmod(max(sec, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", sep)


def fmt(sec: float) -> str:
    """22:07 或 1:22:07"""
    s = int(sec)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m:02d}:{s % 60:02d}"


def link(bv: str, page: int | None = None, t: float | None = None) -> str:
    q = []
    if page:
        q.append(f"p={page}")
    if t is not None:
        q.append(f"t={int(t)}")
    return f"https://www.bilibili.com/video/{bv}" + ("?" + "&".join(q) if q else "")


# ---------------------------------------------------------------- GPU
def enable_cuda() -> list[str]:
    """把 pip 装的 nvidia-*-cu12 的 bin 塞进 DLL 搜索路径。必须在 import ctranslate2 之前调用。"""
    site = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    dirs = glob.glob(str(site / "*" / "bin"))
    if not dirs:
        return []
    os.environ["PATH"] = ";".join(dirs) + ";" + os.environ.get("PATH", "")
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError):
            pass
    return dirs


# ---------------------------------------------------------------- 音频
def decode(path: str | Path, limit: float | None = None):
    """解码成 16kHz 单声道 float32（绕过 faster-whisper 的 av 版本不兼容）。"""
    import av
    import numpy as np

    c = av.open(str(path))
    st = c.streams.audio[0]
    res = av.AudioResampler(format="s16", layout="mono", rate=16000)
    buf = []
    for frame in c.decode(st):
        for f in res.resample(frame):
            buf.append(f.to_ndarray().reshape(-1))
    if not buf:
        raise RuntimeError(f"{path} 里没有可解码的音频帧")
    a = np.concatenate(buf).astype(np.float32) / 32768.0
    if limit:
        a = a[: int(limit * 16000)]
    return a


def to_simplified():
    import opencc
    return opencc.OpenCC("t2s")


# ---------------------------------------------------------------- 产物读取
def segments_path(bv: str, fixed: bool = True) -> Path:
    """纠错版比原版新才用；否则回落到 segments.json（重转过某几个分P 时会出现这种不一致）。"""
    d = OUT / bv
    raw, fix = d / "segments.json", d / "segments_fixed.json"
    if fixed and fix.exists():
        return fix if (not raw.exists() or fix.stat().st_mtime >= raw.stat().st_mtime) else raw
    return raw


def load_segments(bv: str, fixed: bool = True) -> dict:
    p = segments_path(bv, fixed)
    if not p.exists():
        raise SystemExit(f"{p} 不存在：请先跑 transcribe（python cli.py all {bv}）")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    """先写 .tmp 再替换：中途异常不会把已有产物写成半截。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def seg_text(s: dict) -> str:
    return s.get("text_fixed", s["text"])


def domain_of(bv: str) -> list[str]:
    p = OUT / bv / "domain.json"
    if not p.exists():
        return []
    return [d["domain"] for d in json.load(open(p, encoding="utf-8"))["top3"]]


def read_text(path, enc: str = "utf-8") -> str:
    """读文本文件；编码显式给出，避免 Windows 默认 GBK 带来的乱码。"""
    with open(path, encoding=enc) as f:
        return f.read()


def read_lines(path, enc: str = "utf-8") -> list[str]:
    with open(path, encoding=enc) as f:
        return f.readlines()
