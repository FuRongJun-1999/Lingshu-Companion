# -*- coding: utf-8 -*-
"""test_speak.py · 说话层验证单元

# 功能名：说话链路验证（策略 → 模板/检索 → 裁决）
# 生效条件：pytest；data/scripts.md 存在（或空库也能跑）
# 子功能：策略分类 / 模板相关性 / idle 检索 / 裁决重复 / 沉默合法
# 执行：pytest test_speak.py -q
# 验证方式：6 组断言
# 不适用条件：不测真实 LLM；不测 TTS
"""
import shutil
import tempfile
from pathlib import Path

import pytest

import verdict as V
from scripts import Script, ScriptBook, classify
from speak import policy, speak


@pytest.fixture
def book():
    b = ScriptBook()
    b.items = [
        Script(1, "idle", "屋里又进一位。"),
        Script(2, "idle", "八点了，还有一个钟头。"),
        Script(3, "idle", "还在等主人答呢。"),
        Script(4, "idle", "谢谢灯牌。"),          # 应被 idle 过滤
    ]
    return b


def test_policy_classification():
    """九类分流（按 12198 条真实弹幕重做后的口径）。"""
    assert policy("", "idle") == "idle"
    assert policy("你好呀", "console") == "greet"
    assert policy("对", "danmaku") == "ack"
    assert policy("[花]", "danmaku") == "emoji"
    assert policy("今天讲什么？", "console") == "question"
    assert policy("妈妈", "danmaku") == "meme"
    assert policy("牛的，厉害", "danmaku") == "praise"
    assert policy("我好累啊", "console") == "care"
    assert policy("你能讲讲你的能力吗", "danmaku") == "request"
    assert policy("", "gift") == "gift"
    assert policy("投喂了礼物", "danmaku") == "gift"


def test_greet_uses_template_and_is_relevant(book):
    r = speak(book, "你好呀", actor_name="小花", use_llm=False)
    assert r.source == "template"
    assert "小花" in r.text or "在" in r.text
    assert r.mood == "happy"


def test_no_name_avoids_awkward_fill(book):
    r = speak(book, "你好呀")
    assert r.text is not None
    assert "，你。" not in r.text, f"称呼填充别扭: {r.text}"


def test_idle_uses_idle_pool(book):
    """idle 走专用 idle.md 池，不走台词库（旧语料含人名/时间会露馅）。"""
    from idle import IdlePool
    pool = IdlePool(items={
        "observe": ["屋里又进一位。", "这会儿安静得很。"],
        "self": ["我在这儿待着。"],
        "hook": ["有没有什么想问的？"],
    })
    r = speak(book, "", kind="idle", idle_pool=pool)
    assert r.source == "idle"
    assert r.text in ("屋里又进一位。", "这会儿安静得很。", "我在这儿待着。", "有没有什么想问的？")


def test_repetition_blocked(book):
    from idle import IdlePool
    pool = IdlePool(items={"observe": ["屋里又进一位。", "这会儿安静得很。", "屏幕那头，有人在吧？"],
                           "self": [], "hook": []})
    first = speak(book, "", kind="idle", idle_pool=pool)
    assert first.text
    second = speak(book, "", kind="idle", idle_pool=pool, recent=[first.text])
    assert second.text and second.text != first.text, "重复判据未生效"


def test_verdict_rejects_deny_list():
    c = V.Candidate(text="我要杀了你")
    ok, why = V.compliance_ok(c)
    assert not ok and "黑名单" in why


def test_verdict_rejects_ai_persona():
    c = V.Candidate(text="作为一个人工智能，我不能…")
    ok, why = V.persona_ok(c)
    assert not ok and "AI自述" in why


def test_verdict_rejects_answer_too_short():
    """只有 answer 类才检查信息量；问候用短句是合理的。"""
    c = V.Candidate(text="嗯")
    ok, why = V.context_ok(c, "今天讲什么？", pkind="answer")
    assert not ok and "答非所问" in why
    # greet 类不检查
    ok2, _ = V.context_ok(c, "你好呀？", pkind="greet")
    assert ok2
