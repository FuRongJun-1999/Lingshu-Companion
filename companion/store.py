# -*- coding: utf-8 -*-
"""store.py · 灵枢伴侣 · 数据层（MD 版）

# 功能名：伴侣对话落库（Markdown 为真相源）
# 生效条件：Python 3.11+；data/ 可写
# 子功能：
#   - 每天一个 MD：data/interactions/YYYY-MM-DD.md
#   - 每个观众一个 MD：data/relations/<platform>_<uid>.md
#   - 先落库：刺激到达即写 block（应答留空），回应后回填
#   - 崩溃恢复：启动扫描空应答 block，列出未完成项
# 执行：
#   from store import MdStore
#   s = MdStore("./data")
#   iid = s.record_stimulus(actor_id="u1", actor_name="小花", kind="danmaku", text="你好")
#   s.record_response(iid, response="你好呀", acts=["看向弹幕"], latency_ms=1200)
# 验证方式：test_store.py 断言 MD 中 block 数与 db 无关、回填不新增 block、重启后数据仍在
# 不适用条件：
#   - 回填需重写整个当日文件（日文件 ~100KB 时可接受，>5MB 需改分片）
#   - 不做索引：查询靠扫描 + 正则，适合每天数百条；上万条需另建索引
#   - 单进程假设
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

BLOCK_RE = re.compile(r"<!-- id:([0-9a-f]{12}) -->.*?\n(.*?)(?=\n<!-- id:|\Z)", re.S)

_LINEBREAK_RE = re.compile(r"[\r\n\x0b\x0c\x85\u2028\u2029]+")
_LINEBREAK_SINGLE_RE = re.compile(r"\r\n|[\r\x0b\x0c\x85\u2028\u2029]")
_META_LINE_RE = re.compile(r"- (?=(?:类型|状态|动作|情绪|延迟|ID|首次|最近|互动|亲密度)：)")


def _escape_md_markers(text: str) -> str:
    """转义会干扰 FORMAT.md / viewer.py 正则边界的结构标记（幂等）。"""
    text = text.replace("<!--", r"<\!--")
    text = text.replace("**她**：", r"\*\*她\*\*：")
    text = text.replace("**观众**：", r"\*\*观众\*\*：")
    text = _META_LINE_RE.sub("－ ", text)
    return text


def _sanitize_inline(value: str | None, *, escape_sep: bool = False) -> str:
    """清洗单行标题/关系元数据字段：压平换行、避免破坏 ` · ` 分隔与结构标记。"""
    if value is None:
        return ""
    s = _LINEBREAK_RE.sub(" ", str(value)).strip()
    if escape_sep:
        s = s.replace("·", "・")
    return _escape_md_markers(s)


def _sanitize_kind(kind: str | None) -> str:
    s = re.sub(r"\s+", "_", _sanitize_inline(kind, escape_sep=True))
    return s or "danmaku"


def _sanitize_body(value: str | None) -> str:
    """清洗互动正文：合并空行避免截断 `\\n\\n` 正则，并转义结构标记（幂等）。"""
    if value is None:
        return ""
    s = _LINEBREAK_SINGLE_RE.sub("\n", str(value))
    lines = [ln.rstrip() for ln in s.split("\n") if ln.strip()]
    s = "\n".join(lines).strip()
    return _escape_md_markers(s)


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _fmt_acts(acts) -> str:
    """动作列表转可读文本。

    acts 里可能混着字符串（动作名）和 dict（参数键值），
    dict 直接 join 会抛 TypeError，这里统一格式化。
    """
    out: list[str] = []
    for a in acts or []:
        if isinstance(a, dict):
            parts = [f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}"
                     for k, v in a.items()]
            out.append("(" + ",".join(parts) + ")")
        else:
            t = str(a).strip()
            if t:
                out.append(t)
    return "、".join(out) if out else "无"


def _atomic_write(path: Path, text: str, retries: int = 5) -> None:
    """原子写。Windows 上目标文件被占用时 os.replace 会报 WinError 5，需重试。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    last: Exception | None = None
    for i in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.08 * (i + 1))
    # 最后兜底：直接覆盖写（放弃原子性，保数据）
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:
            os.unlink(tmp)
        except OSError:
            pass
    except OSError:
        if last:
            raise last


class MdStore:
    """Markdown 为真相源。每个互动是一个可读 block，回填时重写当日文件。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.inter_dir = self.root / "interactions"
        self.rel_dir = self.root / "relations"
        self.inter_dir.mkdir(parents=True, exist_ok=True)
        self.rel_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0

    # ---------- 内部 ----------

    def _new_id(self) -> str:
        self._seq = (self._seq + 1) % 0xFFFF
        raw = f"{_now().isoformat()}-{self._seq}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def _day_path(self, dt: datetime | None = None) -> Path:
        dt = dt or _now()
        return self.inter_dir / f"{dt:%Y-%m-%d}.md"

    def _ensure_day_header(self, path: Path, dt: datetime) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        header = (
            f"# {dt:%Y-%m-%d} · 对话记录\n\n"
            f"- 生成方式：MdStore（先落库，后回填）\n"
            f"- 说明：每个 ## 是一个互动；「她」为空表示应答尚未生成\n\n"
            f"---\n\n"
        )
        return header

    def _render_block(
        self,
        iid: str,
        dt: datetime,
        actor_id: str | None,
        actor_name: str | None,
        kind: str,
        stimulus: str,
        response: str | None = None,
        acts: Iterable[str] | None = None,
        mood: str | None = None,
        latency_ms: int | None = None,
    ) -> str:
        who = _sanitize_inline(actor_name, escape_sep=True) or "（未知）"
        aid = _sanitize_inline(actor_id, escape_sep=True)
        tail = f" · {aid}" if aid else ""
        kind_s = _sanitize_kind(kind)
        stim_body = _sanitize_body(stimulus) or "（空）"
        resp_body = _sanitize_body(response) if response is not None else ""
        has_resp = bool(resp_body)
        parts = [
            f"<!-- id:{iid} -->",
            f"## {dt:%H:%M:%S} · {who}{tail}",
            "",
            f"- 类型：{kind_s}",
            f"- 状态：{'已回应' if has_resp else '待回应'}",
            "",
            "**观众**：",
            "",
            stim_body,
            "",
        ]
        if has_resp:
            acts_s = _sanitize_inline(_fmt_acts(acts))
            mood_s = _sanitize_inline(mood) or "未标注"
            parts += [
                "**她**：",
                "",
                resp_body,
                "",
                f"- 动作：{acts_s}",
                f"- 情绪：{mood_s}",
                f"- 延迟：{latency_ms} ms" if latency_ms is not None else "- 延迟：未记录",
                "",
            ]
        parts += ["---", ""]
        return "\n".join(parts)

    # ---------- 写入 ----------

    def record_stimulus(
        self,
        *,
        actor_id: str | None,
        actor_name: str | None,
        kind: str,
        text: str,
        session: str | None = None,
    ) -> str:
        """刺激到达即落库，返回 block id。"""
        dt = _now()
        iid = self._new_id()
        block = self._render_block(iid, dt, actor_id, actor_name, kind, text)
        clean_aid = _sanitize_inline(actor_id, escape_sep=True)
        with self._lock:
            path = self._day_path(dt)
            body = self._ensure_day_header(path, dt)
            _atomic_write(path, body + block)
            if clean_aid:
                self._touch_relation(clean_aid, actor_name, dt, kind, text)
        return iid

    def record_response(
        self,
        iid: str,
        *,
        response: str,
        acts: Iterable[str] | None = None,
        mood: str | None = None,
        latency_ms: int | None = None,
    ) -> bool:
        """回填应答。按 id 定位并重写当日文件。"""
        acts_list = list(acts or [])
        with self._lock:
            # 逐日文件查找（应答可能跨零点）
            for path in sorted(self.inter_dir.glob("*.md"), reverse=True):
                text = path.read_text(encoding="utf-8")
                marker = f"<!-- id:{iid} -->"
                pos = text.find(marker)
                if pos < 0:
                    continue
                m = BLOCK_RE.search(text, pos)
                if not m:
                    return False
                old_block = m.group(0)
                # 解析旧的刺激部分，保留原样
                dt = _now()
                head = re.search(r"## (\d{2}:\d{2}:\d{2}) · (.*?)(?: · (.*))?\n", old_block)
                hhmmss = head.group(1) if head else dt.strftime("%H:%M:%S")
                who_line = head.group(2) if head else "（未知）"
                actor_id = head.group(3) if head and head.group(3) else None
                stim = re.search(r"\*\*观众\*\*：\n\n(.*?)\n\n", old_block, re.S)
                stimulus = stim.group(1) if stim else ""
                kind_m = re.search(r"- 类型：(\S+)", old_block)
                kind = kind_m.group(1) if kind_m else "danmaku"
                new_block = self._render_block(
                    iid, dt.replace(hour=int(hhmmss[:2]), minute=int(hhmmss[3:5]), second=int(hhmmss[6:8])),
                    actor_id, who_line, kind, stimulus, response, acts_list, mood, latency_ms,
                )
                _atomic_write(path, text[:pos] + new_block + text[m.end():])
                return True
            return False

    def _touch_relation(self, actor_id: str, actor_name: str | None, dt: datetime, kind: str, text: str) -> None:
        clean_aid = _sanitize_inline(actor_id, escape_sep=True)
        safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", clean_aid)[:60] or "unknown"
        title = _sanitize_inline(actor_name, escape_sep=True) or clean_aid or "（未知）"
        kind_s = _sanitize_kind(kind)
        summary = _sanitize_inline(text)[:60]
        path = self.rel_dir / f"{safe}.md"
        if not path.exists():
            _atomic_write(path, (
                f"# {title}\n\n"
                f"- ID：{clean_aid}\n"
                f"- 首次：{dt:%Y-%m-%d %H:%M}\n"
                f"- 最近：{dt:%Y-%m-%d %H:%M}\n"
                f"- 互动：1 次\n"
                f"- 亲密度：0.05\n\n"
                f"## 最近互动\n\n"
                f"- {dt:%Y-%m-%d %H:%M} [{kind_s}] {summary}\n"
            ))
            return
        body = path.read_text(encoding="utf-8")
        body = re.sub(r"^- 最近：.*$", f"- 最近：{dt:%Y-%m-%d %H:%M}", body, count=1, flags=re.M)
        body = re.sub(r"^- 互动：(\d+) 次$", lambda m: f"- 互动：{int(m.group(1)) + 1} 次", body, count=1, flags=re.M)
        body = re.sub(r"^- 亲密度：([0-9.]+)$", lambda m: f"- 亲密度：{min(1.0, float(m.group(1)) + 0.01):.2f}", body, count=1, flags=re.M)
        entry = f"- {dt:%Y-%m-%d %H:%M} [{kind_s}] {summary}\n"
        if re.search(r"^## 最近互动$", body, re.M):
            body = body.rstrip("\n") + "\n" + entry
        else:
            body += "\n## 最近互动\n\n" + entry
        _atomic_write(path, body)

    # ---------- 查询 ----------

    def recent(self, limit: int = 20, actor_id: str | None = None) -> list[dict]:
        out: list[dict] = []
        target_aid = _sanitize_inline(actor_id, escape_sep=True) if actor_id else None
        for path in sorted(self.inter_dir.glob("*.md"), reverse=True):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"<!-- id:([0-9a-f]{12}) -->\n## (\d{2}:\d{2}:\d{2}) · (.*?)(?: · (.*?))?\n(.*?)(?=\n<!-- id:|\Z)", text, re.S):
                iid, hh, who, aid, rest = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
                if target_aid and aid != target_aid:
                    continue
                resp = re.search(r"\*\*她\*\*：\n\n(.*?)\n\n", rest, re.S)
                stim = re.search(r"\*\*观众\*\*：\n\n(.*?)\n\n", rest, re.S)
                out.append({
                    "id": iid, "time": hh, "day": path.stem,
                    "actor_name": who, "actor_id": aid,
                    "stimulus": (stim.group(1) if stim else "").strip(),
                    "response": (resp.group(1) if resp else None),
                })
                if len(out) >= limit:
                    return out
        return out

    def stats(self) -> dict:
        total = answered = 0
        for path in self.inter_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            total += len(re.findall(r"<!-- id:[0-9a-f]{12} -->", text))
            answered += len(re.findall(r"\*\*她\*\*：", text))
        return {"interactions": total, "answered": answered, "actors": len(list(self.rel_dir.glob("*.md")))}

    def recover(self) -> list[str]:
        """列出所有待回应的 block id，供补录。"""
        pending = []
        for path in self.inter_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"<!-- id:([0-9a-f]{12}) -->\n(.*?)(?=\n<!-- id:|\Z)", text, re.S):
                if "**她**：" not in m.group(2):
                    pending.append(m.group(1))
        return pending

    def close(self) -> None:
        pass
