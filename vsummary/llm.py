"""OpenAI 兼容接口的最小客户端（只用标准库，失败自动重试）。

任何兼容 `/chat/completions` 的服务都能用：DeepSeek、Moonshot、通义、vLLM、
ollama（`--api-base http://127.0.0.1:11434/v1`）、llama.cpp server……

配置优先级：构造参数 > 环境变量 > 默认值
  SUMMARY_API_BASE / OPENAI_BASE_URL
  SUMMARY_MODEL    / OPENAI_MODEL
  OPENAI_API_KEY   / SUMMARY_API_KEY
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class RetryableError(LLMError):
    """这次失败值得重试（空响应、非 JSON、连接抖动）。"""


class LLM:
    def __init__(self, base: str | None = None, key: str | None = None,
                 model: str | None = None, temperature: float = 0.2,
                 timeout: int = 300, retries: int = 3):
        self.base = (base or os.environ.get("SUMMARY_API_BASE")
                     or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.key = key or os.environ.get("OPENAI_API_KEY") or os.environ.get("SUMMARY_API_KEY") or ""
        self.model = (model or os.environ.get("SUMMARY_MODEL")
                      or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL)
        self.temperature = temperature
        self.timeout = timeout
        self.retries = max(1, retries)
        self.calls = 0
        self.in_tokens = 0
        self.out_tokens = 0

    # ------------------------------------------------------------ 基础信息
    @property
    def ready(self) -> bool:
        return bool(self.key)

    def describe(self) -> str:
        return f"{self.model} @ {self.base}"

    # ------------------------------------------------------------ 调用
    def chat(self, prompt: str, system: str = "", temperature: float | None = None,
             max_tokens: int | None = None) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": msgs,
                   "temperature": self.temperature if temperature is None else temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last = ""
        for attempt in range(1, self.retries + 1):
            try:
                return self._post(body)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last = f"HTTP {e.code}: {detail}"
                if e.code not in RETRY_STATUS:
                    raise LLMError(f"{self.describe()} → {last}") from e
            except RetryableError as e:
                last = str(e)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                wait = min(2 ** attempt, 20)
                print(f"[llm] 第 {attempt} 次失败（{last}），{wait}s 后重试", flush=True)
                time.sleep(wait)
        raise LLMError(f"{self.describe()} → {last}")

    def _post(self, body: bytes) -> str:
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = r.read()
        try:
            data = json.loads(raw)
        except ValueError as e:      # 200 但返回 HTML（网关/代理错误页）也会走到这里
            raise RetryableError(f"返回的不是 JSON：{raw[:200]!r}") from e
        self.calls += 1
        usage = data.get("usage") or {}
        self.in_tokens += usage.get("prompt_tokens", 0) or 0
        self.out_tokens += usage.get("completion_tokens", 0) or 0
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"接口返回结构不认识：{str(data)[:300]}") from e
        text = (text or "").strip()
        if not text:
            raise RetryableError("接口返回了空内容")
        return text

    # ------------------------------------------------------------ 统计
    def stats(self) -> str:
        if not self.calls:
            return "未调用"
        tok = f"，约 {self.in_tokens + self.out_tokens:,} tokens" if self.in_tokens else ""
        return f"{self.calls} 次调用{tok}"


def from_args(args) -> LLM:
    """从 argparse 的通用三件套构造客户端。"""
    return LLM(base=getattr(args, "api_base", None),
               key=getattr(args, "api_key", None),
               model=getattr(args, "model", None),
               temperature=getattr(args, "temperature", 0.2))
