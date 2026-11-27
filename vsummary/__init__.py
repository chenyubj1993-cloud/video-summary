"""视频讲义流水线：转写 → 行业判定 → 术语纠错 → 逐P 摘要 → 讲义。"""

import sys

__all__ = ["core", "transcribe", "classify", "fix", "harvest", "overview", "summarize", "handout"]
__version__ = "1.0.0"


def safe_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。

    Windows 控制台默认是 cp1252/cp437，打印 「→」 或中文会直接抛
    UnicodeEncodeError（GitHub 的 windows-latest runner 就是这样）。
    run.cmd 里会先 chcp 65001，但用户直接在 cmd 里敲命令时没有那一步，
    所以这里自己兜住：切不过去就算了，不能让程序因为一句日志崩掉。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


safe_stdio()

