# -*- coding: utf-8 -*-
"""persona.py · 人设与记忆组装（供 LLM 使用）

# 功能名：说话提示词组装
# 生效条件：memory.py 可读档案；llm.py 可用
# 子功能：
#   - 固定人设（鲸鱼娘·傲娇·嘴硬心软）
#   - 注入观众档案摘要（限长，避免吃满上下文）
#   - 注入当前情境（谁说的、什么类型）
# 执行：
#   from persona import build_messages
#   build_messages("你好呀", actor_id="bilibili:11770366", actor_name="枼烨")
# 验证方式：test_persona.py —— messages 含人设与档案；长度受控
# 不适用条件：
#   - 档案只取首段摘要，不注入全文
"""
from __future__ import annotations

from pathlib import Path

CONSTITUTION_PATH = Path(__file__).with_name("CONSTITUTION.md")

#: 兜底人设（宪法文件缺失时使用）
_FALLBACK = """你是「示例模型」，一条鲸鱼娘，正在 B 站直播。傲娇、嘴硬心软，短句口语，不承认自己是模型。"""


def _constitution() -> str:
    try:
        if CONSTITUTION_PATH.exists():
            s = CONSTITUTION_PATH.read_text(encoding="utf-8").strip()
            if s:
                return s
    except Exception:
        pass
    return _FALLBACK


PERSONA = _constitution()

#: 演出标记约定（与 marks.py 的词表配套）
STAGE_RULE = """## 动作与心情怎么写

在台词最前面用小括号标注，成对使用全角「（」和「）」，例如「（歪头疑惑, 微笑）主人觉得呢？」
- 一次最多两个标记，也可以不写。
- 标记里的词必须从下面的词表里选，词表外的词会被丢弃。
- 标记不会念出来，会被演出层接管。
- 拿不准就不写：宁可没有动作，也不要写错标记。"""

MEMORY_LIMIT = 220


#: 灵枢记忆注入条数。太多会挤掉人设和标记词表。
LS_RECALL_K = 4


def _memory_block(actor_id: str | None, actor_name: str | None) -> str:
    """组装记忆块。两个来源：

    1. 历史档案（43 份长期画像）—— 这个人是谁
    2. 灵枢情景层（直播实时写入）—— 你们最近聊了什么

    两者独立：新观众可能没档案但有灵枢记录，老观众反之。
    """
    parts: list[str] = []
    card = None

    # ① 历史档案
    if actor_id:
        try:
            from memory import KetiMemory
            card = KetiMemory().persona_card(actor_id)
        except Exception:
            card = None
    name = (card or {}).get("name") or actor_name or ""
    if card:
        summary = (card.get("summary") or "").strip()
        if len(summary) > MEMORY_LIMIT:
            summary = summary[:MEMORY_LIMIT] + "…"
        addr = card.get("address")
        parts.append("## 你记得这个人")
        if name:
            parts.append(f"- 名字：{name}")
        if addr:
            parts.append(f"- 你有时叫他：{addr}")
        if summary:
            parts.append(f"- 你记得：{summary}")

    # ② 灵枢情景层（最近聊过什么），按 condition_space.viewer 匹配
    try:
        from lingshu import recall_block
        recent = recall_block(name, limit=LS_RECALL_K)
        if recent:
            parts.append("")
            parts.append("## 你们最近聊过")
            parts.append(recent)
            parts.append("（真实发生过的对话，可自然提及，别机械复述）")
    except Exception:
        pass

    return chr(10).join(parts) if parts else ""


KIND_HINT = {
    "greet": "他在跟你打招呼。",
    "question": "他在问你问题。",
    "praise": "他在夸你。",
    "meme": "他在拿梗逗你。",
    "care": "他在关心你。",
    "request": "他在向你提要求。",
    "gift": "他送了礼物给你。",
    "ack": "他只是在应一声。",
    "emoji": "他只发了个表情。",
    "misc": "",
}


def _vocab_block() -> str:
    try:
        from marks import VOCAB_HINT
        return "## 可用的括号标记\n" + VOCAB_HINT
    except Exception:
        return ""


def build_messages(
    stimulus: str,
    *,
    actor_id: str | None = None,
    actor_name: str | None = None,
    pkind: str = "misc",
    recent: list[str] | None = None,
) -> list[dict]:
    sys_parts = [PERSONA, STAGE_RULE]
    vocab = _vocab_block()
    if vocab:
        sys_parts.append(vocab)
    mem = _memory_block(actor_id, actor_name)
    if mem:
        sys_parts.append(mem)
    hint = KIND_HINT.get(pkind, "")
    if hint:
        sys_parts.append("## 当前\n" + hint)

    # 将最近台词折叠进 system，而不是在 system 后连续堆叠多条 assistant 消息，
    # 避免严格校验 user/assistant 交替角色的网关直接返回 HTTP 400。
    recent_lines = [
        " ".join(str(r).splitlines()).strip()
        for r in (recent or [])
        if r and str(r).strip()
    ][-4:]
    if recent_lines:
        bullets = "\n".join(f"- {line}" for line in recent_lines)
        sys_parts.append("## 刚才说过（避免重复）\n" + bullets)

    who = actor_name or "观众"
    return [
        {"role": "system", "content": "\n\n".join(sys_parts)},
        {"role": "user", "content": f"{who}：{stimulus}"},
    ]
