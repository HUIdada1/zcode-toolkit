#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zcode-tokenspeed 插件开关同步（由 SessionStart hook 调用）

把客户端补丁同步到插件配置里的期望状态：
  字节级补丁（用量图表 / 弹窗加宽）—— 立即应用或还原，无需重启 ZCode
  重打包级补丁（TPS 状态栏 / 拉取按钮）—— 交给退出后看护，ZCode 退出时自动应用

只在用户显式保存过某个开关（配置里存在该键）时才动它；从未保存过则完全不操作，
避免插件在用户没表态时改动客户端文件。

诊断日志：scripts/_sync.log
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHER = HERE / "zcode_patcher.py"
WATCHDOG = HERE / "apply_after_exit.py"
CONFIG = Path.home() / ".zcode" / "cli" / "config.json"
PLUGIN_ID = "zcode-tokenspeed@dev-default-22da16fd"
LOG = HERE / "_sync.log"

# 配置键 -> (zcode_patcher.py 参数, 是否重打包级)
PATCHES = [
    ("reasoning_config", ["--reasoning-config"], False),   # 3.14+ 档位配置（配置侧原生）
    ("usage_chart", ["--usage-chart"], False),
    ("model_width", ["--model-width"], False),
    ("tps_footer", ["--tps-footer"], True),
    ("model_puller", ["--model-puller"], True),
    ("core_patch", [], False),                             # ≤3.11 内核补丁
]

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def log(msg: str) -> None:
    try:
        import time
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _search(node, path="", depth=0):
    """在配置树里找本插件的配置对象（键为插件 id 的那一层）。"""
    if depth > 6 or not isinstance(node, dict):
        return None
    entry = node.get(PLUGIN_ID)
    if isinstance(entry, dict) and entry:
        return entry, f"{path}.{PLUGIN_ID}"
    for key, val in node.items():
        if isinstance(val, dict):
            got = _search(val, f"{path}.{key}", depth + 1)
            if got:
                return got
    return None


def _coerce(raw):
    """把宿主可能写入的字符串布尔归一成 bool。"""
    out = {}
    for key, val in raw.items():
        if isinstance(val, bool):
            out[key] = val
        elif isinstance(val, str) and val.strip().lower() in ("true", "false"):
            out[key] = val.strip().lower() == "true"
    return out


def read_options():
    """读本插件的开关值。返回 (options, source)；source 为 None 表示没找到配置。"""
    # 宿主若把插件配置注入 hook 环境，优先用环境变量
    env_opts = _coerce({key[len("ZCODE_PLUGIN_CONFIG_"):]: val
                        for key, val in os.environ.items()
                        if key.startswith("ZCODE_PLUGIN_CONFIG_")})
    if env_opts:
        return env_opts, "env"

    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"读配置失败: {exc}")
        return {}, None
    found = _search(cfg, CONFIG.name)
    if found:
        return _coerce(found[0]), found[1]
    # 没找到条目：把 plugins 下的键名记下来，便于确认宿主的实际存储位置
    plugins = cfg.get("plugins") if isinstance(cfg, dict) else None
    log(f"配置里没有 {PLUGIN_ID}；plugins 现有键: "
        f"{sorted(plugins) if isinstance(plugins, dict) else plugins}")
    return {}, None


def check_state(args) -> str:
    """跑 --check 判断当前注入状态：on / off / unknown。"""
    r = subprocess.run([sys.executable, str(PATCHER), *args, "--check"],
                       capture_output=True, text=True, cwd=str(HERE), timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    if "未打" in out:
        return "off"
    if "已打" in out:
        return "on"
    return "unknown"


def run_patcher(args, revert: bool) -> bool:
    cmd = [sys.executable, str(PATCHER), *args] + (["--revert"] if revert else [])
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE), timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    # zcode_patcher.py 拒绝改写时仍返回 0（只在输出里打 [!] 说明原因），必须看输出判定
    refused = any(mark in out for mark in ("锚点匹配异常", "拒绝", "[!]"))
    ok = r.returncode == 0 and not refused
    log(f"$ zcode_patcher.py {' '.join(args)}{' --revert' if revert else ''} -> "
        f"{'ok' if ok else ('拒绝改写' if refused else 'FAIL')}\n{out}".rstrip())
    return ok


def start_watchdog(wanted: dict) -> None:
    """启动退出后看护：等 ZCode 退出 → 应用重打包级补丁。"""
    args = [f"--want={k}={'on' if v else 'off'}" for k, v in wanted.items()]
    flags = (DETACHED_PROCESS | CREATE_NO_WINDOW) if os.name == "nt" else 0
    subprocess.Popen([sys.executable, str(WATCHDOG), *args], cwd=str(HERE),
                     creationflags=flags, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    log(f"已启动退出后看护: {' '.join(args)}")


def main() -> None:
    opts, source = read_options()
    if source is None:
        return  # 用户从未保存过开关，保持现状

    explicit = {k: v for k, v in opts.items() if isinstance(v, bool)}
    if not explicit:
        log(f"配置来自 {source}，但没有可用的布尔开关值: {opts}")
        return

    changed, deferred, failed = [], {}, []
    for key, args, repack in PATCHES:
        want = explicit.get(key)
        if want is None:
            continue  # 该开关用户没表态，不碰
        state = check_state(args)
        if state == "unknown":
            failed.append(f"{key}(状态未知)")
            continue
        if want and state == "off":
            need_revert = False
        elif not want and state == "on":
            need_revert = True
        else:
            continue  # 已一致
        if repack:
            deferred[key] = want
        elif run_patcher(args, revert=need_revert):
            changed.append(f"{key}→{'开' if want else '关'}")
        else:
            failed.append(f"{key}(执行失败)")

    if deferred:
        start_watchdog(deferred)

    parts = []
    if changed:
        parts.append("已生效: " + "、".join(changed))
    if deferred:
        parts.append("ZCode 退出时自动应用: " + "、".join(
            f"{k}→{'开' if v else '关'}" for k, v in deferred.items()))
    if failed:
        parts.append("未处理: " + "、".join(failed))
    if parts:
        log(f"同步结果 —— {' | '.join(parts)}")
        print(f"[zcode-tokenspeed] {' | '.join(parts)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # hook 绝不能因自身异常打断会话启动
        log(f"sync 异常: {exc!r}")
