#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zcode-tokenspeed 安装自检（doctor）——只读，不改任何文件。

用途：在「插件装了但功能没生效」时，一条命令跑出完整链路诊断：

    python doctor.py

它会按顺序检查并打印结论：
  1. Python 环境（版本 / 解释器路径 / 是否满足 3.10+）
  2. ZCode 安装位置、app.asar、客户端版本
  3. ZCode 是否正在运行（运行中无法应用重打包级补丁）
  4. 插件是否真的「已安装」与「已启用」（两步缺一不可，钩子只在启用后进入新会话）
  5. 插件配置里的开关有没有被保存过（没保存过 → 同步脚本按设计什么也不做）
  6. hooks/hooks.json 是否存在、命令是否可执行
  7. 钩子到底跑没跑过（_sync.last / _sync.log 心跳与日志）
  8. 七项补丁当前在客户端里的实际状态

输出末尾给出「结论」，直接指出卡在哪一环、下一步该做什么。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHER = HERE / "zcode_patcher.py"
LOG = HERE / "_sync.log"
STAMP = HERE / "_sync.last"

PLUGIN_NAME = "zcode-tokenspeed"
REPO_NAME = "zcode-toolkit"
MIN_PY = (3, 10)

PATCH_KEYS = [
    ("reasoning_config", "思考档位配置"),
    ("usage_chart", "用量页去截断"),
    ("model_width", "模型弹窗加宽"),
    ("tps_footer", "TPS 状态栏"),
    ("thought_slider", "思考强度滑条"),
    ("enhance_prompt", "增强提示词"),
    ("model_puller", "模型拉取按钮"),
]
REPACK_KEYS = {"tps_footer", "thought_slider", "enhance_prompt", "model_puller"}

OK, WARN, BAD, INFO = "  [√]", "  [!]", "  [×]", "  [i]"


# ------------------------------------------------------------------ 基础工具

def hr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 62 - len(title)))


def _load_patcher():
    """把同目录的 zcode_patcher 当模块用（复用它的安装探测逻辑）。"""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import zcode_patcher  # noqa: PLC0415

    return zcode_patcher


def _storage_roots() -> list[Path]:
    """ZCode 数据目录候选（默认 ~/.zcode，config 里 storage.dir 可改写）。"""
    roots: list[Path] = [Path.home() / ".zcode"]
    for cand in (Path.home() / ".zcode" / "cli" / "config.json",):
        cfg = _read_json(cand)
        d = ((cfg or {}).get("storage") or {}).get("dir") or ""
        if isinstance(d, str) and d.strip():
            p = Path(os.path.expanduser(d.strip()))
            if p not in roots:
                roots.append(p)
    return roots


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_configs() -> list[Path]:
    out: list[Path] = []
    for root in _storage_roots():
        p = root / "cli" / "config.json"
        if p.is_file() and p not in out:
            out.append(p)
    return out


def _installed_plugin_dirs() -> list[Path]:
    """在 <数据目录>/cli/plugins/marketplaces/<市场>/<插件名> 下找已安装副本。"""
    found: list[Path] = []
    for root in _storage_roots():
        base = root / "cli" / "plugins" / "marketplaces"
        if not base.is_dir():
            continue
        for market in sorted(base.iterdir()):
            cand = market / PLUGIN_NAME
            if (cand / ".zcode-plugin" / "plugin.json").is_file():
                found.append(cand)
    return found


def _prefix_entries(cfg, section: str) -> dict:
    """取 config.json 里 plugins.<section> 下键名以插件名开头的条目。"""
    block = ((cfg or {}).get("plugins") or {}).get(section) or {}
    if not isinstance(block, dict):
        return {}
    return {k: v for k, v in block.items() if str(k).startswith(PLUGIN_NAME)}


def _manifest_version(root: Path) -> str:
    for rel in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
        m = _read_json(root / rel)
        if isinstance(m, dict) and m.get("version"):
            return str(m["version"])
    return "?"


def _enabled_state() -> bool:
    enabled = False
    for cfg_path in _find_configs():
        for _, v in _prefix_entries(_read_json(cfg_path), "enabledPlugins").items():
            enabled = enabled or bool(v)
    return enabled


def _saved_options() -> dict:
    """合并所有 config.json 里保存过的插件开关。"""
    merged: dict = {}
    for cfg_path in _find_configs():
        opts = _prefix_entries(_read_json(cfg_path), "options")
        for _, v in opts.items():
            if isinstance(v, dict):
                merged.update(v)
    return merged


# ------------------------------------------------------------------ 各项检查

def check_python() -> bool:
    hr("1. Python 环境")
    ok = sys.version_info >= MIN_PY
    print(f"{OK if ok else BAD} {sys.version.split()[0]}  ({sys.executable})")
    if not ok:
        print(f"{BAD} 需要 Python {MIN_PY[0]}.{MIN_PY[1]} 或更高版本")
    else:
        print(f"{INFO} 钩子命令是 `python ... || python3 ...`；"
              "命令行里 `python --version` 能跑通即可被调用")
    return ok


def check_zcode() -> tuple[bool, Path | None, str]:
    hr("2. ZCode 客户端")
    try:
        zp = _load_patcher()
        targets = zp.resolve_target(None)
    except SystemExit as e:
        print(f"{BAD} 未找到 ZCode：{e}")
        return False, None, ""
    except Exception as e:  # pragma: no cover
        print(f"{BAD} 探测失败：{e!r}")
        return False, None, ""

    if not targets:
        print(f"{BAD} 未找到 zcode.cjs，请确认 ZCode 已安装")
        return False, None, ""

    asar = targets[0].parent.parent / "app.asar"
    ver = ""
    try:
        ver = str(zp.asar_version(asar) or "")
    except Exception:
        pass

    for t in targets:
        a = t.parent.parent / "app.asar"
        if a.is_file():
            print(f"{OK} {a}  ({a.stat().st_size / 1048576:.0f} MB)")
        else:
            print(f"{BAD} {a} 不存在")
    if ver:
        print(f"{INFO} 客户端版本：{ver}")
    return asar.is_file(), asar, ver


def check_running() -> bool:
    hr("3. 客户端运行状态")
    running = False
    try:
        running = _load_patcher().zcode_running()
    except Exception:
        pass
    if running:
        print(f"{WARN} ZCode 正在运行")
        print("       字节级补丁可热改；重打包级补丁必须等 ZCode 完全退出后才由看护进程应用")
    else:
        print(f"{OK} ZCode 未运行（可以安全打补丁）")
    return running


def check_plugin() -> tuple[bool, list[Path], bool]:
    hr("4. 插件安装与启用")
    dirs = _installed_plugin_dirs()
    if not dirs:
        print(f"{BAD} 没有找到已安装的插件目录")
        print(f"       预期位置：<数据目录>/cli/plugins/marketplaces/<市场>/{PLUGIN_NAME}/")
        print(f"       → 「设置 → 插件 → 创建 → 添加插件市场」添加 {REPO_NAME} 后安装")
        return False, [], False

    for d in dirs:
        print(f"{OK} 安装位置：{d}")
        print(f"{INFO} 清单版本：{_manifest_version(d)}")

    cfgs = _find_configs()
    if not cfgs:
        print(f"{BAD} 读不到 {Path.home() / '.zcode' / 'cli' / 'config.json'}")
        return True, dirs, False

    enabled = False
    for cfg_path in cfgs:
        en = _prefix_entries(_read_json(cfg_path), "enabledPlugins")
        print(f"{INFO} {cfg_path}")
        if not en:
            print(f"{BAD}   plugins.enabledPlugins 里没有 {PLUGIN_NAME}* —— 插件未登记启用状态")
        for k, v in en.items():
            print(f"{OK if v else BAD}   {k} = {v}")
            enabled = enabled or bool(v)

    if enabled:
        print(f"{OK} 插件已启用")
    else:
        print(f"{BAD} 插件未处于「已启用」——**钩子不会进入会话，自动化全部不会发生**")
        print("       → 「设置 → 插件 → 管理已安装」把该插件的开关打开")
    return True, dirs, enabled


def check_options() -> bool:
    hr("5. 插件开关是否已保存")
    saved = _saved_options()
    if not saved:
        print(f"{BAD} config.json 的 plugins.options 里没有 {PLUGIN_NAME}* —— 从未保存过配置")
        print("       → 点开插件 → 高级信息 → 配置 → 拨开开关 → 点「保存配置」")
        print(f"{WARN} 同步脚本只同步「显式保存过的开关」；没保存过就完全不动客户端文件")
        return False
    print(f"{INFO} 已保存的开关：")
    repack_on = []
    for key, label in PATCH_KEYS:
        if key in saved:
            on = bool(saved[key])
            tag = "（重打包级，需退出两次才可见）" if (on and key in REPACK_KEYS) else ""
            print(f"        {label:<14} {key} = {saved[key]}{tag}")
            if on and key in REPACK_KEYS:
                repack_on.append(key)
    if "core_patch" in saved:
        print(f"        {'思考档位内核补丁':<14} core_patch = {saved['core_patch']}")
    unknown = [k for k in saved if k not in dict(PATCH_KEYS) and k != "core_patch"]
    for k in unknown:
        print(f"        (未知键) {k} = {saved[k]}")
    if repack_on:
        print(f"{WARN} 其中 {len(repack_on)} 项是重打包级：要等 ZCode **完全退出**写入 app.asar，"
              "再启动一次才可见")
    print(f"{OK} 有已保存的开关")
    return True


def check_hook(dirs: list[Path]) -> bool:
    hr("6. 钩子文件")
    if not dirs:
        print(f"{BAD} 跳过：插件未安装")
        return False
    ok = False
    for d in dirs:
        hf = d / "hooks" / "hooks.json"
        if not hf.is_file():
            print(f"{BAD} {hf} 不存在")
            continue
        print(f"{OK} {hf}")
        events = ((_read_json(hf) or {}).get("hooks") or {})
        for ev, groups in events.items():
            for g in groups or []:
                for h in (g or {}).get("hooks") or []:
                    print(f"        {ev}  matcher={g.get('matcher', '(默认)')}")
                    print(f"        type={h.get('type')}  shell={h.get('shell')}  "
                          f"timeout={h.get('timeout') or h.get('timeoutMs')}")
                    print(f"        command: {h.get('command', '')}")
                    ok = True
        sync = d / "skills" / PLUGIN_NAME / "scripts" / "sync.py"
        print(f"{OK if sync.is_file() else BAD} 目标脚本：{sync}")
    return ok


def check_heartbeat(dirs: list[Path]) -> bool:
    hr("7. 钩子执行痕迹")
    fired = False
    for d in dirs:
        scripts = d / "skills" / PLUGIN_NAME / "scripts"
        stamp, logf = scripts / STAMP.name, scripts / LOG.name
        if stamp.is_file():
            fired = True
            print(f"{OK} 心跳 {stamp} → "
                  f"{stamp.read_text(encoding='utf-8', errors='replace').strip()}")
        else:
            print(f"{WARN} 没有心跳文件 {stamp}")
        if logf.is_file():
            fired = True
            lines = logf.read_text(encoding="utf-8", errors="replace").splitlines()
            print(f"{OK} 日志 {logf}（{len(lines)} 行，末尾 5 行）")
            for ln in lines[-5:]:
                print(f"        {ln}")
        else:
            print(f"{WARN} 没有日志文件 {logf}")
    if not fired:
        print(f"{BAD} 已安装副本里没有任何执行痕迹 —— 钩子**从未运行过**")
        print("       常见原因：插件没启用 / 启用后没开过新会话 / python 不在 PATH /")
        print("                 装的是改动前的旧版本（旧版钩子没有心跳文件）")
    if STAMP.is_file():
        print(f"{INFO} 本目录（源码仓库）心跳："
              f"{STAMP.read_text(encoding='utf-8', errors='replace').strip()}")
    return fired


def check_patches() -> None:
    hr("8. 补丁在客户端里的实际状态")
    if not PATCHER.is_file():
        print(f"{BAD} 找不到 {PATCHER}")
        return
    try:
        r = subprocess.run([sys.executable, str(PATCHER), "--all", "--check"],
                           capture_output=True, text=True, cwd=str(HERE), timeout=180)
    except Exception as e:
        print(f"{BAD} 执行失败：{e!r}")
        return
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if not out:
        print(f"{BAD} 没有任何输出（退出码 {r.returncode}）")
        return
    for ln in out.splitlines():
        print(f"    {ln}")


def verdict(py_ok: bool, zcode_ok: bool, has_plugin: bool, enabled: bool,
            saved: bool, hook_ok: bool, fired: bool) -> None:
    hr("结论")
    if not py_ok:
        print("Python 版本不满足要求 —— 先装 Python 3.10+，并确保 `python --version` 能跑通。")
        return
    if not zcode_ok:
        print("没找到 ZCode 客户端 —— 补丁没有可注入的目标。")
        return
    if not has_plugin:
        print("插件没装成功。按 README「方式 A」重新添加插件市场并安装。")
        return
    if not enabled:
        print("★ 卡点：插件已安装但**未启用**。")
        print("  ZCode 只在插件启用后，把它的 Hook 注册进**新会话**。")
        print("  → 「设置 → 插件 → 管理已安装」打开开关，然后开一个新会话。")
        return
    if not saved:
        print("★ 卡点：插件已启用，但**配置从未保存过**。")
        print("  同步脚本只同步「用户显式保存过的开关」，没保存过就完全不动客户端文件。")
        print("  → 点开插件 → 高级信息 → 配置 → 拨开开关 → 保存配置 → 退出并重启 ZCode。")
        return
    if not hook_ok:
        print("★ 卡点：钩子文件缺失或结构不对 —— 重新安装插件（升级到最新版）。")
        return
    if not fired:
        print("★ 卡点：开关已保存，但**钩子从未运行过**。")
        print("  依次确认：① 保存配置后是否**完全退出**（托盘右键退出）并重启过 ZCode；")
        print("            ② 命令行 `python --version` 是否可用（macOS/Linux 试 `python3 --version`）；")
        print("            ③ 已安装副本是不是最新版（「检查更新」）。")
        print("  → 兜底：不依赖钩子，直接用命令行打补丁：")
        print(f'       python "{PATCHER}" --all')
        return
    print("链路完整：插件已启用、开关已保存、钩子跑过。")
    print("若功能仍不可见，注意**重打包级补丁需要两次启动**：")
    print("  第一次启动 → 钩子登记待办 → 完全退出 ZCode（看护进程改写 app.asar）→ 第二次启动才生效。")
    print("第 8 节里显示「未打」的重打包项，退出 ZCode 后再看一次即可确认。")


# ------------------------------------------------------------------ 入口

def main() -> int:
    ap = argparse.ArgumentParser(description="zcode-tokenspeed 安装自检（只读，不改任何文件）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（便于贴给别人看）")
    args = ap.parse_args()

    dirs = _installed_plugin_dirs()
    enabled = _enabled_state()
    saved = bool(_saved_options())

    if args.json:
        print(json.dumps({
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "plugin_dirs": [str(d) for d in dirs],
            "manifest_versions": [_manifest_version(d) for d in dirs],
            "enabled": enabled,
            "options_saved": saved,
            "saved_options": _saved_options(),
            "hook_fired": any(
                (d / "skills" / PLUGIN_NAME / "scripts" / STAMP.name).is_file() for d in dirs),
        }, ensure_ascii=False, indent=2))
        return 0

    print("zcode-tokenspeed 自检报告（只读，不会修改任何文件）")
    py_ok = check_python()
    zcode_ok, _, _ = check_zcode()
    check_running()
    has_plugin, dirs, enabled = check_plugin()
    saved = check_options()
    hook_ok = check_hook(dirs)
    fired = check_heartbeat(dirs)
    check_patches()
    verdict(py_ok, zcode_ok, has_plugin, enabled, saved, hook_ok, fired)
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
