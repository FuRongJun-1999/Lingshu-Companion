# -*- coding: utf-8 -*-
"""main.py · 主循环（M1）

# 功能名：最小闭环主循环（感知 → 落库 → 回应 → 回填）
# 生效条件：store.py / perceive.py / speak.py 可用
# 子功能：交互模式 / 单条模式；每步打印落库状态；Ctrl-C 安全退出
# 执行：
#   python main.py                         # 交互
#   python main.py --say "你好"            # 单条（便于测试）
#   python main.py --data ./data --mode echo
# 验证方式：
#   - 交互输入后 data/interactions/<今天>.md 出现 block 且状态为已回应
#   - kill 后重跑 --stats 数据仍在
# 不适用条件：
#   - M1 的回应是回声/极简规则，不是真正的人格生成（见 M2）
#   - 未接 TTS / VTS
"""
from __future__ import annotations

import argparse
import threading
import sys
import time
from pathlib import Path

from perceive import Stimulus, bilibili_source, console_source, one
from bilibili import load_config
from body import Body
from idle import load_idle
from scripts import ScriptBook
from speak import speak
from store import MdStore, _fmt_acts


_BOOK: ScriptBook | None = None
_IDLE = None
_BODY: Body | None = None


def _book(store: MdStore) -> ScriptBook:
    global _BOOK
    global _IDLE
    if _BOOK is None:
        _BOOK = ScriptBook.load(store.root)
        _IDLE = load_idle(store.root)
        print(f"  [台词库] {_BOOK.stats()['total']} 条   冷场库 {_IDLE.total()} 条")
    return _BOOK


def scripted_reply(store: MdStore, ev: Stimulus, recent: list[str]):
    """走 speak 层（模板 + 检索 + 裁决）。"""
    r = speak(_book(store), ev.text, kind=ev.kind, actor_name=ev.actor_name,
              actor_id=ev.actor_id, recent=recent, idle_pool=_IDLE)
    if r.text is None:
        return None, [], "neutral", r
    return r.text, r.acts, r.mood, r


def echo_reply(ev: Stimulus) -> tuple[str, list[str], str]:
    """M1 占位回应：返回 (台词, 动作, 情绪)。"""
    return f"（回声）你说：{ev.text}", [], "neutral"


def rule_reply(ev: Stimulus) -> tuple[str, list[str], str]:
    """极简规则，让人能感觉到'她在回话'。"""
    t = ev.text
    if any(k in t for k in ("你好", "在吗", "hi", "hello")):
        return "在的，你好呀。", ["看向镜头", "微笑"], "happy"
    if "谢谢" in t or "喜欢" in t:
        return "谢谢……被这么说，有点不好意思。", ["歪头", "微笑"], "shy"
    if t.rstrip().endswith(("？", "?")):
        return "这个问题我得想想——先记下来，等下认真回答你。", ["歪头疑惑"], "think"
    if ev.kind == "idle":
        return "……屋里还有人在吗？我就先在这儿待着。", ["看向弹幕"], "neutral"
    return "嗯，我听着呢。", ["点头"], "neutral"


REPLIERS = {"echo": echo_reply, "rule": rule_reply, "script": scripted_reply}


_RECENT: list[str] = []


def _body(enable: bool) -> Body | None:
    global _BODY
    if not enable:
        return None
    if _BODY is None:
        _BODY = Body()
    return _BODY


class IdleWatchdog:
    """冷场看门狗：N 秒无输入则触发一次 idle，随后重新计时。

    # 功能名：自动冷场触发
    # 生效条件：idle_seconds > 0
    # 子功能：每次 kick() 重置计时；超时调 on_idle，完成后自动再计时
    # 执行：wd = IdleWatchdog(60, cb); wd.kick(); ...; wd.stop()
    # 验证方式：--idle-after 5 跑 20 秒应触发 3~4 次
    # 不适用条件：回调在后台线程执行，与 input() 的提示符会交错
    """

    def __init__(self, seconds: int, on_idle):
        self.seconds = seconds
        self.on_idle = on_idle
        self._timer = None
        self._lock = threading.Lock()
        self._stopped = False
        self.fires = 0

    def kick(self) -> None:
        with self._lock:
            if self._stopped or self.seconds <= 0:
                return
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._stopped:
                return
        self.fires += 1
        print("\n  [冷场] " + str(self.seconds) + "s 无输入，第 " + str(self.fires) + " 次")
        try:
            self.on_idle()
        except Exception as e:
            print("  [冷场] 出错: " + str(e))
        finally:
            self.kick()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer:
                self._timer.cancel()


class HealthWatchdog:
    """TTS 真探针看门狗。转交文档警告 /health 会假健康，必须实测合成。

    # 功能名：TTS 合成健康监控
    # 生效条件：Body 已启用且 TTS 服务可达
    # 子功能：每 interval 秒发一句短文本实测合成；失败连续 3 次标记 degraded
    # 执行：hw = HealthWatchdog(body, 300); hw.start()
    # 验证方式：停掉 8010 后 3 次内应打印 degraded
    # 不适用条件：只报警，不自动重启容器（重启策略需人工确认）
    """

    def __init__(self, body: Body, interval: int = 300):
        self.body = body
        self.interval = interval
        self._stop = threading.Event()
        self.degraded = False
        self.checks = 0

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                r = self.body.health()
                self.checks += 1
                was = self.degraded
                self.degraded = bool(r.get("degraded"))
                if self.degraded and not was:
                    print("\n  [体检] TTS 异常: ok=" + str(r.get("ok"))
                          + " ms=" + str(r.get("ms"))
                          + " streak=" + str(r.get("fail_streak")))
                    print("  [体检] 建议重启 TTS 容器：docker restart cortico-tts")
                elif was and not self.degraded:
                    print("\n  [体检] TTS 已恢复")
            except Exception as e:
                print("  [体检] 探针异常: " + str(e))

    def stop(self) -> None:
        self._stop.set()


def handle(store: MdStore, ev: Stimulus, replier, body: Body | None = None) -> None:
    t0 = time.time()
    iid = store.record_stimulus(
        actor_id=ev.actor_id, actor_name=ev.actor_name, kind=ev.kind, text=ev.text,
    )
    print(f"  [落库] id={iid} kind={ev.kind}")

    r = None
    if replier is scripted_reply:
        text, acts, mood, r = scripted_reply(store, ev, _RECENT)
        if text is None:
            print(f"  [裁决] 全部淘汰（{r.rejected} 条），选择沉默")
            return
    else:
        text, acts, mood = replier(ev)
    latency = int((time.time() - t0) * 1000)
    ok = store.record_response(iid, response=text, acts=acts, mood=mood, latency_ms=latency)
    print(f"  [回填] {'ok' if ok else 'FAIL'}  {text}")
    if acts:
        print(f"  [动作] {_fmt_acts(acts)}")
    if body is not None:
        ex = getattr(r, "expr", []) or []
        mo = getattr(r, "motion", []) or []
        res = body.speak(text, acts, expr=ex, motion=mo)
        print(f"  [身体] 动作={'ok' if res['acted'] else '跳过'}  发声={'ok' if res['spoke'] else '失败'}")

    # 写灵枢情景层（异步，失败不影响直播；不依赖 --voice 开关）
    # 冷场自语不写：一场几十条会把情景层刷满
    if ev.kind != "idle":
        try:
            from lingshu import enqueue
            enqueue(ev.actor_name or "未知", ev.text, text,
                    kind=ev.kind, mood=mood, acts=acts,
                    importance=0.55 if ev.kind == "danmaku" else 0.45)
        except Exception as _e:
            print(f"  [灵枢] 投递失败: {type(_e).__name__}: {_e}")
    _RECENT.append(text)
    del _RECENT[:-10]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="灵枢伴侣 · 最小主循环")
    ap.add_argument("--data", default="./data", help="数据目录")
    ap.add_argument("--mode", choices=list(REPLIERS), default="script")
    ap.add_argument("--say", help="单条模式：直接注入一句话")
    ap.add_argument("--stats", action="store_true", help="只打印统计")
    ap.add_argument("--voice", action="store_true", help="开启 TTS 发声（需 8010 服务）")
    ap.add_argument("--idle", action="store_true", help="单次冷场（测试 idle 库）")
    ap.add_argument("--bilibili", type=int, metavar="ROOM", help="B站房间号：接真实弹幕")
    ap.add_argument("--bilibili-only", action="store_true", help="只连弹幕，不读控制台")
    ap.add_argument("--idle-after", type=int, default=0, metavar="SEC",
                    help="交互模式下 N 秒无输入自动冷场（0=关闭）")
    args = ap.parse_args(argv)

    store = MdStore(args.data)
    replier = REPLIERS[args.mode]
    body = _body(args.voice)

    if args.stats:
        print(store.stats())
        pending = store.recover()
        print("待回应:", pending[:5], "…" if len(pending) > 5 else "")
        store.close()
        return 0

    if args.idle:
        handle(store, one("", kind="idle"), replier, body)
        store.close()
        return 0

    if args.say:
        handle(store, one(args.say, actor_id="console:local", actor_name="控制台"), replier, body)
        # 一次性模式：等灵枢写完再退出，否则 daemon 线程被杀、记忆丢失
        try:
            from lingshu import flush as _ls_flush
            n = _ls_flush(timeout=15)
            if n:
                print(f"  [灵枢] 已写入 {n} 条")
        except Exception:
            pass
        store.close()
        return 0

    print("灵枢伴侣 —— 输入文字回车；quit 退出")
    print(f"数据目录: {Path(args.data).resolve()}   模式: {args.mode}")
    if args.idle_after > 0:
        print(f"冷场：{args.idle_after}s 无输入自动自语")

    wd = IdleWatchdog(
        args.idle_after,
        lambda: handle(store, one("", kind="idle"), replier, body),
    )
    wd.kick()

    hw = None
    if body is not None:
        hw = HealthWatchdog(body, interval=300)
        hw.start()
        r0 = body.health()
        print(f"  [体检] TTS 首次探针 ok={r0['ok']} {r0['ms']}ms {r0['bytes']}B")

    # 事件源：B站弹幕 or 控制台
    if args.bilibili:
        # config.local.json 在仓根，不是模块同级
        _root = Path(__file__).resolve().parent.parent
        if not (_root / "config.local.json").exists():
            _root = Path(__file__).resolve().parent
        cfg = load_config(str(_root))
        cfg_bili = cfg.get("bilibili") or {}
        sessdata = cfg_bili.get("sessdata") or None
        room = args.bilibili or cfg_bili.get("room_id")
        print(f"接 B站弹幕：房间 {room}  登录态={'有' if sessdata else '无(可能被风控)'}")
        source = bilibili_source(int(room), sessdata)
    else:
        source = console_source()

    try:
        for ev in source:
            wd.kick()
            # 单条失败不影响整场：记下错误继续下一条
            try:
                handle(store, ev, replier, body)
            except Exception as e:
                import traceback
                print(f"  [错误] 处理失败: {type(e).__name__}: {e}")
                traceback.print_exc()
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        wd.stop()
        if hw:
            hw.stop()
        try:
            from lingshu import flush as _ls_flush
            _ls_flush(timeout=5)
        except Exception:
            pass
        print("统计:", store.stats(), "  冷场触发 " + str(wd.fires) + " 次")
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
