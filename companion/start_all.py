# -*- coding: utf-8 -*-
"""start_all.py · 一键启动直播全套（独立于 DSH 会话）

# 功能名：直播系统一键启动
# 生效条件：Windows；desktop_pet 与 lingshu_companion 目录就位
# 子功能：
#   - 按依赖顺序启动：桥 8789 → 模型服务 8770 → 桌宠 → 对话页面 8792 → 直播主循环
#   - 启动前检查端口，已在跑就跳过（可反复执行）
#   - 用 CREATE_NEW_CONSOLE 让进程独立于当前终端，关掉窗口也不影响
# 执行：python start_all.py   或双击 启动桌宠.bat
# 验证方式：python start_all.py --status 能看到五项全部 ok
# 不适用条件：
#   - DSH 重启不影响这些进程（它们有独立控制台）
#   - TTS 是 Docker 容器，由 Docker Desktop 管理，不在此脚本内
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PET = Path(r"D:\2_ai\desktop_pet")
APP = Path(r"D:\2_ai\lingshu_companion")
ELECTRON = Path(r"D:\2_ai\_ext\airi\node_modules\.pnpm\electron@43.8.4_supports-color@10.2.2\node_modules\electron\dist\electron.exe")

PORTS = {8789: "桥", 8770: "模型服务", 8792: "对话页面", 8791: "桌宠控制",
         8793: "服务配置", 8795: "模型控制", 8010: "TTS"}


def listening(port: int) -> bool:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        return any(f":{port}" in l and "LISTENING" in l for l in out.splitlines())
    except Exception:
        return False


def spawn(cmd: list[str], cwd: Path, title: str) -> bool:
    """独立控制台启动，关掉当前窗口也不影响。"""
    try:
        flags = 0x00000010 if sys.platform == "win32" else 0   # CREATE_NEW_CONSOLE
        subprocess.Popen(cmd, cwd=str(cwd), creationflags=flags,
                         close_fds=True, shell=False)
        print(f"  ✓ {title}")
        return True
    except Exception as e:
        print(f"  ✗ {title}: {e}")
        return False


def status() -> int:
    print("=== 端口状态 ===")
    ok = 0
    for port, name in PORTS.items():
        up = listening(port)
        if up:
            ok += 1
        print(f"  {port} {name:8s} {'ok' if up else 'DOWN'}")
    print()
    print(f"  {ok}/{len(PORTS)} 在线")
    return 0 if ok == len(PORTS) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看状态")
    args = ap.parse_args()

    if args.status:
        return status()

    print("=== 启动直播全套 ===")

    if listening(8789):
        print("  - 桥 8789 已在跑，跳过")
    else:
        spawn(["node", "bridge-server.mjs"],
              Path(r"D:\2_ai\_ext\airi\packages\plugin-airi-bridge"), "桥 8789")

    if listening(8770):
        print("  - 模型服务 8770 已在跑，跳过")
    else:
        spawn([sys.executable, "serve.py"], PET, "模型服务 8770")

    # 等模型服务起来，桌宠才能加载
    import time
    for _ in range(20):
        if listening(8770):
            break
        time.sleep(0.3)

    if listening(8791):
        print("  - 桌宠已在跑，跳过")
    else:
        if ELECTRON.exists():
            spawn([str(ELECTRON), "."], PET, "桌宠")
        else:
            print("  ✗ 找不到 electron.exe")

    for _ in range(30):
        if listening(8791):
            break
        time.sleep(0.4)

    if listening(8792):
        print("  - 对话页面 8792 已在跑，跳过")
    else:
        spawn([sys.executable, "-u", "viewer.py"], APP, "对话页面 8792")

    for port, script, label in [(8793, "config_server.py", "服务配置 8793"),
                                 (8795, "model_server.py", "模型控制 8795")]:
        if listening(port):
            print(f"  - {label} 已在跑，跳过")
        else:
            spawn([sys.executable, "-u", script], APP, label)

    time.sleep(2)
    print()
    print("=== 启动直播主循环 ===")
    rc = subprocess.run([sys.executable, "start_live.py"], cwd=str(APP)).returncode
    if rc != 0:
        print("  主循环启动失败，检查 config.local.json")

    import time as _t
    _t.sleep(12)
    print()
    status()
    print()
    print("对话页面: http://127.0.0.1:8792")
    print("停主循环: python start_live.py --stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
