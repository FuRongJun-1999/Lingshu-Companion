# -*- coding: utf-8 -*-
"""lingshu.py · 把直播对话写进灵枢情景层

# 功能名：灵枢记忆写入
# 生效条件：D:/program/dsh-memory-main 存在且 md_cg 可导入
# 子功能：
#   - remember(): 一条对话 → 一个 contextual 节点（CCG 六要素正文）
#   - 节点 id 形如 live_20261116_213045_a1b2，便于按前缀检索
#   - 任何失败都静默返回 None，绝不让直播链路因记忆写入中断
# 执行：
#   from lingshu import LS
#   LS.remember("观众问……她答……", tags=["live","danmaku"])
# 验证方式：python lingshu.py  —— 写一条测试节点并回读
# 不适用条件：
#   - 冷场自语不写（一场几十条会把情景层刷满）
#   - 不做检索，检索走 cg op=read
"""
from __future__ import annotations

import random
import string
import sys
import threading
import time
from pathlib import Path

def _brain_root() -> Path:
    """灵枢大脑位置。三种来源，优先级从高到低：
    1. 环境变量 LINGSHU_ROOT
    2. 仓内 vendor/lingshu（把大脑 clone 进来）
    3. 同级目录 ../dsh-memory-main
    """
    import os
    env = os.environ.get("LINGSHU_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent.parent
    for cand in (here / "vendor" / "lingshu", here.parent / "dsh-memory-main"):
        if (cand / "md_cg").is_dir():
            return cand
    return here / "vendor" / "lingshu"


LS_ROOT = _brain_root()
LAYER = "contextual"


class LingshuMemory:
    """灵枢写入的唯一入口。懒加载，单例使用。"""

    def __init__(self, root: Path | str = LS_ROOT, layer: str = LAYER,
                 autoflush: int = 1):
        self.root = str(root)
        self.layer = layer
        self.autoflush = autoflush
        self._cg = None
        self._err = ""
        self.written = 0

    # ── 懒加载 ──
    def _ensure(self):
        if self._cg is not None:
            return self._cg
        try:
            if self.root not in sys.path:
                sys.path.insert(0, self.root)
            from md_cg import MdCG
            self._cg = MdCG(self.root, autoflush=self.autoflush)
        except Exception as e:
            self._err = f"{type(e).__name__}: {str(e)[:120]}"
            self._cg = None
        return self._cg

    @property
    def healthy(self) -> bool:
        return self._ensure() is not None

    def error(self) -> str:
        self._ensure()
        return self._err

    # ── 写 ──
    @staticmethod
    def _node_id() -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        suf = "".join(random.choices(string.hexdigits.lower()[:16], k=4))
        return f"live_{ts}_{suf}"

    def remember(self, content: str, *, tags=None, importance: float = 0.5,
                 condition_space=None, summary: str = "") -> str | None:
        """写一条情景记忆。返回节点 id，失败返回 None。"""
        if not (content or "").strip():
            return None
        cg = self._ensure()
        if cg is None:
            return None
        nid = self._node_id()
        tag_list = ["live"] if tags is None else list(tags)
        try:
            cg.add(
                node_id=nid,
                content=content.strip(),
                layer=self.layer,
                tags=tag_list,
                importance=importance,
                condition_space=condition_space or {},
                verification_basis="data",
            )
            self.written += 1
            return nid
        except Exception as e:
            self._err = f"{type(e).__name__}: {str(e)[:120]}"
            return None

    def remember_exchange(self, who: str, said: str, reply: str,
                          *, kind: str = "danmaku", mood: str = "",
                          acts=None, importance: float = 0.5) -> str | None:
        """把一问一答整理成 CCG 六要素正文再写入。"""
        acts_txt = ""
        if acts:
            parts = []
            for a in acts:
                if isinstance(a, dict):
                    parts.append(",".join(f"{k}={v}" for k, v in a.items()))
                else:
                    parts.append(str(a))
            acts_txt = " / ".join(parts) or "无"
        lines = [
            "# 功能名：直播间对话（" + kind + "）",
            "# 生效条件：直播间；观众 " + (who or "未知"),
            "# 子功能：" + (acts_txt or "无动作"),
            "# 执行：观众说「" + (said or "").strip()[:60] + "」→ 她回「" + (reply or "").strip()[:80] + "」",
            "# 验证方式：见 data/interactions 当日 MD 文件",
            "# 不适用条件：冷场自说不入此节点；情绪=" + (mood or "neutral"),
        ]
        return self.remember("\n".join(lines), tags=["live", kind],
                             importance=importance,
                             condition_space={"scene": "livestream", "viewer": who})

    def close(self):
        try:
            if self._cg is not None:
                self._cg.close()
        except Exception:
            pass
        self._cg = None




# ── 读 ──
CONTEXTUAL_DIR = LS_ROOT / "contextual"


def _parse_node(path: Path) -> dict | None:
    """解析一个 live_*.md，取 frontmatter 关键字段 + 正文的「执行」行。"""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if not txt.startswith("---"):
        return None
    parts = txt.split("---", 2)
    if len(parts) < 3:
        return None
    fm, body = parts[1], parts[2]

    viewer = ""
    created = 0.0
    importance = 0.5
    tags = []
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("condition_space:"):
            try:
                import json as _json
                cs = _json.loads(line.split(":", 1)[1].strip())
                viewer = str(cs.get("viewer") or "")
            except Exception:
                pass
        elif line.startswith("created_at:"):
            try:
                created = float(line.split(":", 1)[1].strip())
            except Exception:
                pass
        elif line.startswith("importance:"):
            try:
                importance = float(line.split(":", 1)[1].strip())
            except Exception:
                pass
        elif line.startswith("tags:"):
            raw = line.split(":", 1)[1].strip()
            tags = [t.strip().strip(chr(34)).strip(chr(39))
                    for t in raw.strip("[]").split(",") if t.strip()]

    # 正文里最有信息量的是「执行」那一行（谁说了什么、她回了什么）
    said = reply = ""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# 执行："):
            seg = line[len("# 执行："):]
            if "→ 她回「" in seg:
                a, b = seg.split("→ 她回「", 1)
                said = a.replace("观众说「", "").rstrip("」").strip()
                reply = b.rstrip("」").strip()
            break

    if not created:
        try:
            created = path.stat().st_mtime
        except Exception:
            created = 0.0

    return {
        "id": path.stem,
        "viewer": viewer,
        "created": created,
        "importance": importance,
        "tags": tags,
        "said": said,
        "reply": reply,
    }


def recall(viewer: str = "", limit: int = 5, days: int = 7,
           query: str = "") -> list:
    """查某个观众最近说过什么。返回按时间倒序的条目列表。

    直接扫 contextual/live_*.md 而不走 MdCG.search：
    我们需要按 condition_space.viewer 精确过滤，而 search 的条件闸门
    是给语义检索用的，按人过滤反而更绕。一场几十条，扫文件毫秒级。
    """
    import time as _t
    if not CONTEXTUAL_DIR.exists():
        return []
    cutoff = _t.time() - max(1, days) * 86400
    out = []
    try:
        for p in CONTEXTUAL_DIR.glob("live_*.md"):
            try:
                if p.stat().st_mtime < cutoff:
                    continue
            except Exception:
                continue
            node = _parse_node(p)
            if not node:
                continue
            if node["created"] and node["created"] < cutoff:
                continue
            if viewer and node["viewer"] != viewer:
                continue
            if query:
                hay = node["said"] + node["reply"]
                if query not in hay:
                    continue
            out.append(node)
    except Exception:
        return []
    out.sort(key=lambda n: n["created"], reverse=True)
    return out[:max(1, limit)]


def recall_block(viewer: str, limit: int = 4, per_len: int = 46) -> str:
    """把检索结果拼成可直接塞进 prompt 的一段话。空则返回空串。"""
    items = recall(viewer=viewer, limit=limit)
    if not items:
        return ""
    lines = []
    import time as _t
    now = _t.time()
    for it in items:
        ago = max(0, int((now - it["created"]) / 60))
        if ago < 60:
            when = f"{ago} 分钟前"
        elif ago < 1440:
            when = f"{ago // 60} 小时前"
        else:
            when = f"{ago // 1440} 天前"
        said = it["said"][:per_len]
        reply = it["reply"][:per_len]
        lines.append(f"- {when} 他说「{said}」，你回「{reply}」")
    return chr(10).join(lines)

#: 全局单例。import 时不构造，第一次用才加载。
LS = LingshuMemory()

#: 写入队列。多个线程（主循环 / 8792 网页）都会写同一个 MdCG 实例，
#: 而 MdCG 不是线程安全的，所以统一交给一个后台线程串行消费。
_Q: list = []
_QLock = threading.Lock()
_QCond = threading.Condition(_QLock)
_Worker: threading.Thread | None = None
_InFlight = 0


def enqueue(who: str, said: str, reply: str, **kw) -> None:
    """投递到写入队列，立即返回。直播链路绝不等记忆写完。"""
    global _Worker
    try:
        with _QCond:
            _Q.append((who, said, reply, kw))
            if _Worker is None or not _Worker.is_alive():
                _Worker = threading.Thread(target=_drain, daemon=True, name="lingshu-writer")
                _Worker.start()
    except Exception:
        pass


def _drain() -> None:
    """后台串行消费。空队列时退出，下次 enqueue 会重新拉起。"""
    global _Worker, _InFlight
    while True:
        with _QCond:
            if not _Q:
                _Worker = None
                _QCond.notify_all()
                return
            who, said, reply, kw = _Q.pop(0)
            _InFlight += 1
        try:
            LS.remember_exchange(who, said, reply, **kw)
        except Exception:
            pass
        finally:
            with _QCond:
                _InFlight -= 1
                if not _Q and _InFlight == 0:
                    _QCond.notify_all()


def pending() -> int:
    with _QLock:
        return len(_Q) + _InFlight


def flush(timeout: float = 10.0) -> int:
    """等队列写完。进程退出前必须调用。

    后台写线程是 daemon，进程一退就被杀，队列里没写完的就丢了。
    一次性模式（--say）和主循环收尾都要走这里。
    """
    deadline = time.monotonic() + max(0.0, timeout)
    with _QCond:
        while _Q or _InFlight > 0:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            _QCond.wait(timeout=rem)
    return LS.written


if __name__ == "__main__":
    print("健康检查…")
    print("  healthy:", LS.healthy)
    if not LS.healthy:
        print("  错误:", LS.error())
        raise SystemExit(1)

    print()
    print("写一条测试节点…")
    nid = LS.remember_exchange(
        who="测试观众", said="灵枢你好呀", reply="主人这一声，让我想先问一句——您找的是我吗？",
        kind="danmaku", mood="shy", acts=["挑眉", {"ParamBodyAngleZ": 18}], importance=0.4)
    print("  节点 id:", nid)
    print()

    if nid:
        print("回读验证…")
        try:
            node = LS._cg.get(nid) if hasattr(LS._cg, "get") else None
            if node:
                print("  读到:", str(node)[:200])
            else:
                print("  (无 get 方法，改查文件)")
                for p in LS_ROOT.glob("contextual/**/*" + nid[-8] + "*"):
                    print("  文件:", p)
        except Exception as e:
            print("  回读失败:", e)
    LS.close()
