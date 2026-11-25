# verify-frame 截断误杀、跨文件串行踩踏与 Claude 交互瘦身优化

> 触发样本：2026-11-24 UTC 16:23–16:34，`task_id=6b05ba2b5d081581`  
> 项目目录：`/app/videos/2026-11-24/userclm17919893695531772/6b05ba2b5d081581`  
> 相关日志：`/app/log/htmlvideo_worker.log.2026-11-24.log`、`/app/log/claude/projects/-app-videos-2026-11-24-userclm17919893695531772-6b05ba2b5d081581/*.jsonl`、`/app/log/api-bodies/`

---

## 1. 事故背景与根因回顾

2026-11-24 16:23–16:34 运行 `6b05ba2b5d081581` 时，前置阶段（`init` → `storyboard` → `tts ∥ visual` → `caption ∥ frame_html` → `assemble`）全部成功，但在 `verify` 阶段以如下错误失败：

```text
Failed to verify frame: verify fix round 2 [-] truncated: the model burned its output budget on thinking without emitting an Edit
```

对 worker 日志、Claude Code 会话 JSONL 与 API 原始请求/响应体逐条核对后，定位出 **6 个相互咬合的根因**：

| 序号 | 问题 | 现象与日志证据 |
|---|---|---|
| 1 | **`_TruncationWatcher` 延迟结算导致误杀已成功会话** | Round 2 第 2 次调用（`16:32:08–16:33:50`）触发 `stop_reason="max_tokens"`（16,000 thinking tokens）；新版 Claude Code 自动附加恢复提示并发起第 3、4 次调用（`16:33:50–16:34:14`），成功发出 `Edit`（`margin-top: 2.2cqw → 4.4cqw`，落盘于 `16:34:12.495Z`）并回复 `fixed 1`（`end_turn`）。因新版不再向 JSONL 写 `user/isMeta` 记录，旧 watcher 直到读取恢复回合的新 `mid` 才结算 24 秒前的旧截断，将已修通项目的会话强杀（对磁盘终态实测 `verify-report.mjs` 与 `verify-pixels.py` 均 `EXIT=0`）。 |
| 2 | **`_split_scan_by_file` 跨文件泄露 + 串行踩踏** | Round 1 的 2 条 `gsap_exit_missing_hard_kill` 分片后，共享 `actionable summary` 未按文件过滤。Worker 1（`label=01`）8 秒内把 `01` 和 `07` 一并修完；串行启动的 Worker 2（`label=07`）读到已修复文件后陷入困惑，白耗 34.8s / 5,403 thinking tokens 并追加了冗余的 `visibility: "hidden"`。 |
| 3 | **`content_overlap` 措辞误导 + Triage 漏词 + 缺失几何信息** | `parseCheckJson` 把同级重叠渲染为 `div.word inside div.sub`，且 `TEXT_TOKENS` 缺 `word`/`sub` 导致落入 `ambiguous`，模型在 197 段（60,967 字符）思考中反复猜测 DOM 包含关系、手算字体 metrics 并反复摇摆 `allow-overlap` 与 `reposition`。 |
| 4 | **`gsap_exit_missing_hard_kill` 未自动修复且丢弃 lint `fixHint`** | `hyperframes lint` 已直接给出确定性 `tl.set(...)` `fixHint`，但 `verify-report.mjs` 截断丢弃了 `fixHint`、`known-failures.mjs` 无对应 autofix；lint 失败还触发 `checkPipeline.ts` 短路跳过浏览器检查，平白多出一整轮扫描 + 两次模型调用。 |
| 5 | **Claude Code ↔ 模型交互膨胀与指令对冲** | 请求暴露 15 个工具共 53.6 KB，其中 12 个无用内置工具（`Workflow` 22.9 KB、`ScheduleWakeup` 5.0 KB、`SendMessage` 4.9 KB、`Cron*` 4.9 KB、`Enter/ExitWorktree` 6.6 KB 等）占 **93.7%**；`SessionStart` hook 注入 `superpowers:using-superpowers`（3.7 KB），与 fix prompt 禁调 Skill 直接冲突。 |
| 6 | **扫描失败日志缺失** | `verify.py` 仅打印 `rc=1 (out N bytes)`，未将 error findings 与 `GATE:` 行打入 `htmlvideo_worker.log`。 |

---

## 2. 本次修改详情

### P0：消除截断误杀与跨文件踩踏

1. **`_TruncationWatcher` 状态机重构**（`html_utility/claude_cli.py`）
   - **即时检测终态截断**：当 `assistant` 消息出现 `stop_reason == "max_tokens"` 且仅含 thinking block 时立即计入截断，不再等待后续 `mid` 或 `user` 行。
   - **批次全消费防误杀**：`poll()` 先消费完当前 flush 出的全部 JSONL 行再做判断；只要出现任意可见块（`tool_use` / `text`）立即将连续截断计数 `_streak` 清零，绝不追溯强杀已恢复会话。
   - **放行 1 次自动恢复**：引入 `_DEFAULT_TRUNC_KILL_STREAK = 2`（可通过 `HTMLVIDEO_CLAUDE_TRUNC_KILL_STREAK` 调整；设为 `1` 即首次截断立刻强杀）。首次纯思考打满放行 Claude Code 自动恢复，连续第 2 次纯思考打满则立即终止进程组。
2. **截断落盘兜底**（`html_utility/verify.py`）
   - `_dispatch_fix()` 返回 `fix_rc`（`0` 或 `TRUNCATED_RC`），由 `_require_changed_and_rescan()` 统一检查 `compositions/**/*.html` digest。
   - 即使返回 `TRUNCATED_RC`，只要磁盘上已有有效改动（`after_digest != before_digest`），打 `WARNING` 并继续 rescan，避免丢弃已落盘修复；零改动时仍按原逻辑抛出截断异常。
3. **`_split_scan_by_file` 摘要按文件过滤 + 并发执行**（`html_utility/verify.py`）
   - 新增 `_filter_summary_for_file()` / `_filter_summary_line_for_file()`，按目标文件过滤 `actionable summary` 中的括号文件列表、子项与计数，剔除其他帧的引用。
   - 在单文件分片 prompt 头部增加显式边界约束：`Scope: fix ONLY <rel> (<abs>). Do NOT Read or Edit any other frame file.`
   - `_apply_model_fix()` 将多文件分片从串行 `for` 改为 `ThreadPoolExecutor(max_workers=min(len(fragments), 4))` 并发执行。

### P1：修正重叠诊断、完善 Triage 与 `gsap_exit_missing_hard_kill` 自动修复

4. **`content_overlap` 展示与 Triage 增强**（`scripts/lib/verify-check-parse.mjs`、`scripts/known-failures.mjs`、`skills/hyperframes-core/references/frame-worker-core.md`）
   - `parseCheckJson()` 与 `known-failures.mjs::where()` 对 `overlap` 类 finding 使用 **`overlaps`** 替代误导性的 `inside`；`overlapSides()` 同时兼容 `overlaps` 与旧文本格式 `inside`。
   - 为 `content_overlap` 附带 `[t=<start>-<end>s box(x,y,wxh)]` 紧凑几何与时间信息，并将 overlap 消息截断上限放宽至 `180`。
   - `TEXT_TOKENS` 新增 `word`、`sub`、`subline`、`kicker`、`eyebrow`、`deck`，使 `div.word` × `div.sub` 等主标/副标重叠直接归类为 `real_collision`。
   - 在 `skills/hyperframes-core/references/frame-worker-core.md` 自检清单增加叠排大字号标题+副标题优先用 `display: flex; flex-direction: column; gap` 的约束。
5. **`gsap_exit_missing_hard_kill` 确定性 Autofix 与 lint `fixHint` 透传**（`scripts/known-failures.mjs`、`scripts/verify-report.mjs`）
   - `verify-report.mjs` 保留 lint error 的 `selector` 与 `fixHint`，在错误行末尾输出 `[hint: …]`；`other-lint-errors` 提示模型按 `[hint: …]` 修复。
   - `known-failures.mjs` 新增 `gsap-exit-missing-hard-kill`（`mode: "autofix"`），在 `verify-all.mjs --phase prepare` 中执行：调用 `hyperframes lint --json` 识别缺失 hard kill 的非 clip 元素，解析 `fixHint` 中的精确 `tl.set(...)` 语句并幂等插入对应退出 tween 之后；clip 元素自动跳过留给模型处理。

### P2：交互瘦身与日志可观测性

6. **裁剪无用工具 Schema 与屏蔽冲突钩子**（`html_utility/claude_cli.py`）
   - `_BASE_DENY` 增补 12 个流水线无用内置工具：`Workflow`、`ScheduleWakeup`、`SendMessage`、`CronCreate`、`CronDelete`、`CronList`、`EnterWorktree`、`ExitWorktree`、`ReportFindings`、`ListAgents`、`TaskOutput`、`TaskStop`（削减 ~50 KB 工具定义）。
   - 对禁用 `Skill` 的自包含 profile（`storyboard`、`visual`、`frame_html`、`verify`）自动附加 `--disable-slash-commands --settings '{"disableAllHooks":true}'`，隔离 `superpowers:using-superpowers` 等 `SessionStart` 钩子；`visual_legacy` 保持不变。
   - `verify` profile 默认 `effort` 保持 **`medium`**（仍可通过 `HTMLVIDEO_CLAUDE_VERIFY_EFFORT=low` 做 A/B）。
7. **单文件小 HTML 内联与扫描失败日志**（`html_utility/verify.py`）
   - 当机械修复仅涉及单个 `compositions/frames/*.html` 且文件大小 `≤ 16 KB` 时，将带行号的当前文件内容直接内联至 prompt，引导模型首轮直接 `Edit`（省去一轮 `Read` 往返）；可通过 `HTMLVIDEO_VERIFY_INLINE_FILE=0` 关闭。
   - `_run_verify_phase()` 在 `rc != 0` 时将裁剪后的 `error findings` / `GATE:`（或 `VISUAL_GATE`）摘要（上限 2,000 字符）写入 `htmlvideo_worker.log`。

---

## 3. 运维开关与回滚

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HTMLVIDEO_CLAUDE_TRUNC_KILL_STREAK` | `2` | 连续纯思考打满输出上限多少次后强杀；设为 `1` 恢复首次截断即杀 |
| `HTMLVIDEO_CLAUDE_VERIFY_EFFORT` | `medium` | `verify` 阶段 effort 档位（如 A/B 试测 `low`） |
| `HTMLVIDEO_VERIFY_INLINE_FILE` | `1`（开启） | 设为 `0`/`false`/`off`/`no` 关闭单文件 HTML 内联 |
| `HTMLVIDEO_VERIFY_FIX_FULL_SCAN` | `0`（关闭） | 设为 `1` 关闭分片与单文件内联、透传完整扫描输出 |

---

## 4. 验证记录

1. **Python 单测**：`python3 -m unittest discover -s /app/test -p "test_*.py"` — **244/244 通过**（其中 `test_claude_cli.py` 在 `effort` 回调 `medium` 后复跑 **49/49 通过**）。
2. **Node 单测**：`node --test /app/test/test_verify_check_parse.mjs /app/test/test_known_failures.mjs /app/test/test_frame_packets.mjs` — **14/14 通过**。
3. **真实项目端到端复现**：
   - 将 `6b05ba2b5d081581` 克隆到临时目录并回退 Round 1 的两处 `tl.set` hard kill，运行 `verify-all.mjs --phase prepare` 自动插入 2 处 `tl.set`，复跑 `npx hyperframes lint . --json` 返回 `ok=true, errors=0`（零模型调用消除 Round 1）。
   - 对原项目目录执行只读 `verify-report.mjs`：`GATE: PASS ✓`。

---

## 5. 修改文件列表

| 文件 | 类别 | 主要改动 |
|---|---|---|
| `html_utility/claude_cli.py` | 核心实现 | `_TruncationWatcher` 即时判定+批次防误杀+连续截断阈值；`_BASE_DENY` 增补 12 个无用工具；自包含 profile 附加 `--disable-slash-commands` 与 `--settings '{"disableAllHooks":true}'` |
| `html_utility/verify.py` | 核心实现 | `_filter_summary_for_file` 过滤跨文件摘要 + 单文件 `Scope` 声明；多文件 worker 并行化；`TRUNCATED_RC` 落盘兜底；单文件 `≤ 16KB` 内联；失败扫描摘要日志 |
| `scripts/lib/verify-check-parse.mjs` | 核心实现 | `content_overlap` 使用 `overlaps` 替代 `inside` 并附带时间/box 几何信息；`overlapSides` 兼容双格式；`TEXT_TOKENS` 补齐 `word/sub/subline/kicker/eyebrow/deck` |
| `scripts/verify-report.mjs` | 核心实现 | 保留并输出 lint error 的 `selector` 与 `[hint: …]` |
| `scripts/known-failures.mjs` | 核心实现 | 新增 `gsap-exit-missing-hard-kill` 确定性 `autofix`；`where()` 对 overlap 使用 `overlaps`；`other-lint-errors` 引导使用 `[hint: …]` |
| `skills/hyperframes-core/references/frame-worker-core.md` | 规范文档 | 补充叠排大字号标题+副标题使用 `flex-direction: column; gap` 的自检规范 |
| `test/test_claude_cli.py` | 单元测试 | 新增无用工具/hook 屏蔽测试，以及 `max_tokens` 自动恢复、连续截断即杀、整块 flush 不误杀等 watcher 用例 |
| `test/test_verify.py` | 单元测试 | 新增分片摘要过滤、单文件内联及开关、截断落盘兜底、非零扫描日志等用例 |
| `test/test_verify_check_parse.mjs` | 单元测试 | 新增 `overlaps` 文案、时间/box 几何、`div.word` × `div.sub` 归类 `real_collision` 等断言 |
| `test/test_known_failures.mjs` | 单元测试 | 新增 `gsap-exit-missing-hard-kill` 非 clip 自动插入、幂等性与 clip 跳过用例 |
| `docs/2026-11-25-verify-truncation-and-interaction-hardening.md` | 设计/变更文档 | 本文档（事故根因、P0/P1/P2 改造说明、回滚开关、验证记录与文件列表） |
