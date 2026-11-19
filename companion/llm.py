# -*- coding: utf-8 -*-
"""llm.py · LLM 调用（服务商可切换）

# 功能名：LLM Chat 调用
# 生效条件：providers.py 注册表；llm 能力已选服务商且密钥可用（或本地 Ollama 在跑）
# 子功能：
#   - 从 providers 注册表读 base_url / model / key，支持云端 DeepSeek 与本地 Ollama
#   - 云端走 OpenAI 格式 /chat/completions；本地走 Ollama /api/chat
#   - 超时/失败返回 None，不抛（说话链路不能因此断）
#   - 旧 .env 路径保留为兜底，注册表缺失时行为不变
# 执行：
#   from llm import chat
#   chat([{"role":"system","content":"..."},{"role":"user","content":"..."}])
# 验证方式：python llm.py 打印当前服务商 + 一句探活回复
# 不适用条件：
#   - 只做单轮，无流式
#   - key 缺失时静默返回 None，调用方需自行回退到模板
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
ENV_CANDIDATES = [
        Path(__file__).resolve().parent / ".env",
]


def _cfg() -> dict:
    """取 llm 能力当前配置；注册表不可用时返回空 dict。"""
    try:
        from providers import P
        return P.get("llm")
    except Exception:
        return {}


def _load_key() -> str | None:
    k = (_cfg().get("params") or {}).get("api_key")
    if k:
        return k
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k.strip()
    for p in ENV_CANDIDATES:
        try:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY"):
                    _, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
        except Exception:
            continue
    return None


def available() -> bool:
    cfg = _cfg()
    if cfg.get("kind") == "local":
        return True          # 本地 Ollama 不需要 key
    return bool(_load_key())


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = 20.0,
    thinking: bool = False,
) -> str | None:
    """单轮补全。失败返回 None。"""
    cfg = _cfg()
    p = cfg.get("params") or {}
    kind = cfg.get("kind", "cloud")
    base = (p.get("base_url") or BASE_URL).rstrip("/")
    model = model or p.get("model") or MODEL
    temperature = temperature if temperature is not None else float(p.get("temperature", 0.8))
    max_tokens = max_tokens or int(p.get("max_tokens", 400))

    if kind == "local":
        # Ollama: /api/chat，响应在 message.content
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        url = base + "/api/chat"
        headers = {"Content-Type": "application/json"}
        pick = lambda d: ((d.get("message") or {}).get("content") or "").strip()
    else:
        key = _load_key()
        if not key:
            return None
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if not thinking:
            body["thinking"] = {"type": "disabled"}
        url = base + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        pick = lambda d: (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return pick(data) or None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:160]
        except Exception:
            pass
        print(f"  [llm] HTTP {e.code}: {detail}")
        return None
    except Exception as e:
        print(f"  [llm] {type(e).__name__}: {str(e)[:100]}")
        return None


if __name__ == "__main__":
    cfg = _cfg()
    print("当前服务商:", cfg.get("name"), f"[{cfg.get('kind')}]")
    print("  base_url:", (cfg.get("params") or {}).get("base_url"))
    print("  model   :", (cfg.get("params") or {}).get("model"))
    print("  key     :", "有" if _load_key() else "无")
    print()
    print("探活…")
    r = chat([{"role": "user", "content": "说两个字"}], max_tokens=20)
    print("  回复:", r)
