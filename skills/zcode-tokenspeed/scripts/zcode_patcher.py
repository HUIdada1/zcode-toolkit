#!/usr/bin/env python3
"""
ZCode 客户端补丁工具
=====================

对本地 ZCode 安装打两类补丁（均自动探测安装位置、独立备份、幂等）：

一、思维强度透传（内核 zcode.cjs，**仅 ≤3.11 需要**）
  让「不在内核白名单里的模型」也遵循 config.json 里配置的思维强度档位。
  原理：内核档位解析函数把档位翻译成线上参数
  ({anthropic:{effort,thinking}} / {openaiCompatible:{reasoningEffort}})，
  但参数表 providerOptionsByLevel 只给白名单模型（claude/glm/deepseek 等家族）下发；
  自定义供应商模型拿到的是空表，档位被选中却不产生任何请求参数。
  补丁在取参处加兜底：查表为空时直接用档位名合成参数——
  白名单模型不受影响（它们的表非空，?? 短路）。

  ⚠️ ZCode ≥ 3.14 换了机制：内核里已没有该解析函数，也没有 variants/defaultVariant，
  档位改由配置侧 provider_config.json 的 optionSpecs.reasoningLevel 下发
  （见 `--reasoning-config`）。此时本补丁会明确提示「不适用」，不要去打。

  档位 -> anthropic thinking.budget_tokens 映射：
    low=4000  medium=8000  high=16000  xhigh=32000  max=32000  其它档名=16000
    disabled/none/off/nothink -> thinking disabled

  生效条件（v2 config.json 中该模型，≤3.11）：
    "reasoning": { "enabled": true, "variants": ["low","high","max"], "defaultVariant": "max" }

二、用量页去截断（app.asar，--usage-chart）
  设置→用量 的趋势图只画 Top6 模型、饼图只画 Top5 并把其余合并为「其他模型」。
  补丁解析 asar 头定位渲染文件，把截断表达式替换为全量版本，
  空格补齐到原字节长度后原地覆盖（asar 头/offset/unpacked 零改动）。
  原始字节备份在 app.asar.chart-patch.json，可整体还原。

三、TPS 统计栏（app.asar，--tps-footer）
  向渲染层 out/renderer/index.html 注入 zcode-tps.js（读 ServicePort 事件流得精确
  tok/s/首 token/out；脚本内不调用 port.start()，否则 3.12.2 会卡启动）：
  输入框**下方**常驻统计条（默认位置），左组=本轮即时指标（首 token/tok/s/out）、
  右组=会话累计（第 N 轮/输入/命中+命中率/累出），空会话显示空态绿点常驻；
  胶囊右键可切「输入框下方（默认）/ 输入框工具栏 / 会话顶部 sticky」，localStorage 记忆。
  数据取自页面内 MessagePort 会话事件流，无常驻服务。
  重打包级修改：整体重排 asar 目录、对改动文件重算 integrity。
  原件备份 app.asar.tps.bak（带指纹 meta），记录在 app.asar.tps-patch.json，可整体还原。

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

六、思考档位配置（配置侧，--reasoning-config，**3.14+ 用这个替代内核补丁**）
  3.14 起档位机制改为「配置侧原生」：档位列表与请求参数都由 provider_config.json 的
  config.modelConfigRules.providerModelRules[].config.optionSpecs.reasoningLevel 提供
  （values = 界面档位、末位即默认；map = CEL 表达式，请求发出前由内核
  createModelOptionMapFetch 合并进请求体）。config.json 里的 reasoning.variants 已失效。
  本命令把 config.json 里各模型已配的档位（旧 variants 或已有 optionSpecs）迁移进
  provider_config.json，使自定义模型的档位在新内核上真正下发；--check 看现状、
  --revert 从 provider_config.json.reasoning-bak 还原。
  与界面「手动配置」过的模型（manualProviderModelRules）冲突时不迁移（内核 schema 禁止
  同一模型同时出现在两个规则列表，否则整个供应商配置会降级为空）。

用法：
  python zcode_patcher.py                       # 思维强度补丁：自动探测全部安装并打（幂等）
  python zcode_patcher.py --check               # 只看思维强度补丁状态
  python zcode_patcher.py --revert              # 还原内核备份
  python zcode_patcher.py --extract             # 提取当前内核锚点（新版本无已知锚点时）
  python zcode_patcher.py --reasoning-config    # 【3.14+ 推荐】档位写进 provider_config（原生机制，无需内核补丁）
  python zcode_patcher.py --usage-chart         # 用量页去截断（同样支持 --check/--revert）
  python zcode_patcher.py --tps-footer          # TPS 统计栏注入（同样支持 --check/--revert）
  python zcode_patcher.py --thought-slider      # 思考强度吸附滑条（同样支持 --check/--revert）
  python zcode_patcher.py --model-puller        # 模型拉取按钮注入（同样支持 --check/--revert）
  python zcode_patcher.py "D:\\ZCode"           # 只处理指定安装（安装根目录或 zcode.cjs 均可）

通用开关：
  --dry-run   只报告将要做的改动，不写盘（所有补丁通用）
  --force     跳过备份指纹校验（备份与当前文件对不上时强制继续，慎用）
  --verbose   打印安装探测的每一步结果（定位不到安装时用）

注意：
  * ZCode 升级会覆盖 zcode.cjs 与 app.asar，升级后需重新执行对应补丁；升级后首次打补丁
    会自动归档旧版本的 .bak（备份带指纹），避免还原时把旧内核盖回新客户端。
  * 打完补丁完全退出并重启 ZCode 后生效；打补丁前请先完全退出 ZCode（脚本会预检）。
  * ZCode ≥ 3.14 的内核已换成「配置侧原生档位」：档位参数由 provider_config.json 的
    optionSpecs.reasoningLevel 下发，**不需要内核补丁**——用 --reasoning-config 配置即可。
    此时内核补丁会明确提示「本版本无需补丁」而不是报「锚点匹配异常」。
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
import time
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

# --verbose：打印安装探测与备份校验的细节（定位不到安装、备份指纹异常时用）
VERBOSE = False

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


# ---------------------------------------------------------------- 档位机制探测
# 3.14 起内核换成「配置侧原生档位」：档位参数由 provider_config.json 的
# optionSpecs.reasoningLevel.map（CEL 表达式）在请求发出前合并进请求体
# （createModelOptionMapFetch → maps.apply(body, values)），档位列表也来自同一处
# （optionSpecs.reasoningLevel.values → 模型目录 reasoning.levels）。
# 旧机制（≤3.11）依赖 config.json 的 reasoning.variants + providerOptionsByLevel 查表，
# 该查表函数在 3.14 内核里已不存在（只剩 zod schema 里的字段定义），
# 因此**新版本不需要内核补丁**——此时继续报「锚点匹配异常」会误导用户。

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_reasoning_mechanism(cjs: Path) -> str:
    """判定内核的档位机制：'legacy'（≤3.11，需内核补丁）/ 'native'（≥3.14，配置侧下发）/ 'unknown'。"""
    try:
        data = cjs.read_bytes()
    except OSError:
        return "unknown"
    if any(data.count(a.encode()) == 1 for a in ANCHORS.values()):
        return "legacy"
    if b"optionSpecs" in data and b"defaultVariant" not in data:
        return "native"
    return "unknown"


def asar_version(asar: Path) -> str | None:
    """从 asar 内 package.json 读客户端版本（读不到返回 None）。"""
    try:
        raw, header, data_start = _asar_header_raw(asar)
        ent = _asar_find_entry(header, "package.json")
        if not ent:
            return None
        m = re.search(rb'"version"\s*:\s*"([^"]+)"', _asar_entry_bytes(raw, data_start, ent))
        return m.group(1).decode("utf-8", "replace") if m else None
    except Exception:
        return None


# ---------------------------------------------------------------- 备份指纹
# 备份必须带指纹：ZCode 升级会覆盖 zcode.cjs / app.asar，而 .bak 会留在原地。
# 没有指纹时，「打补丁」会复用旧版本的备份、「还原」会把旧版本文件盖回新客户端
# （A 版内核 + B 版 app.asar 混搭 → 客户端起不来）。指纹失配时一律归档旧备份并重建。

def _backup_meta(backup: Path) -> Path:
    return backup.with_name(backup.name + ".meta.json")


def _load_backup_meta(backup: Path) -> dict | None:
    meta = _backup_meta(backup)
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_backup_meta(backup: Path, original: bytes, patched: bytes | None,
                       version: str | None, source: str) -> None:
    meta = {
        "tool": "zcode-patcher",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,                      # 被备份的文件路径
        "zcode_version": version,              # 备份时的客户端版本（尽力而为）
        "sha256": _sha256(original),           # 原始（未打补丁）内容哈希
        "size": len(original),
        "patched_sha256": _sha256(patched) if patched is not None else None,
    }
    try:
        _backup_meta(backup).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    except OSError:
        pass


def _archive_backup(backup: Path) -> Path | None:
    """把不再可信的旧备份改名留档（不删除），返回归档后的路径。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup.with_name(f"{backup.name}.stale-{stamp}")
    n = 1
    while target.exists():
        target = backup.with_name(f"{backup.name}.stale-{stamp}-{n}")
        n += 1
    try:
        backup.replace(target)
        if _backup_meta(backup).is_file():
            _backup_meta(backup).replace(_backup_meta(target))
        return target
    except OSError:
        return None


def _ensure_backup(backup: Path, current: Path, version: str | None, *,
                   patched: bytes | None = None, dry_run: bool = False) -> bytes | None:
    """确保 backup 是 current 的「同一版本」原始副本。
    返回当前文件内容（调用方复用），失败返回 None。
    指纹失配（例如客户端升级过）→ 归档旧备份并重建，而不是沿用。"""
    try:
        data = current.read_bytes()
    except OSError as e:
        print(f"    [!] 无法读取 {current}：{e}")
        return None
    if not backup.is_file():
        if dry_run:
            print(f"    [~] 将新建备份 {backup.name}")
            return data
        try:
            backup.write_bytes(data)
            _write_backup_meta(backup, data, patched, version, str(current))
        except OSError as e:
            print(f"    [!] 备份失败：{e}")
            return None
        print(f"    [+] 已建立备份 {backup.name}（{len(data):,} 字节，含指纹）")
        return data

    meta = _load_backup_meta(backup)
    cur_sha = _sha256(data)
    if meta is None:
        arch = None if dry_run else _archive_backup(backup)
        print(f"    [!] 旧备份 {backup.name} 无指纹（早期版本生成），无法确认与当前版本一致"
              + (f"，已归档为 {arch.name}" if arch else "（dry-run：将归档）"))
        if dry_run:
            return data
        try:
            backup.write_bytes(data)
            _write_backup_meta(backup, data, patched, version, str(current))
            print(f"    [+] 已按当前版本重建备份（{len(data):,} 字节）")
        except OSError as e:
            print(f"    [!] 备份失败：{e}")
            return None
        return data

    if cur_sha == meta.get("sha256"):
        return data                                  # 已是原始件：备份与当前一致
    if cur_sha == meta.get("patched_sha256"):
        return data                                  # 已是打过补丁的那份：备份有效
    arch = None if dry_run else _archive_backup(backup)
    print(f"    [!] 备份与当前文件不符（版本已变化，如客户端升级过）"
          + (f"，旧备份已归档为 {arch.name}" if arch else "（dry-run：将归档）"))
    if dry_run:
        return data
    try:
        backup.write_bytes(data)
        _write_backup_meta(backup, data, patched, version, str(current))
        print(f"    [+] 已按当前版本重建备份（{len(data):,} 字节）")
    except OSError as e:
        print(f"    [!] 备份失败：{e}")
        return None
    return data


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
        before = len(roots)
        try:
            probe(roots)
        except Exception as e:
            if VERBOSE:
                print(f"[·] 探测器 {probe.__name__} 异常：{type(e).__name__}: {e}")
        if VERBOSE:
            print(f"[·] 探测器 {probe.__name__} 命中 {len(roots) - before} 个候选目录")
    seen, result = set(), []
    for root in roots:
        cjs = root / "resources" / "glm" / "zcode.cjs"
        try:
            key = cjs.resolve()
        except OSError:
            key = cjs
        if VERBOSE:
            print(f"[·] 检查 {cjs} → {'存在' if cjs.is_file() else '不存在'}")
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

def process(target: Path, check_only: bool, revert: bool, *,
            dry_run: bool = False, force: bool = False, version: str | None = None) -> bool:
    """思维强度内核补丁（≤3.11 的 legacy 机制）。
    全程按字节读写：不碰换行、不做编码转换，只改目标锚点那一段字节。
    返回 True=成功/无需改动，False=失败（供 main 汇总退出码）。"""
    backup = target.with_suffix(".cjs.bak")
    try:
        data = target.read_bytes()
    except OSError as e:
        print(f"[!] 无法读取 {target}：{e}")
        return False

    mechanism = detect_reasoning_mechanism(target)
    patched_now = MARKER.encode() in data
    meta = _load_backup_meta(backup)
    state = "已打" if patched_now else ("已还原/未打" if backup.is_file() else "未打")
    print(f"[*] {target}")
    print(f"    {len(data):,} 字节 | 补丁: {state} | 备份: {'有' if backup.is_file() else '无'}"
          + (f"（{meta.get('zcode_version')}）" if meta and meta.get("zcode_version") else ""))

    if mechanism == "native":
        print("    [i] 该内核使用 3.14+ 原生档位机制（optionSpecs），本补丁不适用：")
        print("        档位参数由配置侧下发 —— 用 `--reasoning-config` 写入 provider_config.json"
              "（先 `--reasoning-config --check` 看现状）")
        return True

    if revert:
        if not backup.is_file():
            print("    [.] 没有备份，跳过")
            return True
        bak = backup.read_bytes()
        if not force:
            if meta is None:
                print("    [!] 备份无指纹（早期版本生成），无法确认与当前内核同版本；"
                      "确认要还原请加 --force")
                return False
            if _sha256(data) not in (meta.get("sha256"), meta.get("patched_sha256")):
                print("    [!] 当前内核与备份不是同一版本（客户端可能已升级）——"
                      "还原会把旧版内核盖回新客户端，已拒绝")
                print(f"        备份版本 {meta.get('zcode_version') or '未知'}；"
                      f"确需强行还原加 --force，恢复出厂请用 scripts/restore_clean.py")
                return False
        if dry_run:
            print(f"    [~] 将用备份还原 {len(bak):,} 字节")
            return True
        try:
            target.write_bytes(bak)
        except OSError as e:
            print(f"    [!] 写入失败：{e}（请完全退出 ZCode / 用管理员身份运行）")
            return False
        print(f"    [+] 已从备份还原（{len(bak):,} 字节）")
        return True

    if check_only:
        return True

    if patched_now:
        print("    [=] 已打过补丁，跳过")
        return True

    matched = [(ver, a) for ver, a in ANCHORS.items() if data.count(a.encode()) == 1]
    if len(matched) != 1:
        detail = ", ".join(f"{ver}={data.count(a.encode())}" for ver, a in ANCHORS.items())
        print(f"    [!] 锚点匹配异常（{detail}，期望恰有一版=1）")
        if mechanism == "unknown":
            print("        内核结构与已知版本都不同：先 `--extract` 按结构特征提取锚点；"
                  "若提取失败，多半是 3.14+ 新机制（不需要本补丁，改用 --reasoning-config）")
        return False
    ver, anchor = matched[0]
    anchor_b = anchor.encode()
    patched = data.replace(anchor_b, replacement_for(anchor).encode())
    print(f"    [+] 匹配版本锚点: {ver}")

    if dry_run:
        print(f"    [~] 将插入兜底合成器并改写查表表达式（{len(data):,} → {len(patched):,} 字节）")
        return True

    cur = _ensure_backup(backup, target, version, patched=patched)
    if cur is None:
        return False
    if cur != data:                       # 备份流程重读过文件，以最新字节为准
        data = cur
        patched = data.replace(anchor_b, replacement_for(anchor).encode())

    try:
        target.write_bytes(patched)
    except OSError as e:
        print(f"    [!] 写入失败：{e}（Program Files 需管理员；请完全退出 ZCode）")
        return False

    back = target.read_bytes()            # 回读校验：长度符合预期且原始锚点已消失
    ok = len(back) == len(patched) and MARKER.encode() in back and anchor_b not in back
    print(f"    [{'+' if ok else '!'}] 补丁{'写入成功' if ok else '写入校验失败'}"
          f"（{len(back):,} 字节，备份 {backup.name}）")
    if not ok:
        print("        [!] 请用 `--revert` 还原后重试，或先 --dry-run 观察")
    return ok


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
        # 3.14 起上游把该分支改为按内容自适应宽度（w-max min-w-48 …），长模型名不再截断，
        # 旧锚点自然消失——这是「上游已修复」而不是结构异常，不该报错吓用户。
        "upstream_fixed": b"w-max min-w-48 max-w-[calc(100vw-2rem)]",
        "optional": True,
    },
    {
        "key": None,
        "pattern": b":`w-48 max-h-72 overflow-y-auto`",
        "replacement": b":`w-80 max-h-72 overflow-y-auto`",
        "desc": "模型弹窗（无子菜单的扁平列表）宽度 192px → 320px",
        "optional": False,
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


def _asar_sync_integrity(asar: Path, entry_path: str, new_bytes: bytes, *,
                         dry_run: bool = False) -> None:
    """同长度原地交换 asar 头里该条目的 integrity 记录，使记录与内容一致。
    sha256 hex 定长（64），头部长度与全部 offset 零改动，保持零重打包特性。
    实现要点：**只替换「该条目自己的 integrity 对象」那一段文本**——早期版本按哈希字符串
    全局查找替换，当同一哈希串在头部出现两次（≤4MB 文件的整文件哈希 == 唯一块哈希，或
    另一个内容完全相同的条目）时会误改到别的条目，此处改为整对象唯一定位。"""
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
    if itg == new_itg:
        return
    if len(itg.get("blocks") or []) != len(new_itg["blocks"]):
        print(f"    [!] {entry_path} 的分块数变化，跳过 integrity 同步")
        return
    old_text = json.dumps(itg, separators=(",", ":")).encode()
    new_text = json.dumps(new_itg, separators=(",", ":")).encode()
    if len(old_text) != len(new_text):
        print(f"    [!] {entry_path} 的 integrity 段长度变化，跳过同步")
        return

    # 定位方式①（首选）：用条目自己的 size/offset 前缀锚定，再核对紧随其后的对象文本。
    # offset 唯一，因此这对锚点在整个头部里唯一，同内容条目也不会互相误伤。
    pos = -1
    try:
        prefix = f'"size":{int(ent["size"])},"offset":"{ent["offset"]}","integrity":'.encode()
        if hdr.count(prefix) == 1:
            start = hdr.find(prefix) + len(prefix)
            if hdr[start:start + len(old_text)] == old_text:
                pos = start
    except (KeyError, TypeError, ValueError):
        pass
    # 定位方式②（兜底）：整段对象文本在头部唯一
    if pos < 0 and hdr.count(old_text) == 1:
        pos = hdr.find(old_text)
    if pos < 0:
        print(f"    [!] {entry_path} 的 integrity 段无法唯一定位，跳过同步（内容已改、记录未同步）")
        return
    if dry_run:
        print(f"    [~] 将同步 {entry_path} 的 integrity 记录")
        return
    with open(asar, "r+b") as f:
        f.seek(16 + pos)
        f.write(new_text)


def process_usage_chart(asar: Path, check_only: bool, revert: bool, *,
                        dry_run: bool = False) -> bool:
    """用量页去截断。返回 True=成功/无需改动，False=失败。"""
    side = asar.with_name(asar.name + ".chart-patch.json")
    try:
        asar_size = asar.stat().st_size
        entries = _asar_header_index(asar)
    except OSError as e:
        print(f"[!] 无法读取 {asar}：{e}")
        return False

    specs = []
    for item in USAGE_PATCHES:
        match = [(p, o, s) for p, o, s in entries if item["key"] in p]
        if len(match) != 1:
            print(f"[!] {asar}\n    {item['key']} 命中 {len(match)} 个文件（期望 1），该项跳过")
            continue
        specs.append((item, match[0]))

    try:
        if revert:
            saved = _load_sidecar(side, asar_size)
            if not saved:
                print(f"[.] {asar}\n    没有当前版本的 sidecar 备份，跳过")
                return True
            if dry_run:
                print(f"[~] {asar}\n    将还原 {len(saved)} 处原始字节")
                return True
            with open(asar, "r+b") as f:
                for rec in saved:
                    f.seek(rec["offset"])
                    f.write(base64.b64decode(rec["original_b64"]))
            for rec in saved:
                _asar_sync_integrity(asar, rec["path"], base64.b64decode(rec["original_b64"]))
            side.unlink()
            print(f"[+] {asar}\n    已还原 {len(saved)} 处原始字节")
            return True

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
                    _asar_sync_integrity(asar, path, raw, dry_run=dry_run)
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
            if dry_run:
                print(f"    [~] {fname} | 将替换 {item['pattern'].decode()}（{item['desc']}）")
                changed = True
                continue
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
        if changed and not check_only and not dry_run:
            side.write_text(json.dumps({"patches": saved}, ensure_ascii=False), encoding="utf-8")
            print(f"    原始字节备份: {side.name}")
        return True
    except OSError as e:
        print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n"
              f"    请完全退出 ZCode 后重试（Program Files 下需管理员终端）")
        return False


# ------------------------------------------------- 模型弹窗加宽（asar 内同长度原地改字节）

def process_model_width(asar: Path, check_only: bool, revert: bool, *,
                        dry_run: bool = False) -> bool:
    """模型选择弹窗加宽：把 Tailwind 宽度类 w-48(192px) 换成 w-80(320px)。
    与图表补丁同为「同长度字节级原地覆盖」（w-48/w-80 字面量等长），零重打包。
    文件按内容特征定位（不依赖 assets 文件名哈希），跨版本稳定。
    返回 True=成功/无需改动，False=失败。"""
    side = asar.with_name(asar.name + ".width-patch.json")
    try:
        asar_size = asar.stat().st_size
    except OSError as e:
        print(f"[!] 无法读取 {asar}：{e}")
        return False

    try:
        if revert:
            saved = _load_sidecar(side, asar_size)
            if not saved:
                print(f"[.] {asar}\n    没有当前版本的 sidecar 备份，跳过")
                return True
            if dry_run:
                print(f"[~] {asar}\n    将还原 {len(saved)} 处原始宽度字节")
                return True
            with open(asar, "r+b") as f:
                for rec in saved:
                    f.seek(rec["offset"])
                    f.write(base64.b64decode(rec["original_b64"]))
            for rec in saved:
                _asar_sync_integrity(asar, rec["path"], base64.b64decode(rec["original_b64"]))
            side.unlink()
            print(f"[+] {asar}\n    已还原 {len(saved)} 处原始宽度字节")
            return True

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
                        _asar_sync_integrity(asar, done[0][0], done[0][3], dry_run=dry_run)
                    continue
                # 或「上游已修」：3.14 起该浮窗分支改为按内容自适应宽度，长名不再截断
                if item.get("upstream_fixed"):
                    up = _scan(item["upstream_fixed"])
                    if len(up) == 1 and up[0][4] == 1:
                        print(f"    [=] {up[0][0].split('/')[-1]} | 上游已改为自适应宽度，无需补丁"
                              f"（{item['desc']}）")
                        continue
                if item.get("optional"):
                    print(f"    [.] {item['desc']} | 本版本无此补丁点（可选），跳过")
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
            if check_only:
                print(f"    [ ] {fname} | 未打（{item['desc']}）")
                continue
            if dry_run:
                print(f"    [~] {fname} | 将替换 {item['pattern'].decode()} → "
                      f"{item['replacement'].decode()}（{item['desc']}）")
                changed = True
                continue
            if not any(r.get("offset") == off for r in saved):
                saved.append({
                    "path": path, "offset": off, "size": size,
                    "asar_size": asar_size, "original_b64": base64.b64encode(raw).decode(),
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

        if changed and not check_only and not dry_run:
            side.write_text(json.dumps({"patches": saved}, ensure_ascii=False), encoding="utf-8")
            print(f"    原始字节备份: {side.name}")
        return True
    except OSError as e:
        print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n"
              f"    请完全退出 ZCode 后重试（Program Files 下需管理员终端）")
        return False


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
    # 注入段整体以换行开头（read_h 模板首字符），比对/剥离都要把它算进去，
    # 否则永远比不中 → 每次 --check 都误报「含旧版组件」、每次打补丁都白重写一遍。
    seg_start = m.start() - 1 if m.start() > 0 and blob[m.start() - 1:m.start()] == b"\n" else m.start()
    stripped = blob[:seg_start] + blob[a.start():]
    if blob[seg_start:a.start()] == _puller_main_injection(alias):
        return True, True, stripped, alias
    return True, False, stripped, alias


def _puller_main_injection(ipc_alias: str) -> bytes:
    """在 ipcMain 别名的 SaveMcpToUserDirectory 注册语句前插入 3 个 handler。
    3.14.x 起界面供应商列表真正读写 <dataBaseDir>/.zcode/v2/provider_config.json
    （schemaVersion 1：providerRules.personalModelIds/modelOrder + providerModelRules），
    config.json 仅存模型元数据；dataBaseDir 从 ~/.zcode/v2/setting.json 读出（与应用
    bootstrap 同逻辑）。故：读时把 provider_config 合并成旧格式视图（personalModelIds
    为唯一事实源），写时同时回写 provider_config 与 config.json。"""
    h = ipc_alias
    read_h = '''
__H__.handle("zcode:read-model-config",async()=>{
try{
let{default:e}=await import("node:fs"),{default:t}=await import("node:path"),{default:n}=await import("node:os");
let base=n.homedir();
try{let s=JSON.parse(e.readFileSync(t.join(base,".zcode","v2","setting.json"),"utf-8"));
if(s&&typeof s.dataBaseDir=="string"&&s.dataBaseDir.trim())base=s.dataBaseDir.trim()}catch(_){}
let root=t.join(base,".zcode","v2");
let cfg={provider:{}};
try{let d=JSON.parse(e.readFileSync(t.join(root,"config.json"),"utf-8"));
if(d&&typeof d=="object"&&!Array.isArray(d))cfg=d}catch(_){}
if(!cfg.provider||typeof cfg.provider!="object"||Array.isArray(cfg.provider))cfg.provider={};
let legacy={};
try{legacy=JSON.parse(e.readFileSync(t.join(root,"provider_config.json"),"utf-8"))}catch(_){}
let rules=(legacy&&legacy.config&&legacy.config.providerConfigRules&&legacy.config.providerConfigRules.providerRules)||[];
let kt=(y)=>/anthropic/i.test(String(y))?"anthropic":(/responses/i.test(String(y))?"openai":"openai-compatible");
// 以 config.json 为基准「只补充、不裁剪」：保留条目上的全部字段与全部模型。
// （旧实现用 personalModelIds 重建 models、只保留 5 个字段，写回时会静默丢配置）
for(let rule of rules){
let pid=rule&&rule.providerId;
if(!pid)continue;
let c=rule.config||{},api=c.api||{};
let old=cfg.provider[pid];
if(!old||typeof old!="object"||Array.isArray(old))old=cfg.provider[pid]={};
let models=(old.models&&typeof old.models=="object"&&!Array.isArray(old.models))?old.models:(old.models={});
let ids=Array.isArray(c.personalModelIds)?c.personalModelIds:[];
for(let id0 of ids){
if(typeof id0!="string")continue;
let id=id0.trim();
if(!id||models[id])continue;
models[id]={limit:{context:1000000,output:128000},modalities:{input:["text","image"],output:["text"]},zcode:{modalitiesConfigured:!0,modified:!0}}
}
if(!old.name)old.name=rule.providerName||pid;
if(!old.kind)old.kind=kt(api.type||"");
if(!old.source)old.source="custom";
let opts=(old.options&&typeof old.options=="object"&&!Array.isArray(old.options))?old.options:(old.options={});
if(!opts.baseURL&&api.baseUrl)opts.baseURL=api.baseUrl;
}
// 不回传 apiKey：渲染层只需要 baseURL 与模型名；写回时由主进程保留磁盘上的原 Key
for(let p of Object.values(cfg.provider)){
if(p&&p.options&&typeof p.options=="object")delete p.options.apiKey;
}
return{success:!0,data:cfg}
}catch(e){return{success:!1,error:String(e)}}});
'''.replace("__H__", h)
    write_h = '''
__H__.handle("zcode:write-model-config",async(e,t)=>{
try{
if(typeof t!="object"||t===null)throw new Error("invalid config payload");
let{default:n}=await import("node:fs"),{default:r}=await import("node:path"),{default:i}=await import("node:os");
let base=i.homedir();
try{let s=JSON.parse(n.readFileSync(r.join(base,".zcode","v2","setting.json"),"utf-8"));
if(s&&typeof s.dataBaseDir=="string"&&s.dataBaseDir.trim())base=s.dataBaseDir.trim()}catch(_){}
let root=r.join(base,".zcode","v2");
let o=r.join(root,"config.json");
// 渲染层不回传 apiKey（读接口已脱敏）：条目缺 apiKey 时保留磁盘上的原值，
// 避免"界面读不到 Key → 保存把 Key 抹掉"。
try{
let prev={};try{prev=JSON.parse(n.readFileSync(o,"utf-8"))}catch(_){}
for(let pid of Object.keys(t.provider||{})){
let np=t.provider[pid],op=prev&&prev.provider?prev.provider[pid]:null;
if(!np||typeof np!="object")continue;
if(!np.options||typeof np.options!="object")np.options={};
if(!np.options.apiKey&&op&&op.options&&op.options.apiKey)np.options.apiKey=op.options.apiKey;
}
}catch(_){}
// 备份只在首次创建时写：否则第二次保存会把"已改过的版本"当备份，失去回滚意义
if(!n.existsSync(o+".puller-bak"))try{n.writeFileSync(o+".puller-bak",n.readFileSync(o,"utf-8"))}catch(a){}
n.writeFileSync(o,JSON.stringify(t,null,2),"utf-8");
try{
let pcPath=r.join(root,"provider_config.json");
let pc={};
try{pc=JSON.parse(n.readFileSync(pcPath,"utf-8"))}catch(_){}
if(!pc||typeof pc!="object"||Array.isArray(pc))pc={schemaVersion:1,config:{}};
pc.schemaVersion=pc.schemaVersion||1;
pc.config=pc.config||{};
let pcr=pc.config.providerConfigRules=pc.config.providerConfigRules||{};
let rules=Array.isArray(pcr.providerRules)?pcr.providerRules:(pcr.providerRules=[]);
let mcr=pc.config.modelConfigRules=pc.config.modelConfigRules||{};
let mrules=Array.isArray(mcr.providerModelRules)?mcr.providerModelRules:(mcr.providerModelRules=[]);
let byId={};
for(let rule of rules){if(rule&&rule.providerId)byId[rule.providerId]=rule}
let kt=(y)=>/anthropic/i.test(String(y))?"anthropic-messages":(/responses/i.test(String(y))?"openai-responses":"openai-chat-completions");
for(let ent0 of Object.entries(t.provider||{})){
let pid=ent0[0],pdata=ent0[1];
if(!pdata||typeof pdata!="object")continue;
if(String(pid).startsWith("builtin:"))continue;
let models=pdata.models||{};
let ids=Object.keys(models);
let rule=byId[pid];
if(!rule){
let bu=String((pdata.options&&pdata.options.baseURL)||"").replace(/\\/+$/,"");
if(bu){for(let r2 of rules){let u=(r2&&r2.config&&r2.config.api&&r2.config.api.baseUrl)||"";
if(u.replace(/\\/+$/,"")===bu){rule=r2;break}}}}
if(!rule){rule={providerId:pid,providerName:pdata.name||pid,config:{group:"standard-personal",access:{type:"api-key"},api:{type:kt(pdata.kind||"")},personalModelIds:[],modelOrder:[]}};rules.push(rule);byId[pid]=rule}
let c=rule.config=rule.config||{};
c.group=c.group||"standard-personal";
c.access=c.access||{type:"api-key"};
if(pdata.options&&pdata.options.apiKey)c.access.apiKey=pdata.options.apiKey;
c.api=c.api||{};
if(pdata.options&&pdata.options.baseURL)c.api.baseUrl=pdata.options.baseURL;
if(pdata.kind)c.api.type=kt(pdata.kind);
c.personalModelIds=ids.slice();
c.modelOrder=ids.slice();
let oldRules={},keep=[];
let manual=new Set();
let manualArr=(pc.config&&pc.config.modelConfigRules&&pc.config.modelConfigRules.manualProviderModelRules)||[];
for(let m of manualArr){
if(m&&m.modelId)manual.add(m.providerId+"/"+m.modelId)
}
for(let m of mrules){
if(m&&m.providerId===pid){oldRules[m.modelId]=m;continue}
keep.push(m)
}
for(let mid of ids){
if(oldRules[mid]&&!manual.has(pid+"/"+mid)){keep.push(oldRules[mid]);continue}
if(manual.has(pid+"/"+mid))continue;
let ent=models[mid]||{};
keep.push({modelId:mid,providerId:pid,config:{properties:{contextWindow:(ent.limit&&ent.limit.context)||1000000}}})
}
mrules.length=0;
for(let m of keep)mrules.push(m)
}
if(!n.existsSync(pcPath+".puller-bak"))try{n.writeFileSync(pcPath+".puller-bak",n.readFileSync(pcPath,"utf-8"))}catch(a2){}
n.writeFileSync(pcPath,JSON.stringify(pc,null,2),"utf-8");}catch(syncErr){return{success:!0,warn:"config.json written; provider_config.json sync failed: "+String(syncErr)}}
return{success:!0}}
catch(n){return{success:!1,error:String(n)}}});
'''.replace("__H__", h)
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

    tmp = asar.with_name(f"{asar.name}.{os.getpid()}.tmp")
    try:
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
        for p, want in overwrite.items():
            ent = v_files.get(p)
            if ent is None or _asar_entry_bytes(v_raw, v_start, ent) != want:
                raise ValueError(f"重打包校验失败: {p}")
        os.replace(tmp, asar)
    except BaseException:
        tmp.unlink(missing_ok=True)   # 失败/被占用都不留临时文件残留
        raise
    _cleanup_stale_tmp(asar.parent)
    return asar.stat().st_size


def _cleanup_stale_tmp(folder: Path) -> None:
    """清理历史遗留的临时文件（早期版本固定用 app.asar.tps-tmp，异常退出会留在安装目录）。"""
    for pat in ("app.asar*.tps-tmp", "app.asar*.tmp"):
        for f in folder.glob(pat):
            try:
                f.unlink()
                print(f"    [·] 已清理残留临时文件 {f.name}")
            except OSError:
                pass


def _refresh_chart_sidecar(asar: Path) -> None:
    """asar 重打包后数据区整体位移：字节级补丁（chart/width）记录里的绝对 offset 与
    asar_size 指纹都要重定位；重打包级补丁（tps/slider/puller）的 sidecar 只记 asar_size，
    也必须跟着更新——否则后续 `--check` 会把 sidecar 报成「无」，看着像记录丢了。"""
    for suffix in (".chart-patch.json", ".width-patch.json"):
        _relocate_sidecar(asar, suffix)
    try:
        cur_size = asar.stat().st_size
    except OSError:
        return
    for suffix in (".tps-patch.json", ".slider-patch.json", ".puller-patch.json"):
        side = asar.with_name(asar.name + suffix)
        if not side.is_file():
            continue
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("asar_size") != cur_size:
            rec["asar_size"] = cur_size
            try:
                side.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                print(f"[*] 已同步 {side.name} 的指纹到重打包后的 asar")
            except OSError:
                pass


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


def _mark_backup_patched(backup: Path, current: Path) -> None:
    """补丁写入后刷新备份指纹里的「已打形态」哈希（还原前校验要用）。"""
    meta = _load_backup_meta(backup)
    if not meta:
        return
    try:
        data = current.read_bytes()
        meta["patched_sha256"] = _sha256(data)
        meta["patched_size"] = len(data)
        _backup_meta(backup).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    except OSError:
        pass


def _process_script_inject(asar: Path, check_only: bool, revert: bool, src_path: Path | None,
                           *, label: str, script_entry: str, tag: str,
                           side_suffix: str, bak_suffix: str,
                           dry_run: bool = False, version: str | None = None) -> bool:
    """renderer 单脚本注入通用链路(TPS 统计栏 / 思考强度滑条共用):
    index.html </body> 前挂 <script> + 新增脚本条目,整体重排 asar、重算 integrity。
    ⚠️ zcode-tps.js 内**绝不调用 port.start()**(只 addEventListener,start 交给应用)——
    否则会消费掉服务端 Initialize 启动握手,ZCode 3.12.2 会卡在启动界面。
    详见 scripts/zcode-tps.js 头部说明与 SKILL.md。
    返回 True=成功/无需改动，False=失败。"""
    side = asar.with_name(asar.name + side_suffix)
    bak = asar.with_name(asar.name + bak_suffix)
    tag_b = tag.encode()

    try:
        asar_size = asar.stat().st_size
        raw, header, data_start = _asar_header_raw(asar)
    except (OSError, ValueError) as e:
        print(f"[!] {asar}\n    无法读取：{e}")
        return False
    paths = {p: ent for p, ent in _asar_walk_entries(header)}
    idx_ent = paths.get(TPS_INDEX_PATH)
    if idx_ent is None:
        print(f"[!] {asar}\n    未找到 {TPS_INDEX_PATH}，版本结构可能已变，跳过")
        return False
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
        return True

    if revert:
        if not installed and not saved:
            print(f"[.] {asar}\n    未打{label}注入，跳过")
            return True
        if dry_run:
            print(f"[~] {asar}\n    将移除{label}注入（摘除 index.html 的 tag 与脚本条目）")
            return True
        # 外科手术式还原：只从当前 index.html 摘除本补丁的 tag，不动其他补丁
        # （如 --model-puller）对同一文件的改动；sidecar 原件仅作兜底。
        stripped = idx_bytes.replace(tag_b + b"\n", b"").replace(tag_b, b"")
        if stripped == idx_bytes and saved and saved.get("index_original_b64"):
            stripped = base64.b64decode(saved["index_original_b64"])
        try:
            new_size = _repack_asar(asar, {TPS_INDEX_PATH: stripped}, {script_entry})
        except OSError as e:
            print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
            return False
        side.unlink(missing_ok=True)
        bak.unlink(missing_ok=True)
        _backup_meta(bak).unlink(missing_ok=True)
        _refresh_chart_sidecar(asar)
        print(f"[+] {asar}\n    已移除{label}注入（新大小 {new_size:,} 字节，备份已清理）")
        return True

    if src_path is None:
        src_path = Path(__file__).resolve().parent / script_entry.split("/")[-1]
    if not src_path.is_file():
        print(f"[!] 找不到注入源脚本 {src_path}（可用对应 --*-src 指定路径）")
        return False
    script_bytes = src_path.read_bytes()

    # 脚本热更新：已注入但内容与源不一致时替换脚本条目（无需先 revert）。
    # 只按"脚本条目是否存在"判断会让改动后的脚本永不生效（版本适配修复无法落地）。
    if installed:
        cur_script = _asar_entry_bytes(raw, data_start, paths[script_entry]) if script_entry in paths else None
        if cur_script == script_bytes:
            print(f"[=] {asar}\n    已打{label}注入（脚本同源），跳过")
            return True
        if dry_run:
            print(f"[~] {asar}\n    将热更新{label}脚本（{len(cur_script or b''):,} → {len(script_bytes):,} 字节）")
            return True
        try:
            new_size = _repack_asar(asar, {script_entry: script_bytes}, set())
        except OSError as e:
            print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
            return False
        _refresh_chart_sidecar(asar)
        print(f"[+] {asar}\n    {label}脚本已热更新（{src_path.name} {len(script_bytes):,} 字节，其余组件不变）")
        return True

    if idx_bytes.count(b"</body>") != 1:
        print(f"[!] {asar}\n    index.html 的 </body> 出现 {idx_bytes.count(b'</body>')} 次（期望 1），拒绝盲改")
        return False

    if dry_run:
        print(f"[~] {asar}\n    将注入 {script_entry}（{len(script_bytes):,} 字节）并在 index.html 挂载 tag")
        return True

    if _ensure_backup(bak, asar, version) is None:
        return False

    new_idx = idx_bytes.replace(b"</body>", tag_b + b"</body>", 1)
    try:
        new_size = _repack_asar(asar, {TPS_INDEX_PATH: new_idx, script_entry: script_bytes}, set())
    except OSError as e:
        print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
        return False
    _mark_backup_patched(bak, asar)
    side.write_text(json.dumps({
        "asar_size": new_size,
        "index_path": TPS_INDEX_PATH,
        "script_entry": script_entry,
        "index_original_b64": base64.b64encode(idx_bytes).decode(),
    }, ensure_ascii=False), encoding="utf-8")
    _refresh_chart_sidecar(asar)
    print(f"[+] {asar}\n    {label}注入完成（{src_path.name} {len(script_bytes):,} 字节 -> {script_entry}，index.html 已挂载）\n"
          f"    原件备份: {bak.name} | 记录: {side.name}")
    return True


def process_tps_footer(asar: Path, check_only: bool, revert: bool, tps_src: Path | None, *,
                       dry_run: bool = False, version: str | None = None) -> bool:
    return _process_script_inject(asar, check_only, revert, tps_src,
                                  label="TPS 统计栏", script_entry=TPS_SCRIPT_PATH, tag=TPS_TAG,
                                  side_suffix=".tps-patch.json", bak_suffix=".tps.bak",
                                  dry_run=dry_run, version=version)


def process_thought_slider(asar: Path, check_only: bool, revert: bool, slider_src: Path | None, *,
                           dry_run: bool = False, version: str | None = None) -> bool:
    return _process_script_inject(asar, check_only, revert, slider_src,
                                  label="思考强度滑条", script_entry=SLIDER_SCRIPT_PATH, tag=SLIDER_TAG,
                                  side_suffix=".slider-patch.json", bak_suffix=".slider.bak",
                                  dry_run=dry_run, version=version)


def process_model_puller(asar: Path, check_only: bool, revert: bool, puller_src: Path | None, *,
                         dry_run: bool = False, version: str | None = None) -> bool:
    """模型拉取按钮注入。返回 True=成功/无需改动，False=失败。"""
    side = asar.with_name(asar.name + ".puller-patch.json")
    bak = asar.with_name(asar.name + ".puller.bak")

    try:
        asar_size = asar.stat().st_size
        raw, header, data_start = _asar_header_raw(asar)
    except (OSError, ValueError) as e:
        print(f"[!] {asar}\n    无法读取：{e}")
        return False
    paths = {p: ent for p, ent in _asar_walk_entries(header)}
    idx_ent = paths.get(PULLER_INDEX_PATH)
    if idx_ent is None:
        print(f"[!] {asar}\n    未找到 {PULLER_INDEX_PATH}，版本结构可能已变，跳过")
        return False
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
        return True

    if revert:
        if not installed and not saved:
            print(f"[.] {asar}\n    未打模型拉取注入，跳过")
            return True
        # 全程外科手术式还原：只摘除自己注入的字节段，不依赖 sidecar 指纹
        stripped = idx_bytes.replace(PULLER_TAG.encode() + b"\n", b"").replace(PULLER_TAG.encode(), b"")
        overwrite = {PULLER_INDEX_PATH: stripped}
        if pre_inj:
            if pre_clean is None:
                print(f"[!] {asar}\n    preload 注入段形态不符（可能被手工改过），拒绝还原")
                return False
            overwrite[PULLER_PRELOAD_PATH] = pre_clean
        if main_inj:
            if main_clean is None:
                print(f"[!] {asar}\n    main 注入段形态不符（可能被手工改过），拒绝还原")
                return False
            overwrite[PULLER_MAIN_PATH] = main_clean
        if dry_run:
            print(f"[~] {asar}\n    将还原模型拉取注入（摘除 index.html tag + 还原 preload/main 注入段）")
            return True
        try:
            new_size = _repack_asar(asar, overwrite, {PULLER_SCRIPT_PATH} if script_present else set())
        except OSError as e:
            print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
            return False
        side.unlink(missing_ok=True)
        bak.unlink(missing_ok=True)
        _backup_meta(bak).unlink(missing_ok=True)
        _refresh_chart_sidecar(asar)
        print(f"[+] {asar}\n    已还原模型拉取注入（新大小 {new_size:,} 字节，备份已清理）")
        return True

    if not puller_src.is_file():
        print(f"[!] 找不到注入源脚本 {puller_src}（可用 --puller-src 指定路径）")
        return False
    script_bytes = puller_src.read_bytes()

    # 幂等：四组件齐、且 renderer/preload/main 注入内容均与现行实现逐字节一致才跳过。
    # 只按 marker 判断会让旧版注入被误认成"已是最新"（换实现后不更新），必须内容比对。
    if installed and cur_script == script_bytes and pre_synced and main_synced:
        print(f"[=] {asar}\n    已打模型拉取注入（四组件均为当前版本），跳过")
        return True

    # 锚点核实：三处定位全部唯一才动手
    if idx_bytes.count(b"</body>") != 1:
        print(f"[!] {asar}\n    index.html 的 </body> 出现 {idx_bytes.count(b'</body>')} 次（期望 1），拒绝盲改")
        return False
    pre_m = PULLER_PRELOAD_ANCHOR.findall(pre_blob or b"")
    main_m = PULLER_MAIN_ANCHOR.findall(main_blob or b"")
    if len(pre_m) != 1 or len(main_m) != 1:
        print(f"[!] {asar}\n    锚点异常：preload 命中 {len(pre_m)} 次、main 命中 {len(main_m)} 次（各期望 1），"
              f"版本结构可能已变，拒绝盲改\n    preload 锚点: {PULLER_PRELOAD_ANCHOR.pattern}\n"
              f"    main 锚点: {PULLER_MAIN_ANCHOR.pattern}")
        return False

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
            return False
        anchor = PULLER_PRELOAD_ANCHOR.search(pre_clean).end()
        overwrite[PULLER_PRELOAD_PATH] = (pre_clean[:anchor]
                                          + _puller_preload_injection(pre_m[0].decode())
                                          + pre_clean[anchor:])
        rec.setdefault("preload_original_b64", base64.b64encode(pre_clean).decode())
    if not main_synced:
        if main_clean is None:
            print(f"[!] {asar}\n    main 已注入但形态不符（旧版/被改过），无法安全更新，拒绝"
                  f"（先 --revert 或重装 ZCode）")
            return False
        anchor = PULLER_MAIN_ANCHOR.search(main_clean).start()
        overwrite[PULLER_MAIN_PATH] = (main_clean[:anchor]
                                       + _puller_main_injection(main_m[0].decode())
                                       + main_clean[anchor:])
        rec.setdefault("main_original_b64", base64.b64encode(main_clean).decode())

    if dry_run:
        print(f"[~] {asar}\n    将写入组件: {'+'.join(sorted(overwrite)) or '（无变化）'}")
        return True

    if _ensure_backup(bak, asar, version) is None:
        return False

    try:
        new_size = _repack_asar(asar, overwrite, set())
    except OSError as e:
        print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
        return False
    _mark_backup_patched(bak, asar)
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
    return True


# ==================== 3.14+ 原生档位配置（provider_config.json，无需内核补丁） ====================
# 3.14 起内核不再读 config.json 的 reasoning.variants / defaultVariant（内核里已无这两个符号，
# providerOptionsByLevel 只剩 schema 定义），档位改为「配置侧原生」：
#
#   provider_config.json
#     └ config.modelConfigRules.providerModelRules[]
#         { modelId, providerId,
#           config: { properties: {contextWindow},
#                     optionSpecs: { reasoningLevel: { values:[...], map:"<CEL>" } } } }
#
#   * values → 界面档位列表（**末位即默认档**），内核 y$o/listThoughtLevels 从这里取
#   * map    → CEL 表达式，请求发出前由 createModelOptionMapFetch 合并进请求体 JSON
#   * 与 manualProviderModelRules 冲突（同 providerId+modelId 不能同时在两个列表，否则内核
#     schema 校验失败、整个供应商配置降级为空）——本命令遇到冲突只报告不写入。
#
# 本命令把 config.json 里各模型已配的档位（旧 reasoning.variants 或已有 optionSpecs）
# 同步进 provider_config.json，使自定义模型在新内核上真正下发档位参数。

# map 直接采用「ZCode 自己会写的写法」（内置规则 / 界面手动配置生成的一致形态），
# 保证 CEL 一定能编译通过、行为与官方路径一致：
#   openai 兼容：thinking + enable_thinking + reasoning_effort 三键（界面手动配置即此形态）
#   anthropic ：thinking(adaptive) + output_config.effort，关闭档发 thinking.disabled
# 档名不在 disabled/none/enabled 之内时按原名透传（网关认识就透传、不认识自行降级）。
REASONING_MAP_OPENAI = """{
  "thinking": {
    "type": reasoningLevel == "disabled" || reasoningLevel == "none" ? "disabled" : "enabled"
  },
  "enable_thinking": reasoningLevel != "disabled" && reasoningLevel != "none",
  "reasoning_effort": reasoningLevel == "disabled" ? "none" : reasoningLevel == "enabled" ? "high" : reasoningLevel
}"""
REASONING_MAP_ANTHROPIC = (
    'reasoningLevel == "disabled" || reasoningLevel == "none" || reasoningLevel == "off"\n'
    '  ? {"thinking":{"type":"disabled"}}\n'
    '  : {"thinking":{"type":"adaptive"},"output_config":{"effort": reasoningLevel == "enabled" ? "high" : reasoningLevel}}'
)


def _v2_root(override: str | None = None) -> Path:
    """配置根目录 <dataBaseDir>/.zcode/v2（dataBaseDir 取自 ~/.zcode/v2/setting.json，
    与客户端 bootstrap 同逻辑）。"""
    if override:
        return Path(override)
    base = Path.home()
    try:
        s = json.loads((base / ".zcode" / "v2" / "setting.json").read_text(encoding="utf-8"))
        dbd = s.get("dataBaseDir")
        if isinstance(dbd, str) and dbd.strip():
            base = Path(dbd.strip())
    except Exception:
        pass
    return base / ".zcode" / "v2"


def _desired_levels(entry: dict) -> tuple[list[str], str] | None:
    """从 config.json 的模型条目推导档位列表，返回 (levels, 来源)。"""
    specs = entry.get("optionSpecs")
    if isinstance(specs, dict):
        rl = specs.get("reasoningLevel")
        if isinstance(rl, dict) and isinstance(rl.get("values"), list):
            vals = [str(v).strip() for v in rl["values"] if str(v).strip()]
            if vals:
                return vals, "config.optionSpecs"
    r = entry.get("reasoning")
    if isinstance(r, dict):
        vals = [str(v).strip() for v in (r.get("variants") or []) if str(v).strip()]
        if vals:
            d = r.get("defaultVariant")
            if isinstance(d, str) and d.strip() in vals:
                vals = [v for v in vals if v != d.strip()] + [d.strip()]   # 末位即默认档
            return vals, "config.reasoning.variants"
    return None


def process_reasoning_config(v2_root: Path, check_only: bool, revert: bool, *,
                             dry_run: bool = False) -> bool:
    """把 config.json 的档位同步进 provider_config.json（3.14+ 原生机制）。
    返回 True=成功/无需改动，False=失败。"""
    cfg_path = v2_root / "config.json"
    pc_path = v2_root / "provider_config.json"
    bak = pc_path.with_name(pc_path.name + ".reasoning-bak")

    if not cfg_path.is_file():
        print(f"[!] 找不到配置文件 {cfg_path}")
        return False
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] 解析 config.json 失败：{e}")
        return False

    if revert:
        if not bak.is_file():
            print(f"[.] {v2_root}\n    没有档位配置备份（{bak.name}），跳过")
            return True
        if dry_run:
            print(f"[~] 将用 {bak.name} 还原 provider_config.json")
            return True
        try:
            shutil.copyfile(bak, pc_path)
        except OSError as e:
            print(f"[!] 还原失败：{e}（请先完全退出 ZCode）")
            return False
        bak.unlink(missing_ok=True)
        print(f"[+] {v2_root}\n    已还原 provider_config.json 并删除备份")
        return True

    try:
        pc = json.loads(pc_path.read_text(encoding="utf-8")) if pc_path.is_file() else {}
    except Exception as e:
        print(f"[!] 解析 provider_config.json 失败：{e}")
        return False
    if not isinstance(pc, dict):
        pc = {}
    pc.setdefault("schemaVersion", 1)
    conf = pc.setdefault("config", {})
    mcr = conf.setdefault("modelConfigRules", {})
    rules = mcr.setdefault("providerModelRules", [])
    if not isinstance(rules, list):
        rules = mcr["providerModelRules"] = []
    manual = {f"{m.get('providerId')}/{m.get('modelId')}"
              for m in (mcr.get("manualProviderModelRules") or []) if isinstance(m, dict)}

    print(f"[*] {pc_path}")
    print(f"    配置根目录: {v2_root}")

    by_key = {(r.get("providerId"), r.get("modelId")): r for r in rules if isinstance(r, dict)}
    planned, unchanged, no_levels, conflicts, builtin_skipped = [], [], [], [], 0

    for pid, pdata in (cfg.get("provider") or {}).items():
        if not isinstance(pdata, dict):
            continue
        if str(pid).startswith("builtin:"):
            builtin_skipped += 1          # 内置模板供应商：档位由内核内置规则提供，不介入
            continue
        kind = pdata.get("kind")
        for mid, entry in (pdata.get("models") or {}).items():
            if not isinstance(entry, dict):
                continue
            want = _desired_levels(entry)
            if not want:
                no_levels.append(f"{pid}/{mid}")
                continue
            levels, src = want
            if f"{pid}/{mid}" in manual:
                conflicts.append(f"{pid}/{mid}")
                continue
            map_expr = REASONING_MAP_ANTHROPIC if kind == "anthropic" else REASONING_MAP_OPENAI
            rule = by_key.get((pid, mid))
            rl = None
            if rule:
                rl = ((rule.get("config") or {}).get("optionSpecs") or {}).get("reasoningLevel")
            if isinstance(rl, dict) and rl.get("values") == levels and rl.get("map") == map_expr:
                unchanged.append(f"{pid}/{mid}")
                continue
            ctx = ((entry.get("limit") or {}).get("context")) or 1000000
            planned.append({"pid": pid, "mid": mid, "kind": kind, "levels": levels,
                            "map": map_expr, "src": src, "ctx": int(ctx), "existed": bool(rule)})

    for it in planned:
        print(f"    {'[ ]' if check_only else '[~]'} {it['pid']}/{it['mid']}"
              f"  →  {len(it['levels'])} 档 {'/'.join(it['levels'])}"
              f"  （{'更新已有规则' if it['existed'] else '新建规则'}，来源 {it['src']}）")
    if unchanged:
        print(f"    已是最新 {len(unchanged)} 个：{', '.join(unchanged[:6])}"
              + (" …" if len(unchanged) > 6 else ""))
    if no_levels:
        print(f"    未配档位 {len(no_levels)} 个（跳过）：{', '.join(no_levels[:6])}"
              + (" …" if len(no_levels) > 6 else ""))
    if conflicts:
        print(f"    [·] {len(conflicts)} 个模型已在界面「手动配置」过（manualProviderModelRules，"
              f"本身已带 optionSpecs、已生效）；内核禁止同一模型同时出现在两个规则列表，故不迁移："
              f"{', '.join(conflicts[:4])}")
    if builtin_skipped:
        print(f"    内置供应商 {builtin_skipped} 个未处理（档位由内核内置规则提供）")

    if not planned:
        print(f"[=] {pc_path}\n    档位配置已是最新，无需写入")
        return True
    if check_only:
        print("    （--check 只读；去掉 --check 即写入）")
        return True
    if dry_run:
        print(f"    （--dry-run 未写盘；将修改 {len(planned)} 条 providerModelRules）")
        return True

    if not bak.is_file() and pc_path.is_file():
        try:
            shutil.copyfile(pc_path, bak)
        except OSError as e:
            print(f"[!] 备份失败：{e}")
            return False

    for it in planned:
        rule = by_key.get((it["pid"], it["mid"]))
        if rule is None:
            rule = {"modelId": it["mid"], "providerId": it["pid"], "config": {}}
            rules.append(rule)
            by_key[(it["pid"], it["mid"])] = rule
        c = rule.get("config")
        if not isinstance(c, dict):
            c = rule["config"] = {}
        props = c.get("properties")
        if not isinstance(props, dict):
            props = c["properties"] = {}
        props.setdefault("contextWindow", it["ctx"])
        specs = c.get("optionSpecs")
        if not isinstance(specs, dict):
            specs = c["optionSpecs"] = {}
        specs["reasoningLevel"] = {"values": it["levels"], "map": it["map"]}

    tmp = pc_path.with_name(pc_path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(pc, ensure_ascii=False, indent=2), encoding="utf-8")
        back = json.loads(tmp.read_text(encoding="utf-8"))
        assert back["config"]["modelConfigRules"]["providerModelRules"] == rules
        os.replace(tmp, pc_path)          # 原子替换，避免半截文件
    except (OSError, ValueError, AssertionError, KeyError) as e:
        tmp.unlink(missing_ok=True)
        print(f"[!] 写入失败：{e}（请先完全退出 ZCode 再试）")
        return False

    print(f"[+] 已写入 {len(planned)} 个模型的档位（备份 {bak.name}）\n"
          f"    → 重启 ZCode 后，界面档位列表即来自 provider_config.json；\n"
          f"      请求体参数由内核按 optionSpecs.map 在发送前合并（无需内核补丁）")
    return True


def _resolve_asars(target: str | None) -> list[Path]:
    asars = []
    for cjs in resolve_target(target):
        asar = cjs.parent.parent / "app.asar"   # resources/glm/zcode.cjs -> resources/app.asar
        if asar.is_file() and asar not in asars:
            asars.append(asar)
    if not asars:
        raise SystemExit("[!] 未找到 app.asar")
    return asars


def zcode_running() -> bool:
    """ZCode 是否在运行（打补丁前预检：运行中会锁住 app.asar，配置也可能被回写覆盖）。"""
    try:
        if os.name == "nt":
            # 用 bytes 检索：tasklist 输出是 GBK，text=True 会在读线程抛 UnicodeDecodeError
            out = subprocess.run(["tasklist"], capture_output=True, timeout=15,
                                 creationflags=0x08000000).stdout or b""
            return b"ZCode.exe" in out
        r = subprocess.run(["pgrep", "-f", "ZCode"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ZCode 客户端补丁工具：思考档位（3.14+ 配置侧原生 / ≤3.11 内核补丁）+ 用量页去截断 "
                    "+ TPS 统计栏 + 思考滑条 + 模型拉取按钮（自动探测安装位置）")
    ap.add_argument("target", nargs="?", help="可选：安装根目录或 zcode.cjs 路径；缺省自动探测全部")
    mode_g = ap.add_mutually_exclusive_group()
    mode_g.add_argument("--check", action="store_true", help="只检查状态，不修改")
    mode_g.add_argument("--revert", action="store_true", help="从备份还原")
    ap.add_argument("--dry-run", action="store_true", help="只报告将要做的改动，不写盘")
    ap.add_argument("--force", action="store_true",
                    help="跳过备份指纹校验（备份与当前文件不是同一版本时强制继续，慎用）")
    ap.add_argument("--verbose", action="store_true", help="打印安装探测的每一步结果")
    ap.add_argument("--extract", action="store_true",
                    help="按结构特征提取当前内核的档位解析函数锚点（新版本升级后用）")
    ap.add_argument("--reasoning-config", action="store_true",
                    help="【3.14+ 推荐】把 config.json 的档位写进 provider_config.json 的 "
                         "optionSpecs（原生机制，无需内核补丁）")
    ap.add_argument("--v2-root", default=None,
                    help="覆盖 ZCode v2 配置根目录（默认按 setting.json 的 dataBaseDir 解析）")
    ap.add_argument("--usage-chart", action="store_true",
                    help="补丁用量页：去掉趋势图 Top6 与饼图 Top5+其他模型 的截断，全部模型展示")
    ap.add_argument("--model-width", action="store_true",
                    help="加宽模型选择弹窗（含渠道子菜单）192px→320px，长模型名不再被截断")
    ap.add_argument("--tps-footer", action="store_true",
                    help="注入 TPS 统计栏：输入框下方常驻统计条（本轮指标 + 会话累计），asar 重打包级")
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

    global VERBOSE
    VERBOSE = args.verbose

    asar_flags = any((args.usage_chart, args.model_width, args.tps_footer,
                      args.thought_slider, args.model_puller))
    mode = "检查" if args.check else ("还原" if args.revert else ("预演" if args.dry_run else "打补丁"))
    fails: list[str] = []

    # ---------- 预检：运行中一律不打补丁（asar 被锁、配置会被回写） ----------
    if (asar_flags or args.reasoning_config) and not args.check and not args.dry_run:
        if zcode_running():
            print("[!] 检测到 ZCode 正在运行 —— 打补丁/还原前请完全退出 ZCode")
            print("    运行中 app.asar 会被锁定；config.json / provider_config.json 也可能被客户端回写覆盖")
            print("    只查看状态：加 --check；预演改动：加 --dry-run")
            return 2

    if asar_flags:
        try:
            asars = _resolve_asars(args.target)
        except SystemExit as e:
            print(e)
            return 1
        version = asar_version(asars[0])
        print(f"=== 客户端版本 {version or '未知'} | 目标 {len(asars)} 处 | 模式：{mode} ===")

        steps = [
            (args.usage_chart, "用量页去截断补丁",
             lambda a: process_usage_chart(a, args.check, args.revert, dry_run=args.dry_run)),
            (args.model_width, "模型弹窗加宽补丁",
             lambda a: process_model_width(a, args.check, args.revert, dry_run=args.dry_run)),
            (args.tps_footer, "TPS 统计栏注入",
             lambda a: process_tps_footer(a, args.check, args.revert,
                                          Path(args.tps_src) if args.tps_src else None,
                                          dry_run=args.dry_run, version=version)),
            (args.thought_slider, "思考强度滑条注入",
             lambda a: process_thought_slider(a, args.check, args.revert,
                                              Path(args.slider_src) if args.slider_src else None,
                                              dry_run=args.dry_run, version=version)),
            (args.model_puller, "模型拉取按钮注入",
             lambda a: process_model_puller(a, args.check, args.revert,
                                            Path(args.puller_src) if args.puller_src else None,
                                            dry_run=args.dry_run, version=version)),
        ]
        for enabled, title, fn in steps:
            if not enabled:
                continue
            print(f"=== {title}，目标 {len(asars)} 处，模式：{mode} ===")
            for a in asars:
                try:
                    if not fn(a):
                        fails.append(f"{title} @ {a}")
                except Exception as e:                      # 兜底：不把裸堆栈甩给用户
                    print(f"[!] {a}\n    执行失败：{type(e).__name__}: {e}")
                    fails.append(f"{title} @ {a}")

    if args.reasoning_config:
        print(f"=== 3.14+ 原生档位配置（provider_config.json），模式：{mode} ===")
        try:
            if not process_reasoning_config(_v2_root(args.v2_root), args.check, args.revert,
                                            dry_run=args.dry_run):
                fails.append("3.14+ 原生档位配置")
        except Exception as e:
            print(f"[!] 执行失败：{type(e).__name__}: {e}")
            fails.append("3.14+ 原生档位配置")

    if not asar_flags and not args.reasoning_config:
        targets = resolve_target(args.target)
        if not targets:
            print("[!] 未探测到任何 ZCode 安装；请把安装目录路径作为参数传入（加 --verbose 看探测细节）")
            return 1
        if args.extract:
            for t in targets:
                print(f"[*] {t}")
                anchor = extract_anchor(t)
                if anchor:
                    print("[+] 提取成功，把下面整段加入脚本 ANCHORS 后重新运行打补丁：\n")
                    print(f'    "<版本号>": (\n        {anchor!r}\n    ),')
            return 0
        version = None
        try:
            version = asar_version(targets[0].parent.parent / "app.asar")
        except Exception:
            pass
        print(f"=== 客户端版本 {version or '未知'} | 探测到 {len(targets)} 处安装 | 模式：{mode} ===")
        for t in targets:
            try:
                if not process(t, args.check, args.revert,
                               dry_run=args.dry_run, force=args.force, version=version):
                    fails.append(f"思维强度内核补丁 @ {t}")
            except Exception as e:
                print(f"[!] {t}\n    执行失败：{type(e).__name__}: {e}")
                fails.append(f"思维强度内核补丁 @ {t}")

    if not args.check and not args.revert and not args.dry_run:
        print("=== 提示：完全退出并重启 ZCode 后生效；升级后需重新执行本脚本 ===")

    if fails:
        print(f"\n[!] 执行完成，但有 {len(fails)} 项未成功：")
        for f in fails:
            print("    -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
