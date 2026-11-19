# -*- coding: utf-8 -*-
"""perceive.py · 感知层（M1）

# 功能名：刺激源（控制台 / 单条注入）
# 生效条件：Python 3.11+
# 子功能：从 stdin 读行；单条模式；统一输出 Stimulus 字典
# 执行：
#   for ev in console_source(): ...
#   ev = one("你好", actor="bilibili:123")
# 验证方式：main.py 跑通后 MD 里出现对应 block
# 不适用条件：M1 只有控制台源；B站弹幕见 M4
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterator


@dataclass
class Stimulus:
    kind: str                 # console | danmaku | gift | idle | voice
    text: str
    actor_id: str | None = None
    actor_name: str | None = None


def one(text: str, *, actor_id: str | None = None, actor_name: str | None = None,
        kind: str = "console") -> Stimulus:
    return Stimulus(kind=kind, text=text, actor_id=actor_id, actor_name=actor_name)


def console_source(prompt: str = "> ") -> Iterator[Stimulus]:
    """从标准输入读行。输入 quit / exit / Ctrl-D 结束。"""
    while True:
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            return
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            return
        yield one(line, actor_id="console:local", actor_name="控制台")


def bilibili_source(room_id: int, sessdata: str | None = None) -> Iterator[Stimulus]:
    """B站弹幕源。进房事件(enter)不产生刺激，避免刷屏。"""
    try:
        from bilibili import BilibiliDanmaku
    except ImportError:
        return
    d = BilibiliDanmaku(room_id, sessdata=sessdata)
    for ev in d.events():
        if ev.kind == "enter":
            continue
        yield Stimulus(
            kind=ev.kind,
            text=ev.text,
            actor_id=ev.actor_id,
            actor_name=ev.actor_name,
        )
