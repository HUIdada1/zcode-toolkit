# zcode-tokenspeed · ZCode 客户端增强插件

给 ZCode 桌面客户端补上几件顺手的事：**自定义模型的思考档位真正生效**、**用量页图表不再截断**、
**输入框实时 TPS 统计条**、**思考强度滑条**、**一键增强提示词**、**设置页一键拉取模型**。

纯 Python 标准库实现，不依赖 Node、不需要编译；所有改动都作用于**本地已安装的客户端文件**，
幂等、可 `--check` 核查、可 `--revert` 精确还原、可在插件详情页逐项开关。

> **非官方项目**，与 ZCode 官方无任何关联。请遵守 ZCode 软件许可协议，因使用本工具产生的一切后果由使用者自行承担。
> 第三方组件的许可声明见 [NOTICE.md](NOTICE.md)。
>
> 仓库名是 `zcode-toolkit`，插件在 ZCode 里的名字是 **`zcode-tokenspeed`**（安装目录、技能目录与
> 开关配置键都用这个 id）——两者故意不同：改插件 id 会让已有用户的开关配置全部失效。

---

## 三分钟装好（速览）

| 步骤 | 做什么 |
|---|---|
| 1 | 确认装了 **Python 3.10+**（`python --version`；macOS / Linux 用 `python3 --version`）和 **ZCode 桌面客户端** |
| 2 | ZCode 里打开 **设置 → 插件 → 右上角「创建」→「添加插件市场」**，来源填 `c80361619/zcode-toolkit` |
| 3 | 在「个人」分段找到 **ZCode Patcher**，点 **安装**（装好后默认启用，**保持启用**） |
| 4 | 点开插件 → **高级信息 → 配置**，把要用的功能拨成开 → **保存配置**（没保存过就等于没表态，插件不会动客户端文件） |
| 5 | **完全退出并重启 ZCode**（托盘右键退出，关窗口不算）→ 状态栏 / 滑条 / 增强提示词 / 拉取按钮再**退出一次**才生效 |

> **两步走，别只重启一次。** 用量页、弹窗宽度、思考档位属于字节级改动，第一次重启就生效；
> 而状态栏 / 滑条 / 增强提示词 / 拉取按钮要改写整包，必须等 ZCode **完全退出**后由插件改写
> `app.asar`，所以「退出 → 启动 → 再退出 → 再启动」才会看到它们。
> 拿不准卡在哪一步，直接跑自检：`python skills/zcode-tokenspeed/scripts/doctor.py`。

只想用命令行、不装插件？跳到 [方式 C](#方式-c只用命令行不装插件)。

---

## 功能一览

| # | 功能 | 效果 | 命令 | 改动位置 |
|---|------|------|------|---------|
| 1 | **思考档位配置** | 把各模型已配的档位写进 `provider_config.json` 的 `optionSpecs`——界面档位列表与请求体参数都由它下发（3.14+ 原生机制，**无需内核补丁**） | `--reasoning-config` | `~/.zcode/v2/provider_config.json` |
| 2 | **思考等级透传** | ≤3.11 内核的档位兜底补丁（3.14+ 已不需要，脚本会明确提示） | 无参数 | 内核 `zcode.cjs` |
| 3 | **用量页去截断** | 「设置 → 用量」趋势图不再只画 Top 6、饼图不再只画 Top 5 + 「其他模型」 | `--usage-chart` | `app.asar` 渲染文件 |
| 4 | **模型弹窗加宽** | 模型选择浮窗 192px → 320px，长模型名不再被截断 | `--model-width` | `app.asar` 主 bundle |
| 5 | **TPS 状态栏** | 输入框下方常驻统计条：本轮（首 token / tok/s / out）+ 会话累计（轮数 / 输入 / 命中率 / 累出），空会话空态常驻，右键可切位置 | `--tps-footer` | `app.asar` 注入脚本 |
| 6 | **思考强度滑条** | 工具栏「思考 · 档名」入口，点击弹出吸附拖拽条（进度条样式随应用主题自适应，加载/拖拽/完成/静止四态反馈），拖完走原生链路即时生效 | `--thought-slider` | `app.asar` 注入脚本 |
| 7 | **增强提示词** | 输入框旁「增强提示词」按钮：一键用**当前选中的模型**把草稿改写得更清晰具体，可「恢复原文」 | `--enhance-prompt` | `app.asar` 注入脚本 + IPC 桥 |
| 8 | **模型拉取按钮** | 设置页「⚡️ 自动拉取模型」：拉取供应商 `/models`、勾选即写入，新供应商一步到位（自动建条目） | `--model-puller` | `app.asar` 注入脚本 + IPC 桥 |

命令行版拉模型（不动客户端文件，直接同步配置）：

```bash
python skills/zcode-tokenspeed/scripts/model_pull.py --all [--dry-run] [--refresh]
```

**通用开关**：`--all` 对所有功能生效 · `--check` 只读核查 · `--revert` 还原 ·
`--dry-run` 只报告改动不写盘 · `--verbose` 打印探测细节 · `--force` 跳过备份指纹校验（慎用）·
`--prune` 清理补丁产物（`--deep` 连当前备份一起清）。

---

## 环境要求与依赖

| 项 | 要求 | 怎么确认 |
|---|---|---|
| **Python** | **≥ 3.10**；**只用标准库，不需要 pip 安装任何依赖**（仓库里没有 `requirements.txt`） | `python --version`（Windows）／`python3 --version`（macOS / Linux） |
| **ZCode 桌面客户端** | 3.11.2 / 3.14.1 / 3.14.3 实测通过；**其它版本脚本会先只读探测，锚点不唯一时拒绝盲改并说明原因** | 客户端「关于」 |
| **操作系统** | Windows（实测）／ macOS ／ Linux（后两者为逻辑支持） | — |
| **权限** | 安装目录在 `Program Files`、`/Applications` 等受保护位置时，需要**管理员 / sudo** | 打补丁报「拒绝访问」就是权限不够 |
| **磁盘** | 首次打补丁会在客户端目录旁留整文件备份（单项可达原文件大小，`app.asar` 约 300 MB） | 建议预留 **≥ 1.5 GB** |
| Node.js | **仅开发 / 跑测试需要**（校验注入脚本语法，缺失时相关用例自动跳过） | `node --version` |

### Python 怎么装（按平台）

| 平台 | 做法 |
|---|---|
| Windows | 到 [python.org](https://www.python.org/downloads/) 下载安装，**安装时勾选 “Add python.exe to PATH”**；或用 Microsoft Store 搜 `Python` |
| macOS | 系统自带 `python3`（若提示需要开发者工具，执行 `xcode-select --install`）；或 `brew install python` |
| Linux | `sudo apt install python3`（Debian / Ubuntu）／`sudo dnf install python3`（Fedora） |

> **`python` 还是 `python3`？** 本文示例统一写 `python`。**macOS / 多数 Linux 只有 `python3`**，
> 把示例里的 `python` 换成 `python3` 即可。插件自带的 SessionStart 钩子已经做了
> 「先试 `python`、失败再试 `python3`」的兼容，两种环境都能用。

---

## 安装

### 方式 A：装成 ZCode 插件（推荐，全程零命令）

装好之后靠插件里的开关控制功能，**不需要手动跑任何命令**，也支持逐项开关与精确还原。

1. **打开插件页**：ZCode 里 **设置 → 插件**（该页需要先打开一个工作区 / 项目，否则会提示「打开一个工作区以管理插件」）。
2. **添加插件市场**：右上角 **创建 → 添加插件市场**，来源填 GitHub 仓库：

   ```
   c80361619/zcode-toolkit
   ```

   也可以填完整链接 `https://github.com/c80361619/zcode-toolkit`。
3. **安装插件**：校验通过后，在 **个人** 分段找到 **ZCode Patcher**（`zcode-tokenspeed`），点 **安装**。装好后默认启用。
4. **确认是启用状态**：**设置 → 插件 → 管理已安装**，该插件右侧开关必须是**开**。
   ZCode 只在插件启用后把它的 Hook 注册进**新会话**——未启用时一切自动化都不会发生。
5. **拨开关**：点开插件卡片 → 展开底部 **高级信息** → **配置** 区，把想要的功能拨成开 → 点 **保存配置**（详见 [配置步骤](#配置步骤)）。
6. **重启 ZCode（重打包级功能要两次）**：状态栏 / 滑条 / 增强提示词 / 拉取按钮会在 **ZCode 退出时**由插件自动应用，
   **下次启动**才可见。所以完整顺序是：保存配置 → 退出 ZCode → 启动 → 退出 ZCode → 启动。

> **GitHub 访问不畅？** 插件目录、详情与安装都依赖网络。若加载失败，改用 [方式 B](#方式-b从本地目录安装离线可用)，
> 或先给 ZCode 配好代理再刷新插件页。

### 方式 B：从本地目录安装（离线可用）

适合内网、GitHub 不通，或想改代码自己测试的场景。

```bash
git clone https://github.com/c80361619/zcode-toolkit.git
```

然后 **设置 → 插件 → 创建 → 添加插件市场**，来源选**该目录**（或用 **选择目录** 按钮 / 直接把文件夹拖进弹层）。
校验通过后在 **个人** 分段安装即可。

> 仓库根目录的 `marketplace.json` 就是给这一步用的：它声明了本仓库发布哪些插件。
> 自建市场与发版注意事项见 [开发与发版](#开发与发版)。

### 方式 C：只用命令行（不装插件）

不想装插件、或只想试一两个功能时，直接用脚本。

```bash
# 1) 拉代码
git clone https://github.com/c80361619/zcode-toolkit.git
cd zcode-toolkit

# 2) 先体检（只读，不会改任何文件；安装位置自动探测）
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --check

# 3) 先完全退出 ZCode（托盘右键退出，关窗口不算），然后一次打上全部补丁
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all

# 4) 重启 ZCode 验证
```

只要某几项时，把 `--all` 换成对应参数即可：

```bash
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --usage-chart --model-width --check  # 只看这两项状态
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --tps-footer                         # 只打状态栏
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --tps-footer --revert                # 只还原状态栏
```

**自动探测不到安装位置时**，把安装根目录当参数传进去：

```bash
python skills/zcode-tokenspeed/scripts/zcode_patcher.py "D:\ZCode"                  # Windows
python skills/zcode-tokenspeed/scripts/zcode_patcher.py "/Applications/ZCode.app"  # macOS（.app 包会自动展开到 Contents）
python skills/zcode-tokenspeed/scripts/zcode_patcher.py "/opt/ZCode"               # Linux
```

探测顺序：**运行中进程路径 → 注册表卸载信息 → 常见目录**（跨 Windows / macOS / Linux）。
加 `--verbose` 可以看到每一步的探测结果。

---

## 配置步骤

> **前提：插件必须处于「已启用」。** 到 **设置 → 插件 → 管理已安装** 看一眼开关。
> ZCode 只在插件启用后，把它的 `hooks/hooks.json` 注册进**新会话**；停用状态下改配置不会生效。

### 1. 打开插件的配置区

**设置 → 插件 → 已安装 → 点开本插件 → 高级信息 → 配置**，拨开关后点 **保存配置**。

> 若「高级信息」里没有出现「配置」区（宿主渲染问题，与插件清单无关），
> 可直接写配置文件，效果完全一样，见 [手动写配置](#3-手动写配置兜底方案)。

### 2. 开关与生效时机

| 开关 | 对应功能 | 生效时机 |
|---|---|---|
| 思考档位配置（3.14+） | `--reasoning-config` | 下次会话启动（配置侧，无需重启 ZCode） |
| 用量页去截断 | `--usage-chart` | 下次会话启动（字节级，无需重启 ZCode） |
| 模型弹窗加宽 | `--model-width` | 下次会话启动（同上） |
| TPS 状态栏 | `--tps-footer` | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 思考强度滑条 | `--thought-slider` | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 增强提示词按钮 | `--enhance-prompt` | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 设置页模型拉取按钮 | `--model-puller` | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 思考档位内核补丁（旧版专用） | 无参数 | 仅 ≤3.11.2 需要；3.14.x 请**保持关闭** |

> 为什么重打包级要两次启停：它要改写整个 `app.asar`，而 ZCode 运行时锁着这个文件。
> 所以插件在会话启动时只**登记待办**，等 ZCode 完全退出后由一个看护进程写入，**下一次启动**才看得到。
>
> **`SessionStart` 钩子是在「新会话的第一轮」触发的**，不是开机自启那一刻。所以重启 ZCode 之后
> 要真的**开一个会话 / 发一条消息**，钩子才会跑。同步本身是**后台执行**的（不阻塞会话启动），
> 一两秒内完成；跑没跑过看 `scripts/_sync.last`。

四条行为约定：

- **只同步你显式保存过的开关**。没拨过的开关一律不碰——首次安装插件不会自动改动客户端文件。
  因此「装了插件但没保存过配置」= 什么都不会发生，这是设计如此，不是故障。
- 开关的默认值（`default: false`）是静态的，**不反映客户端的历史状态**。把开关拨成你想要的状态并保存即可。
- 同步日志在插件目录的 `scripts/_sync.log`，**心跳文件是 `scripts/_sync.last`**——
  每次钩子被调用都会刷新它。没有这个文件 = 钩子根本没跑过；文件里写着「未保存过开关」= 钩子跑了但你还没表态。
- 最权威的证据是 **ZCode 自己的日志** `~/.zcode/cli/log/zcode-<日期>.jsonl`：
  在里面搜 `session_start_hooks` 能看到钩子阶段有没有执行（钩子的触发/超时/失败都记在这里）。
- 不想折腾钩子、或想立刻看到效果，随时可以直接用命令行打补丁（[方式 C](#方式-c只用命令行不装插件)），
  效果与插件开关完全一致。

### 3. 手动写配置（兜底方案）

配置区渲染不出来，或想批量设置时，直接编辑 `~/.zcode/cli/config.json`：

```json
"plugins": {
  "options": {
    "zcode-tokenspeed@dev-default-22da16fd": { "tps_footer": true, "usage_chart": true }
  }
}
```

键名与上表一致（`reasoning_config` / `usage_chart` / `model_width` / `tps_footer` /
`thought_slider` / `enhance_prompt` / `model_puller` / `core_patch`）。
`@` 后面是市场名，按你实际安装的市场填写。**改之前先备份一份**，写入后由 `sync.py` 在下次会话启动时读取并应用。

---

## 装完怎么验证

| 功能 | 到哪看 |
|---|---|
| 思考档位配置 | 设置 → 模型，自定义模型的思考档位可选；发消息后请求体带 `thinking` / `reasoning_effort` |
| 用量页去截断 | 设置 → 用量：趋势图不再只有 Top 6，饼图不再有「其他模型」 |
| 模型弹窗加宽 | 点开模型选择浮窗，长模型名不再被截断 |
| TPS 状态栏 | 输入框下方出现统计条（空会话显示空态绿点） |
| 思考强度滑条 | 工具栏「思考 · 档名」入口，点开有拖拽条 |
| 增强提示词 | 输入框旁出现星芒图标按钮 |
| 模型拉取按钮 | 设置 → 模型 / 供应商页出现「⚡️ 自动拉取模型」 |

出现异常时，在渲染层 console 里看诊断对象：`window.__ztpsDiag`（状态栏）、
`window.__zsliderDiag`（滑条）、`window.__zenhanceDiag`（增强提示词）。

---

## 装了没生效？先跑自检

插件这条路要经过「安装 → 启用 → 保存配置 → 钩子触发 → 打补丁」五道关，任何一道没走通，
**表现都是「什么也没发生」**，光看界面分不出卡在哪。所以别猜，直接跑自检：

```bash
# 在仓库根目录（或插件安装目录）执行；只读，不会修改任何文件
python skills/zcode-tokenspeed/scripts/doctor.py
```

它会把整条链路逐项打出来，并在末尾给出结论，例如：

```
=== 4. 插件安装与启用 ===
  [√] 安装位置：~/.zcode/cli/plugins/marketplaces/<市场>/zcode-tokenspeed
  [i] 清单版本：0.5.1
  [×]   plugins.enabledPlugins 里没有 zcode-tokenspeed* —— 插件未登记启用状态
  [×] 插件未处于「已启用」——**钩子不会进入会话，自动化全部不会发生**

=== 结论 ===
★ 卡点：插件已安装但**未启用**。
  ZCode 只在插件启用后，把它的 Hook 注册进**新会话**。
  → 「设置 → 插件 → 管理已安装」打开开关，然后开一个新会话。
```

加上 `--json` 可以输出一段结构化报告，方便贴给他人排查。

### 五个最常见的卡点

| 卡点 | 自检里的样子 | 怎么办 |
|---|---|---|
| **插件没启用** | 第 4 节 `enabledPlugins` 里没有本插件 | 「设置 → 插件 → 管理已安装」打开开关 |
| **配置没保存过** | 第 5 节 `plugins.options` 里没有本插件 | 高级信息 → 配置 → 拨开关 → **保存配置**（或 [手动写配置](#3-手动写配置兜底方案)） |
| **没开过新会话** | 第 7 节没有 `_sync.last` / `_sync.log` | `SessionStart` 钩子在**新会话第一轮**才触发：重启后要真的开一个会话 / 发一条消息 |
| **钩子没跑过** | 同上 | 确认 `python --version` 可用；「检查更新」升到最新版；再到 `~/.zcode/cli/log/zcode-<日期>.jsonl` 里搜 `session_start_hooks` |
| **只重启了一次** | 第 8 节里重打包项显示「未打」 | 再退出一次 ZCode（退出时才写入 `app.asar`），然后启动 |

> 钩子到底跑没跑，有三层证据可以对照，从弱到强：
> ① `scripts/_sync.last` 心跳文件 → ② `scripts/_sync.log` 同步日志 →
> ③ ZCode 自己的日志 `~/.zcode/cli/log/zcode-<日期>.jsonl` 里的 `session_start_hooks` 阶段。

### 完全绕开插件（保底方案）

只要 Python 能跑，命令行这条路与插件开关**效果完全一致**，且不依赖钩子：

```bash
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --check   # 先看状态
# 完全退出 ZCode，然后：
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all          # 一次打上全部补丁
```

跑完重启 ZCode 即可，不需要两次启停——命令行是在 ZCode 关闭状态下直接写文件的。

---

## 卸载与回退

| 想做什么 | 怎么做 |
|---|---|
| 停用插件（保留安装） | 设置 → 插件 → 管理已安装 → 关掉开关 |
| **卸载插件前先还原客户端** | 先把所有开关拨成 **关** → 保存配置 → **完全退出 ZCode**（退出时插件自动还原）→ 再卸载 |
| 命令行还原 | `python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --revert` |
| 客户端起不来 | `python skills/zcode-tokenspeed/scripts/restore_clean.py --latest` 从干净备份整包恢复 |
| 清理备份省空间 | `--prune`（只清旧归档与临时文件）／ `--prune --deep`（连当前备份一起清，之后无法 `--revert`） |

> 卸载插件本身**不会**自动还原已经打上的补丁——所以卸载前请先按上面第 2 行把开关全部关掉。

---

## 平台差异与权限

| 平台 | 注意点 |
|---|---|
| **Windows** | 「完全退出」指**托盘图标右键 → 退出**，关窗口不算；安装在 `Program Files` 下时用管理员身份运行终端；路径分隔符两种都行（`D:\ZCode` 或 `D:/ZCode`） |
| **macOS** | 只有 `python3`；改 `/Applications` 下的 `.app` 会破坏代码签名，异常时执行 `sudo codesign --force --deep --sign - /Applications/ZCode.app` 重签；需要 sudo |
| **Linux** | 只有 `python3`；安装常在 `/opt/ZCode` 或 `/usr/share/ZCode`，需要 sudo |

---

## 常见问题

| 现象 | 处理 |
|---|---|
| **插件装好了但什么都没发生** | 先跑 `python skills/zcode-tokenspeed/scripts/doctor.py`，它会指出卡在哪一环（见 [装了没生效？先跑自检](#装了没生效先跑自检)） |
| 开关拨了但功能没出现 | ① 确认插件在「管理已安装」里是**启用**状态；② 确认点了 **保存配置**；③ 重启后要**开个新会话**（`SessionStart` 在新会话第一轮才触发）；④ 重打包级功能需要**退出两次**才可见 |
| 想知道钩子到底有没有执行 | 看 `scripts/_sync.last`（心跳）与 `scripts/_sync.log`；最权威的是 ZCode 日志 `~/.zcode/cli/log/zcode-<日期>.jsonl` 里搜 `session_start_hooks` |
| 插件页提示「打开一个工作区以管理插件」 | 先打开任意项目 / 工作区，插件页才可用 |
| 添加市场报校验失败 | 确认填的是 `c80361619/zcode-toolkit`（或本地克隆目录本身，目录里要有 `marketplace.json`） |
| 插件详情里没有「配置」区 | 宿主渲染问题；直接写配置文件，见 [手动写配置](#3-手动写配置兜底方案) |
| 插件页「检查更新」一直不提示新版本 | 本项目的发版口径：`marketplace.json` 与 `plugin.json` 的 `version` 必须同步，见 [开发与发版](#开发与发版) |
| 打补丁提示「请完全退出 ZCode」 | 托盘右键退出（关窗口不算），再重跑 |
| 打补丁提示「拒绝访问 / Permission denied」 | 用管理员（Windows）或 `sudo`（macOS / Linux）重跑 |
| 找不到 ZCode 安装 | 把安装根目录作为参数传入，加 `--verbose` 看探测过程 |
| 升级客户端后补丁失效 | 重跑对应命令即可；`--all --check` 先看状态 |
| 3.14+ 跑内核补丁提示「不适用」 | 预期行为——档位改走 `--reasoning-config` |
| 档位能选但请求无 thinking | 3.14+ 看 `--reasoning-config --check` 是否已写入；≤3.11 确认内核补丁已打且已重启 |
| 状态栏 / 滑条 / 增强按钮不出现 | 渲染 console 看 `window.__ztpsDiag` / `window.__zsliderDiag` / `window.__zenhanceDiag` |
| 增强提示词报「没找到可用的模型」 | 先在设置里配好供应商与 API Key |
| 客户端起不来 | `python skills/zcode-tokenspeed/scripts/restore_clean.py --latest` 从干净备份整包恢复 |
| 想清理安装目录里的备份 | `--prune`（只清旧归档与临时文件）/ `--prune --deep`（连当前备份一起清） |

---

## 思考档位怎么用（按版本分流）

ZCode 对「非内核白名单」的自定义模型，档位能选中但参数不一定下发到请求体。**机制在 3.14 换了**：

| 客户端版本 | 档位从哪来 | 参数怎么下发 | 该跑什么 |
|---|---|---|---|
| **≥ 3.14** | `provider_config.json` → `providerModelRules[].config.optionSpecs.reasoningLevel.values` | 同一条规则的 `optionSpecs.reasoningLevel.map`（CEL 表达式），请求发出前由内核合并进请求体 | `--reasoning-config`（**不需要内核补丁**） |
| ≤ 3.11 | `config.json` 的 `reasoning.variants` | 内核查表 `providerOptionsByLevel`（自定义模型为空）→ 需补丁兜底 | 内核补丁 + 手配 `variants` |

判别方法：跑 `zcode_patcher.py --check`，输出「该内核使用 3.14+ 原生档位机制（optionSpecs），本补丁不适用」即为 ≥3.14。

```bash
# 3.14+：先看现状（哪些模型已配档位、哪些已在界面手动配置过）
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --reasoning-config --check
# 写入（先完全退出 ZCode）
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --reasoning-config
```

它会以 `config.json` 为基准，把每个模型的档位写进 `provider_config.json`：

- `values` = 界面档位列表，**末位即默认档**（`defaultVariant` 会自动排到末位）
- `map` = 用 ZCode 自己会写的那套 CEL（openai 兼容：`thinking` + `enable_thinking` + `reasoning_effort`；
  anthropic：`thinking(adaptive)` + `output_config.effort`），保证一定能编译通过
- 已在界面「手动配置」过的模型会**自动跳过**——内核 schema 禁止同一模型同时出现在两个规则列表，
  重复声明会让整份供应商配置降级为空
- 档名不在 `disabled/none/enabled` 之内时按原名透传（网关认识就透传、不认识自行降级）

---

## 增强提示词怎么用

在输入框工具栏点「**增强提示词**」：

1. 取当前草稿 → 经 preload 桥 / main handler，用**你当前选中的那个模型**调一次补全
2. 提示词要求「保持原语言、只输出改写后的提示词、不回答问题、不加解释」
3. 结果写回输入框；按钮临时变成「**恢复原文**」，20 秒内可一键还原

失败时会提示具体原因（未配置供应商 / 桥不可用 / 网关报错 / 超时）。
渲染层诊断对象：`window.__zenhanceDiag`。

---

## 工作原理（简述）

- **asar 补丁**：直接解析 `app.asar` 头（不依赖任何 Node/asar 工具），两种手法——
  同长度字节级原地覆盖（用量图 / 弹窗加宽），与保留 unpacked 原生模块的精确重打包
  （状态栏 / 滑条 / 增强提示词 / 拉取按钮，改动条目重算 SHA256 integrity，写临时文件回读校验后原子替换）。
  重打包**不把整包读进内存**：未改动条目按 1MB 分块流式搬运（实测 312MB 包峰值分配 80MB）。
- **IPC 注入**：preload / main 里的注入段用**标记定界**（`/*zp:begin:<块名>*/ … /*zp:end:<块名>*/`），
  多个补丁共用同一个锚点也能各自独立装卸、互不干扰。
- **内核补丁**：`zcode.cjs` 是 esbuild 压缩产物、符号名随版本重排；按「完整函数原文」做多版本锚点匹配，
  恰好唯一命中才动手，新版本可 `--extract` 按结构特征自动提取锚点。
- **安全兜底**：备份带**版本指纹**（`*.bak.meta.json`）；客户端升级后旧备份自动归档（`.stale-<时间>`），
  还原时若当前文件与备份不是同一版本会**拒绝执行**，避免把旧内核/asar 盖回新客户端。

---

## 目录结构

```
marketplace.json                              插件市场清单（ZCode「添加插件市场」读它）
.zcode-plugin/plugin.json                     插件清单（含 8 个功能开关的声明）
commands/                                     四个斜杠命令
hooks/hooks.json                              SessionStart 钩子（调用 sync.py 同步开关）
skills/zcode-tokenspeed/
  SKILL.md                                    执行流程 + 逆向笔记 + 排障（AI 代执行入口）
  scripts/
    zcode_patcher.py                          主工具：八个补丁
    doctor.py                                 安装自检：一条命令诊断「为什么没生效」
    zcode-tps.js                              TPS 状态栏注入脚本（ServicePort 事件流）
    zcode-thought-slider.js                   思考强度滑条注入脚本
    zcode-enhance-prompt.js                   增强提示词按钮注入脚本
    zcode-model-puller.js                     模型拉取按钮前端脚本
    model_pull.py                             CLI：拉取模型、按元数据刷新已有模型
    sync.py                                   开关同步（SessionStart hook 调用）
    apply_after_exit.py                       退出后看护：等 ZCode 退出 → 应用/还原 → 重启
    restore_clean.py                          紧急整包还原
    tap_proxy.py                              请求捕获代理：看真实发出的请求体
    probe_max_tokens.py                       探测模型真实输出上限（识别网关静默钳制）
tests/test_patcher.py                         回归测试
tests/slider_smoke.js                         滑条脚本冒烟（最小 DOM 桩，校验加载与调试接口）
NOTICE.md                                     第三方组件与许可声明
```

---

## 开发与发版

```bash
python -m unittest discover -s tests -v
```

覆盖 asar 头解析与重打包（offset 重排、unpacked 条目、峰值内存约束）、integrity 精确同步、
内核补丁的字节级改写与备份指纹、3.14+ 档位配置迁移与冲突跳过、注入块共存与迁移、
生成的注入代码语法（`node --check`）、滑条脚本的加载与调试接口（`tests/slider_smoke.js` 用最小 DOM 桩真跑一遍）；
本机装了 ZCode 时还会**只读校验真实 app.asar 的逐条目 integrity**。
CI（`.github/workflows/ci.yml`）在 Python 3.10 / 3.12 / 3.13 上跑这套用例。

### 发版

1. 改 `.zcode-plugin/plugin.json` 的 `version`；
2. **同步改根目录 `marketplace.json` 里该插件条目的 `version`**。

两处必须一致：ZCode 的「检查更新」拿 `marketplace.json` 的 `version` 当「最新版本」、
拿 `plugin.json` 的 `version` 当「已安装版本」，只改一处永远不会提示可更新。
改完提交并推送到默认分支，用户在插件页点「检查更新」即可看到新版本。

---

## 许可

[MIT](LICENSE) © 2026 c80361619。第三方组件与设计参考的声明见 [NOTICE.md](NOTICE.md)。
