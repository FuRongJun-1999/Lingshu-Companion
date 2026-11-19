# -*- coding: utf-8 -*-
"""marks.py · （动作/心情）标记解析与引擎映射

# 功能名：圆括号标记解析 + 演出词到模型能力的映射
# 生效条件：台词中出现全角（）
# 子功能：
#   - parse(): 拆出纯台词与标记
#   - split_by_kind(): 标记归类为 expr（表情）/ pose（参数）/ fx（也走表情）
#   - VOCAB_HINT: 给 LLM 的词表（沿用早期整理的演出词表的口语词）
# 执行：
#   parse("（歪头疑惑, 微笑）主人觉得呢？") -> ("主人觉得呢？", ["歪头疑惑, 微笑"])
# 验证方式：test_marks.py —— 五类词都能归到 expr/pose；未知词被丢弃
# 不适用条件：
#   - 早期词表里的「鼓脸颊」本皮套不支持，映射时丢弃
#   - 特效类无对应 fx 图层的，降级为最接近的表情
"""
from __future__ import annotations

import re

MARK_RE = re.compile(r"[（(]([^（）()]{1,24})[）)]")

#: 情绪/特效类 → 模型 exp3 里的真实表情名
EMOTION_MAP = {
    # 情绪
    "微笑": None, "大笑": None, "生气": "黑脸", "可怜": "流泪",
    "害怕": "流冷汗", "感动": "流泪", "惊讶": None, "困倦": "＞＜",
    # 情绪别名
    "脸红": "脸红", "害羞": "脸红", "不好意思": "脸红",
    "哭": "流泪", "委屈": "流泪", "难过": "流泪",
    "怒": "黑脸", "生气特效": "黑脸", "怒气特效": "黑脸",
    "汗": "流冷汗", "流汗": "流冷汗", "流汗特效": "流冷汗", "紧张": "流冷汗",
    "爱心": "爱心眼", "喜欢": "爱心眼", "星星特效": "爱心眼", "感动特效": "爱心眼",
    "问号": "圈圈眼", "问号特效": "圈圈眼", "困惑": "圈圈眼", "晕": "圈圈眼",
    "加载中": "加载", "思考": "加载", "加载中特效": "加载",
    "＞＜": "＞＜", "闭眼": "＞＜",
}

#: 姿态/视线/肢体 → Cubism 参数
#: 只使用**实测可写**的参数。头部转动（ParamAngleX/Y/Z）是物理输出，
#: 写入会被 physics 覆盖，因此这里不提供"歪头/点头/摇头"。
POSE_MAP = {
    # ── 视线（眼球）──
    "看向弹幕": {"ParamEyeBallX": 0.7, "ParamEyeBallY": -0.4},
    "看向镜头": {"ParamEyeBallX": 0.0, "ParamEyeBallY": 0.0},
    "看向屏幕": {"ParamEyeBallX": -0.7, "ParamEyeBallY": 0.2},
    "看向斜上方": {"ParamEyeBallX": 0.3, "ParamEyeBallY": 0.8},
    "看向下面": {"ParamEyeBallX": 0.0, "ParamEyeBallY": -0.8},
    "环顾四周": {"ParamEyeBallX": 0.9, "ParamEyeBallY": 0.0},

    # ── 眼睛 ──
    "眯眼": {"ParamEyeLOpen": 0.35, "ParamEyeROpen": 0.35},
    "瞪大眼睛": {"ParamEyeLOpen": 1.2, "ParamEyeROpen": 1.2, "ParamBrowLY": 0.7, "ParamBrowRY": 0.7},
    "眨单眼": {"ParamEyeLOpen": 0.1},
    "闭眼": {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0},
    "笑眼": {"EyeSmile": 0.9, "REyeSquint": 0.8},

    # ── 眉毛 ──
    "挑眉": {"ParamBrowLY": 0.9, "ParamBrowRY": 0.9},
    "皱眉": {"BrowInnerUp": 0.8, "ParamBrowRAngle": -0.7, "ParamBrowLY": -0.4, "ParamBrowRY": -0.4},

    # ── 嘴 ──
    "微笑": {"ParamMouthForm": 1.0, "MouthSmile_L": 0.7, "MouthSmile_R": 0.7},
    "大笑": {"ParamMouthForm": 1.0, "MouthSmile_L": 1.0, "MouthSmile_R": 1.0},
    "撇嘴": {"ParamMouthForm": -0.8, "MouthX": -0.5},

    # ── 手/肩 ──
    "举手": {"LArm": 0.9, "LForearm": 0.3, "LHand": 0.4},
    "放下手": {"LArm": -0.4, "LForearm": 0.0, "LHand": 0.0},
    "双手抱胸": {"LArm": 0.4, "RArm": 0.4, "LForearm": -0.5, "RForearm": -0.5},
    "摊手": {"LArm": 0.6, "RArm": 0.6, "LHand": 0.5, "RHand": 0.5},
    "耸肩": {"ShoulderY": 15},

    # ── 耳朵（实测可写，±1）──
    "动耳朵": {"LEar1": 0.8, "REar1": 0.8},
    "耳朵竖起": {"LEar1": 0.9, "LEar2": 0.7, "REar1": 0.9, "REar2": 0.7},
    "耳朵垂下": {"LEar1": -0.8, "LEar2": -0.6, "REar1": -0.8, "REar2": -0.6},
    "耳朵抖动": {"LEar1": 0.5, "LEar2": -0.5, "REar1": -0.5, "REar2": 0.5},

    # ── 身体（可写，实测保持）──
    "前倾": {"ParamBodyAngleX": 28},
    "后仰": {"ParamBodyAngleX": -22},
    "身体左倾": {"ParamBodyAngleZ": 25},
    "身体右倾": {"ParamBodyAngleZ": -25},
    "轻轻摇摆": {"ParamBodyAngleZ": 18},
}

#: 道具动画（motion3 Gesture）
MOTION_WORDS = {
    "举手": ("Gesture", 0), "放下手": ("Gesture", 1),
    "拿狗尾草": ("Gesture", 2), "收起狗尾草": ("Gesture", 1),
    "竖手指": ("Gesture", 3), "收起手指": ("Gesture", 4), "脱捕": ("Gesture", 5),
}

_BAD = {"鼓脸颊"}   # 本皮套不支持，映射时丢弃


def parse(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []
    marks = [m.group(1).strip() for m in MARK_RE.finditer(text)]
    clean = MARK_RE.sub("", text).strip()
    return re.sub(r"\s{2,}", " ", clean), marks


def _parts(raw: str) -> list[str]:
    return [x for x in re.split(r"[，,、;；\s]+", raw or "") if x]


def split_by_kind(marks: list[str]) -> dict:
    """标记 → {expr: [表情名], pose: [参数dict], motion: [{group,index}]}"""
    out = {"expr": [], "pose": [], "motion": []}
    for raw in marks:
        for w in _parts(raw):
            if w in _BAD:
                continue
            if w in MOTION_WORDS:
                g, i = MOTION_WORDS[w]
                out["motion"].append({"group": g, "index": i})
                continue
            if w in POSE_MAP:
                out["pose"].append(POSE_MAP[w])
                continue
            if w in EMOTION_MAP:
                name = EMOTION_MAP[w]
                if name and name not in out["expr"]:
                    out["expr"].append(name)
                continue
            # 模糊匹配
            for k, v in POSE_MAP.items():
                if k in w:
                    out["pose"].append(v); break
            else:
                for k, v in EMOTION_MAP.items():
                    if k in w and v:
                        if v not in out["expr"]:
                            out["expr"].append(v)
                        break
    return out


#: 给 LLM 的词表（沿用早期整理的演出词表的口语说法）
VOCAB_HINT = (
    "心情：「脸红」「生气」「害怕」「感动」「困倦」「委屈」「紧张」「喜欢」「困惑」「愣住」\n"
    "姿态：「歪头」「歪头疑惑」「点头」「摇头」「前倾」「抬头挺胸」「轻轻摇摆」「垂头丧气」\n"
    "视线：「看向弹幕」「看向镜头」「看向屏幕」「看向斜上方」\n"
    "表情：「微笑」「大笑」「眯眼」「瞪大眼睛」「眨单眼」\n"
    "道具：「举手」「拿狗尾草」「竖手指」\n"
    "（一次最多两个；不写也行；「鼓脸颊」不支持，别用）"
)
