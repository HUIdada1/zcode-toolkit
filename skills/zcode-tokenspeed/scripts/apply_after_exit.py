#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode 退出后自动打补丁看护（计划任务调用，勿手动常跑）
轮询等待 ZCode.exe 全部退出 → 注入 TPS 状态栏 + 滑条 + 模型拉取按钮（重打包级，需文件未被占用）
→ 重启 ZCode → 记录日志后退出。日志: scripts/_apply_after_exit.log
取消方式: schtasks /Delete /TN ZCodePatchApply /F（并删除本脚本）

退出码约定（与 zcode_patcher.py 一致）：0=成功、1=有项目失败、2=预检失败（ZCode 仍在运行）。
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "_apply_after_exit.log"
POLL_SEC = 3
MAX_WAIT_SEC = 24 * 3600
# pythonw 无控制台，子进程若是控制台程序（tasklist 等）会每次新弹 cmd 窗口
CREATE_NO_WINDOW = 0x08000000
PYTHON = sys.executable or "python"


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


def resolve_install() -> tuple[Path | None, Path | None]:
    """复用主脚本的跨平台探测拿到 (asar 目录, ZCode 可执行文件)。"""
    try:
        sys.path.insert(0, str(HERE))
        import zcode_patcher as zp
        for cjs in zp.discover():
            res = cjs.parent.parent
            if (res / "app.asar").is_file():
                exe = res.parent / "ZCode.exe"
                return res, (exe if exe.is_file() else None)
    except Exception as e:
        log(f"安装探测失败: {type(e).__name__}: {e}")
    return None, None


def main() -> int:
    log("看护启动，等待 ZCode 退出…")
    waited = 0
    while zcode_running():
        time.sleep(POLL_SEC)
        waited += POLL_SEC
        if waited >= MAX_WAIT_SEC:
            log("等待超时（24h），放弃")
            return 1
    log(f"ZCode 已退出（等待 {waited}s），开始注入 TPS + 滑条 + 拉取按钮")

    for args in (["--tps-footer"], ["--thought-slider"], ["--model-puller"]):
        r = subprocess.run([PYTHON, str(HERE / "zcode_patcher.py"), *args],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
        log(f"$ zcode_patcher.py {' '.join(args)}  [exit={r.returncode}]\n{out}".rstrip())
        if r.returncode == 2:
            log("预检未通过（ZCode 在等待期间被重新拉起），本次放弃，不重启")
            return 1

    res, exe = resolve_install()
    if exe is None:
        log("未找到 ZCode.exe（可用主脚本探测确认安装位置），请手动启动")
        return 1
    log("注入完成，重启 ZCode")
    if zcode_running():
        log("检测到 ZCode 已再次运行，跳过重启")
        return 0
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent),
                         creationflags=0x00000008)   # DETACHED_PROCESS
    except Exception as e:
        log(f"重启 ZCode 失败（请手动启动）: {e}")
        return 1
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
