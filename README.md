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
| 4 | **完全退出并重启 ZCode**（托盘右键退出，关窗口不算），然后**开一个会话 / 发一条消息** —— 钩子在这一刻登记「期望状态」 |
| 5 | **再退出一次 ZCode** —— 看护进程在这一刻把补丁真正写进客户端，**并自动把 ZCode 重新拉起来**；下次启动即可见 |

> **装完即用，不需要打开配置页，也不需要跑任何命令。** 插件清单里每个开关的默认值都是 `true`，
> 钩子按默认值登记期望状态，ZCode 退出时由看护写入；想关掉某个功能，到配置里拨成关并保存即可
> （保存值优先于默认值）。原理与边界见 [自动注入](#自动注入触发时机作用范围与兜底)。
>
> **为什么八项都要等退出**：`zcode_patcher.py` 的运行预检是**全局**的 —— 只要 `tasklist` 里还有
> `ZCode.exe` 就拒绝写入（app.asar 被锁、`config.json` / `provider_config.json` 会被客户端回写覆盖），
> 而会话钩子**必然**在 ZCode 运行中触发。所以插件统一走「钩子登记期望状态 → ZCode 退出时由看护写入
> → 自动重启 ZCode」这一条链路（`sync.py` 仍会先试一次立即写，被拒才转交看护，纯 CLI 场景下可即时生效）。
> 拿不准卡在哪一步，别猜 —— 跑一次自检就能定位：[装了没生效？先跑自检](#装了没生效先跑自检)。

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
5. **重启 ZCode**：**完全退出 ZCode**（托盘图标右键 → 退出；关窗口不算）再启动。
6. **开一个新会话 / 发一条消息**：钩子在**新会话的第一轮**触发，随后**在后台自动注入**——
   不需要打开配置页、不需要点保存、也不需要跑任何命令。
7. **（只有重打包级功能需要）再退出一次**：状态栏 / 滑条 / 增强提示词 / 拉取按钮会在
   ZCode 退出时由看护进程写入，**下次启动**可见。完整顺序：退出 → 启动 → 退出 → 启动。

> **装完即用。** 首次自动注入时，会话里会出现一条说明，告诉你哪些功能已写入、哪些要等
> 完全退出 ZCode。触发时机、作用范围与兜底方式见 [自动注入](#自动注入触发时机作用范围与兜底)。

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

## 自动注入：触发时机、作用范围与兜底

**装完插件就能用 —— 不需要打开配置页，不需要手动执行任何命令，也不需要额外下载代码。**

### 触发时机：安装后的第一次会话启动

ZCode 的插件清单**没有「安装时钩子」**这一项。官方规范
（`plugin-json-spec.md`）只允许插件声明 `skills` / `commands` / `hooks` / `mcpServers`，
没有能在「点下安装按钮的那一刻」执行代码的入口。所以最早能自动触发的时机是：

> **安装并启用之后，下一次会话启动（`SessionStart`）**。

完整路径：

1. 点 **安装** → 保持 **启用**；
2. **完全退出 ZCode**（托盘图标右键 → 退出；关窗口不算）再启动；
3. **开一个会话 / 发一条消息** —— 钩子在这一刻触发，随后在后台开始注入。

钩子本身只做两件事：**登记心跳 + 拉起后台进程**，然后立刻返回，**不阻塞会话启动**。
首次自动注入时，会话里会出现一条提示，明确区分「已直接写入」和「要等完全退出 ZCode 才写入」的功能。

### 作用范围：全部 8 个开关，默认全开

插件清单 `.zcode-plugin/plugin.json` 里每个开关都声明了 `"default": true`。
**从未保存过配置时，钩子就按这份默认值注入** —— 这就是「装完即用」的实现方式。

| 何时写入 | 包含哪些功能 |
|---|---|
| 钩子触发后**立刻**写入 | 思考档位配置、用量页去截断、模型弹窗加宽（字节级，重启 ZCode 后可见） |
| **完全退出 ZCode 时**写入 | TPS 状态栏、思考强度滑条、增强提示词按钮、模型拉取按钮（重打包级，下次启动可见） |
| **自动跳过** | 思考档位内核补丁（仅 ≤3.11.2 需要；3.14+ 上会明确报「本版本不适用」，不会被误报成已生效） |

合并规则是 **已保存的开关优先，没保存过的键退回默认值**：

- 从没点过「保存配置」→ 全部按默认值注入（零配置自动注入）；
- 保存过一部分 → 保存的照做，没提到的用默认值（插件升级新增开关时不会漏）；
- **显式关掉**某个开关 → 保存值是 `false`，优先于默认值，会被正常还原，不会被偷偷打开。

### 想关掉某个功能

**设置 → 插件 → 已安装 → 点开本插件 → 高级信息 → 配置**，把不想要的拨成**关** → **保存配置** →
退出并重启 ZCode。保存过的值优先于默认值，之后不会再被自动打开。

### 自动注入不可行时的兜底

少数环境会挡住钩子：插件被停用、企业策略禁用 hook、安全软件拦截后台进程。
这时**不要依赖任何自动逻辑**，直接用命令行 —— 一条命令等价于「全部开关打开」：

```bash
# 先克隆仓库（兜底路径不依赖插件，也不需要改任何配置）
git clone https://github.com/c80361619/zcode-toolkit.git
cd zcode-toolkit

# 1) 只读体检：插件装在哪、钩子有没有跑、当前注入状态如何
python skills/zcode-tokenspeed/scripts/doctor.py

# 2) 完全退出 ZCode 后，一次打上全部补丁 / 一次全部还原
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --revert
```

> 兜底路径只依赖 **Python 3.9+**，不需要 Node、不需要装插件。自检脚本的详细用法见
> [装了没生效？先跑自检](#装了没生效先跑自检)。

---

## 配置步骤

> **前提：插件必须处于「已启用」。** 到 **设置 → 插件 → 管理已安装** 看一眼开关。
> ZCode 只在插件启用后，把它的 `hooks/hooks.json` 注册进**新会话**；停用状态下改配置不会生效。
>
> **这一章是「想改默认行为」时才需要看的。** 什么都不做也已经能用：没保存过配置时，
> 钩子按清单默认值（全开）自动注入，见 [自动注入](#自动注入触发时机作用范围与兜底)。

### 1. 打开插件的配置区

**设置 → 插件 → 已安装 → 点开本插件 → 高级信息 → 配置**，拨开关后点 **保存配置**。

> 若「高级信息」里没有出现「配置」区（宿主渲染问题，与插件清单无关），
> 可直接写配置文件，效果完全一样，见 [手动写配置](#3-手动写配置兜底方案)。

### 2. 开关与生效时机

| 开关 | 对应功能 | 默认 | 生效时机 |
|---|---|---|---|
| 思考档位配置（3.14+） | `--reasoning-config` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 用量页去截断 | `--usage-chart` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 模型弹窗加宽 | `--model-width` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| TPS 状态栏 | `--tps-footer` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 思考强度滑条 | `--thought-slider` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 增强提示词按钮 | `--enhance-prompt` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 设置页模型拉取按钮 | `--model-puller` | 开 | **ZCode 退出时自动应用**，再启动才生效（需两次启停） |
| 思考档位内核补丁（旧版专用） | 无参数 | 开 | 仅 ≤3.11.2 需要；3.14+ 会**自动跳过**并提示「本版本不适用」 |

> **为什么八项都要等退出**：`zcode_patcher.py` 的运行预检是全局的（只要 `tasklist` 里有
> `ZCode.exe` 就拒绝写入，因为 app.asar 被锁、配置会被客户端回写覆盖），而会话钩子必然在
> ZCode 运行中触发。所以插件改成「钩子登记期望状态 → ZCode 退出时由看护写入 → 自动重启 ZCode」。
> 若你想立刻生效，可以先完全退出 ZCode，再手动跑对应命令。

> 「默认」= **没保存过配置时**采用的初始值。一旦你在配置里保存过，保存值优先，不再用默认值。
> 所以这张表全开并不妨碍你关掉其中几项。

> 为什么重打包级要两次启停：它要改写整个 `app.asar`，而 ZCode **只在启动时读一次**这个文件——
> 运行期间改写不会影响当前会话（文件本身没有被锁，实测可以直接写入）。所以插件在会话启动时
> 只**登记待办**，等 ZCode 完全退出后由一个看护进程写入，**下一次启动**才看得到。
>
> **`SessionStart` 钩子是在「新会话的第一轮」触发的**，不是开机自启那一刻。所以重启 ZCode 之后
> 要真的**开一个会话 / 发一条消息**，钩子才会跑。同步本身是**后台执行**的（不阻塞会话启动），
> 一两秒内完成；跑没跑过看 `scripts/_sync.last`。

四条行为约定：

- **没保存过配置时，按插件清单声明的默认值注入**（默认全开），所以装完即用。
  想关掉某个功能，到配置里拨成关并保存；保存过的值优先于默认值，不会被自动打开。
- 开关的默认值是清单里的静态声明，**不反映客户端的历史状态**。想让某个功能回到「不管」的状态，
  只能显式保存成你要的值。
- 同步日志在插件目录的 `scripts/_sync.log`，**心跳文件是 `scripts/_sync.last`**——
  每次钩子被调用都会刷新它。**没有这个文件 = 钩子根本没跑过**（多半是插件没启用）；
  文件里写着「从未保存过开关」= 钩子跑了，这次是按清单默认值做的自动注入。
- 最权威的证据是 **ZCode 自己的日志** `~/.zcode/cli/log/zcode-<日期>.jsonl`：
  搜 `session_start_hooks` 能看到钩子阶段有没有执行；搜 `hookCount` 能看到**本次启动到底注册了几个钩子**
  ——为 `0` 就说明插件没启用或 `hooks.json` 没被读到，钩子没跑是**必然结果**，而不是钩子本身有问题。
- 不想折腾钩子、或想立刻看到效果，随时可以直接用命令行打补丁（[方式 C](#方式-c只用命令行不装插件)），
  效果与插件开关完全一致。

### 3. 手动写配置（兜底方案）

配置区渲染不出来，或想批量设置时，直接编辑 `~/.zcode/cli/config.json`：

```json
"plugins": {
  "options": {
    "zcode-tokenspeed@zcode-toolkit": { "tps_footer": true, "usage_chart": true }
  }
}
```

键名与上表一致（`reasoning_config` / `usage_chart` / `model_width` / `tps_footer` /
`thought_slider` / `enhance_prompt` / `model_puller` / `core_patch`）。
`@` 后面是市场名，按你实际安装的市场填写。**改之前先备份一份**，写入后由 `sync.py` 在下次会话启动时读取，并在 ZCode 退出时由看护应用到客户端。

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

### 润色按钮报「Model is unavailable」怎么办

**症状**：本机润色正常，其他电脑点润色报
`HTTP 400：Upstream request failed: Model is unavailable.`，
（或反过来，有的机器显示「已用 glm-5.2 增强」并成功）。

**原因**：插件在「界面所选模型 → 配置里的供应商」这一步反查失败时，
旧版会退化成「拿第一个供应商去试」——**跟你界面上选了什么无关**。
不同电脑供应商的排列顺序不同，于是有的机器恰好撞对就能用，撞到没配好
（缺 API Key、或套餐未生效）的供应商就报这个错。

> 这句话来自上游网关的**模型维度**判定（模型不在你的套餐内 / 已下线），
> **与地区限制、代理无关** —— 地域封锁表现为 403 或连接重置。

**三步解决**：

1. **先诊断**（只读，不消耗额度）：

   ```bash
   python skills/zcode-tokenspeed/scripts/enhance_doctor.py
   ```

   看第 3 节的 `how=`：
   - `ref` = 正常，界面模型被准确识别；
   - `label` = 退而用显示名反查（能用，但说明界面没给出模型标识）；
   - `fallback` = **踩坑了**，请求被发给了一个并非你选中的供应商。

2. **加一发真实探测**确认端到端可用（会消耗极少量额度）：

   ```bash
   python skills/zcode-tokenspeed/scripts/enhance_doctor.py --probe
   ```

3. **应急自救**（不改代码）：打开 ZCode「设置 → 模型」，
   把那些 **`apiKey` 为空或套餐未生效的内置供应商**（名字里带 Coding Plan 之类）
   删掉或补全 Key，让列表里只剩真正能用的供应商。

升级到 **0.5.9+** 后该问题已在客户端侧修掉：解析只会选**真正可用**的供应商，
失败时给出「该换什么」的建议，并且对限流 / 超时 / 供应商故障做自动退避重试。

> 其他增强提示词的报错：`no-model` = 没有可用候选（按提示补配置）；
> `no-key` / `no-baseurl` = 命中供应商缺凭据；报「通信桥不可用」= 重跑注入并重启 ZCode。

---

## 装了没生效？先跑自检

插件这条路要经过「安装 → 启用 → 钩子触发 → 自动注入 → 打补丁」五道关，任何一道没走通，
**表现都是「什么也没发生」**，光看界面分不出卡在哪。所以别猜，直接跑自检（**只读**，不改任何文件）。

### 第一步：把自检脚本跑起来

`doctor.py` 是**仓库里的文件**，不是插件安装出来的命令 —— 所以要先把仓库拉下来：

```bash
git clone https://github.com/c80361619/zcode-toolkit
cd zcode-toolkit
python skills/zcode-tokenspeed/scripts/doctor.py      # macOS / Linux 用 python3
```

> **别站在插件的缓存目录里敲相对路径。** 下面这种写法一定会失败：
>
> ```
> C:\Users\你\.zcode\cli\plugins\cache\zcode-toolkit>python skills/zcode-tokenspeed/scripts/doctor.py
> python: can't open file 'C:\Users\你\.zcode\cli\plugins\cache\zcode-toolkit\skills\zcode-tokenspeed\scripts\doctor.py': [Errno 2] No such file or directory
> ```
>
> 原因：`cache\<市场名>\` 这一层是**市场目录**，插件根在更深一层。GitHub 来源的市场缓存成
> **`cache\<市场名>\<插件名>\<版本>\`**，`skills\` 在**版本目录里面**（本机实测：
> `cache\zcode-plugins-official\computer-use\0.5.13\.zcode-plugin\plugin.json`）。
> 相对路径是相对**当前目录**解析的，站在市场目录那层当然找不到 `skills\`。

已经在仓库里，或者想直接跑**已安装的那份副本**，先问一下它装在哪：

```bash
python skills/zcode-tokenspeed/scripts/doctor.py --where       # 只打印命中路径 + 可复制的命令
python skills/zcode-tokenspeed/scripts/doctor.py --where-all   # 连扫过的全部候选目录一起列
```

`--where` 的输出长这样（默认只列命中项，不淹没在别人的插件里）：

```
=== 插件位置扫描（--where） ===============================================
  [i] 数据目录候选：
        C:\Users\你\.zcode

  [√] 命中 1 份 zcode-tokenspeed 副本（候选目录共 120 个，其余 76 个是别的插件）：
    C:\Users\你\.zcode\cli\plugins\cache\zcode-toolkit\zcode-tokenspeed\0.5.2
        清单版本 0.5.2   脚本目录 C:\...\0.5.2\skills\zcode-tokenspeed\scripts

  [i] 直接用绝对路径跑完整自检（复制下面这条）：
       python "C:\...\0.5.2\skills\zcode-tokenspeed\scripts\doctor.py"
```

不想克隆仓库也行，用系统命令直接搜出来：

```powershell
# Windows PowerShell
Get-ChildItem "$env:USERPROFILE\.zcode\cli\plugins" -Recurse -Filter doctor.py |
  Select-Object -First 5 -ExpandProperty FullName
```
```bash
# macOS / Linux
find ~/.zcode/cli/plugins -name doctor.py 2>/dev/null
```

### 第二步：看结论

完整自检会把整条链路逐项打出来，并在末尾给出结论，例如：

```
=== 4. 插件安装与启用 ===
  [√] 安装位置：~/.zcode/cli/plugins/cache/zcode-toolkit/zcode-tokenspeed/0.5.2
  [i] 清单版本：0.5.2
  [×]   plugins.enabledPlugins 里没有 zcode-tokenspeed@* —— 插件未登记启用状态
  [×] 插件未处于「已启用」——**钩子不会进入会话，自动化全部不会发生**

=== 结论 ===
★ 卡点：插件已安装但**未启用**。
  ZCode 只在插件启用后，把它的 Hook 注册进**新会话**。
  → 「设置 → 插件 → 管理已安装」打开开关，然后开一个新会话。
```

加上 `--json` 可以输出一段结构化报告，方便贴给他人排查。

### 七个最常见的卡点

| 卡点 | 自检里的样子 | 怎么办 |
|---|---|---|
| **自检脚本跑不起来** | `can't open file '...\doctor.py'` | 你在插件缓存目录里敲了相对路径。到**克隆的仓库**里跑，或用上面 `--where` / `find` 得到的绝对路径 |
| **插件没启用** | 第 4 节 `enabledPlugins` 里没有本插件 | 「设置 → 插件 → 管理已安装」打开开关 |
| **配置没保存过** | 第 5 节 `plugins.options` 里没有本插件 | 高级信息 → 配置 → 拨开关 → **保存配置**（或 [手动写配置](#3-手动写配置兜底方案)） |
| **没开过新会话** | 第 7 节没有 `_sync.last` / `_sync.log` | `SessionStart` 钩子在**新会话第一轮**才触发：重启后要真的开一个会话 / 发一条消息 |
| **钩子没跑过** | 第 7 节无心跳，且日志里 `hookCount = 0` | 那次启动 ZCode 根本没注册钩子 → 「管理已安装」确认启用 → **完全退出**再启动 → 开个新会话 |
| **钩子注册了但没执行** | 第 7 节无心跳，但日志里 `hookCount ≥ 1` | 重点查 `python --version` 是否可用（macOS/Linux 试 `python3`）与钩子命令里的 `${CLAUDE_PLUGIN_ROOT}` 展开 |
| **只重启了一次** | 第 8 节里重打包项显示「未打」 | 再退出一次 ZCode（退出时才写入 `app.asar`），然后启动 |

> 钩子到底跑没跑，有四层证据，从弱到强（**自检会替你读前三层，不用自己翻**）：
> ① `scripts/_sync.last` 心跳文件 → ② `scripts/_sync.log` 同步日志 →
> ③ ZCode 日志 `~/.zcode/cli/log/zcode-<日期>.jsonl` 里的 `session_start_hooks` 阶段 →
> ④ 同一份日志里 `bootstrap.app.startup.plugins.completed` 记录的 **`hookCount`**。
>
> 第 ④ 层最有用：它说明**这次启动 ZCode 到底注册了几个钩子**。如果是 `0`，
> 那就是「插件没启用 / hooks.json 没被读到」，钩子没跑是**必然结果**，跟钩子怎么写无关 ——
> 自检会直接把这句话打出来，而不是让你在四条原因里猜。

### 完全绕开插件（保底方案）

只要 Python 能跑，命令行这条路与插件开关**效果完全一致**，且不依赖钩子 ——
**连插件都不用装**，把仓库拉下来就能用：

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
| 关掉个别功能 | 高级信息 → 配置 → 对应开关拨成**关** → **保存配置** → 退出并重启 ZCode（保存值优先于默认值） |
| 命令行还原 | `python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --revert` |
| 客户端起不来 | `python skills/zcode-tokenspeed/scripts/restore_clean.py --latest` 从干净备份整包恢复 |
| 清理备份省空间 | `--prune`（只清旧归档与临时文件）／ `--prune --deep`（连当前备份一起清，之后无法 `--revert`） |

> **卸载插件本身不会自动还原已经打上的补丁** —— 所以卸载前请先按上面第 2 行把开关全部关掉，
> 让插件在退出时把客户端还原回去，再卸载。
>
> 想删掉「首次自动注入提示只出一次」的标记（比如想再确认一遍自动注入的说明），
> 删掉插件目录里的 `scripts/_autoinject.done` 即可，下次会话会重新提示。

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
| **插件装好了但什么都没发生** | 别猜，跑自检定位卡点：[装了没生效？先跑自检](#装了没生效先跑自检) |
| 跑自检报 `can't open file '...\doctor.py'` | 你在插件的缓存目录里敲了相对路径。到**克隆的仓库**里跑，或用 `--where` / `find` 找到的绝对路径（[说明](#第一步把自检脚本跑起来)） |
| 开关拨了但功能没出现 | ① 确认插件在「管理已安装」里是**启用**状态；② 重启后要**开个新会话**（`SessionStart` 在新会话第一轮才触发）；③ 重打包级功能需要**退出两次**才可见 |
| 没打开配置页，功能会生效吗 | **会**。没保存过配置时按清单默认值（全开）自动注入，见 [自动注入](#自动注入触发时机作用范围与兜底) |
| 想关掉某个功能 | 高级信息 → 配置 → 拨成关 → **保存配置** → 退出并重启；保存值优先于默认值 |
| 想知道钩子到底有没有执行 | 看 `scripts/_sync.last`（心跳）与 `scripts/_sync.log`；最权威的是 ZCode 日志 `~/.zcode/cli/log/zcode-<日期>.jsonl` 里搜 `session_start_hooks` 与 `hookCount` |
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
- **零配置自动注入**：插件清单里每个开关的 `default` 都是 `true`。ZCode 只在用户点过「保存配置」后
  才把 `plugins.options` 写进 `config.json`，所以没保存过时 `sync.py` 会**退回清单声明的默认值**，
  装完重启开个新会话即自动注入，不需要打开配置页。合并规则是**已保存值优先，缺失键补默认值**。

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
    doctor.py                                 安装自检：一条命令诊断「为什么没生效」（--where 查安装位置）
    _console.py                               控制台编码安全网（中文 Windows 管道里不能直接打 ✓）
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
