# -*- coding: utf-8 -*-
"""test_fixes.py · 核心链路 Bug 回归测试"""
from __future__ import annotations

import base64
import gc
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import warnings
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import body as B
import config_server
import lingshu
import llm
import main as M
import model_server
import providers
import start_live
from perceive import one
from scripts import ScriptBook
from speak import speak
from store import MdStore


def test_config_server_preserves_key_ref(tmp_path, monkeypatch):
    """保存服务商参数后应保留原有 key_ref，避免密钥失效。"""
    conf_dir = tmp_path / "config"
    monkeypatch.setattr(providers, "CONF_DIR", conf_dir)
    monkeypatch.setattr(providers, "SERVICES", conf_dir / "services.json")
    monkeypatch.setattr(providers, "SECRETS", conf_dir / "secrets.json")
    reg = providers.Registry()
    reg.set_secret("deepseek_api_key", "sk-initial")
    monkeypatch.setattr(config_server, "P", reg)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), config_server.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        payload = json.dumps({
            "cap": "llm",
            "name": "deepseek",
            "params": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "temperature": 0.7,
                "max_tokens": 120,
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/params",
            data=payload,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read().decode("utf-8"))["ok"] is True

        # key_ref 应保留，原有密钥仍可读到
        assert reg.get("llm", "deepseek")["params"].get("api_key") == "sk-initial"

        # 紧接着通过 /api/secret 更新密钥也应成功
        sec_payload = json.dumps({
            "cap": "llm",
            "name": "deepseek",
            "value": "sk-updated",
        }).encode("utf-8")
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/secret",
            data=sec_payload,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            assert json.loads(resp2.read().decode("utf-8"))["ok"] is True
        assert reg.get("llm", "deepseek")["params"].get("api_key") == "sk-updated"
    finally:
        srv.shutdown()
        srv.server_close()


def test_speak_trims_llm_before_verdict(monkeypatch):
    """超长 LLM 回复应先剥离标记并截断，再过 verdict 长度检查。"""
    long_reply = (
        "（微笑）主人好呀！今天怎么这么早来直播间呀？"
        "其实我准备了好多关于长期记忆和自我认知的问题想和主人一起探讨，"
        "如果把一个人过去五年的记忆全部擦掉，他还算不算同一个人呢？"
        "而且如果记忆是可以随时备份恢复的，那今天和明天的我又有什么区别呢？"
    )
    assert len(long_reply) > 90
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: long_reply)

    r = speak(ScriptBook(), "你好呀", actor_name="小花", use_llm=True)
    assert r.source == "llm"
    assert len(r.text) <= 90
    assert "（微笑）" not in r.text


def test_speak_rejects_ai_prefix_after_stripping_marks(monkeypatch):
    """开头带动作标记的 AI 自述口吻也应被 verdict 拦截。"""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "chat",
        lambda *a, **kw: "（微笑）作为一个人工智能，我无法回答这个问题。",
    )
    r = speak(ScriptBook(), "你好呀", actor_name="小花", use_llm=True)
    assert r.source == "template"


def test_main_handle_echo_with_voice_and_memory_without_voice(tmp_path, monkeypatch):
    """echo 模式配 body 不应 UnboundLocalError；无 body 时也应写入灵枢记忆。"""
    store = MdStore(tmp_path)
    recorded = []
    monkeypatch.setattr(lingshu, "enqueue", lambda *a, **kw: recorded.append((a, kw)))

    class DummyBody:
        def speak(self, text, acts, expr=None, motion=None):
            return {"acted": True, "spoke": True}

    # 1. echo + body 不崩溃
    M.handle(store, one("你好", actor_id="u1", actor_name="小花"), M.echo_reply, DummyBody())
    assert len(recorded) == 1

    # 2. 不开 body 也应写灵枢记忆（非 idle）
    M.handle(store, one("再聊一句", actor_id="u1", actor_name="小花"), M.echo_reply, None)
    assert len(recorded) == 2

    # 3. idle 不写灵枢记忆
    M.handle(store, one("", kind="idle"), M.rule_reply, None)
    assert len(recorded) == 2


def test_lingshu_flush_waits_for_inflight_write(monkeypatch):
    """flush() 必须等待最后一条正在执行中的写入落盘后再返回。"""
    done = []

    def slow_write(who, said, reply, **kw):
        time.sleep(0.45)
        done.append((who, said, reply))
        lingshu.LS.written += 1
        return "live_test"

    monkeypatch.setattr(lingshu.LS, "remember_exchange", slow_write)
    lingshu.enqueue("测试观众", "问", "答")
    lingshu.flush(timeout=5.0)
    assert len(done) == 1
    assert lingshu.pending() == 0


def test_body_cleans_up_temp_wavs(tmp_path, monkeypatch):
    """合成失败应清理空临时文件；连续合成应清理旧临时文件避免堆积。"""
    b = B.Body(ref="")
    # ref 为空安全返回 False 且不残留临时文件
    created = []
    orig_mkstemp = B.tempfile.mkstemp

    def tracking_mkstemp(*a, **kw):
        fd, path = orig_mkstemp(*a, **kw)
        created.append(path)
        return fd, path

    monkeypatch.setattr(B.tempfile, "mkstemp", tracking_mkstemp)
    assert b.say("测试失败") is False
    assert len(created) == 1 and not os.path.exists(created[0])

    # 连续成功两次：第一次的临时 wav 应被清理，只保留最新一条
    def fake_synth(text, out_path, **kw):
        Path(out_path).write_bytes(b"RIFF" + b"\x00" * 1024)
        return True

    monkeypatch.setattr(B, "synth_tts", fake_synth)
    monkeypatch.setattr(B, "play_wav", lambda p: True)
    monkeypatch.setattr(b, "_push_mouth", lambda p, ts: None)
    monkeypatch.setattr(b, "_get", lambda p, timeout=1.5: True)

    assert b.say("第一句") is True
    first_wav = b.last_wav
    assert first_wav and os.path.exists(first_wav)

    assert b.say("第二句") is True
    second_wav = b.last_wav
    assert second_wav and os.path.exists(second_wav)
    assert not os.path.exists(first_wav)
    try:
        os.unlink(second_wav)
    except OSError:
        pass


def test_bilibili_sc_uses_uid_as_actor_id():
    """SUPER_CHAT_MESSAGE 的 actor_id 应为 uid 而非 uname。"""
    pytest.importorskip("requests")
    from bilibili import BilibiliDanmaku

    dm = BilibiliDanmaku(123)
    payload = json.dumps({
        "cmd": "SUPER_CHAT_MESSAGE",
        "data": {
            "uid": 987654,
            "message": "加油",
            "user_info": {"uname": "热心观众"},
        },
    }).encode("utf-8")
    evs = list(dm._parse(payload))
    assert len(evs) == 1
    assert evs[0].actor_id == "987654"
    assert evs[0].actor_name == "热心观众"


def test_start_live_prefers_repo_root_config(tmp_path, monkeypatch):
    """start_live._find_config() 应优先读取仓根 config.local.json。"""
    repo = tmp_path
    comp = repo / "companion"
    comp.mkdir()
    root_cfg = repo / "config.local.json"
    comp_cfg = comp / "config.local.json"
    root_cfg.write_text('{"bilibili": {"room_id": 100}}', encoding="utf-8")
    comp_cfg.write_text('{"bilibili": {"room_id": 200}}', encoding="utf-8")

    monkeypatch.setattr(start_live, "REPO", repo)
    monkeypatch.setattr(start_live, "ROOT", comp)
    assert start_live._find_config() == root_cfg

    root_cfg.unlink()
    assert start_live._find_config() == comp_cfg


def test_model_server_stage_from_env(tmp_path, monkeypatch):
    """model_server 应支持通过 LIVE2D_MODEL_DIR 读取表情文件。"""
    exp_dir = tmp_path / "Exp3"
    exp_dir.mkdir()
    (exp_dir / "脸红.exp3.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LIVE2D_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(model_server, "STAGE", model_server._stage_dir())
    assert model_server._expressions() == ["脸红"]


def test_inline_scripts_js_syntax(tmp_path):
    """model_server.PAGE 与 config_server.PAGE 中的内联 JS 应无语法错误。"""
    assert r"'\n'" in model_server.PAGE
    assert r"/^\d{4}-/" in config_server.PAGE

    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 node --check 校验")

    for name, page in (("model_server", model_server.PAGE), ("config_server", config_server.PAGE)):
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.S)
        assert scripts, f"{name}.PAGE 未找到 <script> 块"
        for idx, code in enumerate(scripts):
            js_file = tmp_path / f"{name}_{idx}.js"
            js_file.write_text(code, encoding="utf-8")
            proc = subprocess.run([node, "--check", str(js_file)], capture_output=True, text=True)
            assert proc.returncode == 0, f"{name} script #{idx} JS 语法错误:\n{proc.stderr}"


def test_no_syntax_warnings_on_compile():
    """config_server.py 与 model_server.py 编译不应产生 SyntaxWarning。"""
    comp_dir = Path(config_server.__file__).resolve().parent
    for fname in ("config_server.py", "model_server.py"):
        p = comp_dir / fname
        src = p.read_text(encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", SyntaxWarning)
            compile(src, str(p), "exec")
        syntax_warns = [w for w in caught if issubclass(w.category, SyntaxWarning)]
        assert not syntax_warns, f"{fname} 产生 SyntaxWarning: {[str(w.message) for w in syntax_warns]}"


def test_model_server_error_json_and_param_validation(monkeypatch):
    """model_server._get() 应输出合法 JSON；/api/pose-params 与 /api/mouth 应阻断参数注入。"""
    def boom(*a, **kw):
        raise RuntimeError('bad "quote" \\ path\nnext')

    with monkeypatch.context() as mp:
        mp.setattr(model_server.urllib.request, "urlopen", boom)
        err_body = model_server._get("http://127.0.0.1:8789/set?name=x")
    parsed = json.loads(err_body)
    assert "bad \"quote\"" in parsed["error"]

    forwarded: list[str] = []
    monkeypatch.setattr(model_server, "_get", lambda url, timeout=3.0: (forwarded.append(url), '{"ok":true}')[1])

    srv = ThreadingHTTPServer(("127.0.0.1", 0), model_server.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # 正常请求应通过
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/pose-params?p=%7B%7D&ms=4500", timeout=5) as r:
            assert r.status == 200
        assert forwarded[-1].endswith("/pose?params=%7B%7D&ms=4500")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mouth?value=60", timeout=5) as r:
            assert r.status == 200
        assert forwarded[-1].endswith("/mouth?value=60")

        # 越界数值应被钳位到 [0, 100]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mouth?value=150", timeout=5) as r:
            assert r.status == 200
        assert forwarded[-1].endswith("/mouth?value=100")

        # 查询参数注入与非法值应返回 400 且不转发到桥
        before = len(forwarded)
        for bad_path in (
            "/api/pose-params?p=%7B%7D&ms=3000%26evil%3D1",
            "/api/pose-params?p=%7B%7D&ms=nan",
            "/api/mouth?value=50%26evil%3D1",
            "/api/mouth?value=inf",
        ):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{bad_path}", timeout=5)
            try:
                assert exc.value.code == 400
            finally:
                exc.value.close()
        assert len(forwarded) == before
    finally:
        srv.shutdown()
        srv.server_close()


def test_bilibili_short_room_id_cache_hit(tmp_path, monkeypatch):
    """短房间号解析为真实长房间号后，再次启动使用短号/长号均应命中 token 缓存。"""
    pytest.importorskip("requests")
    import bilibili

    monkeypatch.setattr(bilibili, "CACHE_DIR", tmp_path)

    class FakeResp:
        def __init__(self, payload: dict):
            self._payload = payload

        def json(self):
            return self._payload

    calls = {"danmu_info": 0}

    def fake_get_first(url, **kw):
        if "finger/spi" in url:
            return FakeResp({"code": 0, "data": {"b_3": "buvid3-xyz"}})
        if "web-interface/nav" in url:
            return FakeResp({
                "code": 0,
                "data": {
                    "mid": 10001,
                    "wbi_img": {
                        "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                        "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                    },
                },
            })
        if "Room/get_info" in url:
            return FakeResp({"code": 0, "data": {"room_id": 23058, "title": "测试直播间", "live_status": 1}})
        if "getDanmuInfo" in url:
            calls["danmu_info"] += 1
            return FakeResp({
                "code": 0,
                "data": {
                    "token": "tok_short_room",
                    "host_list": [{"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}],
                },
            })
        raise AssertionError(f"unexpected url: {url}")

    dm1 = bilibili.BilibiliDanmaku(6)
    monkeypatch.setattr(dm1.session, "get", fake_get_first)
    assert dm1.bootstrap() == 23058
    assert calls["danmu_info"] == 1

    def fake_get_cached(url, **kw):
        if "getDanmuInfo" in url:
            raise AssertionError("命中缓存时不应再请求 getDanmuInfo")
        if "web-interface/nav" in url:
            return FakeResp({"code": 0, "data": {"mid": 10001}})
        if "Room/get_info" in url:
            return FakeResp({"code": 0, "data": {"room_id": 23058, "title": "测试直播间", "live_status": 1}})
        raise AssertionError(f"unexpected url: {url}")

    # 再次使用短号 6 应命中缓存
    dm2 = bilibili.BilibiliDanmaku(6)
    monkeypatch.setattr(dm2.session, "get", fake_get_cached)
    assert dm2.bootstrap() == 23058
    assert dm2.token == "tok_short_room"

    # 使用解析后的长号 23058 也应命中缓存
    dm3 = bilibili.BilibiliDanmaku(23058)
    monkeypatch.setattr(dm3.session, "get", fake_get_cached)
    assert dm3.bootstrap() == 23058
    assert dm3.token == "tok_short_room"

    # 其他房间号不应命中
    assert bilibili._load_cache(99999) is None


def test_body_ref_b64_and_mouth_sync(tmp_path, monkeypatch):
    """_ref_b64 不泄漏文件句柄；say() 播前预计算包络；_push_mouth() 跳过滞后旧帧并保留尾帧。"""
    # 1. _ref_b64 正常编码且不产生 ResourceWarning
    ref_file = tmp_path / "ref.wav"
    ref_file.write_bytes(b"hello-voice")
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        encoded = B._ref_b64(str(ref_file))
        gc.collect()
    assert encoded == base64.b64encode(b"hello-voice").decode()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]
    B._REF_CACHE.pop(str(ref_file), None)

    # 2. say() 应在启动播放线程前预计算 _envelope 并传给 _push_mouth
    b = B.Body(ref="")
    order: list[str] = []
    pushed_arg: list = []
    expected_env = [(0.0, 0.5), (0.025, 0.8)]

    def fake_synth(text, out_path, **kw):
        Path(out_path).write_bytes(b"RIFF" + b"\x00" * 1024)
        return True

    def fake_envelope(path, frame_ms=25):
        order.append("envelope")
        return list(expected_env)

    def fake_play(path):
        order.append("play")
        return True

    def fake_push(env_or_path, ts):
        order.append("push")
        pushed_arg.append(env_or_path)

    monkeypatch.setattr(B, "synth_tts", fake_synth)
    monkeypatch.setattr(b, "_envelope", fake_envelope)
    monkeypatch.setattr(B, "play_wav", fake_play)
    monkeypatch.setattr(b, "_push_mouth", fake_push)
    monkeypatch.setattr(b, "_get", lambda p, timeout=1.5: True)

    assert b.say("测试顺序") is True
    assert order.index("envelope") < order.index("play")
    assert pushed_arg == [expected_env]
    if b.last_wav and os.path.exists(b.last_wav):
        os.unlink(b.last_wav)

    # 3. _push_mouth() 应跳过 delay < -0.05 的旧帧，但始终发送最后一帧
    b2 = B.Body(ref="")
    sent: list[str] = []
    env = [(0.0, 0.2), (0.025, 0.4), (0.05, 0.6), (0.075, 0.8), (0.10, 0.1)]
    fake_now = iter([100.08, 100.08, 100.08, 100.08, 100.50])
    monkeypatch.setattr(B.time, "perf_counter", lambda: next(fake_now))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(b2, "_get", lambda p, timeout=1.5: (sent.append(p), True)[1])

    b2._push_mouth(env, 100.0)
    assert sent == ["/mouth?value=60", "/mouth?value=80", "/mouth?value=10"]

    # 4. 兼容直接传 wav 路径给 _push_mouth()
    wav_file = tmp_path / "sample.wav"
    with wave.open(str(wav_file), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x20" * 400)  # 50ms -> 2 frames
    sent_wav: list[str] = []
    monkeypatch.setattr(B.time, "perf_counter", lambda: 200.0)
    monkeypatch.setattr(b2, "_get", lambda p, timeout=1.5: (sent_wav.append(p), True)[1])
    b2._push_mouth(str(wav_file), 200.0)
    assert len(sent_wav) == 2
