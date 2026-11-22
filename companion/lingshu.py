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

import inspect
import json
import os
import random
import string
import sys
import threading
import time
from pathlib import Path


def _brain_root() -> Path:
    """灵枢大脑代码位置（含 md_cg/）。优先级从高到低：
    1. 环境变量 LINGSHU_ROOT
    2. 仓内 vendor/lingshu（把大脑 clone 进来）
    3. 同级目录 ../dsh-memory-main 或 ../dsh-memory
    """
    env = (os.environ.get("LINGSHU_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent.parent
    for cand in (here / "vendor" / "lingshu",
                 here.parent / "dsh-memory-main",
                 here.parent / "dsh-memory"):
        if (cand / "md_cg").is_dir():
            return cand
    return here / "vendor" / "lingshu"


def _ensure_code_path(code_root: Path | str | None) -> None:
    """确保 code_root 在 sys.path 中，便于导入 md_cg。"""
    if not code_root:
        return
    s = str(code_root)
    if s and os.path.isdir(s) and s not in sys.path:
        sys.path.insert(0, s)


def _data_root(code_root: Path | str | None = None) -> Path:
    """灵枢认知图数据根（MdCG 存储目录），与代码目录解耦。

    解析优先级：
    1. 环境变量 MDCG_ROOT
    2. md_cg.datapath.mdcg_root()（与 dsh-memory MCP server 共享同一认知图，
       避免把运行期记忆写进代码仓库目录）
    3. 回落到 code_root（兼容旧版无 datapath 模块的脑仓）
    """
    env = (os.environ.get("MDCG_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    croot = Path(code_root) if code_root is not None else _brain_root()
    _ensure_code_path(croot)
    try:
        from md_cg import datapath as _dp
        if hasattr(_dp, "mdcg_root"):
            return Path(_dp.mdcg_root())
    except Exception:
        pass
    return croot


def _supports_async_flush(mdcg_cls) -> bool:
    """检测 MdCG 构造函数是否支持 async_flush 参数。"""
    try:
        return "async_flush" in inspect.signature(mdcg_cls).parameters
    except (TypeError, ValueError):
        return False


def _one_line(text: str | None) -> str:
    """将换行归一为空格，确保 CCG 单行要素与「执行」行稳定可解析。"""
    return (text or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()


LS_ROOT = _brain_root()
LAYER = "contextual"


class LingshuMemory:
    """灵枢写入的唯一入口。懒加载，单例使用。"""

    def __init__(self, root: Path | str | None = None, layer: str = LAYER,
                 autoflush: int = 1, code_root: Path | str | None = None):
        self._explicit_root = str(root) if root is not None else None
        self._explicit_code_root = str(code_root) if code_root is not None else None
        self.layer = layer
        self.autoflush = autoflush
        self._cg = None
        self._err = ""
        self.written = 0

    @property
    def code_root(self) -> str:
        if self._explicit_code_root is not None:
            return self._explicit_code_root
        if self._explicit_root is not None and (Path(self._explicit_root) / "md_cg").is_dir():
            return self._explicit_root
        return str(_brain_root())

    @property
    def data_root(self) -> str:
        if self._explicit_root is not None:
            return self._explicit_root
        return str(_data_root(self.code_root))

    @property
    def root(self) -> str:
        """向后兼容别名，指向当前数据根。"""
        return self.data_root

    # ── 懒加载 ──
    def _ensure(self):
        if self._cg is not None:
            return self._cg
        try:
            _ensure_code_path(self.code_root)
            droot = self.data_root
            from md_cg import MdCG
            kw: dict = {"autoflush": self.autoflush}
            if _supports_async_flush(MdCG):
                kw["async_flush"] = False
            self._cg = MdCG(droot, **kw)
            self._err = ""
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
        who_clean = _one_line(who)
        said_clean = _one_line(said)[:60]
        reply_clean = _one_line(reply)[:80]
        kind_clean = _one_line(kind) or "danmaku"
        mood_clean = _one_line(mood) or "neutral"
        acts_txt = ""
        if acts:
            parts = []
            for a in acts:
                if isinstance(a, dict):
                    parts.append(",".join(f"{k}={v}" for k, v in a.items()))
                else:
                    parts.append(str(a))
            acts_txt = _one_line(" / ".join(parts)) or "无"
        lines = [
            "# 功能名：直播间对话（" + kind_clean + "）",
            "# 生效条件：直播间；观众 " + (who_clean or "未知"),
            "# 子功能：" + (acts_txt or "无动作"),
            "# 执行：观众说「" + said_clean + "」→ 她回「" + reply_clean + "」",
            "# 验证方式：见 data/interactions 当日 MD 文件",
            "# 不适用条件：冷场自说不入此节点；情绪=" + mood_clean,
        ]
        return self.remember("\n".join(lines), tags=["live", kind_clean],
                             importance=importance,
                             condition_space={"scene": "livestream", "viewer": who_clean})

    def sync_flush(self) -> None:
        """将已打开的 MdCG 索引缓冲同步落盘（不触发懒加载）。"""
        cg = self._cg
        if cg is None:
            return
        fn = getattr(cg, "flush", None)
        if callable(fn):
            try:
                fn()
            except Exception as e:
                self._err = f"{type(e).__name__}: {str(e)[:120]}"

    def close(self):
        try:
            if self._cg is not None:
                self._cg.close()
        except Exception:
            pass
        self._cg = None


# ── 读 ──
def _split_frontmatter(txt: str) -> tuple[dict, str] | None:
    """解析节点 frontmatter 与正文。

    优先复用 md_cg.nodefile.loads()；若 md_cg 不可导入，回落到与 nodefile.loads()
    一致的严格 `\\n---\\n` 分隔符切分，避免观众名等字段含 `"---"` 时误切。
    """
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    if not txt.startswith("---\n"):
        return None
    _ensure_code_path(LS.code_root)
    try:
        from md_cg import nodefile as _nf
        if hasattr(_nf, "loads"):
            fm, body = _nf.loads(txt)
            if not fm and "\n---\n" not in txt[4:]:
                return None
            return (fm if isinstance(fm, dict) else {}), body
    except Exception:
        pass
    rest = txt[4:]
    end = rest.find("\n---\n")
    if end < 0:
        return None
    head, body = rest[:end], rest[end + 5:]
    fm = {}
    for line in head.split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        try:
            fm[k] = json.loads(v)
        except ValueError:
            fm[k] = v
    return fm, body


def _parse_node(path: Path) -> dict | None:
    """解析一个 live_*.md，取 frontmatter 关键字段 + 正文的「执行」行。"""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    parsed = _split_frontmatter(txt)
    if parsed is None:
        return None
    fm, body = parsed

    viewer = ""
    cs = fm.get("condition_space")
    if isinstance(cs, dict):
        viewer = str(cs.get("viewer") or "")

    created = 0.0
    raw_created = fm.get("created_at")
    if raw_created is not None and not isinstance(raw_created, bool):
        try:
            created = float(raw_created)
        except (TypeError, ValueError):
            created = 0.0

    importance = 0.5
    raw_imp = fm.get("importance")
    if raw_imp is not None and not isinstance(raw_imp, bool):
        try:
            importance = float(raw_imp)
        except (TypeError, ValueError):
            importance = 0.5

    raw_tags = fm.get("tags")
    if isinstance(raw_tags, (list, tuple)):
        tags = [str(t) for t in raw_tags]
    elif isinstance(raw_tags, str):
        tags = [t.strip().strip('"').strip("'")
                for t in raw_tags.strip("[]").split(",") if t.strip()]
    else:
        tags = []

    # 正文里最有信息量的是「执行」那一行（谁说了什么、她回了什么）
    said = reply = ""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# 执行："):
            seg = line[len("# 执行："):]
            if "→ 她回「" in seg:
                a, b = seg.split("→ 她回「", 1)
                a = a.strip()
                if a.startswith("观众说「"):
                    a = a[len("观众说「"):]
                if a.endswith("」"):
                    a = a[:-1]
                said = a.strip()
                b = b.strip()
                if b.endswith("」"):
                    b = b[:-1]
                reply = b.strip()
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
           query: str = "", root: Path | str | None = None) -> list:
    """查某个观众最近说过什么。返回按时间倒序的条目列表。

    直接扫 contextual/live_*.md 而不走 MdCG.search：
    我们需要按 condition_space.viewer 精确过滤，而 search 的条件闸门
    是给语义检索用的，按人过滤反而更绕。一场几十条，扫文件毫秒级。
    """
    contextual_dir = (Path(root) if root is not None else Path(LS.data_root)) / "contextual"
    if not contextual_dir.exists():
        return []
    cutoff = time.time() - max(1, days) * 86400
    out = []
    try:
        for p in contextual_dir.glob("live_*.md"):
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


def recall_block(viewer: str, limit: int = 4, per_len: int = 46,
                 root: Path | str | None = None) -> str:
    """把检索结果拼成可直接塞进 prompt 的一段话。空则返回空串。"""
    items = recall(viewer=viewer, limit=limit, root=root)
    if not items:
        return ""
    lines = []
    now = time.time()
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
    return "\n".join(lines)


#: 全局单例。import 时不构造，第一次用才加载。
LS = LingshuMemory()

#: 写入队列。多个线程（主循环 / 8792 网页）都会写同一个 MdCG 实例，
#: 而 MdCG 不是线程安全的，所以统一交给一个后台线程串行消费。
_Q: list = []
_QLock = threading.Lock()
_QCond = threading.Condition(_QLock)
_Worker: threading.Thread | None = None
_InFlight = 0
_NeedsFlush = False


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
    """后台串行消费。队列排空时在写线程上同步落盘索引再退出，下次 enqueue 会重新拉起。"""
    global _Worker, _InFlight, _NeedsFlush
    while True:
        with _QCond:
            if _Q:
                item = _Q.pop(0)
                _InFlight += 1
                do_flush = False
            elif _NeedsFlush:
                item = None
                _NeedsFlush = False
                _InFlight += 1
                do_flush = True
            else:
                _Worker = None
                _QCond.notify_all()
                return
        try:
            if do_flush:
                LS.sync_flush()
            else:
                who, said, reply, kw = item
                LS.remember_exchange(who, said, reply, **kw)
        except Exception:
            pass
        finally:
            with _QCond:
                if not do_flush:
                    _NeedsFlush = True
                _InFlight -= 1
                if not _Q and _InFlight == 0 and not _NeedsFlush:
                    _QCond.notify_all()


def pending() -> int:
    with _QLock:
        return len(_Q) + _InFlight + (1 if _NeedsFlush else 0)


def flush(timeout: float = 10.0) -> int:
    """等队列写完并同步落盘索引。进程退出前必须调用。

    后台写线程是 daemon，进程一退就被杀，队列里没写完的就丢了。
    一次性模式（--say）和主循环收尾都要走这里。
    """
    global _Worker, _NeedsFlush
    deadline = time.monotonic() + max(0.0, timeout)
    with _QCond:
        if getattr(LS, "_cg", None) is not None:
            _NeedsFlush = True
        if (_Q or _NeedsFlush) and (_Worker is None or not _Worker.is_alive()):
            _Worker = threading.Thread(target=_drain, daemon=True, name="lingshu-writer")
            _Worker.start()
        while _Q or _InFlight > 0 or _NeedsFlush:
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
                for p in Path(LS.data_root).glob("contextual/**/*" + nid[-8:] + "*"):
                    print("  文件:", p)
        except Exception as e:
            print("  回读失败:", e)
    LS.close()
