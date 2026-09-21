# zcode-tokenspeed（ZCode 插件）

ZCode 桌面客户端的本地补丁注入插件：技能负责执行流程与排障，脚本负责真正改字节。

> **非官方项目**，与 ZCode（智谱）官方无任何关联。所有改动均在本地对已安装的客户端打补丁，随时可精确还原。

## 提供的补丁

| 补丁 | 效果 | 命令参数 |
|---|---|---|
| 思考档位透传 | 自定义模型的档位真正下发到请求体（3.14.x 走原生 `optionSpecs`，无需内核补丁） | 内核补丁 `（无参数）`／配置 `optionSpecs` |
| 用量页去截断 | 「设置 → 用量」趋势图 / 饼图全量展示，不再只画 Top 6 / Top 5 | `--usage-chart` |
| 模型弹窗加宽 | 模型选择浮窗 192px → 320px，长模型名不再截断 | `--model-width` |
| TPS 状态栏 | 输入框工具栏统计胶囊：`● 21:03 · 首 token 37s · 32 tok/s · out 1.7k` | `--tps-footer` |
| 模型拉取按钮 | 设置页「⚡️ 自动拉取模型」，勾选即写入配置，可自动新建供应商 | `--model-puller` |

每个补丁都支持 `--check`（只读查状态）与 `--revert`（精确还原），互不干扰、可单独装卸。

## 插件组件

```
.zcode-plugin/plugin.json                    清单（含 5 个功能开关的 userConfig 声明）
skills/zcode-tokenspeed/SKILL.md                 执行流程 + 逆向笔记 + 排障（AI 代执行入口）
skills/zcode-tokenspeed/scripts/
  zcode_patcher.py                            主工具：五个补丁（纯 Python 标准库，解析/重打包 asar 不依赖 Node）
  sync.py                                     开关同步：读配置 → 比对实际状态 → 应用差异（SessionStart hook 调用）
  apply_after_exit.py                         退出后看护：等 ZCode 退出 → 应用重打包级补丁
  model_pull.py                               CLI 拉模型：不动 asar，直接同步 config.json
  zcode-tps.js                                TPS 状态栏注入脚本
  zcode-model-puller.js                       模型拉取按钮前端脚本
  tap_proxy.py                                请求捕获代理：验证思考参数是否真发出
  probe_max_tokens.py                         探测网关真实输出上限（识别「静默钳制」）
  restore_clean.py                            紧急整包还原（客户端异常时无需重装）
hooks/hooks.json                              SessionStart 钩子（启动时跑开关同步）
commands/
  zcode-patch-status.md                       /zcode-patch-status 只读检查
  zcode-patch-apply.md                        /zcode-patch-apply  注入
  zcode-patch-revert.md                       /zcode-patch-revert 还原
  zcode-patch-toggle.md                       /zcode-patch-toggle 逐项开关功能
```

## 功能开关

在「设置 → 插件管理 → 已安装 → 点开本插件」的**配置**区有 5 个开关，分别控制五个补丁。拨动并点「保存配置」后，插件会在下次会话启动时自动把客户端同步过去，不用手打命令。

> **如果详情页「高级信息」里没有出现「配置」区**：这是 ZCode 侧的渲染问题（界面拿到的插件信息里 `userConfig` 为空时配置区整个不渲染），与插件清单无关。此时直接写配置文件，效果一样：`~/.zcode/cli/config.json` → `plugins.options["zcode-tokenspeed@dev-default-22da16fd"]`，例如 `{ "tps_footer": false }`。也可以打 `/zcode-patch-toggle` 让 AI 代改。

生效时机分两类：用量图表和弹窗加宽是字节级改写，ZCode 运行中也能写，**下次会话启动即生效**；TPS 状态栏和模型拉取按钮要重写 `app.asar`，运行中被文件锁挡住，所以由看护在 **ZCode 退出时自动应用**，下次启动生效。

另外，`core_patch`（思考档位内核补丁）**只对 ZCode 3.11.2 及更早有效**。3.14.x 起内核改用原生 `optionSpecs` 机制，这个开关打开也打不上——脚本会拒绝改写并在日志里说明原因，保持关闭即可。

两条行为约定：**只有你显式保存过的开关才会被同步**——没拨过的开关插件一律不碰，首次安装不会自动改动客户端；开关的默认值（关闭）是静态的，不反映你之前手动打的补丁，**以你想要的状态为准拨一次**，同步后两者就一致了。同步日志写在 `skills/zcode-tokenspeed/scripts/_sync.log`。

## 用法

安装后在**新任务**里二选一：

- 用斜杠命令：`/zcode-patch-status`、`/zcode-patch-apply`、`/zcode-patch-revert`；
- 或直接点名「zcode-tokenspeed」并说明要哪个功能（该技能设计为**仅手动调用**，不会自动触发）。

也可以完全绕开 AI，直接跑脚本：

```bash
python "<插件目录>/skills/zcode-tokenspeed/scripts/zcode_patcher.py" --check
python "<插件目录>/skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer
python "<插件目录>/skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer --revert
```

打完补丁**完全退出并重启 ZCode** 生效。重打包级补丁（TPS / 拉取按钮）在 ZCode 运行中会被文件锁挡住，必须先退出。

## License

[MIT](./LICENSE)
