"""从 板书.md + 已有 jpg 重建 frames.json（不重新 OCR）。

背景：frames.json 在大扫除时被当成中间物删了，但 jpg 与 板书.md 都在。
重新抽帧要跑一小时 OCR，而板书.md 里本来就有每一帧的 OCR 文本与"附近在讲什么"。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vsummary.core import OUT  # noqa: E402

HEAD_P = re.compile(r"^## P(\d+)\s*$")
HEAD_T = re.compile(r"^### (\d{1,2}):(\d{2})[　 ]?(.*)$")


def parse_board(path: Path) -> dict[tuple[int, int], tuple[str, str]]:
    got: dict[tuple[int, int], tuple[str, str]] = {}
    page = None
    t = near = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if page is not None and t is not None and buf:
            got[(page, t)] = ("\n".join(buf).strip(), near or "")
        buf = []

    for line in path.read_text(encoding="utf-8").split("\n"):
        m = HEAD_P.match(line)
        if m:
            flush()
            page, t = int(m.group(1)), None
            continue
        m = HEAD_T.match(line)
        if m:
            flush()
            t = int(m.group(1)) * 60 + int(m.group(2))
            near = m.group(3).strip()
            continue
        if line.startswith("```"):
            continue
        if t is not None and line.strip():
            buf.append(line)
    flush()
    return got


def main() -> None:
    bv = sys.argv[1] if len(sys.argv) > 1 else "BV1E9U8BBEvZ"
    out = OUT / bv
    board = out / "板书.md"
    if not board.exists():
        sys.exit(f"没有 {board}")
    texts = parse_board(board)
    print(f"板书.md 解析出 {len(texts)} 帧有文字")

    frames = []
    miss = 0
    for jpg in sorted((out / "frames").glob("P*_*.jpg")):
        m = re.match(r"P(\d+)_(\d+)_(\d{2})m(\d{2})s", jpg.name)
        if not m:
            continue
        page, t = int(m.group(1)), int(m.group(3)) * 60 + int(m.group(4))
        ocr, near = texts.get((page, t), ("", ""))
        if not ocr:
            miss += 1
        frames.append({"page": page, "t": float(t), "path": str(jpg),
                       "hash": "", "ocr": ocr, "near": near})
    frames.sort(key=lambda f: (f["page"], f["t"]))
    dst = out / "frames.json"
    dst.write_text(json.dumps(frames, ensure_ascii=False, indent=1), encoding="utf-8")
    have = sum(1 for f in frames if f["ocr"])
    print(f"写出 {dst}：{len(frames)} 帧，其中 {have} 帧有 OCR 文本，{miss} 帧无文本")


if __name__ == "__main__":
    main()
