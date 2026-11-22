# -*- coding: utf-8 -*-
"""bilibili.py · B站直播弹幕接入（M4）

# 功能名：B站弹幕/礼物事件源
# 生效条件：可访问 api.live.bilibili.com 与 broadcastlv 弹幕服务器；需真实房间号
# 子功能：
#   - room_init 取真实房间号（短号→长号）
#   - getDanmuInfo 取 token 与弹幕服务器列表
#   - WebSocket 认证 + 心跳 + zlib 解包
#   - 解析 DANMU_MSG / SEND_GIFT / INTERACT_WORD / SUPER_CHAT_MESSAGE
# 执行：
#   for ev in BilibiliDanmaku(room_id).events():
#       print(ev.kind, ev.actor_name, ev.text)
# 验证方式：连上后收到认证回复 op=8 ret=0；进房事件与弹幕能打印
# 不适用条件：
#   - 未登录（uid=0）也能取 token，但风控严格时会失败
#   - protover=2 只支持 zlib；若服务端强制 brotli(3) 需装 brotli
#   - 长连接断线由调用方重连，本模块不自动重试
# 设计参考：早期实现的 bilibili world（16 字节帧头 / 手写解包 / 高能榜限流思路），
#           协议为公开格式，此处按 Python 独立实现，未复用其代码。
"""
from __future__ import annotations

import json
import os
import struct
import threading
import time
import zlib
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://live.bilibili.com/"}

OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

HEADER = struct.Struct(">IHHII")   # 总长 / 头长 / 协议版本 / 操作码 / 序列号


@dataclass
class BiliEvent:
    kind: str            # danmaku | gift | enter | sc
    text: str
    actor_id: str | None = None
    actor_name: str | None = None
    raw: dict | None = None
    ts: float = 0.0


def _pack(op: int, body: bytes = b"", ver: int = 1) -> bytes:
    total = HEADER.size + len(body)
    return HEADER.pack(total, HEADER.size, ver, op, 1) + body


def _unpack(data: bytes) -> list[tuple[int, int, bytes]]:
    """拆包。返回 [(ver, op, body), ...]；zlib 包会递归展开。"""
    out: list[tuple[int, int, bytes]] = []
    offset = 0
    n = len(data)
    while offset + HEADER.size <= n:
        total, hlen, ver, op, _seq = HEADER.unpack_from(data, offset)
        if total < HEADER.size or offset + total > n:
            break
        body = data[offset + hlen: offset + total]
        if ver == 2 and op == OP_MESSAGE:
            try:
                out.extend(_unpack(zlib.decompress(body)))
            except zlib.error:
                pass
        elif ver == 3 and op == OP_MESSAGE:
            try:
                import brotli
                out.extend(_unpack(brotli.decompress(body)))
            except Exception:
                pass
        else:
            out.append((ver, op, body))
        offset += total
    return out



# ---- WBI 签名（B站 2023 起的接口签名，缺它 getDanmuInfo 返回 -352）----

MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29,
    28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25,
    54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "lingshu_companion"


def _cache_path() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "bili_token.json"


def _load_cache(room_id: int) -> dict | None:
    """token 有效期约 1 小时；命中则免去一次 getDanmuInfo（该接口限流严格）。"""
    try:
        target = int(room_id)
        if target <= 0:
            return None
        p = _cache_path()
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        matched_ids = {int(d.get("room_id") or 0)}
        if d.get("input_room_id") is not None:
            matched_ids.add(int(d.get("input_room_id") or 0))
        matched_ids.discard(0)
        if target not in matched_ids:
            return None
        if time.time() - float(d.get("ts", 0)) > 3000:      # 50 分钟
            return None
        if not d.get("token") or not d.get("hosts"):
            return None
        return d
    except Exception:
        return None


def _save_cache(
    room_id: int,
    token: str,
    hosts: list,
    mixin: str,
    buvid3: str,
    input_room_id: int | None = None,
) -> None:
    try:
        _cache_path().write_text(json.dumps({
            "room_id": int(room_id),
            "input_room_id": int(input_room_id) if input_room_id is not None else int(room_id),
            "token": token, "hosts": hosts,
            "mixin": mixin, "buvid3": buvid3, "ts": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _code_of(obj: dict) -> int:
    """安全取 code：0 是合法成功值，不能用 `or` 兜底。"""
    v = obj.get("code")
    try:
        return int(v) if v is not None else -1
    except (TypeError, ValueError):
        return -1


def _key_of(url: str) -> str:
    """从 wbi_img 的 URL 取文件名（去扩展名）。"""
    return url.rsplit("/", 1)[-1].split(".")[0]


def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_TAB if i < len(raw))[:32]


def wbi_query(params: dict, mixin: str) -> str:
    """参数加 wts、按 key 排序、拼 w_rid。"""
    import hashlib
    import re as _re
    import time as _t
    import urllib.parse as _up
    p = dict(params)
    p["wts"] = int(_t.time())
    q = _up.urlencode(sorted(p.items()))
    q = _re.sub(r"[!'()*]", "", q)
    w_rid = hashlib.md5((q + mixin).encode()).hexdigest()
    return q + "&w_rid=" + w_rid

class BilibiliDanmaku:
    def __init__(self, room_id: int, *, sessdata: str | None = None, timeout: float = 10.0):
        self.input_room_id = int(room_id)
        self.room_id = 0
        self.token = ""
        self.hosts: list[dict] = []
        self.sessdata = sessdata
        self.timeout = timeout
        self._stop = threading.Event()
        self.buvid3 = ""
        self.mixin = ""
        self.self_uid = 0
        self.title = ""
        self.living = False
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if sessdata:
            self.session.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
        self._warmed = False

    # ---- 准备 ----

    def _warmup(self) -> None:
        """访问首页取 buvid3 等风控 cookie；缺它会返回 -352。"""
        if self._warmed:
            return
        try:
            self.session.get("https://www.bilibili.com/", timeout=self.timeout)
        except Exception:
            pass
        self._warmed = True

    def _cookies(self) -> dict:
        return {"SESSDATA": self.sessdata} if self.sessdata else {}

    def bootstrap(self) -> int:
        """四步握手。getDanmuInfo 限流严格，命中缓存时跳过它。"""
        cached = _load_cache(self.input_room_id)
        if cached:
            self.room_id = int(cached["room_id"])
            self.token = cached["token"]
            self.hosts = cached["hosts"]
            self.mixin = cached.get("mixin", "")
            self.buvid3 = cached.get("buvid3", "")
            if self.buvid3:
                self.session.cookies.set("buvid3", self.buvid3, domain=".bilibili.com")
            print("  [bili] 使用缓存 token（避免重复握手限流）")
            # 补一次 nav 拿登录态与直播状态（nav 不限流）
            try:
                nav = self.session.get("https://api.bilibili.com/x/web-interface/nav",
                                       timeout=self.timeout).json()
                self.self_uid = int((nav.get("data") or {}).get("mid") or 0)
            except Exception:
                pass
            try:
                info = self.session.get(
                    "https://api.live.bilibili.com/room/v1/Room/get_info",
                    params={"room_id": self.room_id}, timeout=self.timeout).json()
                d0 = info.get("data") or {}
                self.title = d0.get("title") or ""
                self.living = int(d0.get("live_status") or 0) == 1
            except Exception:
                pass
            return self.room_id

        # ① buvid3
        j = self.session.get("https://api.bilibili.com/x/frontend/finger/spi",
                             timeout=self.timeout).json()
        buvid3 = ((j.get("data") or {}).get("b_3")) or ""
        if buvid3:
            self.session.cookies.set("buvid3", buvid3, domain=".bilibili.com")
        self.buvid3 = buvid3

        # ② WBI 密钥（未登录也返回）
        nav = self.session.get("https://api.bilibili.com/x/web-interface/nav",
                               timeout=self.timeout).json()
        wbi = ((nav.get("data") or {}).get("wbi_img")) or {}
        if not wbi.get("img_url") or not wbi.get("sub_url"):
            raise RuntimeError("nav 未返回 wbi_img，无法签名")
        self.mixin = mixin_key(_key_of(wbi["img_url"]), _key_of(wbi["sub_url"]))
        self.self_uid = int((nav.get("data") or {}).get("mid") or 0)

        # ③ 真实房间号
        info = self.session.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info",
            params={"room_id": self.input_room_id}, timeout=self.timeout,
        ).json()
        if _code_of(info) != 0:
            raise RuntimeError(f"get_info 失败: {info.get('code')} {info.get('message')}")
        d0 = info.get("data") or {}
        self.room_id = int(d0.get("room_id") or 0)
        self.title = d0.get("title") or ""
        self.living = int(d0.get("live_status") or 0) == 1

        # ④ WBI 签名的 getDanmuInfo
        q = wbi_query({"id": self.room_id, "type": 0}, self.mixin)
        dm = self.session.get(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?" + q,
            headers={"Referer": f"https://live.bilibili.com/{self.room_id}"},
            timeout=self.timeout,
        ).json()
        if _code_of(dm) != 0:
            code = dm.get("code")
            if code == -352:
                raise RuntimeError(
                    "getDanmuInfo 被限流(-352)：短时间内多次握手所致。"
                    "等 2-5 分钟再试；有缓存 token 时会自动跳过此步。")
            raise RuntimeError(f"getDanmuInfo 失败: {code} {dm.get('message')}")
        self.token = ((dm.get("data") or {}).get("token")) or ""
        self.hosts = ((dm.get("data") or {}).get("host_list")) or []
        if self.token and self.hosts:
            _save_cache(
                self.room_id, self.token, self.hosts, self.mixin, self.buvid3,
                input_room_id=self.input_room_id,
            )
        return self.room_id

    # 兼容旧调用
    def resolve_room(self) -> int:
        return self.room_id or self.bootstrap()

    def fetch_token(self) -> None:
        if not self.token:
            self.bootstrap()

    # ---- 连接 ----

    def events(self) -> Iterator[BiliEvent]:
        from websockets.sync.client import connect

        if not self.token:
            self.bootstrap()

        host = self.hosts[0]
        url = f"wss://{host['host']}:{host['wss_port']}/sub"
        auth = json.dumps({
            "uid": self.self_uid,          # 登录态 uid；0 会被服务端当作无效连接
            "roomid": self.room_id,
            "protover": 3,                 # 3 = brotli；2 = zlib
            "platform": "web",
            "type": 2,
            "buvid": self.buvid3,
            "key": self.token,
        }).encode()

        ws_headers = dict(HEADERS)
        ws_headers["Origin"] = "https://live.bilibili.com"
        with connect(url, additional_headers=ws_headers, open_timeout=self.timeout,
                     max_size=None) as ws:
            ws.send(_pack(OP_AUTH, auth))
            last_beat = 0.0
            while not self._stop.is_set():
                now = time.time()
                if now - last_beat > 25:
                    ws.send(_pack(OP_HEARTBEAT))
                    last_beat = now
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                if isinstance(raw, str):
                    raw = raw.encode()
                for ver, op, body in _unpack(raw):
                    if op == OP_AUTH_REPLY:
                        try:
                            j = json.loads(body or b"{}")
                            if j.get("code") not in (0, None):
                                raise RuntimeError(f"认证失败: {j}")
                        except json.JSONDecodeError:
                            pass
                        continue
                    if op != OP_MESSAGE:
                        continue
                    try:
                        yield from self._parse(body)
                    except Exception:
                        continue

    def _parse(self, body: bytes) -> Iterator[BiliEvent]:
        try:
            j = json.loads(body)
        except json.JSONDecodeError:
            return
        cmd = j.get("cmd", "")
        now = time.time()

        if cmd.startswith("DANMU_MSG"):
            info = j.get("info") or []
            if len(info) < 3:
                return
            text = info[1]
            user = info[2]
            yield BiliEvent(
                kind="danmaku", text=str(text),
                actor_id=str(user[0]) if user and len(user) > 0 else None,
                actor_name=str(user[1]) if user and len(user) > 1 else None,
                raw=j, ts=now,
            )
        elif cmd == "SEND_GIFT":
            d = j.get("data") or {}
            yield BiliEvent(
                kind="gift",
                text=f"{d.get('uname','')} 投喂 {d.get('num',1)} 个 {d.get('giftName','')}",
                actor_id=str(d.get("uid")) if d.get("uid") else None,
                actor_name=d.get("uname"), raw=j, ts=now,
            )
        elif cmd == "INTERACT_WORD":
            d = j.get("data") or {}
            if d.get("msg_type") == 1:
                yield BiliEvent(
                    kind="enter", text="进入了直播间",
                    actor_id=str(d.get("uid")) if d.get("uid") else None,
                    actor_name=d.get("uname"), raw=j, ts=now,
                )
        elif cmd == "SUPER_CHAT_MESSAGE":
            d = j.get("data") or {}
            u = d.get("user_info") or {}
            uid = d.get("uid")
            yield BiliEvent(
                kind="sc", text=str(d.get("message", "")),
                actor_id=str(uid) if uid else None,
                actor_name=u.get("uname"), raw=j, ts=now,
            )

    def stop(self) -> None:
        self._stop.set()


def load_config(root: str = ".") -> dict:
    """读取 config.local.json（不入库）。缺失返回空字典。"""
    from pathlib import Path
    p = Path(root) / "config.local.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def probe(room_id: int, sessdata: str | None = None, seconds: float = 20.0) -> None:
    """自检：连上后打印收到的事件，seconds 秒后停止。"""
    d = BilibiliDanmaku(room_id, sessdata=sessdata)
    print("解析房间:", d.resolve_room())
    d.fetch_token()
    print("token 长度:", len(d.token), " 服务器:", [h["host"] for h in d.hosts][:3])
    t = threading.Timer(seconds, d.stop)
    t.daemon = True
    t.start()
    n = 0
    for ev in d.events():
        n += 1
        print(f"  [{ev.kind}] {ev.actor_name}: {ev.text[:60]}")
        if n >= 30:
            break
    print("共收到", n, "条")


if __name__ == "__main__":
    import sys
    probe(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
