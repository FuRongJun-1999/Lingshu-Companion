# -*- coding: utf-8 -*-
"""asr.py · 语音输入（录音 → 自动断句 → 转文字）

# 功能名：语音输入
# 生效条件：sounddevice 已装；麦克风可用；faster-whisper 已装
# 子功能：
#   - record(): 按设备原生采样率录音，再重采样到 16k；动态阈值判人声，停 1.2 秒自动结束
#   - 归一化：本机麦克风增益极低（说话峰值仅 0.01），必须拉幅，否则 whisper 识别不出
#   - transcribe(): faster-whisper + hotwords + initial_prompt，专名靠词表纠正
# 执行：python asr.py [设备号]
# 验证方式：说一句话，看到文字输出；改 config/hotwords.txt 能纠正同音词
# 不适用条件：
#   - 不做流式识别（说完才转），刻意如此：用户要先校对文字再发
#   - 首次运行会下载模型
"""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import numpy as np

TARGET_SR = 16000          # whisper 要 16k
CHUNK = 0.05               # 每块 50ms

#: 配置目录。优先仓根 config/，其次模块同级 config/（兼容单文件使用）
def _conf_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "config", here / "config"):
        if cand.is_dir():
            return cand
    return here.parent / "config"


CONF_DIR = _conf_dir()
HOTWORDS_FILE = CONF_DIR / "hotwords.txt"

#: 语境提示。让模型一开始就知道在听什么领域，减少同音误判。
INITIAL_PROMPT = (
    "以下是灵枢（鲸鱼娘）直播间的对话。她是 AI 虚拟主播，"
    "称呼观众为「主人」，说话带苏格拉底式反问。")


def hotwords() -> str:
    """读热词表。热词是给 whisper 的提示，能在候选里加权。

    「灵枢 / 您叔」这种同音词，光靠声学无法分辨，只能靠词表纠正。
    """
    fallback = "灵枢 鲸鱼娘 她 示例模型 弹幕 直播 主人"
    try:
        if HOTWORDS_FILE.exists():
            words: list[str] = []
            for line in HOTWORDS_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                words.extend(line.split())
            if words:
                return " ".join(dict.fromkeys(words))
    except Exception:
        pass
    return fallback


def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """线性插值重采样。够 whisper 用，且不引入 scipy 依赖。"""
    if src == dst or len(x) == 0:
        return x
    n_dst = int(round(len(x) * dst / src))
    if n_dst <= 1:
        return x
    xi = np.linspace(0, len(x) - 1, n_dst)
    return np.interp(xi, np.arange(len(x)), x).astype(np.float32)


ALIASES_FILE = CONF_DIR / "aliases.json"


def correct(text: str) -> str:
    # 同音词纠错。这是表音语义的兜底：
    # 「灵枢 / 林叔 / 凌叔 / 您叔」声学上无法区分，只能靠别名表替换。
    # hotwords 在 faster-whisper 上对中文专名几乎无效（实测），
    # 所以最终防线是这里。
    if not text:
        return text
    try:
        import json as _json
        if ALIASES_FILE.exists():
            raw = _json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
            for right, wrongs in raw.items():
                if right.startswith("_"):
                    continue
                if isinstance(wrongs, str):
                    wrongs = [wrongs]
                for w in wrongs:
                    if w and w in text:
                        text = text.replace(w, right)
    except Exception:
        pass
    return text

class VoiceInput:
    def __init__(self, model_size: str = "small", device: int | None = None,
                 language: str = "zh", compute_type: str = "int8",
                 gain: float = 1.0, whisper_device: str = "cpu"):
        self.model_size = model_size
        self.device = device
        self.language = language
        self.compute_type = compute_type
        self.gain = gain
        # CUDA float16 在本机实测 encode 阶段崩溃，CPU int8 稳定且够快
        # （5.76 秒音频约 1.4 秒识别）。需要 GPU 时显式传 whisper_device="cuda"。
        self.whisper_device = whisper_device
        self._model = None

    def _native_sr(self) -> int:
        """取设备原生采样率。硬用 16k 打开某些设备会失败。"""
        import sounddevice as sd
        try:
            d = sd.query_devices(self.device) if self.device is not None \
                else sd.query_devices(kind="input")
            return int(d.get("default_samplerate") or 48000)
        except Exception:
            return 48000

    def record(self, max_sec: float = 30.0, silence_sec: float = 1.2,
               on_level=None) -> np.ndarray | None:
        """录到说完为止，返回 16k 单声道 float32（已归一化）。"""
        import sounddevice as sd

        sr = self._native_sr()
        q: queue.Queue = queue.Queue()
        block = int(sr * CHUNK)

        def cb(indata, frames, t, status):
            q.put(indata.copy())

        chunks: list[np.ndarray] = []
        noise_floor = 0.0
        noise_frames = 0
        started = False
        silent_run = 0.0
        t0 = time.time()

        try:
            with sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                                blocksize=block, device=self.device, callback=cb):
                while time.time() - t0 < max_sec:
                    try:
                        d = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    mono = d[:, 0] if d.ndim > 1 else d
                    if self.gain != 1.0:
                        mono = mono * self.gain
                    rms = float(np.sqrt((mono ** 2).mean()))

                    # 前 8 块（约 0.4 秒）测环境噪声。本机噪声约 0.0009，
                    # 说话峰值约 0.01，阈值 0.0015 起步即可。
                    if noise_frames < 8:
                        noise_floor = max(noise_floor, rms)
                        noise_frames += 1
                        if on_level:
                            on_level(rms, "calibrate")
                        continue
                    thresh = max(0.0015, noise_floor * 2.2)

                    if not started:
                        if rms > thresh:
                            started = True
                            chunks.append(mono)
                        if on_level:
                            on_level(rms, "wait")
                    else:
                        chunks.append(mono)
                        if rms > thresh:
                            silent_run = 0.0
                        else:
                            silent_run += CHUNK
                        if on_level:
                            on_level(rms, "speak")
                        if silent_run >= silence_sec:
                            break
        except Exception as e:
            print(f"  [录音] {type(e).__name__}: {str(e)[:100]}")
            return None

        if not chunks:
            return None
        audio = np.concatenate(chunks).astype(np.float32)
        audio = _resample(audio, sr, TARGET_SR)
        # 归一化。本机麦克风增益极低（说话峰值约 0.01），
        # whisper 对这种幅度不敏感，必须拉到 0.85 附近。
        peak = float(np.abs(audio).max())
        if peak > 1e-5:
            audio = (audio / peak * 0.85).astype(np.float32)
        return audio

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            print(f"  [asr] 加载 {self.model_size} …", flush=True)
            t0 = time.time()
            try:
                self._model = WhisperModel(self.model_size,
                                           device=self.whisper_device,
                                           compute_type=self.compute_type)
            except Exception as e:
                print(f"  [asr] {self.whisper_device} 失败，降级 cpu int8: {str(e)[:60]}", flush=True)
                self._model = WhisperModel(self.model_size, device="cpu",
                                           compute_type="int8")
            print(f"  [asr] 加载完成 {int((time.time()-t0)*1000)}ms", flush=True)
        return self._model

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) < TARGET_SR * 0.2:
            return ""
        try:
            m = self._ensure_model()
            segs, info = m.transcribe(
                audio, language=self.language, beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
                hotwords=hotwords(),
                initial_prompt=INITIAL_PROMPT)
            raw = "".join(s.text for s in segs).strip()
            return correct(raw)
        except Exception as e:
            print(f"  [asr] 转写失败 {type(e).__name__}: {str(e)[:120]}")
            return ""

    def listen(self, max_sec: float = 30.0, silence_sec: float = 1.2,
               on_level=None) -> str:
        audio = self.record(max_sec=max_sec, silence_sec=silence_sec,
                            on_level=on_level)
        if audio is None:
            return ""
        return self.transcribe(audio)


if __name__ == "__main__":
    dev = int(sys.argv[1]) if len(sys.argv) > 1 else None
    v = VoiceInput(model_size="small", device=dev)
    print(f"设备: {dev if dev is not None else '默认'}  原生SR: {v._native_sr()}")
    print(">>> 请说一句话，说完停顿 1.2 秒 <<<")
    t0 = time.time()
    text = v.listen(on_level=lambda r, s: print(f"  [{s:9s}] {r:.4f}"))
    print()
    print(f"耗时 {time.time()-t0:.1f}s")
    print(f"结果: {text!r}")
