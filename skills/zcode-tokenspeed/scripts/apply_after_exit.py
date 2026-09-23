#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode 退出后自动打补丁看护（计划任务 / 插件开关同步调用，勿手动常跑）
轮询等待 ZCode.exe 全部退出 → 按期望状态应用/还原补丁（重打包级需文件未被占用）
→ 重启 ZCode → 记录日志后退出。日志: scripts/_apply_after_exit.log

两种调用方式：
  1) 无参数（本地计划任务）：应用 档位配置 + TPS 状态栏 + 滑条 + 拉取按钮（见 DEFAULT_TASKS）
  2) `--want=<键>=on|off`（插件 sync.py 传入，可多个）：只处理指定开关，off 走 --revert

取消方式: schtasks /Delete /TN ZCodePatchApply /F（并删除本脚本）

退出码约定（与 zcode_patcher.py 一致）：0=成功、1=有项目失败/等待超时、2=预检失败（ZCode 仍在运行）。
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


# 配置键 -> zcode_patcher.py 参数（与 sync.py 的 PATCHES 一一对应，顺序也保持一致）
# 漏一个键的后果：`--want=<键>=on` 会被 parse_wants 当「未知开关」忽略，
# 于是那个开关**开不起来也关不干净**（enhance_prompt 就漏过这一条）。
PATCH_ARGS = {
    "reasoning_config": ["--reasoning-config"],
    "usage_chart": ["--usage-chart"],
    "model_width": ["--model-width"],
    "tps_footer": ["--tps-footer"],
    "thought_slider": ["--thought-slider"],
    "enhance_prompt": ["--enhance-prompt"],
    "model_puller": ["--model-puller"],
    "core_patch": [],
}
# 未收到 --want 时的默认动作（本地计划任务用）：档位配置 + 三个重打包级补丁
DEFAULT_TASKS = [["--reasoning-config"], ["--tps-footer"], ["--thought-slider"], ["--model-puller"]]


def parse_wants(argv: list[str]):
    """解析 sync.py 传入的 --want=<key>=on|off。
    返回 [(args, revert), ...]；没有任何 --want 时返回 None（走 DEFAULT_TASKS）。
    历史问题：早期版本忽略 --want，一律按"全部打开"处理 —— 于是把开关关掉并不会真正还原。"""
    tasks = []
    for a in argv:
        if not a.startswith("--want="):
            continue
        key, _, val = a[len("--want="):].partition("=")
        key = key.strip()
        if key not in PATCH_ARGS:
            log(f"忽略未知开关: {a}")
            continue
        tasks.append((PATCH_ARGS[key], val.strip().lower() in ("off", "false", "0", "no")))
    return tasks or None


def main() -> int:
    log("看护启动，等待 ZCode 退出…")
    waited = 0
    while zcode_running():
        time.sleep(POLL_SEC)
        waited += POLL_SEC
        if waited >= MAX_WAIT_SEC:
            log("等待超时（24h），放弃")
            return 1

    tasks = parse_wants(sys.argv[1:])
    if tasks is None:
        tasks = [(args, False) for args in DEFAULT_TASKS]
    desc = "、".join(("还原 " if rev else "应用 ") + (" ".join(a) or "内核补丁") for a, rev in tasks)
    log(f"ZCode 已退出（等待 {waited}s），开始处理：{desc}")

    failed = 0
    for args, revert in tasks:
        cmd = [PYTHON, str(HERE / "zcode_patcher.py"), *args] + (["--revert"] if revert else [])
        r = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
        log(f"$ zcode_patcher.py {' '.join(cmd[2:])}  [exit={r.returncode}]\n{out}".rstrip())
        if r.returncode == 2:
            log("预检未通过（ZCode 在等待期间被重新拉起），本次放弃，不重启")
            return 1
        if r.returncode != 0:
            failed += 1

    res, exe = resolve_install()
    if exe is None:
        log("未找到 ZCode.exe（可用主脚本探测确认安装位置），请手动启动")
        return 1
    log(f"处理完成（失败 {failed} 项），重启 ZCode")
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
