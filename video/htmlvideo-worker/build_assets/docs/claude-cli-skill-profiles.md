# Claude CLI Skill Profile 设计（提速 / 降本）

> 适用范围：`htmlvideo-worker` 非交互 `claude --print` 调用  
> 主实现：`html_utility/claude_cli.py`  
> 入口：`intent` / `storyboard` / `visual` / `frame_html` / `verify`，以及 `scripts/run_aiflow_build_skills.py`  
> CLI 依据版本：本地核实 2.1.316；容器镜像建议固定并记录实际版本

## 1. 背景与目标

流水线五个 skill 原先共用同一组参数，并继承环境里的 `CLAUDE_CODE_EFFORT_LEVEL=max`，思考走 adaptive、无独立预算。**根因形态**（思考占墙钟大头）仍成立；下列墙钟数字需分清「事故基线」与「skill 侧优化后的最新闭环」——本 profile 调参之前，docs 里**只有 verify 有 11-17 的新闭环**：

| 阶段 | 会话日期 | 墙钟 / 现象 | 数据状态 |
|---|---|---|---|
| storyboard | 11-11（`build-storyboard-5.7m-analysis.md`） | 5.7m；单轮思考 ~223s，输出 94% 为思考 token | 事故基线。11-15/16 已落地契约卡内联 + 单响应双写，报告写明**尚未真跑 A/B**，无更新墙钟 |
| visual | 11-12（`build-frame-visual-13m-analysis.md`） | 13.6m；单块思考 ~10.4m | 事故基线。11-15/16 已落地一次 Write / gate 等；11-16 新跑只记了 gate 误报浪费 ~70s，**无完整阶段新墙钟** |
| frame-html | 11-12（`build-frame-html-63m-analysis.md` + `build-frame-html-deadzone-deepdive.md`） | 63.3m；max+adaptive+32k 无声截断 8–11m/回合 | 事故基线。11-14 已落地 `CLAUDE_CODE_SUBAGENT_EFFORT=high` 等，**docs 无后续全量墙钟闭环** |
| verify | **11-17**（`verify-frame-17m-analysis.md`） | **17.0m**（相对 11-12 的 41.5m 已腰斩）；其中深思考尖峰仍 ~3.2m | **最新闭环**（P3/P4 后）。报告明确：thinking cap 仍是待做项，预期再压到 12–13m |
| identify-intent | （无独立分析文档） | 仅在 verify/storyboard 报告中被引用为同族 63%–86% adaptive thinking | 旁证，非独立实测 |

多数步骤是「按契约填表」或「跑本地脚本 / playbook 修复」，不需要全局 max。目标：

1. **按 skill 分档**设置 effort 与思考预算，可用环境变量覆盖，并可一键回滚。
2. **防跑飞**：轮数上限、墙钟超时（含进程组清理）、禁用流水线无用工具。
3. **可观测**：每次调用打用量日志，支撑 A/B。
4. **不改** prompt、`SKILL.md`、流水线编排。

## 2. 生效机制（CLI 二进制行为）

| 机制 | 正确做法 | 错误 / 无效做法 |
|---|---|---|
| effort | 子进程 env 写 `CLAUDE_CODE_EFFORT_LEVEL` | 只加 `--effort`（会被 env 覆盖） |
| 思考预算 | 子进程 env 写 `MAX_THINKING_TOKENS` | `CLAUDE_CODE_MAX_THINKING_TOKENS`（CLI 不读） |
| thinking 语义 | `>0` → `{type:"enabled", budgetTokens:N}`；`=0` → `{type:"disabled"}` | 改动前为 adaptive，长度不受控 |
| effort + thinking=0 | 可能触发 CLI `effort_requires_thinking` 400 | intent 默认走 32000 档，不用 0 |

注意：

- 项目 `.claude/settings.json` 的 `env` 会在会话启动时 `Object.assign(process.env, …)`，可能覆盖 launcher 注入的值。镜像内 settings **不要**再放 `CLAUDE_CODE_EFFORT_LEVEL` / `MAX_THINKING_TOKENS`。
- 千帆 / deepseek 是否严格按 `budgetTokens` 截断需出站抓包确认；若网关忽略 budget，effort 仍是主杠杆。

## 3. 架构与调用链

```mermaid
flowchart TD
  intent[intent.py] --> runClaude[claude_cli.run_claude]
  storyboard[storyboard.py] --> runClaude
  visual[visual.py] --> runClaude
  frameHtml[frame_html.py] --> runClaude
  verify[verify.py] --> runClaude
  script[run_aiflow_build_skills.py] --> runClaude
  runClaude -->|skill required| profilePath["_run_profile: json + caps"]
  profilePath --> claudeBin[claude --print]
```

`skill` 必填；`None` 或未知名抛 `ValueError`。`HTMLVIDEO_CLAUDE_PROFILE` 已移除（设置也不生效）。

## 4. Profile 表与取值理由

> 2026-11-26 同步自 `html_utility/claude_cli.py` 最新 `_PROFILES`（详见 `docs/2026-11-26-frame-visual-html-truncation-shared-stage-optimization.md`）。

| skill | effort | thinking | max_output | max_turns | timeout(s) | tools / deny | 理由 |
|---|---|---|---|---|---|---|---|
| intent | medium | 12000 | 12000 | 4 | 1800 | `tools=""`；禁 Write/Edit/Bash/Read/Glob/Grep/Skill/Agent/Task | 仅输出 ~160 token JSON；12000 留 >50x 余量并抑止长思考 |
| storyboard | high | 16000 | 16000 | 8 | 1800 | `tools="Write"`；禁 Bash/Read/Glob/Grep/Edit/Skill/Agent/Task | 仅交付 STORYBOARD.md（SCRIPT.md 由 Python 派生），默认 text-out 驱动 |
| visual_design | medium | 24000 | 24000 | 1 | 1800 | `tools=""`；禁 Bash/Glob/Grep/Write/Edit/Read/Skill/Agent/Task | Phase A 全局设计契约 + `## Shared stage spec`；单轮零工具防千帆失忆重跑 |
| visual | medium | 20000 | 20000 | 1 | 1800 | `tools=""`；禁 Bash/Glob/Grep/Write/Edit/Read/Skill/Agent/Task | Phase B 逐帧镜头设计；输入全内联，单轮纯文本输出，由 Python 合并 |
| visual_legacy | high | 64000 | 32000 | 100 | 1800 | 仅禁 Agent/Task | `HTMLVIDEO_FRAME_VISUAL_DRIVER=0` 单会话回退路径 |
| frame_html | medium | 48000 | 48000 | 30（Text-Out 覆写为 1） | 1500 | `tools="Read,Write,Edit"`（Text-Out 覆写 `""`）；禁 Bash/Glob/Grep/Skill/Agent/Task | 每帧独立进程；48000 保障首轮完成率，截断返回 125 后自动提额至 64000 |
| repair | high | 40000 | 16000 | 40 | 600 | 禁 Agent/Task | 仅 assemble/gate 失败的一次性修复会话 |
| verify | medium | 32000 | 16000 | 40 | 1800 | `tools="Read,Write,Edit"`；禁 Bash/Glob/Grep/Skill/Agent/Task | 修色/加 data-layout 的 fix 角色；40 轮 fail-fast；压探盘与 Todo 空转 |

`frame_html` 另由调用方注入 `CLAUDE_CODE_SUBAGENT_EFFORT=high`（`frame_html.py` 与脚本 `--skill html` 一致；B 架构下仅 legacy 回退路径有意义，见 §9.7）。

**frame_html 成功门禁（driver，非模型）**：`rc==0` ∧ 结果含 `done OK <frame_id>` ∧ `frame-selfcheck` PASS ∧ 帧文件存在。骨架自带 `skeleton-v1` 会通过 selfcheck，因此 **不能** 用「文件已存在 / selfcheck PASS」单独短路——必须等模型 `done OK`。

基准禁用工具：`WebFetch`、`WebSearch`、`NotebookEdit`、`TodoWrite`、`TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet`；六个 profile 再禁 `Agent`、`Task`（B 架构下无 stage 派生子代理；工具名按 CLI 2.1.316；版本漂移时未知名通常为空操作）。

## 5. 命令与 env 组装

Profile 开启时命令形态：

```text
claude --print --dangerously-skip-permissions --permission-mode bypassPermissions
  --output-format json
  --max-turns <N>
  --exclude-dynamic-system-prompt-sections
  --disallowedTools <deny...>
  [--model <M>]
  --
  <prompt>
```

`--` 防止 prompt 被 `--disallowedTools` 变长参数吞掉。

```mermaid
flowchart LR
  osEnv[os.environ + extra_env] --> profileEnv[profile effort / thinking 注入]
  profileEnv --> extraEnv[extra_env 再覆盖 effort / thinking]
  extraEnv --> clamp[clamp MAX_THINKING_TOKENS，对标注入前输出 cap]
  clamp --> capInject[profile max_output 注入 CLAUDE_CODE_MAX_OUTPUT_TOKENS]
  capInject --> extraCap[extra_env 输出 cap 再应用（最后赢家）]
  extraCap --> child[claude child process]
```

Clamp 规则（`_clamp_thinking` 在 profile `max_output` 注入**之前**执行，对的是调用方继承或 `extra_env` 给的 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`，缺省按 128000 计）：

- `0`：关闭思考，保留。
- 非法值：回退 profile 默认；负数按 `0`。
- 值 < 该输出 cap 且 < 128000：原样保留。
- 值 ≥ 该输出 cap：cap > 127000 时压到 127000，否则压到 `cap - 1`。
- profile `max_output` 注入在 clamp 之后，`extra_env` 里的输出 cap 最后再应用（最后赢家）；注入后 thinking 可能仍 ≥ 子进程最终输出 cap——本后端 adaptive 剥 budget_tokens，此项 inert。

超时 / 进度：`Popen(start_new_session=True, stdout/stderr=PIPE)` + `_wait_claude_proc`（轮询 + 心跳，**不用** `communicate()`——否则 adaptive thinking 长回合会在 worker 日志里静默数分钟）。超时对进程组 `SIGTERM` → 宽限 → `SIGKILL`，返回 **124**。该等待路径对全部 skill（intent / storyboard / visual / frame_html / repair / verify）共用。

## 6. 运维接口

| 变量 | 作用 |
|---|---|
| `HTMLVIDEO_CLAUDE_<SKILL>_EFFORT` | 覆盖 effort；白名单 `low\|medium\|high\|xhigh\|max` |
| `HTMLVIDEO_CLAUDE_<SKILL>_THINKING_TOKENS` | 覆盖思考预算 |
| `HTMLVIDEO_CLAUDE_<SKILL>_MAX_OUTPUT_TOKENS` | 覆盖输出上限（max_output）；未设置用 profile 默认，设空则不注入（沿用父进程 cap） |
| `HTMLVIDEO_CLAUDE_<SKILL>_MAX_TURNS` | 覆盖轮数；≤0 回退默认 |
| `HTMLVIDEO_CLAUDE_<SKILL>_TIMEOUT` | 覆盖超时秒；≤0 回退默认 |
| `HTMLVIDEO_CLAUDE_<SKILL>_MODEL` | 设置则追加 `--model`；默认不设 |
| `HTMLVIDEO_CLAUDE_HEARTBEAT_SECONDS` | 全局：等待 Claude 时心跳间隔（默认 15）；`0` 关闭 |
| `HTMLVIDEO_CLAUDE_DEBUG_REQUEST` | 全局：完整请求/响应抓包开关（**默认 `1` 开启**，注入 `ANTHROPIC_LOG=debug` + `BUN_OPTIONS=--preload=...`）；设 `0`/`false`/`off`/`no` 关闭 |
| `HTMLVIDEO_CLAUDE_TRUNC_KILL_STREAK` | 全局：连续仅思考满额截断容忍次数（零工具调用默认 `1`，多轮工具调用默认 `2`） |

`<SKILL>`：`INTENT` / `STORYBOARD` / `VISUAL_DESIGN` / `VISUAL` / `FRAME_HTML` / `VERIFY` / `REPAIR`。

全局 `CLAUDE_CODE_EFFORT_LEVEL`（见 `conf/env_htmlvideo_example`）在 `run_claude` 子进程中总被各 skill profile 覆盖，仅对手工直接运行 `claude` 生效。

非法 effort / 非正 turns / timeout：打 warning 并回退 profile 默认，不致命失败。

## 7. 返回码与日志

| 情况 | 返回 |
|---|---|
| CLI 正常退出码 | 透传 |
| CLI rc=0 且 JSON `is_error=true` | **1** |
| 墙钟超时 | **124** |
| 思考满额截断（watcher 查杀，或 `rc=0` 空结果且 `output_tokens ≥ 0.95 × cap`） | **125**（`TRUNCATED_RC`） |
| 对冲竞速取消 | **130**（`CANCELLED_RC`） |
| JSON 解析失败 | 透传 CLI rc，日志打 stdout 尾 2000 字符 |
| `subtype=error_max_turns` | warning；rc 仍按上表 |

日志：

- start：`claude skill start label=… cwd=… skill=… effort=… thinking=… max_turns=… model=… timeout=…`（不打 prompt 正文）
- invoke：`cmd=[…, '--', '<prompt>']`（prompt 正文替换为占位符）+ 子进程 env
- running：等待中每 N 秒 `claude skill running label=… skill=… elapsed=…s`（防长思考静默）
- progress：stderr 匹配 tool/API/`unrecognized_model` 等行时即时 `claude skill progress …`（flush 到 worker 日志）
- done：`… subtype=… is_error=… num_turns=… duration_ms=… input=… output=… cache_read=… cache_create=…`
- result：截断 2000 字符（替代原先 text 模式刷到 stdout 的可见摘要）

观测：B 架构下 frame-html / visual worker 是 `run_claude` 直调（无 `<command-name>`）。`stat/skill-timing.py` 用首条用户消息的提示词签名补标（如 `aiflow-build-frame-html:01-…`），避免 list 表 skill 列全是 `-`。另报 `first_write_s`（t0→首个 Write|Edit）与 `first_write_tool_turn`，用于观测 frame-html「1–2 回合落盘」软纪律是否被遵守（尚未硬约束 max_turns）。

## 8. 验收与 A/B

**出站抓包**（`/app/log/api-bodies/*.request.json` 或等价捕获器）：

1. `output_config.effort`：intent/storyboard/visual/frame_html/repair=high；verify=medium。
2. `thinking`：enabled + `budget_tokens`（各 profile 32000–64000 档）；确认非 adaptive。
3. 工具列表无 WebFetch/WebSearch/Agent/Task。

**耗时预期**（相对 **profile 上线前** 基线；skill 侧优化已计入的用最新闭环，其余先用事故基线作对照，质量门禁不降）：

- verify（最新闭环）：17.0m → ≤13m（吃掉 ~3.2m 思考尖峰）
- storyboard（事故基线 5.7m；契约卡后尚无新墙钟）：对照 ≤2.5m，以用量日志 `num_turns`/`output` 闭环为准
- frame-html：不再出现 `output≈64000` + `end_turn` 且无工具调用的空烧回合（128k 回退时按 ≈128000 核对）
- visual / intent：对照事故旁证或 ≤1m（intent），以新跑用量日志补齐，勿把 13.6m 当「当前常态」

**质量门槛**：各阶段确定性 gate 一次通过率不降；frame-html 重派帧数不增；`verify-report` 终态通过。回退只调对应 skill 的 env，不改代码。

**单测**：`python3 -m unittest test.test_claude_cli test.test_frame_html_driver -v`；`node test/test_frame_packets.mjs`。

## 9. 已知限制

1. 千帆是否尊重 `budgetTokens` 以抓包为准；忽略时只靠 effort。
2. `--disallowedTools` 名依赖 CLI 版本；未知名通常无害。
3. thinking clamp 对「输出上限 &gt; 127000」仍可能压到 127000（有意保守）。
4. profile 模式 stdout 被 JSON 解析占用，外部若依赖 text stdout 需改看 worker 日志里的 result 行。
5. `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 为降低遥测/更新检查的附加项，与 profile 正交。
6. `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`：去掉内置 git/PR 工作流说明与 git status 快照。`run_claude` 会注入到子进程；本流水线 skills 不依赖 git。视频项目 cwd 勿放大型 `CLAUDE.md` / 多余 MCP（工具 schema 会进 system）。
7. worker 会话不再经子代理（B 架构）；CLAUDE_CODE_SUBAGENT_EFFORT 仅 legacy 回退路径有意义。
8. profile 开启时，ambient env 里父进程设置的 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` 会被每个 profile 的显式 `max_output` 覆盖（与 profile effort 覆盖 ambient effort 一致）；如需定制输出上限，走 `HTMLVIDEO_CLAUDE_<SKILL>_*` 覆盖变量或 `extra_env`。

## 10. 回滚

已无全量 env 开关。单项回退走 `HTMLVIDEO_CLAUDE_<SKILL>_*`（§6）；整体回退需 revert 代码。
