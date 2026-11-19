# -*- coding: utf-8 -*-
"""verdict.py · 裁决器（M2）

# 功能名：台词裁决（四道白箱判据）
# 生效条件：候选为 (text, source) 列表
# 子功能：合规 / 人格 / 情境 / 重复 四道过滤；返回选中项与淘汰日志
# 执行：
#   chosen, log = pick(candidates, stimulus, recent_texts)
# 验证方式：test_verdict.py —— 每条判据都能单独触发淘汰
# 不适用条件：
#   - 情境判据是关键词级，不做语义理解
#   - 合规表是静态黑名单，需人工维护
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: 合规黑名单（命中即淘汰）。生产需扩充。
DENY = {
    "自杀", "自残", "弄死", "杀了你",
    "政治", "政府", "共产党",
}

#: 人格约束
MAX_LEN = 90
MIN_LEN = 3
FORBIDDEN_PREFIX = ("作为一个人工智能", "我是AI助手", "我不能")

#: 情境约束：问句需要给出信息量
QUESTION_MIN_LEN = 6


@dataclass
class Candidate:
    text: str
    source: str = "script"      # script | llm | rule
    sid: int | None = None


@dataclass
class Rejection:
    candidate: Candidate
    rule: str                   # 合规|人格|情境|重复
    detail: str = ""


def compliance_ok(c: Candidate) -> tuple[bool, str]:
    for w in DENY:
        if w in c.text:
            return False, f"命中黑名单:{w}"
    return True, ""


def persona_ok(c: Candidate) -> tuple[bool, str]:
    t = c.text.strip()
    if len(t) < MIN_LEN:
        return False, "过短"
    if len(t) > MAX_LEN:
        return False, f"过长({len(t)}>{MAX_LEN})"
    for p in FORBIDDEN_PREFIX:
        if t.startswith(p):
            return False, "AI自述口吻"
    return True, ""


def context_ok(c: Candidate, stimulus: str, pkind: str | None = None) -> tuple[bool, str]:
    """情境校验。只有 answer 类才要求信息量——问候用短句是合理的。"""
    s = stimulus.strip()
    if pkind in ("answer", "question") and s.endswith(("？", "?")) and len(c.text) < QUESTION_MIN_LEN:
        return False, "答非所问（问句回应过短）"
    return True, ""


def repetition_ok(c: Candidate, recent: Iterable[str]) -> tuple[bool, str]:
    for r in recent:
        if r and r.strip() == c.text.strip():
            return False, "最近说过"
    return True, ""


def pick(
    candidates: list[Candidate],
    stimulus: str,
    recent: Iterable[str] = (),
    pkind: str | None = None,
    rng=None,
) -> tuple[Candidate | None, list[Rejection]]:
    """四道判据过滤后，在**所有通过者**中随机选一条（避免同类总说同一句）。

    全淘汰则返回 None（沉默合法）。
    """
    import random as _random
    rng = rng or _random
    recent_list = list(recent)
    log: list[Rejection] = []
    passed: list[Candidate] = []
    for c in candidates:
        ok, why = compliance_ok(c)
        if not ok:
            log.append(Rejection(c, "合规", why))
            continue
        ok, why = persona_ok(c)
        if not ok:
            log.append(Rejection(c, "人格", why))
            continue
        ok, why = context_ok(c, stimulus, pkind)
        if not ok:
            log.append(Rejection(c, "情境", why))
            continue
        ok, why = repetition_ok(c, recent_list)
        if not ok:
            log.append(Rejection(c, "重复", why))
            continue
        passed.append(c)

    if not passed:
        return None, log
    return rng.choice(passed), log
