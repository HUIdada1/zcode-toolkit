---
name: zcode-tokenspeed
description: "[仅手动调用，禁止自动触发] ZCode 客户端本地补丁注入工具：①自定义模型思考档位透传 ②用量页去截断（趋势图/饼图全量）③模型弹窗加宽 ④TPS 状态栏（输入框统计胶囊：时间·首 token·tok/s·out）⑤设置页一键模型拉取按钮。全部幂等、可 --check、可 --revert 精确还原。只有当用户明确要求执行本 skill、或明确点名「zcode-tokenspeed」时才加载；用户只是泛泛提到思考等级、用量图、状态栏、补丁等话题时，一律不要自动触发本 skill。"
---

# ZCode 客户端补丁工具

> 本 skill 由 **zcode-tokenspeed 插件**提供，脚本就在本 skill 目录下的 `scripts/`（下文所有 `<skill目录>` 均指本目录）。

## 版本现状（动手前先看）

补丁会随 ZCode 升级失效或过时，先确认目标版本，再决定打哪个：

| 补丁 | 3.11.2 及更早 | 3.14.x（含当前 D:\ZCode 3.14.1） |
|---|---|---|
| ① 思考档位透传（内核补丁） | 需要，走 `providerOptionsByLevel` 兜底 | **已过时**：内核机制整体重写，锚点与结构提取都失效。改用**原生 `optionSpecs.reasoningLevel`**（模型条目配 `{values:[...], map:"<CEL>"}`，内核发请求前求值并打进请求体）——不需要内核补丁，也不需要 `--extract` |
| ② 用量页去截断 | 需要 | 需要 |
| ③ 模型弹窗加宽 | 两处锚点 | 需要，但只剩**扁平弹窗**一处（渠道子菜单锚点在 3.14.x 结构已变） |
| ④ TPS 状态栏 | 需要 | 需要 |
| ⑤ 模型拉取按钮 | 需要 | 需要，模板已按 `optionSpecs` 新格式写入 |

- 3.14.x 下**不要**再跑思考等级内核补丁（`python zcode_patcher.py` 不带参数那条），它找不到锚点；档位配置改走 `optionSpecs`。
- `model_pull.py` 与 `zcode-model-puller.js` 已适配新格式：拉取模型时直接写 `optionSpecs`，`--refresh` 会把旧 `reasoning` 条目迁移过来。
- 确认版本：看客户端「关于」，或安装根目录（如 `D:\ZCode`）的版本信息。

### 插件开关（配置区）

插件在 ZCode 的「设置 → 插件管理 → 已安装 → 点开插件」详情页里声明了 5 个开关。拨动开关并点「保存配置」后，插件会在**下次会话启动时**自动把客户端同步到该状态，不需要手动跑命令：

| 开关 | 控制的功能 | 生效时机 |
|---|---|---|
| 用量页去截断 | `--usage-chart` | 下次会话启动（字节级，无需重启 ZCode） |
| 模型弹窗加宽 | `--model-width` | 下次会话启动（同上） |
| TPS 状态栏 | `--tps-footer` | ZCode 退出时自动应用，下次启动生效 |
| 设置页模型拉取按钮 | `--model-puller` | ZCode 退出时自动应用，下次启动生效 |
| 思考档位内核补丁（旧版专用） | 无参数 | 仅 ≤3.11.2 需要；3.14.x 请保持关闭 |

行为约定（`scripts/sync.py`，由 SessionStart hook 调用）：

- **只同步显式保存过的开关**。没拨过的开关一律不碰——首次安装插件不会自动改动客户端文件。
- 字节级补丁（图表 / 加宽）当场执行；重打包级补丁（TPS / 拉取按钮）写进退出后看护 `scripts/apply_after_exit.py`，等 ZCode 完全退出时自动应用（asar 运行时被锁，只能这么来）。
- 开关状态与客户端实际状态不一致是正常的：ZCode 的开关默认值（`default: false`）是静态的，不会反映历史补丁状态。**把开关拨成你想要的状态并保存**，同步后两者就一致了。
- 同步日志在 `scripts/_sync.log`；没找到配置时会记录 `config.json` 里 `plugins` 的实际键名，便于定位宿主的存储位置。

> **如果详情页「高级信息」里没有出现「配置」区**：这是 ZCode 侧的渲染问题，与插件清单无关——界面拿到的插件信息里 `userConfig` 为空时，配置区整个不渲染（`Y2t` 组件里 `userConfig` 为空直接 `return null`）。清单本身是正确的（Agent 侧 `M5s` 完整解析、`f5s` 赋 `userConfig: e.manifest.userConfig`、`jGo` 条件展开，链路已逐环节核对）。此时**直接写配置文件**，效果完全一样：
>
> `~/.zcode/cli/config.json` → `plugins.options["zcode-tokenspeed@dev-default-22da16fd"]`：
> ```json
> "plugins": {
>   "options": {
>     "zcode-tokenspeed@dev-default-22da16fd": { "tps_footer": false, "model_puller": true }
>   }
> }
> ```
> 键名见上表；写入后同样由 `sync.py` 在会话启动时读取并应用。也可以直接打 `/zcode-patch-toggle` 让 AI 代改（会先展示"配置值 vs 实际状态"再改）。改 `config.json` 前先备份一份。

### 退出后自动注入

重打包级补丁（TPS 状态栏、拉取按钮）要求 ZCode **完全退出**（运行中锁定 `app.asar`）。插件里由 `scripts/apply_after_exit.py` 承担：轮询等 ZCode 退出 → 按期望状态应用/还原 → 写日志。它由开关同步（`sync.py`）在检测到重打包级差异时自动拉起，平时不运行，**不需要常驻服务**。

手动执行同类操作时，直接跑命令即可（ZCode 已退出的前提下）：

```bash
python "<skill目录>/scripts/zcode_patcher.py" --tps-footer
python "<skill目录>/scripts/zcode_patcher.py" --model-puller
```

> 本机另有一条独立路径：计划任务 `ZCodePatchApply` 指向原仓库 `F:\ZcodeData\zcode-patcher\scripts\_apply_after_exit.py`（硬编码 `D:/ZCode`，退出后注入 TPS + 拉取按钮并重启 ZCode）。它与插件机制互不干扰；要取消：`schtasks /Delete /TN ZCodePatchApply /F`。

五类补丁，均幂等、可检查、可还原、ZCode 升级后需重打：

| 能力 | 说法 | 命令 | 改哪里 |
|---|---|---|---|
| 思考等级透传 | 给自定义模型配思考等级 | `python zcode_patcher.py [--check/--revert/--extract]` | 内核 zcode.cjs（原地改写，.bak 备份） |
| 打开统计图 | 用量页趋势图/饼图去截断 | `python zcode_patcher.py --usage-chart [--check/--revert]` | app.asar 内渲染文件（同长度原地改字节 + integrity 同步） |
| 打开状态栏 | 输入框工具栏状态胶囊（**v2 纯 DOM 观测，3.12.2+ 安全**） | `python zcode_patcher.py --tps-footer [--check/--revert]` | app.asar（重打包级：注入脚本 + 挂载 index.html） |
| 加宽模型弹窗 | 模型选择浮窗加宽，长模型名不再截断 | `python zcode_patcher.py --model-width [--check/--revert]` | app.asar 内主 bundle（同长度原地改字节） |
| 模型拉取按钮 | 设置页一键拉取/勾选模型 | `python zcode_patcher.py --model-puller [--check/--revert]` | app.asar（重打包级：renderer 脚本 + index.html + preload 桥 + main IPC） |

两个及以上功能可一次执行：`python zcode_patcher.py --usage-chart --model-width --tps-footer --model-puller`。
另有命令行版拉模型（不动 asar，直接同步 config.json）：`python scripts/model_pull.py --all [--dry-run]`。

## 标准执行流程（AI 代执行与人工自助通用）

调用本 skill 时按以下序列执行，**AI 代执行时必须走完全部步骤，不得跳过核实与展示**：

1. **定位安装**：脚本自动探测（运行中进程 → 注册表 → 常见目录，跨 Windows/macOS/Linux，见「跨平台约定」）；探测不到就把安装根目录作为位置参数传入。
2. **只读核实**（能否生效的判断，全部只读，可放心先跑）：
   - `python zcode_patcher.py --check`：思考等级补丁状态；ZCode 版本不在「已知符号表」时跑 `--extract`——能提取出锚点即可生效，提取失败说明内核结构变了，按「新版本锚点提取」人工分析后再动。
   - `python zcode_patcher.py --usage-chart --check`：两个截断表达式是否命中。
   - `python zcode_patcher.py --model-width --check`：扁平弹窗锚点是否命中（3.14.x 渠道子菜单锚点已不存在，报「未找到锚点，跳过」属预期）。
   - `python zcode_patcher.py --tps-footer --check`：index.html 是否找到、注入状态。
   - `python zcode_patcher.py --model-puller --check`：四组件（renderer / index 挂载 / preload 桥 / main handler）逐个是否就位。
   - 脚本对「表达式出现次数 ≠1」「锚点不唯一」等情况一律拒绝盲改并报告原因——报告即结论，不要绕过。
3. **确认备份就绪并展示还原命令**（打补丁前必须完成，AI 代执行时明确提示用户保存还原命令）：
   - 思考等级：首次打补丁自动生成 `zcode.cjs.bak`（整文件备份）
   - 状态栏：首次注入自动生成 `app.asar.tps.bak`（整包备份）+ `app.asar.tps-patch.json`（原始 index.html 记录）
   - 统计图：sidecar `app.asar.chart-patch.json` 记录全部原始字节
4. **展示执行命令与还原命令**——**单功能单命令，按用户点名的功能给对应命令，不要捆绑其他功能**（各补丁相互独立；低风险的思考等级/统计图 AI 可在核实与备份确认后直接代执行，重打包级的状态栏交由用户执行）。以「打开状态栏」为例：
   ```bash
   # 执行
   python "<skill目录>/scripts/zcode_patcher.py" --tps-footer
   # 还原（万一异常，保存备用；完全退出 ZCode 后执行，还原后重启 ZCode）
   python "<skill目录>/scripts/zcode_patcher.py" --tps-footer --revert
   ```
   人工自助时用户自行执行；AI 代执行时经用户确认后由 AI 运行，或用户复制命令自己跑。
5. **重启验证**：完全退出并重启 ZCode（Windows 运行中锁 app.asar，打补丁前必须退出）后，按各功能的「验证」说明确认。
6. **失败回退**：执行上面展示的还原命令 → 重启 ZCode → 重新核实。思考等级补丁还原走 zcode.cjs.bak；状态栏还原自动清理 `.tps.bak` 与 sidecar。

## 跨平台约定

| 系统 | 安装根目录（resources 的上一级） | 典型位置 |
|---|---|---|
| Windows | `D:\ZCode`、`%LOCALAPPDATA%\Programs\ZCode` 之类 | 探测顺序：运行中进程路径 → 注册表卸载信息 → Program Files 系目录 |
| macOS | `/Applications/ZCode.app/Contents` | 探测 /Applications 与 ~/Applications 下的 `.app` 包（自动进入 `Contents`） |
| Linux | `/opt/ZCode`、`/usr/share/ZCode` 之类 | 探测 /opt、/usr/share |

- 关键文件相对安装根目录固定：`resources/glm/zcode.cjs`（内核）、`resources/app.asar`（桌面端资源包）
- Python ≥ 3.10，用系统可用的 `python3`/`python` 即可，脚本仅用标准库
- Program Files / /Applications 类目录可能需要管理员/sudo 权限
- 执行 AI 可按上述规则自行定位安装（如 `ls /Applications`、查运行中进程的 exe 路径）

## 升级后自查清单

ZCode 升级会覆盖 zcode.cjs 与 app.asar，升级后过一遍：

```bash
python zcode_patcher.py --check            # 思考等级补丁状态
python zcode_patcher.py --usage-chart --check
python zcode_patcher.py --model-width --check
python zcode_patcher.py --tps-footer --check
python zcode_patcher.py --model-puller --check
```

失配的按「自助使用流程」重打；思考等级补丁在新内核上先 `--extract` 确认锚点可提取。

## 一、思考等级：完整结论一张表

| 场景 | 档位来源 | 是否需要 config 配置 | 是否需要内核补丁 |
|---|---|---|---|
| 模型 id 含 "ox-alpha"，ZCode ≥ 3.9.1 | 内核硬编码白名单（`isOxAlphaReasoningModelId`），天生 low/high/max，defaultLevel=max | **不需要**（配了也是冗余） | **不需要** |
| 其它模型，ZCode ≥ 3.9.1，标准档名（low/medium/high/xhigh/max） | 引擎原生通用表：anthropic → `thinking:{type:"adaptive"}` + `output_config.effort` | **需要**：模型条目配 `reasoning.variants` | **不需要** |
| 其它模型，自定义档名（如 "turbo"） | 无原生表，留空 | 需要 | **需要**：补丁兜底合成 |
| ZCode ≤ 3.8.1，任何模型任何档位 | 无原生表 | 需要 | **需要** |

判别某次请求走的哪条路：`thinking:{type:"adaptive"} + output_config.effort` = 原生路径；`thinking:{type:"enabled", budget_tokens:N}` = 补丁兜底。补丁在原生表非空时惰性（`??` 短路），留着无害但升级后要重打才有意义。

### 档位从配置到请求的完整链路（补丁原理）

```
~/.zcode/v2/config.json  模型条目 reasoning.variants      ← 档位名数组（UI 显示的就是它）
      │  桌面端 host：variants → 内部 levels（每档参数对象）
      │  内核 override 构建器 → catalogOverrides 注入模型目录
      ▼
capability.reasoning = { enabled, levels, providerOptionsByLevel }
      │  内核档位解析函数 sD(3.8.1)/AD(3.9.1)/mN(3.11.2)(modelRef, 选中档名, catalog)
      ▼
{ level, providerOptions: providerOptionsByLevel[档名] }   ← 断点：自定义模型此表为空
      │  合并进请求
      ▼
anthropic: thinking:{type:"enabled",budget_tokens:N} + effort
openai 系: reasoning_effort:"档名"
```

`providerOptionsByLevel`（档名→请求参数表）只给内核认识的白名单家族（claude/glm/deepseek 等）下发；自定义模型拿到空表，档位能选中却不产生任何请求参数。补丁 = 在取参处加 `?? zCfgEffort(档名)` 兜底，按档名现场合成参数；白名单模型表非空，`??` 短路，行为零变化。

### ox-alpha 白名单的代码依据（3.9.1+ 原生支持）

- **(a) 硬编码白名单**：内核 override 构建函数 `t2t` 首分支 `t.reasoningProfile===Iue || j5(t.modelId)` → anthropic 协议拿 `jye()` = `{defaultLevel:"max", levels:["low","high","max"], providerOptionsByLevel:每档{effort, thinking:{type:"adaptive"}}}`。其中 `Iue=iLe([111,120,45,97,108,112,104,97])="ox-alpha"`，`j5` 注册名 `isOxAlphaReasoningModelId`（正则 `/ox-alpha/i` 子串匹配 + 两个内部预览模型）。与端点协议无关，同端点其它模型无此待遇。
- **(b) 标准档名通用表**：非白名单模型配了 `reasoning.variants` 且档名为标准名，构建时也套通用表（anthropic → adaptive+effort）。config 里配的档位集合即 UI 显示的集合。

### 工作流

1. **配档位**（每模型一次，完全退出 ZCode 后改 `~/.zcode/v2/config.json`）：
   ```json
   "reasoning": {"enabled": true, "variants": ["low", "high", "max"], "defaultVariant": "max"}
   ```
   **关于 `"zcode": {"modified": true}`**（客户端内部的"用户已改过、目录同步别覆盖"豁免标记：内核做模型目录合并时，`modified===true || deleted===true` 的条目整体保留本地版本）：
   - **走 UI 新增/编辑模型时客户端会自动盖章**——保存模型列表会重写整条 provider 的模型（3.11.2 实测：UI 里新增 deepseek-v4-pro 后，同 provider 的 deepseek-v4-flash 也一并被补上该标记）。UI 路径无需手动补。
   - **手改 config.json 绕过了 UI，没有任何路径替你盖章**，需要豁免就手动补——并入现有 `zcode` 块（如 `"zcode": {"modalitiesConfigured": true, "modified": true}`），别整体替换，否则丢掉既有键。对参与目录同步的条目是刚需；对自建 provider 的模型目前是纯保险（没有同步会碰它们，实测不补也长期存活）。
   - **手改更隐蔽的风险在 UI 保存**：保存 provider 的链路会先剥掉旧条目的 `variants`/`modified`/`contextWindow` 等（`omitModelCarryoverKeys`）再按表单状态重建——手改的 `reasoning.variants` 可能被一次 UI 保存吃掉（"档位退回开启/关闭开关"的另一条成因，与目录同步并列）。改完 config 后避免在 UI 里保存该 provider；保存过就回头确认档位仍在。
2. **仅当需要补丁时**（见判断表）：`--check` → 打补丁 → 完全退出并重启 ZCode。新版本无已知锚点先 `--extract`。
3. **验证**：rollout（`~/.zcode/cli/rollout/model-io-*.jsonl`）请求体里 `thinking`/`effort` 随档位变化；引擎日志（`~/.zcode/cli/log/`）无 400。**注意 rollout 请求体是脱敏的，`thinking` 字段会被剥掉（内置模型也一样），不能作为判据**——以 OpenRouter Dashboard → Activity Log 之类的外部请求日志为准。

### 内核补丁点与版本匹配

断点只有一处——内核 `resources/glm/zcode.cjs` 里的**思考档位解析函数**（见上方链路图）。zcode.cjs 是 esbuild 压缩产物，**每个版本的顶层符号名整体重排**，锚点（解析函数完整原文）必须与已安装版本逐字符一致：

| 版本 | 已知符号 |
|---|---|
| 3.8.1 | sD / G_e / Fgo / gXe |
| 3.9.1 | AD / Zye / fxo / utt |
| 3.9.2 | RD / Xye / Sxo / ptt |
| 3.11.2 | mN / k2e / CIo / _nt（本版返回处局部变量名为 s，替换逻辑已通用化） |

脚本按「全文唯一匹配」自动选择版本：对每个已知锚点统计出现次数，**恰好有一版 =1 才动手**；全为 0 或多版命中都拒绝修改。锚点匹配失败 ≠ 补丁思路失效——要改的内核点（解析函数返回处 `providerOptionsByLevel?.[X]` 查表表达式）所有版本语义不变，变的只是符号名。

**新版本锚点提取**：首选 `--extract` 自动提取——按结构特征（函数以 `{level:...providerOptionsByLevel?.[...]}:void 0}` 收尾、体内无嵌套 function）定位，已打补丁的文件自动回退 .bak 原始件，打印可直接粘贴进脚本 `ANCHORS` 字典的锚点（打印格式即条目格式，加个版本号键即可）。`--extract` 失败（候选数 ≠1）时人工提取：在内核里搜 `providerOptionsByLevel?.[`，找到以 `:void 0}` 收尾的完整解析函数整段复制为锚点；若函数体结构有变（不止符号改名），同步调整 `replacement_for()` 的拼接假设。加锚点后用 `--check` 验证恰好唯一命中再打。

### 档名与预算映射（补丁内置）

| 档名 | anthropic budget_tokens | openai 系 reasoning_effort |
|---|---|---|
| low | 4000 | low |
| medium | 8000 | medium |
| high | 16000 | high |
| xhigh | 32000 | xhigh |
| max | 32000 | max（**原名透传**，不折算成 xhigh） |
| 其它任意名 | 16000 兜底 | 不带 |
| disabled / none / off / nothink | thinking disabled | `reasoning_effort:"off"`（**不省略字段**） |

改数值或加档名：编辑脚本 `HELPER` 里的映射行 `{low:4e3,medium:8e3,high:16e3,xhigh:32e3,max:32e3}[t]??16e3`，重打补丁。

**openai 侧为何原名透传**（实测 workbuddy2api 网关联动得出）：智谱系网关的档位表是 low/high/max 且带 supported_efforts 自动降级——补丁若把 max 折算成 xhigh，网关会降级成 high（max 档丢失）；若「关闭」省略 reasoning_effort 字段，上游会落到默认档（glm-5.3 默认≈max，等于关不掉）。因此 openai 侧一律发原始档名：网关认识就透传，不认识自动降级/floor 到最近档（有日志）。注意「关闭」发 `off` 在"支持档全高于 off"的网关会被 floor 到最低档（如 low）而非真关闭——真关闭需要网关侧特判（如 workbuddy2api 正在加的 canDisableThinking 处理）；对官方 OpenAI 端点 `max`/`off` 是非法值会 400，此折中面向智谱系自定义网关生态。

**输出上限约束**：anthropic 协议要求 `budget_tokens < max_tokens`，等值会被内核钳到 max-1。模型条目不写 `limit.output` 时请求不带 max_tokens，由网关兜默认值；若写了 `limit.output`，必须大于所选档位预算（如 output 32000 配 max 档 32000 会直接 400）。

### deepseek 家族实测记录（3.11.2）

- 内核 override 构建器（`gwt`）分支顺序：ox-alpha 白名单 → glm-5.3 → kimi-k3 → **config.reasoning（自定义档位在此生效）** → 家族兜底
- 无自定义配置时 deepseek 走兜底 `CA()`：只有 enabled/disabled 两档开关、预算固定 1024
- 配了 variants + 内核补丁后：deepseek-flash 实测 max 档下发 `budget_tokens:32000 + effort:"max"`（补丁兜底路径，与内置 1024 明显区分）

### 故障排查

| 现象 | 原因与处理 |
|---|---|
| 档位能选但请求无 thinking | 内核补丁没打或打完没重启；`--check` 确认（3.9.1+ 标准档名走原生，无补丁也应生效） |
| 400: budget_tokens 必须小于 max_tokens | 所选档位预算 ≥ `limit.output`；调大上限或不设上限 |
| 400: max_tokens 缺失 | 不设 output 上限且网关不兜默认时出现；给模型设较大的 `limit.output` |
| 升级后失效 | 内核/app.asar 被覆盖；重跑补丁（无已知锚点先 `--extract`） |
| 档位选不到某名字 | `variants` 里没写，或 `defaultVariant` 不在列表内 |
| 档位配置丢失（退回"开启/关闭"开关） | 手改的 `reasoning` 被覆盖，两条成因：①目录同步——补 `zcode.modified: true` 豁免（UI 加的模型客户端会自动补）；②UI 保存 provider 时条目被重建、连带剥掉 `variants`——改完 config 别在 UI 里保存该 provider，保存过就回头确认档位仍在 |

## 二、打开统计图：用量页去截断

「设置 → 用量」两处展示截断，本补丁一并放开：

| 位置 | 原始行为 | 补丁点 |
|---|---|---|
| 每日 Token 趋势图 | 只画 Top 6 模型折线（`n.models.slice(0,6)`；每日 total 含全部模型） | 改为 `n.models` 全量出线 |
| 模型用量饼图 | 模型 >6 个时只画 Top 5，其余合并为「其他模型」（`i=n.length>Q,a=i?Q-1:Q`，Q=6） | 改为 `i=!1,a=1/0` 全量出块、无合并 |

```bash
python zcode_patcher.py --usage-chart            # 打补丁（asar 内同长度字节级原地覆盖）
python zcode_patcher.py --usage-chart --check    # 查状态
python zcode_patcher.py --usage-chart --revert   # 从 sidecar 还原全部原始字节
```

- 原理：解析 asar 头定位渲染文件偏移，替换截断表达式后用空格补齐到原字节长度原地写回——asar 头、offset、unpacked 结构零改动，无需重打包
- **integrity 同步**：改内容的同时按 sha256 hex 定长特性，同长度原地交换 asar 头里该条目的 integrity 哈希串，使记录与内容一致（Electron 默认不校验，但保持诚实；打/还原/已打重跑三条路径都会自动同步，可修复历史遗留的失配）
- 原始字节 base64 存同目录 `app.asar.chart-patch.json`（sidecar，含全部补丁点记录）
- **sidecar 带 asar 尺寸指纹**：每条记录绑定当时的 app.asar 总大小，ZCode 升级覆盖 asar 后旧 offset 不可信，指纹失配的记录自动作废重建（实测 3.9.1→3.9.2 升级后重打正常；TPS 重打包后脚本自动同步本 sidecar 的 offset/指纹）
- 两处调色盘均 6 色循环取色，第 7+ 个模型颜色重复，靠图例/标签区分
- 定位按文件名特征 + 截断表达式匹配，与文件名哈希无关，跨版本稳定（3.9.1→3.9.2 文件名哈希变化仍能命中）

## 三、加宽模型选择弹窗

模型选择浮窗（点工具栏模型名弹出的那个，含「先选渠道、再选模型」的二级子菜单）默认宽 `w-48` = **192px**，长模型名（如 `cn:deepseek-v4.1-flash`、带前缀的供应商模型）会被 `truncate` 截断，只能靠 hover 的 `title` 看全。本补丁把它加宽到 `w-80` = **320px**。

```bash
python zcode_patcher.py --model-width            # 打补丁（同长度字节级原地覆盖）
python zcode_patcher.py --model-width --check    # 查状态
python zcode_patcher.py --model-width --revert   # 从 sidecar 还原
```

两处补丁点（都在渲染层主 bundle，如 `out/renderer/assets/styles-*.js`）：

| 位置 | 原始 | 补丁后 |
|---|---|---|
| 二级子菜单（渠道 → 模型列表，`j??\`w-48\``） | 192px | 320px |
| 扁平弹窗（无渠道分组的单层列表，`\`w-48 max-h-72 overflow-y-auto\``） | 192px | 320px |

- **零重打包**：`w-48` 与 `w-80` 字面量**等长**（同为 4 字符），属同长度原地覆盖，asar 头/offset 零改动；ZCode 运行中也能写（不涉及文件替换）
- 宽度类名需已编译进 CSS：已核实 `.w-80` 存在（若未来版本裁剪了该类，可换 `w-64`/`w-72`/`w-96`，都已在 CSS 中；`min-w-*` 多数未编译，勿用）
- **文件按内容定位**（不依赖 assets 文件名里的哈希），跨版本稳定
- 状态判定兼容"已打"：锚点已被替换后，用替换后形态反查确认，不会误报「未找到锚点」
- 原始字节存 `app.asar.width-patch.json`，与图表补丁同为字节级 sidecar

## 四、打开状态栏：TPS 统计胶囊

> **实现：`zcode-tps.js`（唯一方案）—— 读 ServicePort 事件流，含精确 tok/s / 首 token / out**
>
> 数据来自 preload 转交的端口事件流：`usage.delta`（真实 outputTokens/inputTokens）、
> `stream.chunk`、行事件（turnHeader/userInput/reasoning/assistantText/row.delta）。
>
> ⚠️ **脚本内绝不调用 `port.start()`**（这是唯一但关键的约束）
>
> MessagePort 队列一旦启用，消息只派发给"启用瞬间已注册的监听器"。脚本是普通 script，
> 早于 `type=module` 的应用主包执行；若先 `start()` 就会消费掉服务端的 `Initialize`
> 启动握手 → 应用协议客户端永远收不到握手 → **ZCode 3.12.2 卡在启动界面**。
> 正确做法：只 `addEventListener`，把 `start()` 留给应用（届时双方都收到消息）。
>
> 真实 Chrome / MessageChannel 实测（双方持有同一 port）：
>
> | 做法 | 我们收到 | 应用收到 | 结果 |
> |---|---|---|---|
> | 我们先 `start()` | `["Initialize"]` | `[]` | ✗ 卡启动界面 |
> | 只监听、不 start | `["Initialize"]` | `["Initialize"]` | ✓ 双方正常 |
>
> **注意：不可用 Node 验证此语义** —— Node 的 MessagePort 在 `addEventListener` 时隐式
> start，会让"不 start"也失败，从而误判为"方案不可行"（曾据此绕过一圈）。
>
> **已知特性**：tok/s 在开始生成后约 1~4 秒才出现（滑动窗口需 ≥2 个采样点，且思考阶段
> 无文本增量），期间只显示 `●` 与时间，属预期；out（本轮累计输出）到达后立即显示。
>
> 本脚本源自 [linux.do 2886711](https://linux.do/t/topic/2886711)（lanvv）分享的
> `zcode-patcher.zip`，本仓库只删除了 `port.start()` 一行并补充自诊断，**计算逻辑与原版一致**。
> ⚠️ **回滚预案**：改动 asar 前先 `python scripts/restore_clean.py --backup` 存一份干净副本，
> 若客户端异常，完全退出后 `python scripts/restore_clean.py --latest` 秒级还原，无需重装 ZCode。

> 本能力与思考等级补丁源自 [linux.do 帖子 2886711](https://linux.do/t/topic/2886711)（作者 lanvv）分享的 `zcode-patcher.zip`（原帖授权"可以直接借鉴定制"）；本仓库在原基础上做了打包内核重写（纯 Python、保留 unpacked、原子替换）、跨平台/跨版本适配、全外科手术式还原等工程化改造。

输入框工具栏常驻一枚统计胶囊（水平居中于工具栏行，宽度上限 50%），展示**当前会话最近一轮**的生成指标：

```
生成中:  ● 21:03 · 32 tok/s · out 410
结束后:  ● 21:03 · 首 token 37s · out 1.7k
```

```bash
python zcode_patcher.py --tps-footer             # 注入 scripts/zcode-tps.js
python zcode_patcher.py --tps-footer --check     # 查状态
python zcode_patcher.py --tps-footer --revert    # 整体还原
python zcode_patcher.py --tps-footer --tps-src /path/to/zcode-tps.js   # 指定注入源
```

### 行为规则（验收标准）

- **绿点 ● 与时间常驻**：有可展示的轮次就在；流式生成中绿点发亮，空闲静态。无省略号占位。
- **分隔符 `·`** 隔开各段；标签灰、数值白、tok/s 橙、tabular-nums 对齐。
- **同一 turnId 复用（编辑重发/重试）自动清零**：检测到新一轮开始即重置旧统计，杜绝「时间变新、指标是旧的」残留。
- **out 语义**：**本轮累计输出**（最近一次提问→回答完成为止），非会话累计。
- **动态刷新**：流式中 1 秒节奏刷新——tok/s 为 4s 滑动窗口即时速度、out 为本轮估算值；基于回答文本的 token 估算（CJK 1 字≈1 token、其余 4 字符≈1 token）。`usage.delta` 精确值随每次模型请求完成到达即覆盖估算；轮结束后为精确值（精确 out ÷ 首块→末次 usage 的解码窗口）。
- **静默期保持**：工具执行期间文本停止增长，速度保持最近值不消失；点停止/出错时该次请求不报 usage，out 以内容估算兜底、速度保持最近值——已产生的数据不凭空消失。
- **切换会话立即消失**：渲染只认「DOM 可见轮次（`section[data-turn-id]`）+ `data-session-id` 匹配当前会话」双重条件，不依赖任何会话切换事件；多会话并行时各 tab 互不干扰。
- **历史会话只有 `● 时间`**：usage.delta 不回放，重新打开旧会话拿不到当时的 token 统计，属预期。
- **无假时钟**：轮次连时间戳都没有且无生成活动时不渲染，绝不拿当前时间冒充轮次时间。

### 数据链路原理（无常驻服务）

1. ZCode 桌面端 preload 把主进程的 MessagePort 经 `window.postMessage("zcode:service-port", "*", [port])` 转交渲染页面；注入脚本监听该事件接管端口（`window.__ztpsHook` 可对存量端口手动补挂，`window.__ztpsPort` 暴露端口供调试旁路监听）。
2. 会话协议帧为二进制（Uint8Array）内嵌 JSON（自首个 `{` 起），两类：
   - **version:1 事件流**（顶层带 sessionId/sourceCommandId/occurredAt）：`usage.delta`（inputTokens/outputTokens/cacheReadTokens/totalTokens/reasoningTokens，**每次模型请求完成时发**——一轮含工具调用会有多条，out 为该次请求输出）、`stream.chunk`（`assistantMessageId` + `chunkLength` + `channel`，流式期间每 50-100ms 一批）
   - **conversation 行事件**（`frame.payload.deltas`/`events`）：`turnHeader`（startedAt/endedAt/state）、`userInput`（createdAt）、`reasoning`/`assistantText`（`text` 全量 + `assistantResponseId`）、`row.delta`（`{rowId, path:"text", append:"文本增量"}`）
3. **轮关联链**（事件里的 id 有两套，务必分清）：轮的 key 是 productTurnId（`msg_xxx`，与 DOM `section[data-turn-id]` 一致）；stream.chunk 的 `assistantMessageId` 是 assistantResponseId（另一个 msg_xxx），需经行事件的 `assistantResponseId → turnId` 映射中转；usage.delta 经 `sourceCommandId` 关联（turnHeader/userInput 行携带）。关联断了的表现：out/tok/s 一直不出现。
4. 渲染：扫描 `section[data-turn-id]` + sessionId 双条件取当前会话最新轮 → 算指标 → 更新胶囊。
5. 刷新机制：MutationObserver 回调里 16ms 节流的**同步**刷新（切换会话零残留）＋ 60ms 防抖全量扫 ＋ 1s 估算刷新节奏；渲染带内容签名（stamp/ttft/tps/out/streaming），数据未变零 DOM 写，保证同步刷新不触发 observer 自激。

### 注入原理（asar 重打包级）

与统计图补丁的同长度原地覆盖不同，状态栏要**新增文件**，必须整体重打包：

1. 注入内容：`out/renderer/index.html` 的 `</body>` 前插 `<script src="./zcode-tps.js"></script>`；zcode-tps.js 作为新条目写入 `out/renderer/`。index.html 无 CSP meta、无 nonce，普通脚本标签即可（在 `type="module"` 的 React bundle 之前同步执行，注册监听早于应用挂载）。
2. asar 布局（读/写同一公式）：头 16 字节 = 4 个 uint32 LE `[4, headerSize, pickleLen, jsonLen]`，`pickleLen = 4 + jsonLen + pad4`，`headerSize = 8 + jsonLen + pad4`，数据区起点 = `16 + jsonLen + pad4`（pad4 把 JSON 补齐到 4 字节倍数）；文件条目 `offset` 为相对数据区起点的字符串，全部文件带 integrity（SHA256 全文 + 4MB 分块 hex）。
3. 重打包流程：读全量 → 树上删条目/插占位/标记覆盖 → 全部条目 offset 重排 → 覆盖与新增条目重算 integrity → 写临时文件 → **回读校验**（逐条比对注入条目字节）→ 原子替换。
4. **实现关键坑**（改 `_repack_asar` 前必读）：offset 重排会直接改写条目，此后从旧文件切片必须用**重排前快照的旧位置**，否则「新 offset + 旧数据区起点」错位读取（实测 50 个抽查文件错 11 个，且改动文件恰好走覆盖分支不受影响，极易漏测）；新增条目的树插入必须在重打包函数内部做（外层持有的树引用与函数内部重新读入的不是同一棵）。
5. 备份与记录：首次注入前整包备份 `app.asar.tps.bak`；`app.asar.tps-patch.json` 记录原始 index.html（base64）与 asar 尺寸指纹。
6. 与统计图补丁联动：重打包使 chart sidecar 的绝对 offset/指纹失效，脚本自动按「文件路径 + 尺寸」重定位同步；反向无影响（统计图是同长度覆盖，不改 offset）。

### 升级 / 回退 / 排障

| 现象 | 处理 |
|---|---|
| 升级后胶囊消失 | app.asar 被覆盖，重跑 `--tps-footer`（注入源默认 skill 自带 zcode-tps.js） |
| 重启后无胶囊 | `--tps-footer --check` 看 state；渲染进程 console 查 `window.__ztps` 是否存在 |
| console 出现 CSP 拦截报错 | 当前版本 index.html 无 CSP；若未来版本加了，需同步放宽 `script-src` 允许同目录脚本 |
| 指标一直只有「● 时间」 | usage.delta 未关联到轮（看 `window.__ztpsTurns` 里轮的 out/lastUsageAt 是否为空）；版本升级导致帧结构变化时，用 `window.__ztpsPort` 旁路监听原始帧比对字段 |
| tok/s 不出现 | 该轮从未有过文本流（纯工具调用轮）时无速度可算，属预期；有文本流后静默期（工具执行）保持最近值 |
| 想换脚本逻辑 | 改 zcode-tps.js 后重跑 `--tps-footer`（内容变了会自动热更新脚本条目，无需 revert） |

## 五、模型拉取按钮：设置页一键拉取/勾选模型

设置 → 模型供应商页注入「⚡️ 自动拉取模型」按钮：点开弹窗自动拉取该供应商 `/models` 接口的模型列表，标注「已添加/新模型」并勾选，保存后经**原生 IPC** 读写 `~/.zcode/v2/config.json` 并自动刷新界面——不用退出 ZCode、不用手改 config。

```bash
python zcode_patcher.py --model-puller             # 注入（默认用本 skill scripts/zcode-model-puller.js）
python zcode_patcher.py --model-puller --check     # 查状态（逐组件：renderer/挂载/preload/main）
python zcode_patcher.py --model-puller --revert    # 整体还原
python zcode_patcher.py --model-puller --puller-src /path/to/zcode-model-puller.js
```

- **来源**：前端脚本 vendored from [HHQ-666/zcode-model-puller](https://github.com/HHQ-666/zcode-model-puller)（MIT）；原项目的 npx @electron/asar 全量解包/重打包方案未采纳（Node 依赖 + 会把 12 个 unpacked 原生模块打进 asar 内部，Electron 无法从 asar 加载 .node），改用本 skill 自带的纯 Python `_repack_asar`（unpacked 条目原样跳过、原子替换、回读校验），路径走 `discover()` 跨平台探测（原项目仅支持 macOS）
- **注入四件套**：① `out/renderer/zcode-model-puller.js` 新文件；② index.html `</body>` 前挂 `<script type="module">`（与 TPS 同文件共存、互不干扰）；③ preload 在 `contextBridge.exposeInMainWorld("zcode",{` 对象开头插入 3 个 IPC 桥方法；④ main 在 `SaveMcpToUserDirectory` 注册语句前插入 3 个 IPC handler（读 config / 写 config（先落 config.json.puller-bak）/ 代理拉模型列表）
- **锚点跨版本设计**：preload/main 锚点用语义字符串（`exposeInMainWorld("zcode",{`、`SaveMcpToUserDirectory`——后者是 IPC 通道名，非压缩符号），正则捕获周边的压缩别名（electron 导入别名 / ipcMain 包装别名）拼进注入代码——**无需按版本维护符号表**；命中数 ≠1 一律拒绝
- **保存语义（重要）**：整份 config 读出 → 只对不存在的模型 `p.models[mid] = {模板}` → 整份写回。**已有条目原样保留**（手改的 reasoning.variants 安全，比 ZCode 自带设置页保存还会剥 variants 更安全）
- **新供应商一步到位**：保存时按 baseURL（去尾斜杠）匹配供应商，匹配不到再按界面名称兜底，仍匹配不到则**自动创建** provider 条目（UUID id、source:custom、表单里的 baseURL/apiKey），创建后 toast 提示——破解 ZCode 自带流程的死锁（「添加供应商」按钮要求先有 ≥1 个模型，而手填模型 id 正是痛点）。`kind`（API 协议）判定优先读表单「API 格式」下拉的显示文案（3.11.2 实测三选项：`Anthropic Messages (/v1/messages)` → anthropic、`Chat Completions (/chat/completions)` → openai-compatible、`Responses (/responses)` → openai），读不到再按 URL 特征（含 anthropic → anthropic，`/v1` 结尾 → openai-compatible）→ 现有自定义供应商的 kind → openai-compatible。猜错表现为请求协议不对，设置页改 API 格式保存即可
- **新模型模板（元数据感知）**：拉取时读取 `/v1/models` 透出的每模型元数据——`context_length`/`max_input_tokens` → `limit.context`、`max_output_tokens`/`max_completion_tokens` → `limit.output`、`supported_efforts` → `reasoning.variants`（自动前插 off 档）、`default_effort` → `defaultVariant`（workbuddy2api 等网关透出这些字段）；元数据缺失时退回保守模板 `limit 1M/128k + off/high/max`。`zcode.modified: true` 恒有——与 CLI `model_pull.py` 一致
- **还原全外科手术式**：index.html 只摘自己的 tag、preload/main 用正则精确摘除自己注入的字节段（别名通配），**不依赖 sidecar 指纹**——其他补丁重打包改了 asar 大小也能精确还原；TPS 的还原同样已改为外科手术式，两个重打包级补丁任意顺序装/卸互不误伤
- **组件级热更新**：`--check`/注入以「内容比对」判定每个组件是否为现行版本（不能只看 marker——换实现后 marker 不变会漏更新）；不一致的组件自动重注入，一致的原样跳过；注入段形态不符（被手工改过）时拒绝改写并提示
- **备份**：首次注入整包备份 `app.asar.puller.bak` + sidecar `app.asar.puller-patch.json`；与 TPS/chart 的 sidecar 联动同现有机制（重打包后自动同步 chart sidecar 的 offset/指纹）

### 命令行版（不动 asar）

```bash
python scripts/model_pull.py                 # 交互选供应商
python scripts/model_pull.py --all           # 同步全部自定义供应商
python scripts/model_pull.py --provider 关键字 [--dry-run] [--refresh] [--no-reasoning]
python scripts/model_pull.py --test https://api.example.com/v1 [KEY]
```

新模型默认**元数据感知**：网关透出 `context_length/max_output_tokens/supported_efforts/default_effort` 时按模型写实 limit 与思考档位，缺失退回 `1M/128k + off/high/max` 模板（`--no-reasoning` 关闭）。已有条目默认原样保留；**`--refresh` 按服务器元数据刷新已有条目的 limit 与思考档位**（其余键不动，手改过的条目也只动这两处）。注意 CLI 在 ZCode 运行中写 config 有被回写覆盖的竞态，建议退出后跑；注入版无此问题。

### 排障

| 现象 | 处理 |
|---|---|
| 设置页没有按钮 | `--model-puller --check` 看四组件哪个缺；渲染 console 查 `window.__ZCODE_MODEL_PULLER_LOADED_PRO__` |
| 按钮报「通信桥不可用」 | preload 桥缺失（`window.zcode.readConfigFile` 应为函数）；重跑注入会逐组件补齐 |
| 拉取失败 | 供应商 baseURL 不通或鉴权失败；先用 `model_pull.py --test <URL> [KEY]` 单测 |
| 自动创建的供应商协议不对（请求报错/无响应） | kind 猜错（如 OpenAI 兼容端点被判成 anthropic）：设置页打开该供应商，把 API 格式改对保存即可 |
| 想换注入脚本逻辑 | 改 `scripts/zcode-model-puller.js` 或改 preload/main 注入构造器后重跑 `--model-puller` 即可——**四组件（renderer/preload/main/index）均按内容比对**，只更新与现行实现不一致的组件，无需 revert；`--check` 会显示「含旧版组件，重跑可自动更新」 |
| 保存后档位丢失 | 正常不会（整读整写不重建条目）；若用 ZCode 自带设置页保存过该 provider，属其重建路径剥掉 variants，重配 reasoning 即可 |
