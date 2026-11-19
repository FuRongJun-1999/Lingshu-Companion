# -*- coding: utf-8 -*-
"""memory.py · 读取历史记忆（观众档案）

# 功能名：从她认知图/观众档案读取既有记忆
# 生效条件：观众档案目录存在（可配置）
# 子功能：
#   - 按 actor_id 读取档案全文
#   - 提取首段（她对这个人最凝练的印象）
#   - 提取要点（- 开头的条目）
#   - 迁移到我们 data/relations/ 格式（保留原文）
# 执行：
#   from memory import KetiMemory
#   km = KetiMemory()
#   km.profile("bilibili:11770366")   -> dict | None
#   km.migrate_to("data")              -> 迁移条数
# 验证方式：test_memory.py —— 能读到档案；迁移后 relations 目录条数增加
# 不适用条件：
#   - 只读，不写回她目录（不污染旧系统）
#   - 昵称在旧档案里已打码，取不到真名
"""
from __future__ import annotations

import re
from pathlib import Path

#: 观众档案目录。优先环境变量，其次仓内 data/relations。
#: 档案是可选的：没有档案也能正常说话，只是不认识人。
def _viewers_dir() -> Path:
    import os
    env = os.environ.get("COMPANION_VIEWERS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "relations"


def _sessions_dir() -> Path:
    import os
    env = os.environ.get("COMPANION_SESSIONS_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "data" / "sessions"


KETI_VIEWERS = _viewers_dir()
KETI_SESSIONS = _sessions_dir()
KETI_BRAIN = None



class KetiMemory:
    def __init__(self, viewers: Path | None = None):
        self.viewers = viewers or KETI_VIEWERS

    # ---------- 读 ----------

    def _path(self, actor_id: str) -> Path | None:
        """actor_id 形如 bilibili:11770366 或纯数字。"""
        if not actor_id:
            return None
        uid = actor_id.split(":")[-1].strip()
        if not uid.isdigit():
            return None
        p = self.viewers / f"{uid}.md"
        return p if p.exists() else None

    def profile(self, actor_id: str) -> dict | None:
        p = self._path(actor_id)
        if not p:
            return None
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        return {
            "actor_id": actor_id,
            "uid": p.stem,
            "chars": len(text),
            "summary": self._summary(text),
            "points": self._points(text),
            "text": text,
            "source": str(p),
        }

    @staticmethod
    def _summary(text: str) -> str:
        """首段 = 她对这个人最凝练的印象。"""
        for block in re.split(r"\n\s*\n", text):
            b = block.strip()
            if b and not b.startswith(("-", "#", "*")):
                return b[:300]
        return text[:200]

    @staticmethod
    def _points(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith(("- ", "* ")) and len(s) > 3:
                out.append(s[2:].strip())
        return out

    # ---------- 提取可操作字段 ----------

    @staticmethod
    def name_of(text: str) -> str | None:
        """首段形如「量子Bug（id 100970679）：…」，取括号前的名字。"""
        head = text.strip().split("\n", 1)[0]
        mm = re.match(r"^(.{1,20}?)[（(]\s*id\s*\d+", head)
        if mm:
            cand = mm.group(1).strip()
            if 1 < len(cand) <= 20:
                return cand
        return None

    @staticmethod
    def address_of(text: str) -> str | None:
        """她对这个人的称呼。必须是「唤他/叫他/称呼他」，排除「管我叫」这类反向指代。"""
        # 先剔掉"管我叫X""叫我X"这类（那是他叫她）
        cleaned = re.sub(r"(?:管我|冲我|对我|叫我|喊我|称我)(?:叫|为|作)?[「『\"][^」』\"]{1,12}[」』\"]", "", text)
        pats = [
            r"(?:唤|叫|称呼)(?:他|她|您)(?:为|作)?[「『\"]([^」』\"]{1,10})[」』\"]",
            r"(?:他|她|您)(?:让我|要我|叫我)?(?:唤|叫|称呼)[「『\"]([^」』\"]{1,10})[」』\"]",
        ]
        for p in pats:
            mm = re.search(p, cleaned)
            if mm:
                cand = mm.group(1).strip()
                if 1 <= len(cand) <= 10 and cand not in ("我", "你", "他", "她", "主人"):
                    return cand
        return None

    def persona_card(self, actor_id: str) -> dict | None:
        """给说话层用的精简卡：名字 + 称呼 + 一句话印象。"""
        prof = self.profile(actor_id)
        if not prof:
            return None
        return {
            "actor_id": actor_id,
            "name": self.name_of(prof["text"]),
            "address": self.address_of(prof["text"]),
            "summary": prof["summary"],
            "chars": prof["chars"],
        }

    # ---------- 索引 ----------

    def all_uids(self) -> list[str]:
        if not self.viewers.exists():
            return []
        return sorted(p.stem for p in self.viewers.glob("*.md") if p.stem.isdigit())

    def stats(self) -> dict:
        uids = self.all_uids()
        sizes = []
        for u in uids:
            try:
                sizes.append((self.viewers / f"{u}.md").stat().st_size)
            except OSError:
                pass
        return {
            "count": len(uids),
            "total_kb": round(sum(sizes) / 1024) if sizes else 0,
            "non_empty": sum(1 for s in sizes if s > 50),
        }

    # ---------- 迁移 ----------

    def migrate_to(self, root: str) -> int:
        """把历史档案迁进我们的 relations 目录（保留原文，加结构化头）。

        已存在的档案不覆盖，只在末尾追加一次迁移标记，避免重复。
        """
        dst = Path(root) / "relations"
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for uid in self.all_uids():
            prof = self.profile(f"bilibili:{uid}")
            if not prof or prof["chars"] < 30:
                continue
            target = dst / f"bilibili_{uid}.md"
            if target.exists() and "keti-import" in target.read_text(encoding="utf-8", errors="replace"):
                continue
            body = [
                f"# bilibili:{uid}",
                "",
                f"- ID：bilibili:{uid}",
                f"- 首次：（早期，未知）",
                f"- 最近：（早期，未知）",
                f"- 互动：>0 次",
                f"- 亲密度：0.30",
                f"- 来源：keti-import",
                "",
                "## 她对这个人记得的事",
                "",
                prof["text"],
                "",
                "<!-- keti-import v1 -->" if False else "",
            ]
            target.write_text("\n".join(body).strip() + "\n", encoding="utf-8")
            n += 1
        return n
