# zcode-tokenspeed(ZCode 插件)

ZCode 桌面客户端的本地增强补丁插件:六个补丁覆盖模型思考档位、用量图表、输入框统计条、思考强度滑条与模型拉取。纯 Python 标准库,不依赖 Node;全部补丁幂等、可检查、可精确还原。

> **非官方项目**,与 ZCode(智谱)官方无任何关联。所有改动均在本地对已安装的客户端打补丁,随时可精确还原。

## 功能一览

| 补丁 | 一句话效果 | 命令参数 |
|---|---|---|
| 思考档位透传 | 自定义模型的档位真正下发到请求体(3.14.x 走原生 `optionSpecs`,无需内核补丁) | 内核补丁 `(无参数)` / 配置 `optionSpecs` |
| 用量页去截断 | 趋势图 / 饼图全量展示,不再只画 Top 6 / Top 5 | `--usage-chart` |
| 模型弹窗加宽 | 模型浮窗 192px → 320px,长模型名不再截断 | `--model-width` |
| TPS 状态栏 | 输入框下方居中统计条:本轮指标 + 会话累计,右键可切位置 | `--tps-footer` |
| 思考强度滑条 | 原生下拉替换为点击弹出的吸附拖拽条,拖完即时生效 | `--thought-slider` |
| 模型拉取按钮 | 一键拉取 `/models`,已添加自动标注,勾选即写入 | `--model-puller` |

**TPS 状态栏**(`--tps-footer`)——`● 32 tok/s · out 1.7k │ 第 8 轮 │ 输入 45.2k · 命中 38.1k · 平均命中 84% · 累出 12.3k`:左组本轮即时指标(首 token / tok/s / out),右组会话累计(轮数 / 累计输入 / 累计命中与平均命中率 / 累计输出),组间竖线分隔;流式中实时刷新,空会话空态常驻,右键可切「输入框工具栏 / 会话顶部 sticky」。

**思考强度滑条**(`--thought-slider`)——工具栏常驻「思考 · 档名」入口(迷你电量条),点击弹出吸附拖拽条:八帧奔跑小人滑块(拖得越快跑得越快,松手减速停下)、换档涟漪、填充弹性扫入、刻度级联弹入;档位取自模型实际配置(配几档吸几档),写档走原生链路,与原生状态双向同步,会话内即时生效。

每个补丁都支持 `--check`(只读查状态)与 `--revert`(精确还原),互不干扰、可单独装卸。

## 快速开始

要求 [Python](https://www.python.org/) ≥ 3.10(仅标准库,无第三方依赖)。

**方式一:安装插件**(推荐)

把本仓库放入 ZCode 插件目录,或用插件市场安装。安装后在新任务里:

- 用斜杠命令:`/zcode-patch-status`(只读检查)、`/zcode-patch-apply`(注入)、`/zcode-patch-revert`(还原)、`/zcode-patch-toggle`(逐项开关);
- 或直接点名「zcode-tokenspeed」说明要哪个功能。

**方式二:直接跑脚本**

```bash
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --check           # 只读检查(可放心先跑)
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer      # 打 TPS 统计条
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --thought-slider  # 打思考强度滑条
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer --revert   # 还原
```

安装位置自动探测(运行中进程 → 注册表 → 常见目录),也可显式传参:`python zcode_patcher.py "D:\ZCode"`。

> 重打包级补丁(TPS / 滑条 / 拉取按钮)在 ZCode 运行中会被文件锁挡住,**打补丁前完全退出 ZCode**,打完重启生效。每个补丁首次执行自动生成整包备份(`.bak`)与逐字节记录(sidecar json),还原精确到字节。

## 插件开关

「设置 → 插件管理 → 已安装 → 点开本插件」的**配置**区有 5 个开关,分别控制五个补丁。拨动并点「保存配置」后,插件在下次会话启动时自动把客户端同步过去,不用手打命令。

- **只同步你显式保存过的开关**——没拨过的一律不碰,首次安装不会自动改动客户端;
- 生效时机:用量图表、弹窗加宽是字节级改写,下次会话启动即生效;TPS 状态栏、滑条、拉取按钮要重写 `app.asar`,由看护在 **ZCode 退出时自动应用**;
- `core_patch`(思考档位内核补丁)只对 ZCode 3.11.2 及更早有效,3.14.x 用原生 `optionSpecs` 机制,保持关闭即可;
- 详情页没有「配置」区(渲染问题)时,直接写 `~/.zcode/cli/config.json` → `plugins.options["zcode-tokenspeed@dev-default-22da16fd"]`,或打 `/zcode-patch-toggle` 让 AI 代改。

## 仓库结构

```
.zcode-plugin/plugin.json                    清单(含功能开关的 userConfig 声明)
skills/zcode-tokenspeed/SKILL.md             执行流程 + 逆向笔记 + 排障(AI 代执行入口)
skills/zcode-tokenspeed/scripts/
  zcode_patcher.py                           主工具:六个补丁(解析/重打包 asar 不依赖 Node)
  zcode-tps.js                               TPS 统计条注入脚本(ServicePort 事件流)
  zcode-thought-slider.js                    思考强度滑条注入脚本(奔跑小人滑块)
  zcode-model-puller.js                      模型拉取按钮前端脚本
  sync.py                                    开关同步(SessionStart hook 调用)
  apply_after_exit.py                        退出后看护:等 ZCode 退出 → 应用重打包级补丁
  model_pull.py                              CLI 拉模型:不动 asar,直接同步 config.json
  probe_max_tokens.py                        探测网关真实输出上限(识别「静默钳制」)
  tap_proxy.py                               请求捕获代理:验证思考参数是否真发出
  restore_clean.py                           紧急整包还原(客户端异常时无需重装)
hooks/hooks.json                             SessionStart 钩子
commands/                                    /zcode-patch-status / apply / revert / toggle
```

## 版本与平台

| 平台 | 支持 | 备注 |
|---|---|---|
| Windows | ✅ 实测 | 打 asar 补丁前需完全退出 ZCode;Program Files 下需管理员终端 |
| macOS | ✅ 逻辑支持 | 修改 `.app` 会破坏签名,启动异常时重新 ad-hoc 签名即可 |
| Linux | ✅ 逻辑支持 | 探测 `/opt`、`/usr/share` |

对未知版本 / 未知结构,脚本一律拒绝盲改并报告原因,不会写坏文件。

## License

[MIT](./LICENSE)
