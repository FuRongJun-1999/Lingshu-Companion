# -*- coding: utf-8 -*-
"""scripts.py · 台词库（M2）

# 功能名：台词库导入与检索
# 生效条件：data/scripts.md 存在；或由 import_corpus() 从 paired_tagged.jsonl 生成
# 子功能：
#   - MD 持久（人可读可改）
#   - 启动加载进内存索引
#   - bigram 相似度检索（无需分词）
#   - 使用计数与冷却（避免连续重复）
# 执行：
#   scripts = ScriptBook.load("data")
#   hits = scripts.search("今天讲什么", kind="answer", limit=5)
#   scripts.bump(script_id)
# 验证方式：test_scripts.py —— 检索命中相关台词；重复调用后冷却生效
# 不适用条件：
#   - bigram 对短句（<4 字）区分度低
#   - 无向量检索；语义相近但用词不同的句子搜不到
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("greet", "thanks", "answer", "comfort", "tease", "idle", "deflect", "misc")

MD_HEADER = """# 台词库

> 每行一条台词，格式：`- [kind] 台词内容`
> kind 取值：greet / thanks / answer / comfort / tease / idle / deflect / misc
> 可直接手工增删；改动后重启进程生效。

"""


@dataclass
class Script:
    sid: int
    kind: str
    text: str
    used: int = 0
    last_used_seq: int = -1


@dataclass
class ScriptBook:
    items: list[Script] = field(default_factory=list)
    root: Path | None = None
    _seq: int = 0

    # ---------- 持久 ----------

    @staticmethod
    def path(root: str | Path) -> Path:
        return Path(root) / "scripts.md"

    @classmethod
    def load(cls, root: str | Path) -> "ScriptBook":
        p = cls.path(root)
        if not p.exists():
            return cls()
        book = cls(root=Path(root))
        sid = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^- \[([a-z]+)\] (.+)$", line.strip())
            if not m:
                continue
            sid += 1
            book.items.append(Script(sid=sid, kind=m.group(1), text=m.group(2).strip()))
        return book

    def save(self, root: str | Path) -> None:
        p = self.path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [MD_HEADER]
        by_kind: dict[str, list[Script]] = {}
        for s in self.items:
            by_kind.setdefault(s.kind, []).append(s)
        for kind in KINDS:
            group = by_kind.get(kind)
            if not group:
                continue
            lines.append(f"## {kind}\n")
            for s in group:
                lines.append(f"- [{s.kind}] {s.text}")
            lines.append("")
        p.write_text("\n".join(lines), encoding="utf-8")

    # ---------- 构造 ----------

    @classmethod
    def from_corpus(cls, jsonl_path: str | Path, *, limit: int = 40000) -> "ScriptBook":
        """从她直播语料导入。只用 clean 字段（已剥离动作标记）。"""
        import json
        book = cls()
        seen: set[str] = set()
        sid = 0
        for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            text = (rec.get("clean") or "").strip()
            if not (4 <= len(text) <= 80):
                continue
            if text in seen:
                continue
            seen.add(text)
            sid += 1
            book.items.append(Script(sid=sid, kind=classify(text, rec.get("stim_kind")), text=text))
            if sid >= limit:
                break
        return book

    # ---------- 检索 ----------

    def search(self, query: str, *, kind: str | None = None, limit: int = 5) -> list[Script]:
        q = _bigrams(query)
        if not q:
            # 空查询：先按短句取候选池，再随机（顺序不能反，否则排序会吃掉随机性）
            import random
            pool = [s for s in self.items if kind is None or s.kind == kind]
            if kind == "idle":
                # idle 不应混入礼物致谢类，那些属于 thanks
                bad = ("谢谢", "灯牌", "舰长", "礼物", "心心", "投喂", "欢迎",
                       "嗓子", "语音", "坏了", "TTS", "ts", "链路", "试音", "出声", "哑")
                pool = [s for s in pool if not any(b in s.text for b in bad)]
            pool.sort(key=lambda s: len(s.text))
            short_pool = pool[:60]
            random.shuffle(short_pool)
            return short_pool[:limit]
        scored: list[tuple[float, Script]] = []
        for s in self.items:
            if kind and s.kind != kind:
                continue
            b = _bigrams(s.text)
            if not b:
                continue
            overlap = len(q & b)
            if overlap == 0:
                continue
            score = overlap / (len(q) ** 0.5 * len(b) ** 0.5)
            score *= 0.6 ** self._cooldown(s)          # 最近用过的降权
            scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]

    def _cooldown(self, s: Script) -> int:
        if s.last_used_seq < 0:
            return 0
        gap = self._seq - s.last_used_seq
        if gap <= 0:
            return 3
        if gap <= 3:
            return 2
        if gap <= 10:
            return 1
        return 0

    def bump(self, s: Script) -> None:
        self._seq += 1
        s.used += 1
        s.last_used_seq = self._seq

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for s in self.items:
            kinds[s.kind] = kinds.get(s.kind, 0) + 1
        return {"total": len(self.items), "by_kind": kinds}


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


def classify(text: str, stim_kind: str | None = None) -> str:
    """粗分类：按关键词。可后续用 NN 替换。"""
    if stim_kind and "idle" in stim_kind:
        return "idle"
    if any(k in text for k in ("欢迎", "你好", "晚上好", "好呀")):
        return "greet"
    if any(k in text for k in ("谢谢", "感谢", "收到啦")):
        return "thanks"
    if any(k in text for k in ("？", "?", "吗")):
        return "answer"
    if any(k in text for k in ("别", "不行", "不给")):
        return "deflect"
    if any(k in text for k in ("哼", "才不", "笨蛋")):
        return "tease"
    if any(k in text for k in ("没事", "别怕", "我在")):
        return "comfort"
    return "misc"
