# -*- coding: utf-8 -*-
"""test_store.py · MdStore 的验证单元

# 功能名：MD 数据层的落库与回填验证
# 生效条件：pytest；临时目录可写
# 子功能：block 写入 / 回填不新增 / 关系累积 / 重启持久 / recover 列出待应答
# 执行：pytest test_store.py -q
# 验证方式：5 组断言，对应 MD 方案的三条硬要求
# 不适用条件：不测并发；不测上万条性能
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from store import MdStore


@pytest.fixture
def s():
    d = tempfile.mkdtemp(prefix="lsmd_")
    store = MdStore(d)
    yield store, Path(d)
    store.close()
    shutil.rmtree(d, ignore_errors=True)


def _day_text(root: Path) -> str:
    files = sorted((root / "interactions").glob("*.md"))
    assert files, "当日 Markdown 未生成"
    return "".join(f.read_text(encoding="utf-8") for f in files)


def test_stimulus_writes_block_immediately(s):
    store, root = s
    iid = store.record_stimulus(actor_id="u1", actor_name="小花", kind="danmaku", text="你好呀")
    assert len(iid) == 12

    text = _day_text(root)
    assert f"<!-- id:{iid} -->" in text
    assert "**观众**：" in text
    assert "你好呀" in text
    assert "待回应" in text
    assert "**她**：" not in text


def test_response_backfills_without_new_block(s):
    store, root = s
    iid = store.record_stimulus(actor_id="u1", actor_name="小花", kind="danmaku", text="在吗")
    ok = store.record_response(iid, response="在的呀", acts=["看向弹幕", "微笑"], mood="happy", latency_ms=1200)
    assert ok is True

    text = _day_text(root)
    assert text.count(f"<!-- id:{iid} -->") == 1, "回填不应产生新 block"
    assert "已回应" in text
    assert "在的呀" in text
    assert "看向弹幕、微笑" in text
    assert "延迟：1200 ms" in text


def test_relation_md_accumulates(s):
    store, root = s
    for i in range(3):
        store.record_stimulus(actor_id="u1", actor_name="小花", kind="danmaku", text=f"第{i}句")
    rels = list((root / "relations").glob("*.md"))
    assert len(rels) == 1
    body = rels[0].read_text(encoding="utf-8")
    assert "互动：3 次" in body
    assert "小花" in body
    assert "第2句" in body


def test_data_survives_restart(s):
    store, root = s
    iid = store.record_stimulus(actor_id="u1", actor_name="小花", kind="danmaku", text="重启前")
    store.record_response(iid, response="重启后应该还在")
    store.close()

    store2 = MdStore(root)
    rows = store2.recent(10)
    assert len(rows) == 1
    assert rows[0]["stimulus"] == "重启前"
    assert rows[0]["response"] == "重启后应该还在"
    store2.close()


def test_recover_lists_pending(s):
    store, root = s
    a = store.record_stimulus(actor_id="u1", actor_name="小花", kind="danmaku", text="没回应的")
    b = store.record_stimulus(actor_id="u2", actor_name="小草", kind="danmaku", text="已回应的")
    store.record_response(b, response="回了")
    pending = store.recover()
    assert a in pending
    assert b not in pending
