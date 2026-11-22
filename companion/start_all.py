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
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
REPO = APP.parent
PET = Path(os.environ.get("COMPANION_PET_DIR") or (REPO / "pet"))
BRIDGE_DIR = Path(os.environ.get("COMPANION_BRIDGE_DIR") or (REPO / "bridge"))


def _find_electron() -> Path | None:
    env = os.environ.get("ELECTRON_BIN")
    if env and Path(env).exists():
        return Path(env)
    exe = "electron.exe" if sys.platform == "win32" else "electron"
    cmd = "electron.cmd" if sys.platform == "win32" else "electron"
    for cand in (
        PET / "node_modules" / "electron" / "dist" / exe,
        PET / "node_modules" / ".bin" / cmd,
    ):
        if cand.exists():
            return cand
    found = shutil.which("electron")
    return Path(found) if found else None

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
    elif (BRIDGE_DIR / "bridge-server.mjs").exists():
        spawn(["node", "bridge-server.mjs"], BRIDGE_DIR, "桥 8789")
    else:
        print(f"  ✗ 找不到桥服务 {BRIDGE_DIR / 'bridge-server.mjs'}（可设置 COMPANION_BRIDGE_DIR）")

    if listening(8770):
        print("  - 模型服务 8770 已在跑，跳过")
    elif (PET / "serve.py").exists():
        spawn([sys.executable, "serve.py"], PET, "模型服务 8770")
    else:
        print(f"  ✗ 找不到模型服务 {PET / 'serve.py'}（可设置 COMPANION_PET_DIR）")

    # 等模型服务起来，桌宠才能加载
    import time
    for _ in range(20):
        if listening(8770):
            break
        time.sleep(0.3)

    if listening(8791):
        print("  - 桌宠已在跑，跳过")
    else:
        electron = _find_electron()
        if electron:
            spawn([str(electron), "."], PET, "桌宠")
        else:
            print("  ✗ 找不到 electron（请在 pet/ 执行 npm install，或设置 ELECTRON_BIN）")

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
