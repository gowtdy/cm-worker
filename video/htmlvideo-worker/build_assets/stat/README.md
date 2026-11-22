# skill-timing.py 使用说明

Claude Code 转录中各 skill/阶段耗时统计。扫描 `/app/log/claude/projects/**/*.jsonl`,自动识别 skill 运行并输出与 `/app/log/docs/*-analysis.md` 各阶段分析对齐的统计。

## 快速上手

```bash
python3 skill-timing.py                           # 全量列表(默认仅 skill 运行,按时间排序)
python3 skill-timing.py --group                   # 按 skill 聚合:runs / 耗时分布 / rounds / 令牌
python3 skill-timing.py b4c8974f                  # 单次运行 detail(session-id 前缀或 jsonl 路径)
python3 skill-timing.py b4c8974f --timeline       # detail + 逐工具时间线
python3 skill-timing.py --since '2026-11-16'      # 只统计该时间(UTC)之后开始的运行
python3 skill-timing.py --csv                     # list 输出 CSV(time,path,skill,ttfb_s,dur_s,...)
```

其他开关:`--agents`(列入子代理会话)、`--all`(列入非 skill 会话,含人工分析会话)、`--full`(显示完整路径)、`--root <dir>`(默认 `/app/log/claude/projects`)。脚本只读转录文件,无副作用。

## 运行识别(四种签名,按优先级)

| 优先级 | 签名 | 位置 | 示例 |
|---|---|---|---|
| 1 | sdk-cli 流水线命令 | 首个用户消息 `<command-name>` 标签 | `aiflow-build-storyboard` |
| 2 | B 架构 driver 提示词 | 首个用户/enqueue 正文匹配 worker 签名 | `aiflow-verify-frame`(含 findings 修复) |
| 3 | Skill 工具调用 | `tool_use` name=Skill 的 `input.skill` | 交互式 / 无 driver 时的回退 |
| 4 | 子代理会话 | `<parent>/subagents/agent-*.jsonl` + `.meta.json` | `agent:frame-html-worker │ ...` |

`<command-name>` 与 Skill 工具名归并显示(剥离 `Skill:` 前缀)。**driver 阶段身份优先于中途查阅用的 Skill 调用**,避免 verify-repair 会话因调用 `hyperframes-core` 被误标。

## 统计口径(与既往分析文档一致)

| 指标 | 定义 |
|---|---|
| `wall` | 会话首尾时间戳差。**子代理等待计入父会话 wall**,父会话行即该阶段总耗时 |
| `ttfb` | 入队(enqueue)/首个用户消息 → 首个 assistant 消息;负数(续写/无入队会话)显示 `-` |
| `rounds` | 含工具调用的 assistant 回合数(list/group 表口径,与 P3 "回合预算 ≤12" 同口径) |
| `tools` | tool_use 块总数;`ag` = Agent/Task/spawn 工具调用次数(子代理数) |
| `model` | `wall - 工具执行` = 思考 + 流式 + 每轮摊薄开销 |
| 工具执行 | 按 `tool_use_id` 配对 `tool_result` 时间戳,Σ(结果 − 调用) |
| 令牌 | Σ `message.usage`:in/out/cache_read/cache_write(output 含思考 token) |
| 回合墙钟 | 工具回合 → 下一个工具回合(最后一个取会话尾);detail 给 median/mean/p90/max |

## detail 报告内容

单次运行输出:会话路径、起止时间、总账(工具/模型侧占比)、ttfb、回合数(含工具/全部 assistant 消息)、回合墙钟分布、令牌、思考 block 字符量、**耗时最长的 8 个回合**、**工具直方图**(名称 / 次数 / 总执行时间)。`--timeline` 追加逐工具时间线(含与上一事件的间隔)。

## 与已知参照的校验(2026-11-17 全量转录)

| 参照 | 脚本输出 | 一致 |
|---|---|---|
| identify-intent 7 次 17.5–48.2s,合计 213.7s | 7 行逐个命中,合计精确 213.7s | ✓ |
| build-storyboard 5.7m(341.8s),T5 尖峰 222.9s,output 88,662 | 5.7m / 223s / 88,662 | ✓ |
| verify-frame 41.5m(25% 工具)、4.7m、17.0m(24% 工具、72 回合) | 41.5m / 4.7m / 17.0m 全中 | ✓ |
| frame-visual 13.6m、frame-html 63.3m(14 子代理) | 13.6m / 1.05h / 14 | ✓ |

## 口径差异备忘

旧手写表格的"回合"列与本脚本不完全可比(如 storyboard 旧表记 8:手工按逻辑阶段 T1–T8 编号;脚本 rounds=17、assistant 消息 31)。脚本统一按"含工具的 assistant 回合"计,与 P3 预算同口径,**以后以脚本为准**。41.5m 文档的"107 回合/124 工具调用"中 107 为当时手工口径,本脚本对同一会话输出 rounds=124。

## 变更记录

- 2026-11-21:driver 提示词签名优先于中途 Skill 工具调用,修复 verify-repair 被误标为 `hyperframes-core`。
- 2026-11-17 初版:三签名识别、list/group/detail/timeline/csv、--since 过滤;对全量转录校验(0 工具/回合墙钟 median/p90、负 ttfb 显示、分组中位数取偶均值、BrokenPipe 容错)。