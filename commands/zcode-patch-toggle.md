---
description: 逐项查看/切换 zcode-tokenspeed 的注入功能开关
---

加载本插件自带的 `zcode-tokenspeed` 技能（`${CLAUDE_PLUGIN_ROOT}/skills/zcode-tokenspeed/SKILL.md`；若该变量未展开，就按插件安装目录下的同名路径读取），然后按下面的流程帮用户逐项开关补丁功能。

**开关的存储位置**：`~/.zcode/cli/config.json` 的 `plugins.options["zcode-tokenspeed@dev-default-22da16fd"]`，键名与功能对应：

| 键 | 功能 | 生效时机 |
|---|---|---|
| `usage_chart` | 用量页去截断 | 下次会话启动（字节级，无需重启 ZCode） |
| `model_width` | 模型弹窗加宽 | 下次会话启动（字节级） |
| `tps_footer` | TPS 状态栏 | ZCode 退出时自动应用，重启后生效 |
| `model_puller` | 设置页模型拉取按钮 | ZCode 退出时自动应用，重启后生效 |
| `core_patch` | 思考档位内核补丁（仅 ≤3.11.2） | 下次会话启动 |

**流程**：

1. 读 `~/.zcode/cli/config.json`，取出上述键的当前值（缺失表示"用户未表态"，插件不会去动它）。
2. 同时跑 `python "<skill目录>/scripts/zcode_patcher.py" <对应参数> --check` 拿到**客户端实际注入状态**，把"配置值 / 实际状态"两列一起用中文表格展示给用户。
3. 问用户要改哪个键、改成什么。用户明确指定后再改。
4. 改配置：**整份读入 → 只增改目标键 → 写回**，保持 JSON 缩进与其余内容不变；`plugins.options` 或插件 id 那一层不存在时按需创建。**不要动 `enabledPlugins` 等其他字段。**
5. 告诉用户生效时机（见上表）；字节级的下次会话启动自动生效，重打包级的会在 ZCode 退出时由看护应用。
6. 若用户想立刻生效而不等下次会话，可以按技能里的「标准执行流程」直接执行对应命令（ZCode 已退出的前提下）。

**注意**：改 `config.json` 前建议先备份一份（`config.json.bak`）；若 ZCode 正在运行且用户在设置页保存过供应商配置，有被回写的可能，改完提示用户确认一次。
