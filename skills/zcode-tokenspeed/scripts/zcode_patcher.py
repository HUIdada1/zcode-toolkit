#!/usr/bin/env python3
"""
ZCode 客户端补丁工具
=====================

对本地 ZCode 安装打两类补丁（均自动探测安装位置、独立备份、幂等）：

一、思维强度透传（内核 zcode.cjs）
  让「不在内核白名单里的模型」也遵循 config.json 里配置的思维强度档位。
  原理：内核档位解析函数把档位翻译成线上参数
  ({anthropic:{effort,thinking}} / {openaiCompatible:{reasoningEffort}})，
  但参数表 providerOptionsByLevel 只给白名单模型（claude/glm/deepseek 等家族）下发；
  自定义供应商模型拿到的是空表，档位被选中却不产生任何请求参数。
  补丁在取参处加兜底：查表为空时直接用档位名合成参数——
  白名单模型不受影响（它们的表非空，?? 短路）。

  档位 -> anthropic thinking.budget_tokens 映射：
    low=4000  medium=8000  high=16000  xhigh=32000  max=32000  其它档名=16000
    disabled/none/off/nothink -> thinking disabled

  生效条件（v2 config.json 中该模型）：
    "reasoning": { "enabled": true, "variants": ["low","high","max"], "defaultVariant": "max" }

二、用量页去截断（app.asar，--usage-chart）
  设置→用量 的趋势图只画 Top6 模型、饼图只画 Top5 并把其余合并为「其他模型」。
  补丁解析 asar 头定位渲染文件，把截断表达式替换为全量版本，
  空格补齐到原字节长度后原地覆盖（asar 头/offset/unpacked 零改动）。
  原始字节备份在 app.asar.chart-patch.json，可整体还原。

三、TPS 统计栏（app.asar，--tps-footer）
  向渲染层 out/renderer/index.html 注入 zcode-tps.js（读 ServicePort 事件流得精确
  tok/s/首 token/out；脚本内不调用 port.start()，否则 3.12.2 会卡启动）：
  输入框工具栏常驻统计胶囊 ● 时间 · 首 token · tok/s · out（当前会话最近一轮，
  切换会话即消失），数据取自页面内 MessagePort 会话事件流，无常驻服务。
  胶囊右键可切换位置：输入框工具栏（默认）/ 会话顶部 sticky，localStorage 记忆。
  重打包级修改：整体重排 asar 目录、对改动文件重算 integrity。
  原件备份 app.asar.tps.bak，记录在 app.asar.tps-patch.json，可整体还原。

四、思考强度吸附滑条（app.asar，--thought-slider）
  向工具栏原生「思考级别」下拉旁注入 zcode-thought-slider.js：横向吸附拖拽条，
  档位动态取自原生状态探针（data-thought-levels，模型配几档吸几档），拖完经
  React fiber 直调原生 onValueChange（降级：模拟点开原生菜单按序点选），等价
  于用户点选菜单项 → 内核 session/setThoughtLevel，会话内即时生效。
  注入/备份/还原与 TPS 同链路：app.asar.slider.bak + app.asar.slider-patch.json。

五、模型拉取按钮（app.asar，--model-puller）
  设置页注入「⚡️ 自动拉取模型」按钮：拉取供应商 /models 接口 → 弹窗勾选 → 写入
  config.json（整读整写、已有条目原样保留，含手改的 reasoning.variants）。
  四处改动：out/renderer/ 新增 zcode-model-puller.js + index.html 挂载 +
  preload 暴露 3 个 IPC 方法（readConfigFile/writeConfigFile/fetchModelsFromUrl）+
  main 注册 3 个 IPC handler（读写 ~/.zcode/v2/config.json、代理拉模型列表）。
  前端脚本 vendored from HHQ-666/zcode-model-puller (MIT)；
  preload/main 锚点用语义字符串定位（exposeInMainWorld("zcode",{ / SaveMcpToUserDirectory），
  压缩符号经正则捕获，跨版本无需维护符号表。
  原件备份 app.asar.puller.bak，记录在 app.asar.puller-patch.json，可整体还原。
  另有命令行版 scripts/model_pull.py：不动 asar，直接同步 config.json。

用法：
  python zcode_patcher.py                       # 思维强度补丁：自动探测全部安装并打（幂等）
  python zcode_patcher.py --check               # 只看思维强度补丁状态
  python zcode_patcher.py --revert              # 还原内核备份
  python zcode_patcher.py --extract             # 提取当前内核锚点（新版本无已知锚点时）
  python zcode_patcher.py --usage-chart         # 用量页去截断（同样支持 --check/--revert）
  python zcode_patcher.py --tps-footer          # TPS 统计栏注入（同样支持 --check/--revert）
  python zcode_patcher.py --thought-slider      # 思考强度吸附滑条（同样支持 --check/--revert）
  python zcode_patcher.py --model-puller        # 模型拉取按钮注入（同样支持 --check/--revert）
  python zcode_patcher.py "D:\\ZCode"           # 只处理指定安装（安装根目录或 zcode.cjs 均可）

注意：ZCode 升级会覆盖 zcode.cjs 与 app.asar，升级后需重新执行对应补丁；
     打完补丁完全退出并重启 ZCode 后生效。
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

# 兜底合成器：档位名 -> 各协议命名空间的线上参数
# openai 侧按「原名透传」：智谱系自定义网关（如 workbuddy2api）的档位表是 low/high/max，
# 折算 max→xhigh 会被网关按 supported_efforts 降级成 high（max 档丢失）；
# 关闭档发 reasoning_effort:"off" 而非省略——省略会让上游落到默认档（glm-5.3 默认≈max，关不掉），
# 发 "off" 在带档位表的网关会被 floor 到最低支持档，网关特判 canDisableThinking 后即真·关闭。
HELPER = (
    'function zCfgEffort(e){'
    'let t=String(e).toLowerCase();'
    'if(t==="disabled"||t==="none"||t==="off"||t==="nothink")'
    'return{anthropic:{thinking:{type:"disabled"}},openaiCompatible:{reasoningEffort:"off"}};'
    'if(t==="enabled"||t==="on")'
    'return{anthropic:{effort:"high",thinking:{type:"enabled",budgetTokens:16e3}},'
    'openaiCompatible:{reasoningEffort:"high"}};'
    'let r={low:4e3,medium:8e3,high:16e3,xhigh:32e3,max:32e3}[t]??16e3,'
    'n={anthropic:{thinking:{type:"enabled",budgetTokens:r}},openaiCompatible:{},openai:{}};'
    '["low","medium","high","xhigh","max"].includes(t)&&(n.anthropic.effort=t);'
    '["none","minimal","low","medium","high","xhigh","max"].includes(t)&&'
    '(n.openaiCompatible.reasoningEffort=t,n.openai.reasoningEffort=t);'
    'return n}'
)

MARKER = "zCfgEffort"

# 已知版本的档位解析函数原文（全文唯一锚点；符号名随构建版本变化）。
# 新版本若两版锚点都匹配不上，按文档《自定义思考等级指南》重新提取锚点后加一版。
ANCHORS = {
    "3.8.1": (
        "function sD(e,t,r){if(!e)return;let n=G_e(e,r);"
        "if(!n?.enabled||n.levels.length===0)return;"
        "let o=t?.trim(),i=Fgo(e,o,n.levels);if(o&&!i)return;"
        "let a=i??gXe(n);"
        'return a?{level:a,providerOptions:n.providerOptionsByLevel?.[a]}:void 0}'
    ),
    "3.9.1": (
        "function AD(e,t,r){if(!e)return;let n=Zye(e,r);"
        "if(!n?.enabled||n.levels.length===0)return;"
        "let o=t?.trim(),i=fxo(e,o,n.levels);if(o&&!i)return;"
        "let a=i??utt(n);"
        'return a?{level:a,providerOptions:n.providerOptionsByLevel?.[a]}:void 0}'
    ),
    "3.9.2": (
        "function RD(e,t,r){if(!e)return;let n=Xye(e,r);"
        "if(!n?.enabled||n.levels.length===0)return;"
        "let o=t?.trim(),i=Sxo(e,o,n.levels);if(o&&!i)return;"
        "let a=i??ptt(n);"
        'return a?{level:a,providerOptions:n.providerOptionsByLevel?.[a]}:void 0}'
    ),
    "3.11.2": (
        "function mN(e,t,r){if(!e)return;let n=k2e(e,r);"
        "if(!n?.enabled||n.levels.length===0)return;"
        "let o=t?.trim(),i=CIo(e,o,n.levels);if(o&&!i)return;"
        "let s=i??_nt(n);"
        'return s?{level:s,providerOptions:n.providerOptionsByLevel?.[s]}:void 0}'
    ),
}


def replacement_for(anchor: str) -> str:
    """锚点函数前插入兜底合成器，返回处在查表后追加 ?? 兜底。
    兼容不同版本的局部变量名（3.8.1/3.9.1 用 a，3.11.2 用 s）。"""
    m = re.search(r"return (\w+)\?\{level:\1,providerOptions:n\.providerOptionsByLevel\?\.\[\1\]\}", anchor)
    if not m:
        raise ValueError(f"锚点返回表达式形态未识别：{anchor[-160:]}")
    var = m.group(1)
    patched_return = (
        f"return {var}?{{level:{var},"
        f"providerOptions:n.providerOptionsByLevel?.[{var}]??zCfgEffort({var})}}:void 0}}"
    )
    head = anchor[:m.start()]
    return HELPER + head + patched_return


# ---------------------------------------------------------------- 锚点自动提取

def extract_anchor(target: Path) -> str | None:
    """
    按结构特征（而非符号名）在内核中定位档位解析函数，返回可直接加入 ANCHORS 的锚点。
    特征：函数以 {level:...,providerOptions:...providerOptionsByLevel?.[...]}:void 0} 收尾。
    已打补丁的文件自动回退到 .bak 原始件提取。
    """
    bak = target.with_suffix(".cjs.bak")
    try:
        data = target.read_text(encoding="utf-8", errors="surrogatepass")
    except PermissionError:
        print(f"[!] 无权限读取 {target}")
        return None
    if MARKER in data and bak.is_file():
        data = bak.read_text(encoding="utf-8", errors="surrogatepass")
        print(f"[*] 当前文件已打补丁，改从原始备份提取锚点")

    candidates = set()
    start = 0
    while True:
        idx = data.find("providerOptionsByLevel?.[", start)
        if idx == -1:
            break
        start = idx + 1
        fstart = data.rfind("function ", 0, idx)
        if fstart == -1:
            continue
        brace = data.find("{", fstart)
        depth, j = 0, brace
        while j < len(data):
            if data[j] == "{":
                depth += 1
            elif data[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        cand = data[fstart:j + 1]
        if "{level:" in cand and cand.endswith("}:void 0}") and "function " not in cand[len("function "):]:
            candidates.add(cand.replace("??zCfgEffort(a)", ""))

    if not candidates:
        print("[!] 未找到符合结构特征的候选，目标函数形态可能已变，需人工分析")
        return None
    if len(candidates) > 1:
        print(f"[!] 找到 {len(candidates)} 个候选（应为 1），请人工甄别：")
        for c in candidates:
            print("    -", c[:150])
        return None
    return candidates.pop()


# ---------------------------------------------------------------- 安装位置探测

def _norm(s: str) -> str:
    return s.lower().replace(" ", "").replace("-", "")


def _from_running_processes(found: list[Path]) -> None:
    """1) 正在运行的 ZCode 进程路径（最准：用户实际在用哪个）"""
    if os.name != "nt":
        return
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object {$_.Path} | "
             "Select-Object -ExpandProperty Path -Unique"],
            capture_output=True, timeout=15, errors="replace",
        ).stdout or b""
    except Exception:
        return
    for line in out.decode(errors="replace").splitlines():
        line = line.strip()
        # ZCode.exe / ZCode Skin Manager 等都指向安装根目录
        if line.lower().endswith(".exe") and "zcode" in _norm(Path(line).name):
            found.append(Path(line).parent)


def _from_registry(found: list[Path]) -> None:
    """2) 注册表卸载信息里的 InstallLocation / DisplayIcon"""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                base = winreg.OpenKey(hive, sub)
            except OSError:
                continue
            with base:
                for i in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, i)) as k:
                            try:
                                name = winreg.QueryValueEx(k, "DisplayName")[0]
                            except OSError:
                                continue
                            if "zcode" not in _norm(str(name)):
                                continue
                            for val in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    v = str(winreg.QueryValueEx(k, val)[0])
                                except OSError:
                                    continue
                                p = Path(v.strip('"').split(",")[0].strip())
                                found.append(p if p.is_dir() else p.parent)
                                break
                    except OSError:
                        continue


def _from_common_dirs(found: list[Path]) -> None:
    """3) 常规安装目录：Windows Program Files 系 / macOS /Applications / Linux /opt、/usr/share"""
    bases = [os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)"),
             os.environ.get("ProgramW6432"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
             "/Applications",
             os.path.expanduser("~/Applications"),
             "/opt",
             "/usr/share"]
    for base in bases:
        if not base or not Path(base).is_dir():
            continue
        try:
            for entry in os.scandir(base):
                if not entry.is_dir() or "zcode" not in _norm(entry.name):
                    continue
                if entry.name.endswith(".app"):
                    found.append(Path(entry.path) / "Contents")   # macOS .app 包，资源在 Contents 下
                else:
                    found.append(Path(entry.path))
        except OSError:
            continue


def discover() -> list[Path]:
    """返回所有探测到的 zcode.cjs（去重、保序）"""
    roots: list[Path] = []
    for probe in (_from_running_processes, _from_registry, _from_common_dirs):
        try:
            probe(roots)
        except Exception:
            pass
    seen, result = set(), []
    for root in roots:
        cjs = root / "resources" / "glm" / "zcode.cjs"
        try:
            key = cjs.resolve()
        except OSError:
            key = cjs
        if cjs.is_file() and key not in seen:
            seen.add(key)
            result.append(cjs)
    return result


def resolve_target(arg: str | None) -> list[Path]:
    if not arg:
        return discover()
    p = Path(arg)
    # 显式路径兼容多种形态：安装根目录 / macOS 的 .app 包 / zcode.cjs 文件本身
    roots = [p, p / "Contents"] if p.name.lower().endswith(".app") else [p]
    for root in roots:
        cjs = root / "resources" / "glm" / "zcode.cjs"
        if cjs.is_file():
            return [cjs]
    raise SystemExit(f"[!] 指定路径下找不到 zcode.cjs：{arg}")


# ---------------------------------------------------------------- 单个目标处理

def process(target: Path, check_only: bool, revert: bool) -> None:
    backup = target.with_suffix(".cjs.bak")
    try:
        data = target.read_text(encoding="utf-8", errors="surrogatepass")
    except PermissionError:
        print(f"[!] 无权限读取 {target}（Program Files 需要管理员运行本脚本）")
        return

    state = "已打" if MARKER in data else ("已还原/未打" if backup.is_file() else "未打")
    print(f"[*] {target}")
    print(f"    {len(data):,} 字符 | 补丁: {state} | 备份: {'有' if backup.is_file() else '无'}")

    if revert:
        if not backup.is_file():
            print("    [.] 没有备份，跳过")
            return
        try:
            shutil.copyfile(backup, target)
            print("    [+] 已从备份还原")
        except PermissionError:
            print("    [!] 无权限写入，请用管理员身份运行本脚本")
        return

    if check_only:
        return

    if MARKER in data:
        print("    [=] 已打过补丁，跳过")
        return

    matched = [(ver, a) for ver, a in ANCHORS.items() if data.count(a) == 1]
    if len(matched) != 1:
        detail = ", ".join(f"{ver}={data.count(a)}" for ver, a in ANCHORS.items())
        print(f"    [!] 锚点匹配异常（{detail}，期望恰有一版=1），"
              f"版本可能不在已支持列表，按文档重新提取锚点后添加")
        return
    ver, anchor = matched[0]
    print(f"    [+] 匹配版本锚点: {ver}")

    try:
        if not backup.is_file():
            shutil.copyfile(target, backup)
        patched = data.replace(anchor, replacement_for(anchor))
        target.write_text(patched, encoding="utf-8", errors="surrogatepass", newline="\n")
    except PermissionError:
        print(f"    [!] 无权限写入（Program Files 需要管理员），未修改。"
              f"可用管理员身份的 PowerShell/Git Bash 重新运行本脚本")
        return

    ok = MARKER in target.read_text(encoding="utf-8", errors="surrogatepass")
    print(f"    [{'+' if ok else '!'}] 补丁{'写入成功' if ok else '写入失败'}"
          f"{'（备份 -> ' + str(backup) + '）' if backup.is_file() else ''}")


# ------------------------------------------------------- 用量页去截断补丁（asar 内同长度原地改字节）

# 每项：key=定位用的文件名特征，pattern=截断表达式原文，replacement=等价短替换（空格补齐）
USAGE_PATCHES = [
    {
        "key": "AppUsageDailyModelTrendChart",
        "pattern": b"n.models.slice(0,6)",
        "replacement": b"n.models",
        "desc": "每日趋势图：去掉 Top6 截断，全部模型出线",
    },
    {
        "key": "AppUsageModelUsagePieChart",
        "pattern": b"i=n.length>Q,a=i?Q-1:Q",
        "replacement": b"i=!1,a=1/0",
        "desc": "模型用量饼图：去掉 Top5+其他模型 合并，全部模型出块",
    },
]

# 模型选择弹窗加宽（同样长度原地覆盖；key=None 表示按内容特征定位文件，
# 不依赖 assets 文件名里的内容哈希，跨版本稳定）
WIDTH_PATCHES = [
    {
        "key": None,
        "pattern": b"j??`w-48`",
        "replacement": b"j??`w-80`",
        "desc": "渠道子菜单（选完供应商后的模型列表）宽度 192px → 320px",
    },
    {
        "key": None,
        "pattern": b":`w-48 max-h-72 overflow-y-auto`",
        "replacement": b":`w-80 max-h-72 overflow-y-auto`",
        "desc": "模型弹窗（无子菜单的扁平列表）宽度 192px → 320px",
    },
]


def _asar_header_index(asar: Path):
    """解析 asar 头，返回 [(文件路径, 绝对偏移, 尺寸), ...]。"""
    with open(asar, "rb") as f:
        head = f.read(16)
        header_size = struct.unpack("<I", head[4:8])[0]
        json_len = struct.unpack("<I", head[12:16])[0]
        f.seek(16)
        header = json.loads(f.read(json_len).decode("utf-8"))

    def walk(node, path):
        for name, ent in (node.get("files") or {}).items():
            p = f"{path}/{name}" if path else name
            if "files" in ent:
                yield from walk(ent, p)
            else:
                yield p, ent

    data_start = 8 + header_size
    return [(p, data_start + int(e["offset"]), e["size"])
            for p, e in walk(header, "") if not e.get("unpacked")]


def _load_sidecar(side: Path, asar_size: int | None = None) -> list[dict]:
    """读取补丁记录；兼容旧的单条格式。带 asar_size 指纹校验：
    ZCode 升级会整个覆盖 app.asar，旧记录的 offset 不再可信，失配即作废。"""
    if not side.is_file():
        return []
    data = json.loads(side.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "patches" in data:
        data = data["patches"]
    elif isinstance(data, dict):
        data = [data]
    if asar_size is not None:
        data = [r for r in data if r.get("asar_size") == asar_size]
    return data


def _asar_find_entry(header: dict, entry_path: str) -> dict | None:
    node = header
    for part in entry_path.split("/"):
        files = node.get("files") or {}
        if part not in files:
            return None
        node = files[part]
    return node


def _asar_sync_integrity(asar: Path, entry_path: str, new_bytes: bytes) -> None:
    """同长度原地交换 asar 头里该条目的 integrity 哈希串，使记录与内容一致。
    sha256 hex 定长（64），头部长度与全部 offset 零改动，保持零重打包特性。
    旧的图表补丁只改了内容未同步此处，Electron 默认不校验能跑，但记录不诚实——
    本函数在打补丁/已打/还原三条路径上都会被调用，自动修复历史遗留。"""
    with open(asar, "rb") as f:
        head = f.read(16)
        json_len = struct.unpack("<I", head[12:16])[0]
        f.seek(16)
        hdr = f.read(json_len)
    ent = _asar_find_entry(json.loads(hdr.decode("utf-8")), entry_path)
    itg = (ent or {}).get("integrity")
    if not itg:
        return
    new_itg = _asar_integrity(new_bytes)
    pairs = []
    if itg.get("hash") != new_itg["hash"]:
        pairs.append((itg["hash"], new_itg["hash"]))
    old_blocks, new_blocks = itg.get("blocks") or [], new_itg["blocks"]
    if len(old_blocks) == len(new_blocks):
        pairs.extend((a, b) for a, b in zip(old_blocks, new_blocks) if a != b)
    if not pairs:
        return
    with open(asar, "r+b") as f:
        for old, new in pairs:
            ob_, nb_ = old.encode(), new.encode()
            cnt = hdr.count(ob_)
            if cnt == 0:
                continue
            if cnt > 2:   # 与其他同内容条目共享哈希，全局替换会误伤，保守跳过
                print(f"    [!] {entry_path} 的 integrity 哈希出现 {cnt} 次（存在同内容条目），跳过同步")
                continue
            start = 0
            while True:
                i = hdr.find(ob_, start)
                if i < 0:
                    break
                f.seek(16 + i)
                f.write(nb_)
                start = i + len(nb_)


def process_usage_chart(asar: Path, check_only: bool, revert: bool) -> None:
    side = asar.with_name(asar.name + ".chart-patch.json")
    asar_size = asar.stat().st_size
    entries = _asar_header_index(asar)

    specs = []
    for item in USAGE_PATCHES:
        match = [(p, o, s) for p, o, s in entries if item["key"] in p]
        if len(match) != 1:
            print(f"[!] {asar}\n    {item['key']} 命中 {len(match)} 个文件（期望 1），该项跳过")
            continue
        specs.append((item, match[0]))

    if revert:
        saved = _load_sidecar(side, asar_size)
        if not saved:
            print(f"[.] {asar}\n    没有当前版本的 sidecar 备份，跳过")
            return
        with open(asar, "r+b") as f:
            for rec in saved:
                f.seek(rec["offset"])
                f.write(base64.b64decode(rec["original_b64"]))
        for rec in saved:
            _asar_sync_integrity(asar, rec["path"], base64.b64decode(rec["original_b64"]))
        side.unlink()
        print(f"[+] {asar}\n    已还原 {len(saved)} 处原始字节")
        return

    print(f"[*] {asar}")
    saved = _load_sidecar(side, asar_size)
    changed = False
    for item, (path, off, size) in specs:
        fname = path.split("/")[-1]
        with open(asar, "r+b") as f:
            f.seek(off)
            raw = f.read(size)
        if item["pattern"] not in raw:
            print(f"    [=] {fname} | 已打（{item['desc']}）")
            if not check_only:
                _asar_sync_integrity(asar, path, raw)
            continue
        cnt = raw.count(item["pattern"])
        if cnt != 1:
            print(f"    [!] {fname} | 截断表达式出现 {cnt} 次（期望 1），拒绝盲改")
            continue
        if check_only:
            print(f"    [ ] {fname} | 未打（{item['desc']}）")
            continue
        new = raw.replace(item["pattern"], item["replacement"], 1)
        padded = new + b" " * (len(raw) - len(new))   # 同长度原地覆盖，asar 头零改动
        if not any(r.get("offset") == off for r in saved):
            saved.append({
                "path": path,
                "offset": off,
                "size": size,
                "asar_size": asar_size,
                "original_b64": base64.b64encode(raw).decode(),
            })
        with open(asar, "r+b") as f:
            f.seek(off)
            f.write(padded)
            f.seek(off)
            back = f.read(size)
        ok = item["pattern"] not in back and len(back) == size
        if ok:
            _asar_sync_integrity(asar, path, padded)
        print(f"    [{'+' if ok else '!'}] {fname} | {'写入成功（' + item['desc'] + '）' if ok else '写入失败'}")
        changed = True
    if changed and not check_only:
        side.write_text(json.dumps({"patches": saved}, ensure_ascii=False), encoding="utf-8")
        print(f"    原始字节备份: {side.name}")


# ------------------------------------------------- 模型弹窗加宽（asar 内同长度原地改字节）

def process_model_width(asar: Path, check_only: bool, revert: bool) -> None:
    """模型选择弹窗加宽：把 Tailwind 宽度类 w-48(192px) 换成 w-80(320px)。
    与图表补丁同为「同长度字节级原地覆盖」（w-48/w-80 字面量等长），零重打包。
    文件按内容特征定位（不依赖 assets 文件名哈希），跨版本稳定。"""
    side = asar.with_name(asar.name + ".width-patch.json")
    asar_size = asar.stat().st_size

    if revert:
        saved = _load_sidecar(side, asar_size)
        if not saved:
            print(f"[.] {asar}\n    没有当前版本的 sidecar 备份，跳过")
            return
        with open(asar, "r+b") as f:
            for rec in saved:
                f.seek(rec["offset"])
                f.write(base64.b64decode(rec["original_b64"]))
        for rec in saved:
            _asar_sync_integrity(asar, rec["path"], base64.b64decode(rec["original_b64"]))
        side.unlink()
        print(f"[+] {asar}\n    已还原 {len(saved)} 处原始宽度字节")
        return

    entries = _asar_header_index(asar)
    print(f"[*] {asar}")
    saved = _load_sidecar(side, asar_size)
    changed = False

    for item in WIDTH_PATCHES:
        if item["key"] is not None:
            cands = [(p, o, s) for p, o, s in entries if item["key"] in p]
        else:
            cands = [(p, o, s) for p, o, s in entries
                     if p.startswith("out/renderer/") and p.endswith(".js")
                     and s and 1000 < s < 80_000_000]

        def _scan(needle: bytes):
            got = []
            for p, o, s in cands:
                with open(asar, "rb") as f:
                    f.seek(o)
                    raw = f.read(s)
                cnt = raw.count(needle)
                if cnt:
                    got.append((p, o, s, raw, cnt))
            return got

        hits = _scan(item["pattern"])
        if not hits:
            # 未命中可能是「已打」（锚点已被替换掉）——用替换后形态确认
            done = _scan(item["replacement"])
            if len(done) == 1 and done[0][4] == 1:
                print(f"    [=] {done[0][0].split('/')[-1]} | 已打（{item['desc']}）")
                if not check_only:
                    _asar_sync_integrity(asar, done[0][0], done[0][3])
                continue
            print(f"    [!] {item['desc']} | 未找到锚点（版本结构可能已变），跳过")
            continue
        if len(hits) > 1:
            print(f"    [!] {item['desc']} | 锚点在 {len(hits)} 个文件中出现（期望 1），拒绝盲改")
            continue
        path, off, size, raw, cnt = hits[0]
        fname = path.split("/")[-1]
        if cnt != 1:
            print(f"    [!] {fname} | {item['desc']}：锚点出现 {cnt} 次（期望 1），拒绝盲改")
            continue

        new = raw.replace(item["pattern"], item["replacement"], 1)
        if len(new) != len(raw):
            print(f"    [!] {fname} | 替换不等长（{len(raw)}→{len(new)}），拒绝改写")
            continue
        padded = new   # 等长，无需补齐
        if not any(r.get("offset") == off for r in saved):
            saved.append({
                "path": path, "offset": off, "size": size,
                "asar_size": asar_size, "original_b64": base64.b64encode(raw).decode(),
            })
        if check_only:
            print(f"    [ ] {fname} | 未打（{item['desc']}）")
            continue
        with open(asar, "r+b") as f:
            f.seek(off)
            f.write(padded)
            f.seek(off)
            back = f.read(size)
        ok = item["pattern"] not in back and len(back) == size
        if ok:
            _asar_sync_integrity(asar, path, padded)
        print(f"    [{'+' if ok else '!'}] {fname} | {'写入成功（' + item['desc'] + '）' if ok else '写入失败'}")
        changed = True

    if changed and not check_only:
        side.write_text(json.dumps({"patches": saved}, ensure_ascii=False), encoding="utf-8")
        print(f"    原始字节备份: {side.name}")


# ------------------------------------------------- TPS 统计栏注入（asar 重打包级）

TPS_INDEX_PATH = "out/renderer/index.html"
TPS_SCRIPT_PATH = "out/renderer/zcode-tps.js"
TPS_TAG = f'<script src="./{TPS_SCRIPT_PATH.split("/")[-1]}"></script>'

# ---------------------------------------- 思考强度吸附滑条(asar 重打包级,--thought-slider)
# 与 TPS 同款注入链路:out/renderer 新增脚本 + index.html 挂载。
# 滑条挂在原生「思考级别」下拉旁,档位吸附自原生状态探针(data-thought-levels),
# 提交走 React fiber 直调 onValueChange(降级:模拟点开原生菜单按序点选),
# 等价于用户点选菜单项 → 内核 session/setThoughtLevel,会话内即时生效。

SLIDER_SCRIPT_PATH = "out/renderer/zcode-thought-slider.js"
SLIDER_TAG = f'<script src="./{SLIDER_SCRIPT_PATH.split("/")[-1]}"></script>'

# ---------------------------------------- 模型拉取按钮注入（asar 重打包级，--model-puller）
# 前端脚本 vendored from HHQ-666/zcode-model-puller (MIT)；preload 桥与 main IPC handler
# 在此内置。锚点用语义字符串 + 正则捕获压缩符号（electron 别名 / ipcMain 包装别名），
# 不随版本符号重排失效——等价于内核锚点的结构化提取，天然跨版本。

PULLER_SCRIPT_PATH = "out/renderer/zcode-model-puller.js"
PULLER_INDEX_PATH = TPS_INDEX_PATH
PULLER_PRELOAD_PATH = "out/preload/index.cjs"
PULLER_MAIN_PATH = "out/main/index.js"
PULLER_TAG = f'<script type="module" src="./{PULLER_SCRIPT_PATH.split("/")[-1]}"></script>'
PULLER_MARKER = b'zcode:read-model-config'
PULLER_PRELOAD_ANCHOR = re.compile(rb'(\w+)\.contextBridge\.exposeInMainWorld\("zcode",\{')
PULLER_MAIN_ANCHOR = re.compile(rb'(\w+)\.handle\(\w+\.SaveMcpToUserDirectory')
# 还原用：精确匹配自身注入的整段字节（压缩符号经 \w+ 通配），与 sidecar 无关，
# 其他补丁（TPS/chart）重打包改变 asar 大小也不影响还原精确性
PULLER_PRELOAD_STRIP = re.compile(
    rb'readConfigFile:\(\)=>\w+\.ipcRenderer\.invoke\("zcode:read-model-config"\),'
    rb'writeConfigFile:t=>\w+\.ipcRenderer\.invoke\("zcode:write-model-config",t\),'
    rb'fetchModelsFromUrl:\(t,n\)=>\w+\.ipcRenderer\.invoke\("zcode:fetch-models-from-url"\,\{baseUrl:t,apiKey:n\}\),'
)
PULLER_MAIN_STRIP = re.compile(rb'\w+\.handle\("zcode:read-model-config"')


def _puller_preload_injection(electron_alias: str) -> bytes:
    """在 exposeInMainWorld("zcode",{ 对象字面量开头插入 3 个 IPC 桥方法。
    不依赖压缩 helper s()：contextBridge 对普通箭头函数无要求。"""
    e = electron_alias
    return (
        f'readConfigFile:()=>{e}.ipcRenderer.invoke("zcode:read-model-config"),'
        f'writeConfigFile:t=>{e}.ipcRenderer.invoke("zcode:write-model-config",t),'
        f'fetchModelsFromUrl:(t,n)=>{e}.ipcRenderer.invoke("zcode:fetch-models-from-url",'
        f'{{baseUrl:t,apiKey:n}}),'
    ).encode()


def _puller_preload_state(blob: bytes | None):
    """解析 preload 注入状态：(injected, synced, clean, alias)。
    synced=当前注入段与现行生成器逐字节一致（marker 存在但内容旧时为 False）；
    clean=剥离注入后的原始内容（无法安全剥离时为 None，调用方应拒绝改写）。"""
    if blob is None:
        return False, False, None, None
    a = PULLER_PRELOAD_ANCHOR.search(blob)
    if a is None:
        return False, False, None, None
    alias = a.group(1).decode()
    if PULLER_MARKER not in blob:
        return False, False, blob, alias
    m = PULLER_PRELOAD_STRIP.search(blob)
    if m is None:
        return True, False, None, alias          # 有注入但形态不符，无法安全剥离
    stripped = blob[:m.start()] + blob[m.end():]
    if m.group(0) == _puller_preload_injection(alias):
        return True, True, stripped, alias
    return True, False, stripped, alias


def _puller_main_state(blob: bytes | None):
    """解析 main 注入状态：注入段 = [首条 handler 起点, SaveMcpToUserDirectory 锚点起点)。
    返回 (injected, synced, clean, alias)；clean 恒为剥离注入后的内容（未注入时即 blob）。"""
    if blob is None:
        return False, False, None, None
    a = PULLER_MAIN_ANCHOR.search(blob)
    if a is None:
        return False, False, None, None
    alias = a.group(1).decode()
    if PULLER_MARKER not in blob:
        return False, False, blob, alias
    m = PULLER_MAIN_STRIP.search(blob)
    if m is None or m.start() >= a.start():
        return True, False, None, alias
    stripped = blob[:m.start()] + blob[a.start():]
    if blob[m.start():a.start()] == _puller_main_injection(alias):
        return True, True, stripped, alias
    return True, False, stripped, alias


def _puller_main_injection(ipc_alias: str) -> bytes:
    """在 ipcMain 别名的 SaveMcpToUserDirectory 注册语句前插入 3 个 handler。
    写 config 前先落一份 config.json.puller-bak；动态 import 保持与原包一致。"""
    h = ipc_alias
    read_h = (
        f'{h}.handle("zcode:read-model-config",async()=>{{'
        'try{let{default:e}=await import("node:fs"),{default:t}=await import("node:path"),'
        '{default:n}=await import("node:os");'
        'let r=t.join(n.homedir(),".zcode","v2","config.json");'
        'return{success:!0,data:JSON.parse(e.readFileSync(r,"utf-8"))}}'
        'catch(e){return{success:!1,error:String(e)}}});\n'
    )
    write_h = (
        f'{h}.handle("zcode:write-model-config",async(e,t)=>{{'
        'try{'
        'if(typeof t!="object"||t===null)throw new Error("invalid config payload");'
        'let{default:n}=await import("node:fs"),{default:r}=await import("node:path"),'
        '{default:i}=await import("node:os");'
        'let o=r.join(i.homedir(),".zcode","v2","config.json");'
        'try{n.writeFileSync(o+".puller-bak",n.readFileSync(o,"utf-8"))}catch(a){}'
        'n.writeFileSync(o,JSON.stringify(t,null,2),"utf-8");'
        'return{success:!0}}'
        'catch(n){return{success:!1,error:String(n)}}});\n'
    )
    fetch_h = (
        f'{h}.handle("zcode:fetch-models-from-url",async(e,t)=>{{'
        'try{let{default:ht}=await import("node:https"),{default:hh}=await import("node:http");'
        'let u=(t&&t.baseUrl||"").trim().replace(/\\/+$/,""),k=(t&&t.apiKey||"").trim();'
        'if(!u)return{success:!1,error:"baseUrl 为空"};'
        'let cs=[];'
        'if(u.endsWith("/v1")){cs.push(u+"/models");cs.push(u.replace(/\\/v1$/,"")+"/models")}'
        'else{cs.push(u+"/v1/models");cs.push(u+"/models")}'
        'if(u.endsWith("/api"))cs.unshift(u+"/v1/models");'
        'for(let cur of cs){'
        'try{let res=await new Promise(resolve=>{'
        'let mod=cur.startsWith("https:")?ht:hh;'
        'let hs={"User-Agent":"ZCode","Accept":"application/json"};'
        'if(k){hs.Authorization="Bearer "+k;hs["x-api-key"]=k}'
        'let req=mod.request(cur,{method:"GET",headers:hs,timeout:8000},r=>{'
        'let b="";r.on("data",c=>b+=c);'
        'r.on("end",()=>{'
        'if(r.statusCode>=200&&r.statusCode<300){try{'
        'let j=JSON.parse(b);'
        'let l=Array.isArray(j)?j:Array.isArray(j.data)?j.data:Array.isArray(j.models)?j.models:[];'
        'let ids=[],metas={};'
        'for(let it of l){let id,ctx=0,out=0,eff=null,def=null;'
        'if(typeof it=="string")id=it.trim();'
        'else if(it){id=(it.id||it.name||"").trim();'
        'ctx=+it.context_length||+it.max_input_tokens||0;'
        'out=+it.max_output_tokens||+it.max_completion_tokens||+(it.top_provider?it.top_provider.max_completion_tokens:0)||0;'
        'if(Array.isArray(it.supported_efforts))eff=it.supported_efforts.filter(x=>typeof x=="string"&&x.trim());'
        'if(typeof it.default_effort=="string")def=it.default_effort;}'
        'if(id&&!metas[id]){metas[id]={context:ctx,output:out,efforts:eff,def:def};ids.push(id)}}'
        'if(ids.length>0)return resolve({success:!0,models:ids,metas:metas})'
        '}catch(e){}}'
        'resolve(null)})});'
        'req.on("error",()=>resolve(null));'
        'req.on("timeout",()=>{req.destroy();resolve(null)});'
        'req.end()});'
        'if(res&&res.success)return res'
        '}catch(e){}}'
        'return{success:!1,error:"未能获取到模型列表，请检查 Base URL 和 API Key"}}'
        'catch(e){return{success:!1,error:String(e)}}});\n'
    )
    return (read_h + write_h + fetch_h).encode()


def _asar_header_raw(asar: Path):
    """读整个 asar：返回 (原始全量 bytes, header 树, 数据区起始偏移)。"""
    raw = asar.read_bytes()
    if len(raw) < 16:
        raise ValueError(f"asar 文件过小: {asar}")
    f0, f1, f2, f3 = struct.unpack("<4I", raw[:16])
    if f0 != 4:
        raise ValueError(f"asar 头格式不符（首 uint32={f0}，期望 4）: {asar}")
    header = json.loads(raw[16:16 + f3].decode("utf-8"))
    return raw, header, 8 + f1


def _asar_entry_bytes(raw: bytes, data_start: int, ent: dict) -> bytes:
    off = data_start + int(ent["offset"])
    return raw[off:off + ent["size"]]


def _asar_integrity(data: bytes, block_size: int = 4194304) -> dict:
    blocks = [hashlib.sha256(data[i:i + block_size]).hexdigest()
              for i in range(0, len(data), block_size)]
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(),
            "blockSize": block_size, "blocks": blocks}


def _asar_walk_entries(node, path=""):
    """yield (全路径, 叶子条目 dict)。目录与 unpacked 条目不产出。"""
    for name, ent in (node.get("files") or {}).items():
        p = f"{path}/{name}" if path else name
        if "files" in ent:
            yield from _asar_walk_entries(ent, p)
        elif not ent.get("unpacked"):
            yield p, ent


def _repack_asar(asar: Path, overwrite: dict[str, bytes], remove: set[str]) -> int:
    """通用 asar 重打包：树中删除 remove 条目，overwrite 覆盖/新增文件数据并重算 integrity，
    全部条目 offset 重排；写临时文件、回读校验后原子替换。返回新文件大小。"""
    raw, header, data_start = _asar_header_raw(asar)

    # overwrite 中树里尚不存在的路径（新增文件）按层级插入占位条目
    for p in overwrite:
        parts = p.split("/")
        node = header
        for part in parts[:-1]:
            node = node.setdefault("files", {}).setdefault(part, {"files": {}})
        leaf = node.setdefault("files", {})
        leaf.setdefault(parts[-1], {"size": 0, "offset": "0"})

    def purge(node, prefix):
        files = node.get("files") or {}
        for name in list(files.keys()):
            ent = files[name]
            p = f"{prefix}/{name}" if prefix else name
            if "files" in ent:
                purge(ent, p)
                if not ent["files"]:
                    del files[name]          # 删空的目录一并移除
            elif p in remove:
                del files[name]

    purge(header, "")
    # relayout 会改写 ent.offset，旧数据位置必须先快照（emit 二次读数据时用）
    old_positions = {p: (int(ent["offset"]), ent["size"]) for p, ent in _asar_walk_entries(header)}
    cursor = 0

    def entry_data(p: str, ent: dict) -> bytes:
        data = overwrite.get(p)
        if data is None:
            off, size = old_positions[p]
            data = raw[data_start + off:data_start + off + size]
        return data

    def relayout(node, prefix):
        nonlocal cursor
        for name, ent in (node.get("files") or {}).items():
            p = f"{prefix}/{name}" if prefix else name
            if "files" in ent:
                relayout(ent, p)
            elif ent.get("unpacked"):
                continue
            else:
                data = entry_data(p, ent)
                ent["size"] = len(data)
                ent["offset"] = str(cursor)
                if p in overwrite:
                    ent["integrity"] = _asar_integrity(data)
                cursor += len(data)

    relayout(header, "")
    json_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    header_blob = (struct.pack("<4I", 4, 8 + len(json_bytes) + pad, 4 + len(json_bytes) + pad, len(json_bytes))
                   + json_bytes + b"\x00" * pad)

    tmp = asar.with_name(asar.name + ".tps-tmp")
    with open(tmp, "wb") as out:
        out.write(header_blob)

        def emit(node, prefix):
            for name, ent in (node.get("files") or {}).items():
                p = f"{prefix}/{name}" if prefix else name
                if "files" in ent:
                    emit(ent, p)
                elif ent.get("unpacked"):
                    continue
                else:
                    out.write(entry_data(p, ent))

        emit(header, "")

    v_raw, v_header, v_start = _asar_header_raw(tmp)
    v_files = dict(_asar_walk_entries(v_header))
    try:
        for p, want in overwrite.items():
            ent = v_files.get(p)
            if ent is None or _asar_entry_bytes(v_raw, v_start, ent) != want:
                raise ValueError(f"重打包校验失败: {p}")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    try:
        os.replace(tmp, asar)
    except OSError:
        tmp.unlink(missing_ok=True)   # asar 被占用（ZCode 运行中）时不留 tmp 残留
        raise
    return asar.stat().st_size


def _refresh_chart_sidecar(asar: Path) -> None:
    """asar 重打包后数据区整体位移，字节级补丁（chart/width）记录里的绝对 offset
    与 asar_size 指纹都需重定位，否则还原时按指纹判过期直接作废。"""
    for suffix in (".chart-patch.json", ".width-patch.json"):
        _relocate_sidecar(asar, suffix)


def _relocate_sidecar(asar: Path, suffix: str) -> None:
    side = asar.with_name(asar.name + suffix)
    if not side.is_file():
        return
    try:
        recs = json.loads(side.read_text(encoding="utf-8")).get("patches", [])
    except Exception:
        return
    if not recs:
        return
    entries = {p: (o, s) for p, o, s in _asar_header_index(asar)}
    cur_size = asar.stat().st_size
    changed = False
    for r in recs:
        hit = entries.get(r.get("path"))
        if hit and hit[1] == r.get("size") and (r.get("offset") != hit[0] or r.get("asar_size") != cur_size):
            r["offset"], r["asar_size"] = hit[0], cur_size
            changed = True
    if changed:
        side.write_text(json.dumps({"patches": recs}, ensure_ascii=False), encoding="utf-8")
        print(f"[*] 已同步 {side.name} 的 offset/指纹到重打包后的 asar")


def _process_script_inject(asar: Path, check_only: bool, revert: bool, src_path: Path | None,
                           *, label: str, script_entry: str, tag: str,
                           side_suffix: str, bak_suffix: str) -> None:
    """renderer 单脚本注入通用链路(TPS 统计栏 / 思考强度滑条共用):
    index.html </body> 前挂 <script> + 新增脚本条目,整体重排 asar、重算 integrity。
    ⚠️ zcode-tps.js 内**绝不调用 port.start()**(只 addEventListener,start 交给应用)——
    否则会消费掉服务端 Initialize 启动握手,ZCode 3.12.2 会卡在启动界面。
    详见 scripts/zcode-tps.js 头部说明与 SKILL.md。"""
    side = asar.with_name(asar.name + side_suffix)
    bak = asar.with_name(asar.name + bak_suffix)
    asar_size = asar.stat().st_size
    tag_b = tag.encode()

    raw, header, data_start = _asar_header_raw(asar)
    paths = {p: ent for p, ent in _asar_walk_entries(header)}
    idx_ent = paths.get(TPS_INDEX_PATH)
    if idx_ent is None:
        print(f"[!] {asar}\n    未找到 {TPS_INDEX_PATH}，版本结构可能已变，跳过")
        return
    idx_bytes = _asar_entry_bytes(raw, data_start, idx_ent)
    tagged = tag_b in idx_bytes
    installed = tagged and script_entry in paths

    saved = None
    if side.is_file():
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
            if rec.get("asar_size") == asar_size:
                saved = rec
        except Exception:
            saved = None

    if check_only:
        state = "已打" if installed else ("不完整（index.html 有 tag 但缺脚本条目）" if tagged else "未打")
        print(f"[*] {asar}\n    {label}注入: {state} | sidecar: {'有' if saved else '无'} | 备份: {'有' if bak.is_file() else '无'}")
        return

    if revert:
        if not installed and not saved:
            print(f"[.] {asar}\n    未打{label}注入，跳过")
            return
        # 外科手术式还原：只从当前 index.html 摘除本补丁的 tag，不动其他补丁
        # （如 --model-puller）对同一文件的改动；sidecar 原件仅作兜底。
        stripped = idx_bytes.replace(tag_b + b"\n", b"").replace(tag_b, b"")
        if stripped == idx_bytes and saved and saved.get("index_original_b64"):
            stripped = base64.b64decode(saved["index_original_b64"])
        new_size = _repack_asar(asar, {TPS_INDEX_PATH: stripped}, {script_entry})
        side.unlink(missing_ok=True)
        bak.unlink(missing_ok=True)
        _refresh_chart_sidecar(asar)
        print(f"[+] {asar}\n    已移除{label}注入（新大小 {new_size:,} 字节，备份已清理）")
        return

    if src_path is None:
        src_path = Path(__file__).resolve().parent / script_entry.split("/")[-1]
    if not src_path.is_file():
        raise SystemExit(f"[!] 找不到注入源脚本 {src_path}（路径由调用方指定）")
    script_bytes = src_path.read_bytes()

    # 脚本热更新：已注入但内容与源不一致时替换脚本条目（无需先 revert）。
    # 只按"脚本条目是否存在"判断会让改动后的脚本永不生效（版本适配修复无法落地）。
    if installed:
        cur_script = _asar_entry_bytes(raw, data_start, paths[script_entry]) if script_entry in paths else None
        if cur_script == script_bytes:
            print(f"[=] {asar}\n    已打{label}注入（脚本同源），跳过")
            return
        new_size = _repack_asar(asar, {script_entry: script_bytes}, set())
        _refresh_chart_sidecar(asar)
        print(f"[+] {asar}\n    {label}脚本已热更新（{src_path.name} {len(script_bytes):,} 字节，其余组件不变）")
        return

    if idx_bytes.count(b"</body>") != 1:
        print(f"[!] {asar}\n    index.html 的 </body> 出现 {idx_bytes.count(b'</body>')} 次（期望 1），拒绝盲改")
        return

    if not bak.is_file():
        shutil.copyfile(asar, bak)

    new_idx = idx_bytes.replace(b"</body>", tag_b + b"</body>", 1)
    new_size = _repack_asar(asar, {TPS_INDEX_PATH: new_idx, script_entry: script_bytes}, set())
    side.write_text(json.dumps({
        "asar_size": new_size,
        "index_path": TPS_INDEX_PATH,
        "script_entry": script_entry,
        "index_original_b64": base64.b64encode(idx_bytes).decode(),
    }, ensure_ascii=False), encoding="utf-8")
    _refresh_chart_sidecar(asar)
    print(f"[+] {asar}\n    {label}注入完成（{src_path.name} {len(script_bytes):,} 字节 -> {script_entry}，index.html 已挂载）\n"
          f"    原件备份: {bak.name} | 记录: {side.name}")


def process_tps_footer(asar: Path, check_only: bool, revert: bool, tps_src: Path | None) -> None:
    _process_script_inject(asar, check_only, revert, tps_src,
                           label="TPS 统计栏", script_entry=TPS_SCRIPT_PATH, tag=TPS_TAG,
                           side_suffix=".tps-patch.json", bak_suffix=".tps.bak")


def process_thought_slider(asar: Path, check_only: bool, revert: bool, slider_src: Path | None) -> None:
    _process_script_inject(asar, check_only, revert, slider_src,
                           label="思考强度滑条", script_entry=SLIDER_SCRIPT_PATH, tag=SLIDER_TAG,
                           side_suffix=".slider-patch.json", bak_suffix=".slider.bak")


def process_model_puller(asar: Path, check_only: bool, revert: bool, puller_src: Path | None) -> None:
    side = asar.with_name(asar.name + ".puller-patch.json")
    bak = asar.with_name(asar.name + ".puller.bak")
    asar_size = asar.stat().st_size

    raw, header, data_start = _asar_header_raw(asar)
    paths = {p: ent for p, ent in _asar_walk_entries(header)}
    idx_ent = paths.get(PULLER_INDEX_PATH)
    if idx_ent is None:
        print(f"[!] {asar}\n    未找到 {PULLER_INDEX_PATH}，版本结构可能已变，跳过")
        return
    idx_bytes = _asar_entry_bytes(raw, data_start, idx_ent)

    def _blob(path: str) -> bytes | None:
        ent = paths.get(path)
        return _asar_entry_bytes(raw, data_start, ent) if ent else None

    pre_blob, main_blob = _blob(PULLER_PRELOAD_PATH), _blob(PULLER_MAIN_PATH)
    script_present = PULLER_SCRIPT_PATH in paths
    idx_tagged = PULLER_TAG.encode() in idx_bytes
    pre_inj, pre_synced, pre_clean, _ = _puller_preload_state(pre_blob)
    main_inj, main_synced, main_clean, _ = _puller_main_state(main_blob)
    cur_script = _asar_entry_bytes(raw, data_start, paths[PULLER_SCRIPT_PATH]) if script_present else None
    installed = script_present and idx_tagged and pre_inj and main_inj

    saved = None
    if side.is_file():
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
            if rec.get("asar_size") == asar_size:
                saved = rec
        except Exception:
            saved = None

    def _ver(ok: bool, synced: bool) -> str:
        return "无" if not ok else ("有" if synced else "有（版本旧）")

    comp = (f"renderer 脚本: {'有' if script_present else '无'} | index.html 挂载: {'有' if idx_tagged else '无'} | "
            f"preload 桥: {_ver(pre_inj, pre_synced)} | main handler: {_ver(main_inj, main_synced)}")

    if puller_src is None:
        puller_src = Path(__file__).resolve().parent / PULLER_SCRIPT_PATH.split("/")[-1]
    old_script = (script_present and puller_src.is_file()
                  and cur_script != puller_src.read_bytes())

    if check_only:
        state = "已打" if installed else ("不完整" if (script_present or idx_tagged or pre_inj or main_inj) else "未打")
        if installed and (not (pre_synced and main_synced) or old_script):
            state = "已打（含旧版组件，重跑可自动更新）"
        print(f"[*] {asar}\n    模型拉取按钮注入: {state} | {comp} | sidecar: {'有' if saved else '无'} | "
              f"备份: {'有' if bak.is_file() else '无'}")
        return

    if revert:
        if not installed and not saved:
            print(f"[.] {asar}\n    未打模型拉取注入，跳过")
            return
        # 全程外科手术式还原：只摘除自己注入的字节段，不依赖 sidecar 指纹
        stripped = idx_bytes.replace(PULLER_TAG.encode() + b"\n", b"").replace(PULLER_TAG.encode(), b"")
        overwrite = {PULLER_INDEX_PATH: stripped}
        if pre_inj:
            if pre_clean is None:
                print(f"[!] {asar}\n    preload 注入段形态不符（可能被手工改过），拒绝还原")
                return
            overwrite[PULLER_PRELOAD_PATH] = pre_clean
        if main_inj:
            if main_clean is None:
                print(f"[!] {asar}\n    main 注入段形态不符（可能被手工改过），拒绝还原")
                return
            overwrite[PULLER_MAIN_PATH] = main_clean
        new_size = _repack_asar(asar, overwrite, {PULLER_SCRIPT_PATH} if script_present else set())
        side.unlink(missing_ok=True)
        bak.unlink(missing_ok=True)
        _refresh_chart_sidecar(asar)
        print(f"[+] {asar}\n    已还原模型拉取注入（新大小 {new_size:,} 字节，备份已清理）")
        return

    if not puller_src.is_file():
        raise SystemExit(f"[!] 找不到注入源脚本 {puller_src}（可用 --puller-src 指定路径）")
    script_bytes = puller_src.read_bytes()

    # 幂等：四组件齐、且 renderer/preload/main 注入内容均与现行实现逐字节一致才跳过。
    # 只按 marker 判断会让旧版注入被误认成"已是最新"（换实现后不更新），必须内容比对。
    if installed and cur_script == script_bytes and pre_synced and main_synced:
        print(f"[=] {asar}\n    已打模型拉取注入（四组件均为当前版本），跳过")
        return

    # 锚点核实：三处定位全部唯一才动手
    if idx_bytes.count(b"</body>") != 1:
        print(f"[!] {asar}\n    index.html 的 </body> 出现 {idx_bytes.count(b'</body>')} 次（期望 1），拒绝盲改")
        return
    pre_m = PULLER_PRELOAD_ANCHOR.findall(pre_blob or b"")
    main_m = PULLER_MAIN_ANCHOR.findall(main_blob or b"")
    if len(pre_m) != 1 or len(main_m) != 1:
        print(f"[!] {asar}\n    锚点异常：preload 命中 {len(pre_m)} 次、main 命中 {len(main_m)} 次（各期望 1），"
              f"版本结构可能已变，拒绝盲改\n    preload 锚点: {PULLER_PRELOAD_ANCHOR.pattern}\n"
              f"    main 锚点: {PULLER_MAIN_ANCHOR.pattern}")
        return

    if not bak.is_file():
        shutil.copyfile(asar, bak)

    # 逐组件更新：仅写入与现行实现不一致的组件；sidecar 原件按组件记录、不覆盖已有记录
    rec = dict(saved or {})
    overwrite: dict[str, bytes] = {}
    if cur_script != script_bytes:
        overwrite[PULLER_SCRIPT_PATH] = script_bytes
    if not idx_tagged:
        overwrite[PULLER_INDEX_PATH] = idx_bytes.replace(b"</body>", PULLER_TAG.encode() + b"\n</body>", 1)
        rec.setdefault("index_original_b64", base64.b64encode(idx_bytes).decode())
    if not pre_synced:
        if pre_clean is None:
            print(f"[!] {asar}\n    preload 已注入但形态不符（旧版/被改过），无法安全更新，拒绝"
                  f"（先 --revert 或重装 ZCode）")
            return
        anchor = PULLER_PRELOAD_ANCHOR.search(pre_clean).end()
        overwrite[PULLER_PRELOAD_PATH] = (pre_clean[:anchor]
                                          + _puller_preload_injection(pre_m[0].decode())
                                          + pre_clean[anchor:])
        rec.setdefault("preload_original_b64", base64.b64encode(pre_clean).decode())
    if not main_synced:
        if main_clean is None:
            print(f"[!] {asar}\n    main 已注入但形态不符（旧版/被改过），无法安全更新，拒绝"
                  f"（先 --revert 或重装 ZCode）")
            return
        anchor = PULLER_MAIN_ANCHOR.search(main_clean).start()
        overwrite[PULLER_MAIN_PATH] = (main_clean[:anchor]
                                       + _puller_main_injection(main_m[0].decode())
                                       + main_clean[anchor:])
        rec.setdefault("main_original_b64", base64.b64encode(main_clean).decode())

    new_size = _repack_asar(asar, overwrite, set())
    rec.update({
        "asar_size": new_size,
        "index_path": PULLER_INDEX_PATH,
        "script_entry": PULLER_SCRIPT_PATH,
        "preload_path": PULLER_PRELOAD_PATH,
        "main_path": PULLER_MAIN_PATH,
    })
    side.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    _refresh_chart_sidecar(asar)
    done = "+".join(k for k, v in (("renderer", True), ("index", PULLER_INDEX_PATH in overwrite),
                                   ("preload", PULLER_PRELOAD_PATH in overwrite), ("main", PULLER_MAIN_PATH in overwrite)) if v)
    print(f"[+] {asar}\n    模型拉取按钮注入完成（{done}，{puller_src.name} {len(script_bytes):,} 字节）\n"
          f"    原件备份: {bak.name} | 记录: {side.name}")


def _resolve_asars(target: str | None) -> list[Path]:
    asars = []
    for cjs in resolve_target(target):
        asar = cjs.parent.parent / "app.asar"   # resources/glm/zcode.cjs -> resources/app.asar
        if asar.is_file() and asar not in asars:
            asars.append(asar)
    if not asars:
        raise SystemExit("[!] 未找到 app.asar")
    return asars


def main() -> None:
    ap = argparse.ArgumentParser(description="ZCode 客户端补丁工具：思维强度透传 + 用量页去截断 + TPS 统计栏（自动探测安装位置）")
    ap.add_argument("target", nargs="?", help="可选：安装根目录或 zcode.cjs 路径；缺省自动探测全部")
    ap.add_argument("--check", action="store_true", help="只检查状态，不修改")
    ap.add_argument("--revert", action="store_true", help="从备份还原")
    ap.add_argument("--extract", action="store_true",
                    help="按结构特征提取当前内核的档位解析函数锚点（新版本升级后用）")
    ap.add_argument("--usage-chart", action="store_true",
                    help="补丁用量页：去掉趋势图 Top6 与饼图 Top5+其他模型 的截断，全部模型展示")
    ap.add_argument("--model-width", action="store_true",
                    help="加宽模型选择弹窗（含渠道子菜单）192px→320px，长模型名不再被截断")
    ap.add_argument("--tps-footer", action="store_true",
                    help="注入 TPS 统计栏：输入框工具栏常驻胶囊（● 时间 · 首 token · tok/s · out），asar 重打包级")
    ap.add_argument("--tps-src", default=None,
                    help="指定注入脚本路径（默认本目录 zcode-tps.js）")
    ap.add_argument("--thought-slider", action="store_true",
                    help="注入思考强度吸附滑条：原生「思考级别」下拉旁的拖拽刻度条，档位即时生效，asar 重打包级")
    ap.add_argument("--slider-src", default=None,
                    help="指定注入脚本路径（默认本目录 zcode-thought-slider.js）")
    ap.add_argument("--model-puller", action="store_true",
                    help="注入模型拉取按钮：设置页「自动拉取模型」，经 preload/main IPC 读写 config，asar 重打包级")
    ap.add_argument("--puller-src", default=None,
                    help="指定注入的 zcode-model-puller.js 路径（默认用本脚本同目录自带的）")
    args = ap.parse_args()

    if args.usage_chart or args.tps_footer or args.model_puller or args.model_width or args.thought_slider:
        asars = _resolve_asars(args.target)
        if args.usage_chart:
            mode = "检查" if args.check else ("还原" if args.revert else "打补丁")
            print(f"=== 用量页去截断补丁，目标 {len(asars)} 处，模式：{mode} ===")
            for a in asars:
                process_usage_chart(a, args.check, args.revert)
        if args.model_width:
            mode = "检查" if args.check else ("还原" if args.revert else "打补丁")
            print(f"=== 模型弹窗加宽补丁，目标 {len(asars)} 处，模式：{mode} ===")
            for a in asars:
                process_model_width(a, args.check, args.revert)
        if args.tps_footer:
            mode = "检查" if args.check else ("还原" if args.revert else "打补丁")
            print(f"=== TPS 统计栏注入，目标 {len(asars)} 处，模式：{mode} ===")
            src = Path(args.tps_src) if args.tps_src else None
            for a in asars:
                try:
                    process_tps_footer(a, args.check, args.revert, src)
                except PermissionError:
                    print(f"[!] {a}\n    文件被占用（ZCode 正在运行）或无写入权限；完全退出 ZCode 后重试")
        if args.thought_slider:
            mode = "检查" if args.check else ("还原" if args.revert else "打补丁")
            print(f"=== 思考强度滑条注入，目标 {len(asars)} 处，模式：{mode} ===")
            src = Path(args.slider_src) if args.slider_src else None
            for a in asars:
                try:
                    process_thought_slider(a, args.check, args.revert, src)
                except PermissionError:
                    print(f"[!] {a}\n    文件被占用（ZCode 正在运行）或无写入权限；完全退出 ZCode 后重试")
        if args.model_puller:
            mode = "检查" if args.check else ("还原" if args.revert else "打补丁")
            print(f"=== 模型拉取按钮注入，目标 {len(asars)} 处，模式：{mode} ===")
            src = Path(args.puller_src) if args.puller_src else None
            for a in asars:
                try:
                    process_model_puller(a, args.check, args.revert, src)
                except PermissionError:
                    print(f"[!] {a}\n    文件被占用（ZCode 正在运行）或无写入权限；完全退出 ZCode 后重试")
        if not args.check and not args.revert:
            print("=== 提示：完全退出并重启 ZCode 后生效；升级后需重新执行 ===")
        return

    targets = resolve_target(args.target)
    if not targets:
        raise SystemExit("[!] 未探测到任何 ZCode 安装；请把安装目录路径作为参数传入")

    if args.extract:
        for t in targets:
            print(f"[*] {t}")
            anchor = extract_anchor(t)
            if anchor:
                print("[+] 提取成功，把下面整段加入脚本 ANCHORS 后重新运行打补丁：\n")
                print(f'    "<版本号>": (\n        {anchor!r}\n    ),')
        return

    mode = "检查" if args.check else ("还原" if args.revert else "打补丁")
    print(f"=== 探测到 {len(targets)} 处安装，模式：{mode} ===")
    for t in targets:
        process(t, args.check, args.revert)

    if not args.check and not args.revert:
        print("=== 提示：完全退出并重启 ZCode 后生效；升级后需重新执行本脚本 ===")


if __name__ == "__main__":
    main()
