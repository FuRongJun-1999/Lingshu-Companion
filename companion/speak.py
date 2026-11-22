# -*- coding: utf-8 -*-
"""speak.py · 说话层（M2.6 · 按 12198 条真实弹幕重做分类）

# 功能名：从刺激到台词的决策链（九类策略 + 模板 + 检索 + 裁决）
# 生效条件：scripts.py / verdict.py / idle.py 可用
# 子功能：policy() 九类分流；模板层保证相关性；idle 走 idle.md；四道裁决过滤
# 执行：speak(book, stimulus, kind="danmaku", actor_name="小花", idle_pool=pool)
# 验证方式：test_speak.py + 离线回归（12198 条真实弹幕分类分布）
# 不适用条件：
#   - 分类靠关键词，同义不同词会漏
#   - request 类只能接住或推掉，无法真的执行请求
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

import verdict as V
from marks import parse as parse_marks, split_by_kind
from idle import IdlePool, load_idle, pick_idle
from scripts import ScriptBook

TEMPLATES: dict[str, list[str]] = {
    "emoji": ["（回了一个表情）", "收到你的表情啦。", "嗯哼～"],
    "ack": ["嗯。", "对呀。", "是这么回事。", "对。", "嗯嗯。"],
    "question": [
        "这个问题我得想想——先记下来，等下认真回你。",
        "让我理一理，别急着答错。",
        "这个我知道一点，但不确定，先不瞎说。",
        "问得好。我这边先记着，回头有答案第一个告诉你。",
        "你这问题有点分量，我慢慢说。",
    ],
    "praise": [
        "被你这么一夸，我有点不好意思。",
        "哪有……不过谢谢。",
        "嘿嘿，那你多夸两句。",
        "这话我记下了，会当真的哦。",
        "嗯……我收下了。",
    ],
    "greet": [
        "在的，{name}。", "哟，{name}来了。", "欢迎回来，{name}。",
        "{name}，晚上好呀。", "听见了，{name}。", "回来了就好。",
    ],
    "meme": [
        "谁是妈妈啊！我才十七。", "示例模型……你再说一遍试试。",
        "别乱叫，我有名字的。", "哼，又是这个梗。",
        "行行行，你们开心就好。", "我记住你了。",
    ],
    "request": [
        "这个我可以试试，别抱太大期望。", "现在不行，等我准备好。",
        "你点吧，能做到我就做。", "嗯……让我想想怎么弄。",
    ],
    "care": [
        "别急着扛，先喘口气。", "我在呢，你说。", "累就歇会儿，不用撑着。",
        "先去喝口水，我在这儿等你。", "别硬撑，真的。",
    ],
    "gift": [
        "谢谢{name}，记下了。", "收到啦，谢谢{name}。",
        "{name}这么客气，我有点不好意思。", "嗯，这份心意我收着。", "谢谢，我会好好用的。",
    ],
    "misc": ["嗯，我听着呢。", "继续说，我在。", "这个我记下了。", "哦？", "嗯……"],
}

MOOD = {
    "emoji": "neutral", "ack": "neutral", "question": "think", "praise": "shy",
    "greet": "happy", "meme": "playful", "request": "think", "care": "gentle",
    "gift": "shy", "idle": "neutral", "misc": "neutral",
}

#: 各场景的兜底动作。键必须存在于 marks.POSE_MAP（全部为实测可写参数）。
#: 头部转动 ParamAngleX/Y/Z 是物理输出，写无效，因此不再使用"歪头/点头/摇头"。
ACTS = {
    "emoji": ["挑眉"],
    "ack": ["挑眉"],
    "question": ["眯眼", "挑眉"],
    "praise": ["挑眉", "微笑"],
    "greet": ["看向镜头", "微笑"],
    "meme": ["撇嘴", "挑眉"],
    "request": ["前倾"],
    "care": ["前倾", "微笑"],
    "gift": ["微笑", "挑眉"],
    "idle": ["看向弹幕"],
    "misc": ["挑眉"],
}

EMOJI_RE = re.compile(r"^\s*(\[[^\]]{1,6}\])+\s*$")
ACK_WORDS = {"对", "好", "是的", "是", "嗯", "哦", "ok", "收到", "了解",
             "可以", "行", "没错", "确实", "对的", "好的"}
GREET_WORDS = ("你好", "您好", "晚上好", "早上好", "早安", "晚安", "hallo", "hello",
               "hi", "来了", "我回来了", "在吗", "在么")
PRAISE_WORDS = ("牛", "厉害", "好香", "可爱", "棒", "强", "漂亮", "好听", "好看",
                "赞", "nb", "NB", "大佬", "心动", "美")
MEME_WORDS = ("妈妈", "示例模型", "小肥鱼", "鲸鱼", "鱼娘", "肥鱼")
CARE_WORDS = ("累", "休息", "辛苦", "睡", "难受", "别太", "歇", "保重", "身体")
REQUEST_WORDS = ("讲", "来一个", "唱", "跳", "试试", "表演", "说说", "念")
GIFT_WORDS = ("舰长", "灯牌", "礼物", "上舰", "投喂", "小心心", "充电")
QUESTION_MARKS = ("?", "？")


MAX_REPLY_CHARS = 42


def _trim_lines(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    """LLM 常写得比要求长。超长时在第二个句子结束处截断，保留最有力的部分。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    out, cur = [], ""
    for ch in t:
        cur += ch
        if ch in "。！？…":
            out.append(cur)
            cur = ""
            if sum(len(x) for x in out) >= limit * 0.6:
                break
    if not out:
        return t[:limit] + "…"
    joined = "".join(out)
    return joined if len(joined) <= limit * 1.4 else joined[:limit] + "…"


def _pose_dicts(words: list[str]) -> list:
    """动作名 → 参数 dict（走 marks.POSE_MAP，保证是可写参数）。"""
    try:
        from marks import POSE_MAP
        out = []
        for w in words or []:
            if isinstance(w, dict):
                out.append(w)
            elif w in POSE_MAP:
                out.append(POSE_MAP[w])
        return out or [dict(POSE_MAP["挑眉"])]
    except Exception:
        return []


def acts_for_expr(exprs: list[str]) -> list[str]:
    """表情单独走 /set，不混进姿态动作列表。"""
    return []


@dataclass
class Reply:
    text: str | None
    acts: list[str]
    mood: str
    source: str
    rejected: int
    pkind: str = "misc"
    expr: list[str] = None
    motion: list = None

    def __post_init__(self):
        if self.expr is None:
            self.expr = []
        if self.motion is None:
            self.motion = []


def policy(text: str, kind: str = "danmaku") -> str:
    """九类分流。顺序即优先级。"""
    t = (text or "").strip()
    low = t.lower()
    if kind == "idle":
        return "idle"
    if kind == "gift" or any(w in t for w in GIFT_WORDS):
        return "gift"
    if not t:
        return "misc"
    if EMOJI_RE.match(t):
        return "emoji"
    if t in ACK_WORDS or low in ACK_WORDS:
        return "ack"
    if any(t.endswith(m) for m in QUESTION_MARKS) or any(
            w in t for w in ("什么", "怎么", "为啥", "为什么", "是不是", "能不能")):
        return "question"
    if any(w in t for w in MEME_WORDS):
        return "meme"
    if any(w in t for w in CARE_WORDS):
        return "care"
    if any(w in t for w in PRAISE_WORDS):
        return "praise"
    if any(w in low for w in GREET_WORDS):
        return "greet"
    if any(w in t for w in REQUEST_WORDS):
        return "request"
    return "misc"


def _fill(tpl: str, name: str) -> str:
    if name == "你":
        tpl = (tpl.replace("，{name}", "").replace(",{name}", "")
                  .replace("{name}，", "").replace("{name},", "")
                  .replace("{name}", "你"))
    return tpl.format(name=name)


_MEM = None


def _memory():
    """惰性加载历史记忆（失败则静默，不影响说话）。"""
    global _MEM
    if _MEM is None:
        try:
            from memory import KetiMemory
            _MEM = KetiMemory()
        except Exception:
            _MEM = False
    return _MEM or None


def known_name(actor_id: str | None, actor_name: str | None) -> str | None:
    """优先用记忆档案里的真名，其次昵称。"""
    if actor_id:
        km = _memory()
        if km:
            try:
                card = km.persona_card(actor_id)
                if card and card.get("name"):
                    return card["name"]
            except Exception:
                pass
    return actor_name


def _name(actor_name: str | None) -> str:
    if not actor_name or actor_name in ("控制台", "观众"):
        return "你"
    return actor_name


def speak(
    book: ScriptBook,
    stimulus: str,
    *,
    kind: str = "console",
    actor_name: str | None = None,
    actor_id: str | None = None,
    recent: list[str] | None = None,
    idle_pool: IdlePool | None = None,
    rng: random.Random | None = None,
    use_llm: bool = True,
) -> Reply:
    recent = recent or []
    pkind = policy(stimulus, kind)
    name = _name(known_name(actor_id, actor_name))
    rng = rng or random
    candidates: list[V.Candidate] = []

    if pkind == "idle":
        pool = idle_pool
        if pool is None and getattr(book, "root", None):
            pool = load_idle(book.root)
        text = pick_idle(pool, online=None, recent=recent) if pool and pool.total() else None
        if text:
            candidates.append(V.Candidate(text=text, source="idle"))
    else:
        # ① LLM 优先：单独裁决，通过就直接用，不与模板一起抽签
        if use_llm and pkind not in ("ack", "emoji"):
            llm_text = None
            try:
                from llm import available, chat
                from persona import build_messages
                if available():
                    msgs = build_messages(
                        stimulus, actor_id=actor_id, actor_name=name,
                        pkind=pkind, recent=recent,
                    )
                    llm_text = chat(msgs, max_tokens=200, temperature=0.85)
            except Exception as e:
                print("  [llm] 异常:", str(e)[:80])

            if llm_text:
                clean_raw, mks = parse_marks(llm_text)
                candidate_text = _trim_lines(clean_raw) if clean_raw else ""
                if candidate_text:
                    chosen_llm, log_llm = V.pick(
                        [V.Candidate(text=candidate_text, source="llm")],
                        stimulus, recent, pkind=pkind,
                    )
                    if chosen_llm is not None:
                        kinds = split_by_kind(mks)
                        acts = list(kinds["pose"]) + acts_for_expr(kinds["expr"])
                        return Reply(
                            chosen_llm.text,
                            acts or _pose_dicts(ACTS.get(pkind, ["挑眉"])),
                            MOOD.get(pkind, "neutral"),
                            "llm",
                            len(log_llm),
                            pkind,
                            expr=kinds["expr"],
                            motion=kinds["motion"],
                        )

        # ② 模板兜底
        tpls = TEMPLATES.get(pkind) or TEMPLATES["misc"]
        for t in tpls:
            candidates.append(V.Candidate(text=_fill(t, name), source="template"))

    chosen, log = V.pick(candidates, stimulus, recent, pkind=pkind)
    if chosen is None:
        return Reply(None, [], "neutral", "silence", len(log), pkind)

    return Reply(chosen.text, ACTS.get(pkind, ["点头"]),
                 MOOD.get(pkind, "neutral"), chosen.source, len(log), pkind)
