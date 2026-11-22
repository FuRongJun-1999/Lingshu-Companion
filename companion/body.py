# -*- coding: utf-8 -*-
"""body.py · 表达层（M3）

# 功能名：身体表达（TTS 说话 + VTS 动作）
# 生效条件：TTS 服务 8010 可达；VTS 可选（8001 不可达时只说话不动）
# 子功能：
#   - say(): 文本 → VoxCPM2 合成 → 播放
#   - act(): 动作名 → VTS 参数注入（未连接则跳过）
#   - speak(): 台词 + 动作标记 → 解析 → 先动后说
# 执行：
#   b = Body(tts_url="http://127.0.0.1:8010", voice_ref="whale-voice.wav")
#   b.speak("在的，小花。", acts=["看向镜头", "微笑"])
# 验证方式：test_body.py —— 动作标记解析正确；TTS 不可达时返回 False 不抛
# 不适用条件：
#   - VTS 需手动启动并在其设置里开启 API
#   - 动作词到 VTS 参数的映射需按模型调参，当前是近似映射
#   - 播放走 winsound，仅 Windows
"""
from __future__ import annotations

import array
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets"


def _tts_cfg() -> dict:
    """取 tts 能力当前配置；注册表不可用返回空 dict。"""
    try:
        from providers import P
        return P.get("tts")
    except Exception:
        return {}


_c = _tts_cfg().get("params") or {}
DEFAULT_TTS = _c.get("url") or "http://127.0.0.1:8010"
#: 音色参考放 assets/whale-voice.wav（不进仓库，自行准备）
_local_ref = _ASSETS / (_c.get("voice") or "whale-voice.wav")
DEFAULT_REF = str(_local_ref) if _local_ref.exists() else ""
#: 兜底文本。实际优先读与 wav 同名的 .txt（克隆引擎要求逐字转写，文本不匹配会导致音色跑偏）
DEFAULT_REF_TXT = "大家好呀，我是鲸鱼娘。今天的风好舒服，像在海面上飘着一样。"


def ref_text_for(wav_path: str) -> str:
    """优先读 <同名>.txt，读不到才用兜底。"""
    try:
        txt = Path(wav_path).with_suffix(".txt")
        if txt.exists():
            s = txt.read_text(encoding="utf-8").strip()
            if s:
                return s
    except Exception:
        pass
    return DEFAULT_REF_TXT

_REF_CACHE: dict[str, str] = {}


def _ref_b64(path: str) -> str:
    import base64
    if path not in _REF_CACHE:
        _REF_CACHE[path] = base64.b64encode(open(path, "rb").read()).decode()
    return _REF_CACHE[path]


def synth_tts(text: str, out_path: str, *, url: str = DEFAULT_TTS,
              ref: str = DEFAULT_REF, ref_text: str | None = None,
              timeout: float = 120.0) -> bool:
    """调 VoxCPM2 合成 WAV。失败返回 False，不抛。"""
    if not ref or not Path(ref).is_file():
        return False
    try:
        body = json.dumps({
            "model": "voxcpm2", "voice": "default", "response_format": "wav",
            "seed": 42, "cfg_value": 2, "inference_timesteps": 10,
            "max_steps": 200, "temperature": 1,
            "input": text,
            "reference_audio": _ref_b64(ref),
            "prompt_text": ref_text if ref_text is not None else ref_text_for(ref),
        }).encode("utf-8")
        req = urllib.request.Request(url.rstrip("/") + "/v1/audio/speech",
                                     data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 1000:
            return False
        Path(out_path).write_bytes(data)
        return True
    except Exception:
        return False


def probe_tts(*, url: str = DEFAULT_TTS, ref: str = DEFAULT_REF,
              ref_text: str | None = None, timeout: float = 20.0) -> dict:
    """真实合成探针：发一句短文本，看是否真的产出音频。

    转交文档警告：/health 会假健康（推理队列死锁但端点照答）。
    所以必须实测合成，而不是查 health。
    返回 {ok, ms, bytes, error}
    """
    import time as _t
    t0 = _t.time()
    fd, path = tempfile.mkstemp(prefix="ls_probe_", suffix=".wav")
    os.close(fd)
    try:
        ok = synth_tts("嗯。", path, url=url, ref=ref, ref_text=ref_text, timeout=timeout)
        size = os.path.getsize(path) if ok and os.path.exists(path) else 0
        return {"ok": bool(ok) and size > 1000, "ms": int((_t.time() - t0) * 1000),
                "bytes": size, "error": None}
    except Exception as e:
        return {"ok": False, "ms": int((_t.time() - t0) * 1000),
                "bytes": 0, "error": str(e)[:120]}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def play_wav(path: str) -> bool:
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return True
    except Exception:
        return False


# ---- 桌宠（HTTP 桥 8789）----

BRIDGE = "http://127.0.0.1:8789"

#: 动作名 → Cubism 参数（直接注入，不依赖 VTS）
ACTS_MAP = {
    "看向镜头": {"ParamAngleX": 0, "ParamEyeBallX": 0},
    "看向弹幕": {"ParamAngleX": 6, "ParamAngleY": -4, "ParamEyeBallX": 0.3},
    "看向屏幕": {"ParamAngleX": -5},
    "歪头": {"ParamAngleZ": 8},
    "歪头疑惑": {"ParamAngleZ": 12, "ParamBrowLY": 0.5, "ParamBrowRY": 0.5},
    "点头": {"ParamAngleY": 5},
    "前倾": {"ParamBodyAngleX": 6},
    "轻轻摇摆": {"ParamBodyAngleZ": 3},
    "微笑": {"ParamMouthForm": 1.0},
    "别过脸": {"ParamAngleX": -12, "ParamEyeBallX": -0.5},
    "惊讶": {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0, "ParamBrowLY": 1.0},
    "看向斜上方": {"ParamAngleX": 5, "ParamAngleY": 8},
    "低头": {"ParamAngleY": -8},
}

#: 动作名 → 预置表情（走 /set，比参数更明显）
ACT_EXPR = {
    "微笑": "smile",
    "脸红": "shy",
    "爱心眼": "like",
    "流泪": "cry",
    "黑脸": "angry",
}


def acts_to_params(acts: list) -> dict[str, float]:
    """把姿态动作合并成参数字典。

    支持两种元素：
      - str         : ACTS_MAP 里的动作名
      - dict        : 直接就是 {参数名: 值}（marks.py 映射后的结果）
    """
    params: dict[str, float] = {}
    for a in acts or []:
        if isinstance(a, dict):
            params.update(a)
            continue
        key = str(a).strip()
        if key in ACTS_MAP:
            params.update(ACTS_MAP[key])
    return params


class Body:
    def __init__(self, *, tts_url: str = DEFAULT_TTS, ref: str = DEFAULT_REF,
                 ref_text: str | None = None, enable_vts: bool = False):
        self.tts_url = tts_url
        self.ref = ref
        self.ref_text = ref_text
        self.enable_vts = enable_vts
        self.last_wav: str | None = None
        self.last_synth_ms = 0
        self.fail_streak = 0
        # 表情驻留：同一个表情不重复发；切换后至少保持 expr_hold 秒
        self._cur_expr: str | None = None
        self._expr_hold_until = 0.0
        self.expr_hold = 2.5        # 秒，表情最短驻留时间
        self._expr_min_gap = 1.2    # 两次切换的最小间隔


    def say(self, text: str) -> bool:
        """合成 → 后台播放 → 主线程按音频时间轴推口型 → 说完闭嘴。"""
        fd, path = tempfile.mkstemp(prefix='ls_tts_', suffix='.wav')
        os.close(fd)
        t0 = time.time()
        ok = synth_tts(text, path, url=self.tts_url, ref=self.ref, ref_text=self.ref_text)
        self.last_synth_ms = int((time.time() - t0) * 1000)
        if not ok:
            self.fail_streak += 1
            try:
                os.unlink(path)
            except OSError:
                pass
            return False
        self.fail_streak = 0
        prev_wav = self.last_wav
        self.last_wav = path
        if prev_wav and prev_wav != path:
            try:
                os.unlink(prev_wav)
            except OSError:
                pass

        # 播放线程：记录真正开始出声的时刻，供包络对齐
        import threading
        started = []
        done = threading.Event()

        def _play():
            try:
                started.append(time.perf_counter())
                play_wav(path)
            finally:
                done.set()

        threading.Thread(target=_play, daemon=True).start()
        # 等播放真正开始（最多 1 秒）
        t_wait = time.perf_counter()
        while not started and time.perf_counter() - t_wait < 1.0:
            time.sleep(0.002)
        if started:
            self._push_mouth(path, started[0])
        done.wait(timeout=60)
        # 说完立刻闭嘴
        self._get('/mouth?value=0', timeout=0.5)
        return True

    def _envelope(self, wav_path: str, frame_ms: int = 25) -> list:
        """预计算口型包络：[(相对时间秒, 0-1 开合)]。

        帧长 25ms 比 40ms 细，配合桌宠侧插值才不会有台阶感。
        开方压缩让小音量也张口，静音段给 0。
        """
        import array
        import math
        import wave as _wave
        out = []
        try:
            with _wave.open(wav_path, 'rb') as w:
                rate = w.getframerate()
                ch = w.getnchannels()
                sw = w.getsampwidth()
                if sw != 2:
                    return out
                chunk = max(1, int(rate * frame_ms / 1000.0))
                idx = 0
                while True:
                    data = w.readframes(chunk)
                    if not data:
                        break
                    a = array.array('h', data)
                    if ch > 1:
                        a = a[::ch]
                    if a:
                        rms = math.sqrt(sum(x * x for x in a) / len(a))
                        v = (rms / 32768.0) ** 0.6 * 1.35
                    else:
                        v = 0.0
                    out.append((idx * frame_ms / 1000.0, max(0.0, min(1.0, v))))
                    idx += 1
        except Exception as e:
            print('  [口型] 包络失败:', str(e)[:80])
        return out

    def _push_mouth(self, wav_path: str, base_ts: float) -> None:
        """按音频时间轴推包络，保证和实际出声对齐。

        base_ts 是播放线程开始的时间戳（perf_counter）。
        每一帧都按「该帧应有的时刻」睡到点再发，误差不累积。
        """
        env = self._envelope(wav_path)
        if not env:
            return
        for rel_t, v in env:
            target = base_ts + rel_t
            now = time.perf_counter()
            delay = target - now
            if delay > 0:
                time.sleep(delay)
            self._get('/mouth?value=%d' % int(v * 100), timeout=0.3)

    def health(self) -> dict:
        """真实探针（不是 /health）。连续失败时返回 degraded。"""
        r = probe_tts(url=self.tts_url, ref=self.ref, ref_text=self.ref_text)
        r["fail_streak"] = self.fail_streak
        r["degraded"] = self.fail_streak >= 3 or not r["ok"]
        return r

    def act(self, acts: list[str], *, expr: list[str] | None = None,
            motion: list | None = None) -> bool:
        """把表情/动作/姿态发到桌宠（桥 8789）。多条可同时生效，失败静默。

        expr   : 表情名列表，走 /set（最直观）
        motion : [{"group":..., "index":...}]，走 /motion（道具动画）
        acts   : 姿态词，走 /pose（参数注入）
        """
        import json as _json
        import urllib.parse as _up

        ok = False
        now = time.time()
        # 一条台词只对应一个情绪：取第一个，忽略其余，避免同一条里连续闪切
        picked = None
        for name in (expr or []):
            name = str(name).strip()
            if name:
                picked = name
                break
        if picked and picked != self._cur_expr and now >= self._expr_hold_until:
            if self._get("/set?name=" + _up.quote(picked)):
                self._cur_expr = picked
                self._expr_hold_until = now + self.expr_hold
                ok = True
        for mo in (motion or []):
            try:
                g = mo.get("group", "Gesture") if isinstance(mo, dict) else "Gesture"
                i = int(mo.get("index", 0)) if isinstance(mo, dict) else 0
                if self._get(f"/motion?group={_up.quote(g)}&index={i}"):
                    ok = True
            except Exception:
                continue

        params = acts_to_params(acts or [])
        if params:
            q = _up.quote(_json.dumps(params))
            if self._get(f"/pose?params={q}&ms=2600"):
                ok = True
        return ok

    def _get(self, path: str, timeout: float = 1.5) -> bool:
        try:
            with urllib.request.urlopen(BRIDGE + path, timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def speak(self, text: str, acts: list[str] | None = None, *,
              expr: list[str] | None = None, motion: list | None = None) -> dict:
        """先做表情与动作，再说话。"""
        acted = self.act(acts or [], expr=expr, motion=motion)
        spoke = self.say(text) if text else False
        return {"acted": acted, "spoke": spoke, "acts": acts or [],
                "expr": expr or [], "motion": motion or []}