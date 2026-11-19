# -*- coding: utf-8 -*-
"""providers.py · 服务商注册表（本地/云端可切换）

# 功能名：服务商注册表
# 生效条件：config/services.json 存在；Python 3.10+
# 子功能：
#   - 五类能力：asr / tts / llm / vision / image，每类多个可选服务商
#   - current 指向当前启用的服务商；切 provider 只改这一处
#   - 密钥与配置分离：services.json 可提交，secrets.json 不进 git
#   - test(name) 做连通性探测，供网页面板显示绿/红点
# 执行：from providers import P; P.get("tts") -> dict
# 验证方式：python providers.py --list 打印全部能力与当前选择
# 不适用条件：
#   - 只做配置管理，不实现具体调用；调用方仍各自实现，但参数从这里取
#   - 缺失服务商时返回内置默认值，不抛错（保证旧代码能跑）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: 仓根目录。配置在各仓根 config/ 下，而不是模块同级。
def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here):
        if (cand / "config").is_dir():
            return cand
    return here.parent


ROOT = _repo_root()
CONF_DIR = ROOT / "config"
SERVICES = CONF_DIR / "services.json"
SECRETS = CONF_DIR / "secrets.json"

#: 默认注册表。第一次运行会写入 services.json。
#: 默认值 = 当前系统实际在用的配置，保证换过来不改行为。
DEFAULTS: dict[str, Any] = {
    "version": 1,
    "capabilities": {
        "asr": {
            "label": "语音识别",
            "current": "none",
            "options": {
                "none": {
                    "label": "未启用",
                    "kind": "none",
                    "params": {},
                    "note": "开启「语音对话」前需要先选一个识别服务",
                },
                "faster_whisper": {
                    "label": "本地 faster-whisper",
                    "kind": "local",
                    "params": {
                        "model": "large-v3",
                        "device": "cuda",
                        "compute_type": "float16",
                        "language": "zh",
                    },
                    "note": "CTranslate2 加速，中文好，吃显存约 3G",
                },
            },
        },
        "tts": {
            "label": "语音合成",
            "current": "voxcpm",
            "options": {
                "voxcpm": {
                    "label": "本地 VoxCPM2",
                    "kind": "local",
                    "params": {
                        "url": "http://127.0.0.1:8010",
                        "voice": "whale-voice.wav",
                    },
                    "note": "现有声音，别换",
                },
                "edge_tts": {
                    "label": "Edge TTS（微软，免费）",
                    "kind": "cloud",
                    "params": {"voice": "zh-CN-XiaoyiNeural", "rate": "+0%"},
                    "note": "不占显存，音色偏播音腔",
                },
            },
        },
        "llm": {
            "label": "语言模型",
            "current": "deepseek",
            "options": {
                "deepseek": {
                    "label": "DeepSeek（云端）",
                    "kind": "cloud",
                    "params": {
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-flash",
                        "thinking": "disabled",
                        "temperature": 0.8,
                        "max_tokens": 120,
                        "key_ref": "deepseek_api_key",
                    },
                    "note": "当前在用，约 1 秒一条",
                },
                "ollama": {
                    "label": "Ollama（本地）",
                    "kind": "local",
                    "params": {
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen2.5:7b",
                        "temperature": 0.8,
                        "max_tokens": 120,
                    },
                    "note": "断网可用，7B 约吃 6G 显存",
                },
            },
        },
        "vision": {
            "label": "识图",
            "current": "none",
            "options": {
                "none": {"label": "未启用", "kind": "none", "params": {}},
                "qwen_vl": {
                    "label": "Qwen-VL（云端）",
                    "kind": "cloud",
                    "params": {
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "model": "qwen-vl-plus",
                        "key_ref": "dashscope_api_key",
                    },
                    "note": "中文场景强，按量计费",
                },
                "minicpm_v": {
                    "label": "本地 MiniCPM-V",
                    "kind": "local",
                    "params": {"model": "openbmb/MiniCPM-V-2_6", "device": "cuda"},
                    "note": "吃 8G 显存，速度慢但免费",
                },
            },
        },
        "image": {
            "label": "画图",
            "current": "none",
            "options": {
                "none": {"label": "未启用", "kind": "none", "params": {}},
                "comfyui": {
                    "label": "本地 ComfyUI",
                    "kind": "local",
                    "params": {"url": "http://127.0.0.1:8188", "workflow": "default"},
                    "note": "节点式，直播可实时出图",
                },
                "dashscope_image": {
                    "label": "通义万相（云端）",
                    "kind": "cloud",
                    "params": {"model": "wanx-v1", "key_ref": "dashscope_api_key"},
                },
            },
        },
        "control": {
            "label": "键鼠控制",
            "current": "off",
            "options": {
                "off": {"label": "关闭（安全）", "kind": "none", "params": {}},
                "pynput": {
                    "label": "本地 pynput",
                    "kind": "local",
                    "params": {
                        "allow_keys": "WASD,SPACE,ESC",
                        "allow_clicks": True,
                        "max_actions_per_min": 20,
                    },
                    "note": "白名单模式，超频自动停",
                },
            },
        },
    },
}


def _ensure() -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    if not SERVICES.exists():
        SERVICES.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    if not SECRETS.exists():
        SECRETS.write_text("{}", encoding="utf-8")


class Registry:
    """服务注册表。所有读写都过这里，保证磁盘与内存一致。"""

    def __init__(self) -> None:
        _ensure()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        try:
            self._data = json.loads(SERVICES.read_text(encoding="utf-8"))
        except Exception:
            self._data = json.loads(json.dumps(DEFAULTS))
        # 补齐新增能力（老配置升级）
        caps = self._data.setdefault("capabilities", {})
        for name, spec in DEFAULTS["capabilities"].items():
            caps.setdefault(name, spec)

    def save(self) -> None:
        SERVICES.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 读 ──
    def caps(self) -> dict[str, Any]:
        return self._data.get("capabilities", {})

    def current_name(self, cap: str) -> str:
        c = self.caps().get(cap) or {}
        return c.get("current") or "none"

    def get(self, cap: str, name: str | None = None) -> dict[str, Any]:
        """取某能力的当前服务商配置（含解析后的密钥）。

        返回 {"cap", "name", "label", "kind", "params", "note"}。
        找不到时返回空 params，调用方用内置默认值兜底。
        """
        c = self.caps().get(cap) or {}
        n = name or c.get("current") or "none"
        opt = (c.get("options") or {}).get(n) or {}
        params = dict(opt.get("params") or {})
        key_ref = params.pop("key_ref", None)
        if key_ref:
            params["api_key"] = self.secret(key_ref)
        return {
            "cap": cap,
            "name": n,
            "label": opt.get("label", n),
            "kind": opt.get("kind", "none"),
            "params": params,
            "note": opt.get("note", ""),
        }

    # ── 写 ──
    def set_current(self, cap: str, name: str) -> bool:
        c = self.caps().get(cap)
        if not c or name not in (c.get("options") or {}):
            return False
        c["current"] = name
        self.save()
        return True

    def set_params(self, cap: str, name: str, params: dict) -> bool:
        c = self.caps().get(cap)
        if not c:
            return False
        opt = (c.get("options") or {}).get(name)
        if not opt:
            return False
        opt["params"] = dict(params)
        self.save()
        return True

    # ── 密钥 ──
    def secrets(self) -> dict[str, str]:
        try:
            return json.loads(SECRETS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def secret(self, ref: str) -> str:
        v = self.secrets().get(ref, "")
        if v:
            return v
        # 兜底：环境变量
        return os.environ.get(ref.upper(), "")

    def set_secret(self, ref: str, value: str) -> None:
        s = self.secrets()
        s[ref] = value
        SECRETS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 探测 ──
    def test(self, cap: str, name: str | None = None) -> dict:
        """轻量连通性探测。只做能快速判死的检查，不下载模型。"""
        cfg = self.get(cap, name or self.current_name(cap))
        kind = cfg["kind"]
        p = cfg["params"]
        t0 = time.time()
        if kind == "none":
            return {"ok": False, "ms": 0, "error": "未启用"}
        try:
            import urllib.request
            if cap == "tts":
                url = p.get("url", "").rstrip("/") + "/health"
                urllib.request.urlopen(url, timeout=3).read()
                return {"ok": True, "ms": int((time.time() - t0) * 1000)}
            if cap == "llm":
                if kind == "cloud" and not p.get("api_key"):
                    return {"ok": False, "ms": 0, "error": "缺少 api_key"}
                base = p.get("base_url", "").rstrip("/")
                if kind == "local":
                    urllib.request.urlopen(base + "/api/tags", timeout=3).read()
                else:
                    # DeepSeek 等没有 /models，用最小 chat 请求判活
                    body = json.dumps({
                        "model": p.get("model", "deepseek-flash"),
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                        "stream": False,
                    }).encode()
                    req = urllib.request.Request(
                        base + "/chat/completions", data=body,
                        headers={"Content-Type": "application/json",
                                 "Authorization": "Bearer " + p["api_key"]})
                    urllib.request.urlopen(req, timeout=15).read()
                return {"ok": True, "ms": int((time.time() - t0) * 1000)}
            if cap == "image" and p.get("url"):
                urllib.request.urlopen(p["url"].rstrip("/") + "/system_stats", timeout=3).read()
                return {"ok": True, "ms": int((time.time() - t0) * 1000)}
            if cap == "asr":
                try:
                    import faster_whisper  # noqa: F401
                    return {"ok": True, "ms": int((time.time() - t0) * 1000)}
                except ImportError:
                    return {"ok": False, "ms": 0, "error": "未安装 faster-whisper"}
            if cap == "vision":
                if kind == "cloud" and not p.get("api_key"):
                    return {"ok": False, "ms": 0, "error": "缺少 api_key"}
                return {"ok": True, "ms": 0, "error": "需实际调用才能验证"}
            if cap == "control":
                try:
                    import pynput  # noqa: F401
                    return {"ok": True, "ms": int((time.time() - t0) * 1000)}
                except ImportError:
                    return {"ok": False, "ms": 0, "error": "未安装 pynput"}
        except Exception as e:
            return {"ok": False, "ms": int((time.time() - t0) * 1000), "error": str(e)[:100]}
        return {"ok": True, "ms": int((time.time() - t0) * 1000)}


P = Registry()


if __name__ == "__main__":
    import sys
    if "--list" in sys.argv:
        for cap, spec in P.caps().items():
            cur = P.current_name(cap)
            print(f"{cap:10s} {spec.get('label',''):8s} 当前: {cur}")
            for n, o in (spec.get("options") or {}).items():
                mark = "→" if n == cur else " "
                print(f"   {mark} {n:20s} [{o.get('kind')}] {o.get('label')}")
    elif "--test" in sys.argv:
        for cap in P.caps():
            r = P.test(cap)
            flag = "ok " if r["ok"] else "FAIL"
            print(f"  {flag} {cap:10s} {P.current_name(cap):20s} {r.get('ms',0):>5}ms {r.get('error','')}")
    else:
        print("用法: python providers.py --list | --test")
