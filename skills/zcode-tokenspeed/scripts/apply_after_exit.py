#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode 退出后自动打补丁看护（计划任务调用，勿手动常跑）
轮询等待 ZCode.exe 全部退出 → 注入 TPS 状态栏与模型拉取按钮（重打包级，需文件未被占用）
→ 重启 ZCode → 记录日志后退出。日志: scripts/_apply_after_exit.log
取消方式: schtasks /Delete /TN ZCodePatchApply /F（并删除本脚本）
"""

import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "_apply_after_exit.log"
ASAR_DIR = Path("D:/ZCode/resources")
EXE = Path("D:/ZCode/ZCode.exe")
POLL_SEC = 3
MAX_WAIT_SEC = 24 * 3600
# pythonw 无控制台，子进程若是控制台程序（tasklist 等）会每次新弹 cmd 窗口
CREATE_NO_WINDOW = 0x08000000


def log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def zcode_running() -> bool:
    # 用 bytes 检索，不走 text 解码：tasklist 输出是 GBK，而本机 Python 为 UTF-8
    # 模式，text=True 会在读线程里抛 UnicodeDecodeError → stdout 变空 → 误判「已退出」。
    # 同理不带 /FI：从 Git Bash/MSYS 环境启动时 "/FI" 会被路径转换破坏。
    out = subprocess.run(["tasklist"], capture_output=True,
                         creationflags=CREATE_NO_WINDOW).stdout or b""
    return b"ZCode.exe" in out


def main() -> None:
    log("看护启动，等待 ZCode 退出…")
    waited = 0
    while zcode_running():
        time.sleep(POLL_SEC)
        waited += POLL_SEC
        if waited >= MAX_WAIT_SEC:
            log("等待超时（24h），放弃")
            return
    log(f"ZCode 已退出（等待 {waited}s），开始注入 TPS + 滑条 + 拉取按钮")
    for args in (["--tps-footer"], ["--thought-slider"], ["--model-puller"]):
        r = subprocess.run(["python", str(HERE / "zcode_patcher.py"), *args],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
        log(f"$ zcode_patcher.py {' '.join(args)}\n{out}".rstrip())
        if "文件被占用" in out:
            log("注入被文件占用中断（ZCode 可能在等待期间被重新拉起），本次放弃，不重启")
            return
    log("注入完成，重启 ZCode")
    if zcode_running():
        log("检测到 ZCode 已再次运行，跳过重启")
        return
    try:
        EXE.exists() and subprocess.Popen([str(EXE)], cwd=str(EXE.parent),
                                          creationflags=0x00000008)  # DETACHED_PROCESS
    except Exception as e:
        log(f"重启 ZCode 失败（请手动启动）: {e}")
    log("DONE")


if __name__ == "__main__":
    main()
