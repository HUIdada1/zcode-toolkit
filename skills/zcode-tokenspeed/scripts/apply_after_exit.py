#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zcode-tokenspeed 退出后看护（由 sync.py 自动启动，勿手动常跑）

重打包级补丁（TPS 状态栏 / 拉取按钮）需要 app.asar 未被占用才能改，所以：
  轮询等待 ZCode 完全退出 → 按期望状态应用/还原对应补丁 → 写日志后退出

用法：apply_after_exit.py --want=tps_footer=on --want=model_puller=off
日志：scripts/_sync.log
"""

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHER = HERE / "zcode_patcher.py"
LOG = HERE / "_sync.log"
PIDFILE = HERE / "_watchdog.pid"
POLL_SEC = 3
MAX_WAIT_SEC = 24 * 3600
CREATE_NO_WINDOW = 0x08000000

PATCH_ARGS = {
    "tps_footer": ["--tps-footer"],
    "model_puller": ["--model-puller"],
}


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [看护] {msg}\n")
    except Exception:
        pass


def zcode_running() -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ZCode.exe"],
                             capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW).stdout or ""
        return "ZCode.exe" in out
    out = subprocess.run(["pgrep", "-f", "ZCode"], capture_output=True, text=True).stdout or ""
    return bool(out.strip())


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW).stdout or ""
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def another_watchdog_alive() -> bool:
    try:
        pid = int(PIDFILE.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    return pid != os.getpid() and pid_alive(pid)


def check_state(args) -> str:
    r = subprocess.run([sys.executable, str(PATCHER), *args, "--check"],
                       capture_output=True, text=True, cwd=str(HERE), timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    if "未打" in out:
        return "off"
    if "已打" in out:
        return "on"
    return "unknown"


def apply(wants: dict) -> None:
    for key, want in wants.items():
        args = PATCH_ARGS.get(key)
        if not args:
            continue
        state = check_state(args)
        if state == "unknown":
            log(f"{key}: 状态未知，跳过")
            continue
        if want and state == "off":
            revert = False
        elif not want and state == "on":
            revert = True
        else:
            log(f"{key}: 已是期望状态（{'开' if want else '关'}），无需处理")
            continue
        cmd = [sys.executable, str(PATCHER), *args] + (["--revert"] if revert else [])
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE), timeout=300)
        log(f"$ zcode_patcher.py {' '.join(args)}{' --revert' if revert else ''} -> "
            f"{'ok' if r.returncode == 0 else 'FAIL'}\n{(r.stdout or '') + (r.stderr or '')}".rstrip())


def main() -> None:
    wants = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--want="):
            body = arg[len("--want="):]
            key, _, val = body.partition("=")
            if key in PATCH_ARGS and val in ("on", "off"):
                wants[key] = val == "on"
    if not wants:
        return
    if another_watchdog_alive():
        log("已有看护在运行，本次退出")
        return
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        log(f"看护启动，目标: {wants}，等待 ZCode 退出…")
        waited = 0
        while zcode_running():
            time.sleep(POLL_SEC)
            waited += POLL_SEC
            if waited >= MAX_WAIT_SEC:
                log("等待超时（24h），放弃")
                return
        log(f"ZCode 已退出（等待 {waited}s），开始应用")
        apply(wants)
        log("应用完成，下次启动 ZCode 生效")
    finally:
        try:
            PIDFILE.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
