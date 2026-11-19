# -*- coding: utf-8 -*-
"""start_live.py · 以独立进程启动直播主循环（脱离当前会话）

# 功能名：主循环后台启动器
# 生效条件：lingshu_companion 目录；config.local.json 有房间号
# 子功能：DETACHED_PROCESS 启动、日志落 _live.log、记录 PID 到 _live.pid
# 执行：python start_live.py [--stop]
# 验证方式：启动后 _live.pid 有值、_live.log 出现"接 B站弹幕"
# 不适用条件：仅 Windows；不处理桌面通知
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "_live.log"
ERR = ROOT / "_live.err"
PID_FILE = ROOT / "_live.pid"


def stop() -> None:
    if not PID_FILE.exists():
        print("无 PID 文件")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True)
        print("已停止 PID", pid)
    except Exception as e:
        print("停止失败:", e)
    finally:
        PID_FILE.unlink(missing_ok=True)


def start() -> None:
    import json
    cfg_path = ROOT / "config.local.json"
    room = 0
    if cfg_path.exists():
        room = (json.loads(cfg_path.read_text(encoding="utf-8"))
                .get("bilibili", {}).get("room_id", 0))
    if not room:
        print("config.local.json 缺 room_id")
        return

    env = {**os.environ, "PYTHONUTF8": "1",
           "NO_PROXY": "localhost,127.0.0.1,::1",
           "PYTHONIOENCODING": "utf-8"}
    out = open(LOG, "w", encoding="utf-8", buffering=1)
    err = open(ERR, "w", encoding="utf-8", buffering=1)

    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS

    p = subprocess.Popen(
        [sys.executable, "-u", "main.py",
         "--bilibili", str(room), "--idle-after", "90", "--voice"],
        cwd=str(ROOT), env=env, stdout=out, stderr=err,
        creationflags=flags, close_fds=True,
    )
    PID_FILE.write_text(str(p.pid))
    print("已启动 PID", p.pid, " 房间", room)
    print("日志:", LOG)


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop()
    else:
        start()
