# zcode-tokenspeed(ZCode 插件)

ZCode 桌面客户端的本地增强补丁插件:七个补丁覆盖模型思考档位、用量图表、输入框统计条、思考强度滑条与模型拉取。纯 Python 标准库,不依赖 Node;全部补丁幂等、可检查、可精确还原。

> **非官方项目**,与 ZCode(智谱)官方无任何关联。所有改动均在本地对已安装的客户端打补丁,随时可精确还原。

## 功能一览

| 补丁 | 一句话效果 | 命令参数 |
|---|---|---|
| 思考档位配置（3.14+） | 自定义模型的档位真正下发到请求体:档位写进 `provider_config.json` 的 `optionSpecs.reasoningLevel`(界面档位列表 + 请求体参数都由它下发),无需内核补丁 | `--reasoning-config` |
| 思考档位透传（≤3.11） | 老机制下的内核补丁兜底;3.14+ 会提示"本补丁不适用" | 内核补丁 `(无参数)` |
| 用量页去截断 | 趋势图 / 饼图全量展示,不再只画 Top 6 / Top 5 | `--usage-chart` |
| 模型弹窗加宽 | 模型浮窗 192px → 320px,长模型名不再截断 | `--model-width` |
| TPS 状态栏 | 输入框下方居中统计条:本轮指标 + 会话累计,右键可切位置 | `--tps-footer` |
| 思考强度滑条 | 原生下拉替换为点击弹出的吸附拖拽条,拖完即时生效;视觉规格对齐 [dsh-reasoning-effort](https://github.com/HanaAyane/dsh-reasoning-effort) | `--thought-slider` |
| 模型拉取按钮 | 一键拉取 `/models`,已添加自动标注,勾选即写入;3.14.x 起同步 `provider_config.json` 新 schema,删除后可再次添加 | `--model-puller` |

通用开关:`--check`(只读核实) · `--revert`(还原) · `--dry-run`(只报告改动不写盘) · `--verbose`(打印探测细节)。
打补丁/还原前会**预检 ZCode 进程**,运行中直接拒绝(退出码 2);`--check` 与 `--dry-run` 不受限。
备份带**版本指纹**:客户端升级后旧备份自动归档,还原时若与当前版本不符会拒绝执行,避免把旧内核/asar 盖回新客户端。

**TPS 状态栏**(`--tps-footer`)——`● 32 tok/s · out 1.7k │ 第 8 轮 │ 输入 45.2k · 命中 38.1k · 平均命中 84% · 累出 12.3k`:左组本轮即时指标(首 token / tok/s / out),右组会话累计(轮数 / 累计输入 / 累计命中与平均命中率 / 累计输出),组间竖线分隔;流式中实时刷新,空会话空态常驻,右键可切「输入框工具栏 / 会话顶部 sticky」。

**思考强度滑条**(`--thought-slider`)——工具栏常驻「思考 · 档名」入口(迷你电量条),点击弹出吸附拖拽条:八帧奔跑小人滑块(拖得越快跑得越快,松手减速停下)、换档涟漪、填充弹性扫入、刻度级联弹入;视觉规格对齐 [HanaAyane/dsh-reasoning-effort](https://github.com/HanaAyane/dsh-reasoning-effort):深蓝→紫渐变轨道、滑块左侧拖尾光斑、拖拽增辉、max 档轨道呼吸泛光,深浅主题各自适配;档位取自模型实际配置(配几档吸几档),写档走原生链路,与原生状态双向同步,会话内即时生效。

**模型拉取按钮**(`--model-puller`)——3.14.x 的界面模型列表以 `<dataBaseDir>/.zcode/v2/provider_config.json` 为唯一事实源(`dataBaseDir` 从 `~/.zcode/v2/setting.json` 解析,数据目录迁移到 F 盘等场景也能正确定位),本补丁读写时自动合并/回写新 schema 的 `personalModelIds`/`modelOrder` 与模型规则,同时保持旧 `config.json` 元数据一致;界面删除模型后再次拉取可正常重加,不再出现「已添加却写不进列表」。

每个补丁都支持 `--check`(只读查状态)与 `--revert`(精确还原),互不干扰、可单独装卸。

## 快速开始

要求 [Python](https://www.python.org/) ≥ 3.10(仅标准库,无第三方依赖)。

**方式一:安装插件**(推荐)

```bash
git clone https://github.com/c80361619/zCode-TokenSpeed.git
```

然后把克隆得到的目录放入 ZCode 的插件目录(Windows 默认 `~/.zcode/plugins/`,即 `C:\Users\<用户名>\.zcode\plugins\`;数据目录迁移过的用户是 `<dataBaseDir>\.zcode\plugins\`),重启 ZCode 即完成安装。安装后在新任务里:

- 用斜杠命令:`/zcode-patch-status`(只读检查)、`/zcode-patch-apply`(注入)、`/zcode-patch-revert`(还原)、`/zcode-patch-toggle`(逐项开关);
- 或直接点名「zcode-tokenspeed」说明要哪个功能。

**方式二:直接跑脚本**(不想装插件,手动打补丁)

```bash
git clone https://github.com/c80361619/zCode-TokenSpeed.git
cd zCode-TokenSpeed

python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --check           # 只读检查(可放心先跑)
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer      # 打 TPS 统计条
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --thought-slider  # 打思考强度滑条
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --model-puller    # 打模型拉取按钮
python "skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer --revert   # 还原
```

安装位置自动探测(运行中进程 → 注册表 → 常见目录),也可显式传参:`python zcode_patcher.py "D:\ZCode"`。

> 重打包级补丁(TPS / 滑条 / 拉取按钮)在 ZCode 运行中会被文件锁挡住,**打补丁前完全退出 ZCode**(托盘右键退出,不是关窗口),打完重启生效。每个补丁首次执行自动生成整包备份(`.bak`)与逐字节记录(sidecar json),还原精确到字节。

## 打完补丁后怎么用

| 补丁 | 在哪里用 |
|---|---|
| TPS 状态栏 | 打开任意会话,输入框下方自动出现统计条;**右键**它可切换「输入框工具栏 / 会话顶部 sticky」位置 |
| 思考强度滑条 | 输入框工具栏的「思考 · 档名」入口(原生下拉已被替换),点击弹出拖拽条,拖到目标档位松手即生效;←/→ 键可微调 |
| 模型拉取按钮 | 「设置 → 模型供应商」新建/编辑自定义供应商,填好 **Base URL 和 API Key** 后,点旁边的「⚡️ 自动拉取模型」→ 弹窗里勾选要的模型(新模型默认勾选,已添加的标注「已添加」)→ 点「确认添加并保存」,模型立即出现在列表 |
| 用量页去截断 / 模型弹窗加宽 | 无需操作,重启 ZCode 后自动生效 |

**模型拉取的典型流程**(第一次添加供应商):

1. 设置 → 模型供应商 → 添加自定义供应商;
2. 填名称、Base URL(如 `https://api.example.com/v1`)、API Key;
3. 点「⚡️ 自动拉取模型」——**不用先保存供应商**,补丁会自动创建条目并判定 API 协议;
4. 弹窗勾选模型 → 确认 → 模型列表即刻出现,聊天输入框里就能选到。

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
  model_pull.py                              CLI 拉模型:不动 asar,直接同步 config.json + provider_config.json
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

对未知版本 / 未知结构,脚本一律拒绝盲改并报告原因,不会写坏文件。开发与实测基于 **ZCode 3.11.2 / 3.14.1(Windows)**;3.14.x 起界面供应商列表由 `<dataBaseDir>/.zcode/v2/provider_config.json` 驱动,模型拉取补丁与 CLI 已同步适配,旧版客户端不受影响。

## 常见问题(FAQ)

**Q:补丁打了但界面没变化?**
确认两点:① 打补丁时 ZCode 是否完全退出(运行中会被文件锁挡住,命令会报「文件被占用」);② 打完后是否重启了 ZCode。可用 `--check` 查看各补丁状态。

**Q:点「自动拉取模型」提示拉取失败?**
检查 Base URL 是否可直接访问 `<baseURL>/models`(部分网关要求 Key,补丁会自动带上表单里的 Key);URL 结尾带不带 `/v1` 都可以,补丁会自动尝试多种路径组合。

**Q:拉取成功但模型列表里没有?**
确认用的是最新版代码(2026-09-22 之后的提交修复了 3.14.x 的 `provider_config.json` 适配);旧版脚本在新版客户端上会出现「写入成功但界面不显示」。重跑 `--model-puller` 更新注入即可,无需先还原。

**Q:供应商列表全部消失?**
查看 `<dataBaseDir>/.zcode/v2/logs/` 最新日志,若出现「Personal Provider Config 加载失败」,说明配置文件被旧版脚本写坏(内置供应商污染或智能/手动规则冲突)。用 `provider_config.json.puller-bak` 或同目录 `.conflict-bak*` 备份覆盖回去,再重启 ZCode;并确保补丁已更新到最新版。

**Q:ZCode 升级后补丁失效?**
升级会覆盖 `app.asar`,重跑对应补丁命令即可(内核补丁可用 `--extract` 自动提取新版本锚点)。

**Q:想全部还原?**
逐个 `--revert`,或用 `restore_clean.py` 从干净备份整包恢复(客户端异常时无需重装 ZCode)。

## License

[MIT](./LICENSE)
