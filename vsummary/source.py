"""把"用户随手贴的一串东西"变成 BV 号。

支持这些写法（大小写、空格、尾随参数都无所谓）：

  BV1E9U8BBEvZ
  bv1E9U8BBEvZ
  av114261397346227
  https://www.bilibili.com/video/BV1E9U8BBEvZ
  https://www.bilibili.com/video/BV1E9U8BBEvZ/?p=3&spm_id_from=...
  https://m.bilibili.com/video/BV1E9U8BBEvZ
  https://b23.tv/AbCdEf          ← 短链，会跟一次跳转
  【标题】 https://b23.tv/AbCdEf  ← 从 App 复制出来常带标题
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

from .core import UA, api

BV_RE = re.compile(r"(?i)\bBV([0-9A-Za-z]{10})\b")
AV_RE = re.compile(r"(?i)\bav(\d{1,15})\b")
P_RE = re.compile(r"[?&]p=(\d{1,4})")
SHORT = re.compile(r"(?i)https?://(?:b23\.tv|bili2233\.cn)/\S+")


def _follow(url: str) -> str:
    """b23.tv 短链 → 真实地址。只跟一次跳转，失败就原样返回。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.geturl() or url
    except (urllib.error.URLError, OSError, ValueError):
        return url


def parse(text: str) -> tuple[str, int | None]:
    """返回 (bvid, page)。page 只在链接里带 ?p=N 时有值。"""
    s = (text or "").strip()
    if not s:
        raise SystemExit("没给视频地址。示例：python cli.py all https://www.bilibili.com/video/BV1E9U8BBEvZ")

    m = SHORT.search(s)
    if m:
        s = _follow(m.group(0))

    page = None
    pm = P_RE.search(s)
    if pm:
        page = int(pm.group(1))

    bv = BV_RE.search(s)
    if bv:
        return f"BV{bv.group(1)}", page

    av = AV_RE.search(s)
    if av:
        data = api(f"/x/web-interface/view?aid={av.group(1)}").get("data") or {}
        if data.get("bvid"):
            return data["bvid"], page
        raise SystemExit(f"av 号 {av.group(1)} 查不到视频（可能已删除或需要登录）")

    if "bilibili.com" in s or "b23.tv" in s:
        raise SystemExit(f"这个链接里没找到 BV 号，认不出：{s[:80]}\n"
                         f"  支持 /video/BVxxx 形式；合集/个人空间链接请先打开某一集再复制地址")
    raise SystemExit(f"看不懂的输入：{s[:80]}\n"
                     f"  可以给 BV 号、av 号，或 bilibili.com / b23.tv 的链接")


def is_video_ref(tok: str) -> bool:
    """这个 token 看起来是"视频引用"吗（用于自动改写命令行参数）。"""
    if not tok or tok.startswith("-"):
        return False
    return bool(BV_RE.search(tok) or AV_RE.search(tok)
                or "bilibili.com" in tok or "b23.tv" in tok or "bili2233.cn" in tok)


def normalize_argv(argv: list[str]) -> list[str]:
    """把命令行里所有"视频引用"就地换成 BV 号，其它参数原样保留。

    逗号分隔的多集（`--bvs BV1,BV2`）和 `--bvs` 后面的值不动——
    那是列表，不是单个视频引用。
    """
    out, page = [], None
    keep_next = False
    for tok in argv:
        if keep_next:                     # --bvs 的取值
            out.append(tok)
            keep_next = False
            continue
        if tok in ("--bvs", "--series"):
            out.append(tok)
            keep_next = True
            continue
        if "," in tok:                    # 逗号列表，逐个解析但保留结构
            pats = [t.strip() for t in tok.split(",")]
            if all(is_video_ref(p) or not p for p in pats):
                out.append(",".join(parse(p)[0] if p else "" for p in pats))
                continue
        if is_video_ref(tok):
            bv, p = parse(tok)
            out.append(bv)
            page = page or p
        else:
            out.append(tok)
    if page:                                  # 只做第 p 集：转成 --parts p
        out = _scope_to_parts(out, page)
    return out


def _scope_to_parts(argv: list[str], page: int) -> list[str]:
    """链接里带 ?p=3 时只处理第 3 个分P：
    transcribe 用 --parts；all 把它透传给 transcribe 阶段；其它命令读已有结果，不必处理。"""
    if "--parts" in argv:
        return argv
    if argv and argv[0] in ("transcribe", "all"):
        return argv + ["--parts", str(page)]
    return argv


def meta(bv: str) -> dict:
    """视频元信息，统一成这套字段（缺的给空值，不抛异常）。"""
    data = api(f"/x/web-interface/view?bvid={bv}").get("data")
    if not data:
        raise SystemExit(f"查不到 {bv}（可能已删除、或需要登录）")
    pages = data.get("pages") or []
    parts = [{"page": p.get("page", i + 1),
              "part": (p.get("part") or "").strip() or f"P{p.get('page', i + 1)}",
              "duration": float(p.get("duration") or 0)}
             for i, p in enumerate(pages)]
    if not parts:                             # 单集视频：pages 为空时兜底
        parts = [{"page": 1, "part": (data.get("title") or bv)[:60],
                  "duration": float(data.get("duration") or 0)}]
    return {
        "bv": bv,
        "title": (data.get("title") or "").strip(),
        "desc": (data.get("desc") or "").strip(),
        "uploader": ((data.get("owner") or {}).get("name") or "").strip(),
        "duration": float(data.get("duration") or sum(p["duration"] for p in parts)),
        "pubdate": data.get("pubdate") or 0,
        "tname": (data.get("tname") or "").strip(),
        "pic": (data.get("pic") or "").strip(),
        "parts": parts,
        "pages": len(parts),
        "is_single": len(parts) == 1,
    }


def canonical_url(bv: str, page: int | None = None) -> str:
    q = f"?p={page}" if page else ""
    return f"https://www.bilibili.com/video/{bv}{q}"


def quote(s: str) -> str:
    return urllib.parse.quote(s)
