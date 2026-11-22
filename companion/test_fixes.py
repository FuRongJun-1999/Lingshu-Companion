# -*- coding: utf-8 -*-
"""test_fixes.py · 核心链路 Bug 回归测试"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
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
