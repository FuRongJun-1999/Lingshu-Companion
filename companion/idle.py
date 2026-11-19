# -*- coding: utf-8 -*-
"""idle.py · 冷场自语（M2.5）

# 功能名：冷场台词选择（环境观察 + 自处 + 引子）
# 生效条件：data/idle.md 存在
# 子功能：解析分类 / 变量替换（{time}{minute}{count}）/ 三类轮换 / 冷却避免重复
# 执行：
#   pool = load_idle("data")
#   text = pick_idle(pool, now=datetime.now(), online=12, recent=[])
# 验证方式：test_idle.py —— 变量被替换；连续调用不重复；三类都会出现
# 不适用条件：
#   - 在线人数由调用方给，无则省略该变量
#   - 不做语义理解，纯模板池
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

KINDS = ("observe", "self", "hook")


@dataclass
class IdlePool:
    items: dict[str, list[str]] = field(default_factory=lambda: {k: [] for k in KINDS})
    _recent: list[str] = field(default_factory=list)

    def total(self) -> int:
        return sum(len(v) for v in self.items.values())


def load_idle(root: str | Path) -> IdlePool:
    p = Path(root) / "idle.md"
    pool = IdlePool()
    if not p.exists():
        return pool
    current = None
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = re.match(r"^##\s+(\w+)\s*$", line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r"^-\s+\[(\w+)\]\s+(.+)$", line)
        if m and m.group(1) in pool.items:
            pool.items[m.group(1)].append(m.group(2).strip())
    return pool


def _fill(tpl: str, now: datetime, online: int | None) -> str:
    s = tpl
    s = s.replace("{time}", f"{now.hour}点" if now.minute < 30 else f"{now.hour}点半")
    s = s.replace("{minute}", str(now.minute))
    if online is not None:
        s = s.replace("{count}", str(online))
    else:
        s = re.sub(r"\{count\}", "几位", s)
    return s


def pick_idle(
    pool: IdlePool,
    *,
    now: datetime | None = None,
    online: int | None = None,
    recent: list[str] | None = None,
    rng: random.Random | None = None,
) -> str | None:
    if pool.total() == 0:
        return None
    now = now or datetime.now()
    rng = rng or random
    recent_set = set(recent or [])

    # 三类轮换：优先选最近没出现的类别
    avail_kinds = [k for k in KINDS if pool.items.get(k)]
    if not avail_kinds:
        return None
    rng.shuffle(avail_kinds)

    for kind in avail_kinds:
        cands = [t for t in pool.items[kind] if _fill(t, now, online) not in recent_set]
        if not cands:
            continue
        tpl = rng.choice(cands)
        return _fill(tpl, now, online)

    # 全都在 recent 里 → 允许重复，随机取一条
    kind = rng.choice(avail_kinds)
    return _fill(rng.choice(pool.items[kind]), now, online)
