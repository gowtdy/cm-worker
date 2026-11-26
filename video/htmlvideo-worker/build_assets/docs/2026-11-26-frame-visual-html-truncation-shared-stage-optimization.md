# `frame-visual` & `frame-html` 满额截断修复、共享舞台脚手架与微运行时优化（2026-11-26）

> **关联任务复盘**：`6b06ff057bb54345`（2026-11-25 15:30Z，对比基线 `6b06dfa0a2a2d105` 13:16Z）  
> **实施范围**：P0（控制面截断与并发对冲）+ P1（共享舞台脚手架与复杂度护栏）+ P2（`__hf` 微运行时与动画自检门禁）  
> **明确边界**：共享舞台预设严格限定 3 套（`top-stepper-center-card`、`left-rail-right-card`、`center-scorecard`）+ `none`；请求调试抓包 `HTMLVIDEO_CLAUDE_DEBUG_REQUEST` 默认开启（`=0` 可关闭）；不切换模型、不动 `music/sfx`。

---

## 1. 背景与根因（`6b06ff057bb54345` 卡顿复盘）

2026-11-25 15:30Z 重跑中，`frame-html` 耗时超过 28 分钟（`F5` 耗时 `1509s` 仍未完成），与预期优化方向相反。结合 `htmlvideo_worker.log`、Claude Code 会话 JSONL 与 `/app/log/api-bodies/*.json` 抓包，定位到 4 个核心根因：

1. **千帆 `omitted/unsigned` thinking + 下调 `max_output` 触发“失忆 Turn-2 空烧”**：
   - 网关运行 `thinking: {"type": "adaptive", "display": "omitted"}`，不返回 thinking 签名。将 `visual_design` 压至 `12000`、`frame_html` 压至 `28000` 后，Turn 1 触顶零可见输出；Claude Code 自动发起 Turn 2 时丢弃 100% Turn 1 思考并在**同一上限**下从头重算，累计空烧 `156,003` thinking tokens（`visual_design`、`F2`、`F4`、`F5`、`F5-hedge`）。
2. **千帆触顶返回 `stop_reason: "end_turn"` 绕过截断检测**：
   - `_TruncationWatcher._feed()` 仅对 `stop_reason == "max_tokens"` 即时计数；且 `TEXT_OUT_MAX_TURNS=2` 结束时返回 `rc=0, result=""`，绕过 `TRUNCATED_RC (125)`，导致 `F5#2` 未能提额至 `64000`。
3. **对冲触发门禁过严且击穿 KV 前缀缓存**：
   - `maybe_hedge()` 的 `len(live) != 1` 门禁将 `F5` 的 300s 对冲拖延到 `896s` 才启动；随后对冲携带完整 46KB `.dispatch.md` 且运行在临时目录 `.hyperframes/hedge/...`，导致 `<system-reminder>` 中 `Primary working directory` 变化，KV 前缀缓存命中从 `14,336` 暴跌至 `768` tokens，对冲自身也满额超时（`600s`）。
4. **纯文字共享舞台约束漂移 + 复杂蓝图误配 + 打字机截断**：
   - 仅靠散文描述 `**Shared stage.**`，并发 worker `F2/F3/F4/F6` 仍构建了互不兼容的 DOM/坐标（`F4` 出现 `307px × 86px` 重叠）；`panel-edit-live-sync` 等 UI 录屏蓝图被误配给概念/清单帧；`F1` 手写打字机数组缺少 `onComplete` 收口导致尾词 `" it"` 丢失。

---

## 2. 落地设计详情

### P0：控制面截断检测、单轮零工具契约与独立瘦身对冲

| 模块 | 改动点 | 生效逻辑 |
|---|---|---|
| `html_utility/claude_cli.py` | Profile 预算与轮次 | `visual_design`: `medium × 24000`, `max_turns=1`, `tools=""`<br>`visual`: `medium × 20000`, `max_turns=1`, `tools=""`, 禁 `Read`<br>`frame_html`: `medium × 48000`（`TEXT_OUT` 运行时由 driver 覆写 `max_turns=1`） |
| `html_utility/claude_cli.py` | 千帆 `end_turn` 满额截断识别 | `_TruncationWatcher._feed()` 在 `stop_reason == "end_turn"` 且 `output_tokens >= 0.98 * cap` 无可见块时立即计数；零工具调用（`tools == ""`）默认 `max_streak=1`；`_log_parsed_result()` 对 `rc=0`、空 `result` 且 `output_tokens >= 0.95 * cap` 强制升级返回 `TRUNCATED_RC (125)` |
| `html_utility/claude_cli.py`<br>`start_htmlvideo-task.sh` | Debug 请求日志默认开启 | `_apply_debug_request_env()` 改为默认启用（仅当 `HTMLVIDEO_CLAUDE_DEBUG_REQUEST` 显式为 `0/false/off/no` 时关闭）；启动脚本默认导出 `HTMLVIDEO_CLAUDE_DEBUG_REQUEST="${HTMLVIDEO_CLAUDE_DEBUG_REQUEST:-1}"` |
| `html_utility/frame_html.py` | 单轮 Text-Out + 第 3 次极简降级 | `TEXT_OUT_MAX_TURNS = 1`；第 1 次截断触发 `64000` 提额重试，若第 2 次仍 `TRUNCATED_RC` 则第 3 次自动切换为 `degraded` 瘦身极简模式 |
| `html_utility/frame_html.py` | 跨帧独立瘦身对冲 + 缓存保留 | 移除 `len(live) != 1` 门禁，`DEFAULT_HEDGE_AFTER = 210`、`DEFAULT_MAX_HEDGES = 2`、`HEDGE_MAX_OUTPUT = "24000"`、`HEDGE_TIMEOUT = 360`；新增 `_minimal_dispatch_text()` 保留 `# Dispatch:` 前完整共享前缀并剥离蓝图/长 recipe；`TEXT_OUT` 模式下对冲保持 `cwd=project_dir`（同时将产出写入隔离 `.hyperframes/hedge/...` 目录），保持前缀缓存命中 |

### P1：确定性共享舞台脚手架与复杂度护栏

1. **UI 录屏蓝图隔离**（`html_utility/visual.py`）：
   - 新增 `UI_ONLY_BLUEPRINTS = frozenset({"panel-edit-live-sync", "cursor-ui-demo"})`，在 Phase A 目录提示词中标注 `[UI-screencast only]`，禁止分配给概念/清单/观点帧。
2. **结构化 `## Shared stage spec` 契约**（`html_utility/visual.py`）：
   - Phase A 末尾输出 `## Shared stage spec`（`layout` 仅允许 `top-stepper-center-card`、`left-rail-right-card`、`center-scorecard`、`none`，外加 `title`、`slots` 与逐帧 `F<n>: [done:<accent>, active:<accent>, dim]`）。
   - `_split_design()` 与 `_splice_assignment()` 保持蓝图分配修复时无损保留 `## Shared stage spec`；Phase B 在 `[YOUR FRAME]` 尾部注入本帧锁定的共享舞台槽位状态，避免跨帧重置已完成的 `done` 槽位。
3. **确定性共享舞台脚手架生成**（`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` & `scripts/frame-packets.mjs`）：
   - 新增 `parseSharedStageSpec()` 与 `parseFrameStageTokens()`，在 `richSkeleton()` 中为启用共享舞台的帧预生成像素锁定的 `.hf-stage-lockup`（track 1）与无重叠 `#stage-card`（track 2）：
     - `left-rail-right-card`：左侧栏 `left: 5cqw; width: 25cqw`，主卡 `left: 34cqw; width: 60cqw`（`4cqw` 隔离带，底边 `<= 78cqh < 83cqh`）。
     - `top-stepper-center-card`：顶部步进条 `top: 5cqh; width: 80cqw`，主卡 `top: 19cqh; width: 64cqw`（底边 `<= 78cqh`）。
     - `center-scorecard`：居中汇总轨 `top: 11cqh; width: 60cqw`，主卡 `top: 38cqh; width: 60cqw`（底边 `<= 78cqh`）。
   - 预生成骨架节点均带 `data-skeleton="stage|card"`，确保未填充骨架仍被 `frame-selfcheck.mjs` 精确拦截为 `skeleton_unfilled`。
4. **复杂度与提示词消歧护栏**：
   - `frame-packets-core.mjs`：`MAX_RULES` 由 `5` 降至 `3`（支持 `HTMLVIDEO_FRAME_PACKETS_MAX_RULES` 覆盖），`narrowBlueprint` 默认 `minBytes` 由 `8000` 降至 `3500`。
   - `visual.py`：约束每帧 `- rules:` 最多 1–3 项、`≤9s` 帧控制在 2–3 个 Scene。
   - `frame_html.py`：在 `target_block` 增加 `Text-out override` 及共享舞台填充指引，消除规则卡中工具模式与纯文本模式的指令冲突。

### P2：`__hf` 微运行时、前缀预热对齐与动画门禁

1. **预注入 `__hf` 确定性微运行时**（`frame-packets-core.mjs`）：
   - 在 `richSkeleton` `<script>` 中以 `/* hf-runtime-start */ ... /* hf-runtime-end */` 包裹注入 `__hf.type(tl, textEl, caretEl, str, at, dur)`（基于 `proxy.n` + `onUpdate` + `onComplete` 强制落盘完整字符串，随机 seek 确定且零尾字截断）与 `__hf.draw(tl, target, at, dur, ease)`（自动计算 `getTotalLength()` 执行描边）。
2. **KV 前缀缓存预热对齐**：
   - `visual.py` 与 `frame_html.py` 的 `DEFAULT_PREFIX_WARMUP_SECONDS` 统一由 `1.8s` 提升至 `5.0s`。
3. **动画规则消歧与无限循环静态门禁**：
   - `frame-worker-card.md` 与 `frame-worker-core.md` 将禁令精确为“禁止 CSS `transition` 与无限循环 `repeat: -1`”（允许有界有限脉冲）；`cursor-click-ripple.md` 改为显式按压 + 回弹双补间。
   - `scripts/frame-selfcheck.mjs`：
     - 在 `skeleton_unfilled` 检查中剥离 `/* hf-runtime-start */ ... /* hf-runtime-end */` 并识别 `__hf.type` / `__hf.draw` 调用。
     - 新增 `gsap_infinite_repeat` 静态 FAIL 门禁，拦截内联脚本中的 `repeat: -1`（放行有限 `repeat: 1`）。

---

## 3. 新增 / 调整的环境变量与回滚开关

| 环境变量 | 默认值 | 说明 / 回滚方式 |
|---|---|---|
| `HTMLVIDEO_CLAUDE_DEBUG_REQUEST` | `1`（默认开启） | 设 `0` / `false` / `off` 可关闭 `ANTHROPIC_LOG=debug` 与 `api-bodies` 抓包 |
| `HTMLVIDEO_CLAUDE_VISUAL_DESIGN_MAX_OUTPUT_TOKENS` | `24000` | 覆盖 Phase A 输出上限 |
| `HTMLVIDEO_CLAUDE_VISUAL_MAX_OUTPUT_TOKENS` | `20000` | 覆盖 Phase B 输出上限 |
| `HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS` | `48000` | 覆盖 `frame_html` Attempt 1 上限（设 `64000` 一键回退） |
| `HTMLVIDEO_FRAME_HTML_HEDGE_AFTER` | `210` | 单帧无落盘触发独立对冲的等待秒数 |
| `HTMLVIDEO_FRAME_HTML_MAX_HEDGES` | `2` | 最大并发对冲路数 |
| `HTMLVIDEO_FRAME_HTML_SLIM_FALLBACK` | `1`（默认开启） | 对冲/降级时剥离蓝图与 recipe；设 `0` 回退完整 dispatch |
| `HTMLVIDEO_PREFIX_WARMUP_SECONDS` | `5.0` | `visual` / `frame_html` 首帧预热等待秒数；设 `0` 关闭 |
| `HTMLVIDEO_FRAME_PACKETS_MAX_RULES` | `3` | 单帧内联 rule recipes 上限 |

---

## 4. 修改文件清单

### 核心代码与脚本
- `html_utility/claude_cli.py`
- `html_utility/frame_html.py`
- `html_utility/visual.py`
- `start_htmlvideo-task.sh`
- `scripts/frame-packets.mjs`
- `scripts/frame-selfcheck.mjs`

### Skills 与 `.claude/skills` 镜像（已保持零 diff 同步）
- `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` ↔ `.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`
- `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md` ↔ `.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`
- `skills/hyperframes-core/references/frame-worker-core.md` ↔ `.claude/skills/hyperframes-core/references/frame-worker-core.md`
- `skills/hyperframes-animation/rules/cursor-click-ripple.md` ↔ `.claude/skills/hyperframes-animation/rules/cursor-click-ripple.md`

### 单元测试
- `test/test_claude_cli.py`
- `test/test_frame_html_driver.py`
- `test/test_visual_driver.py`
- `test/test_storyboard_contract.py`
- `test/test_frame_packets.mjs`

### 文档
- `docs/2026-11-26-frame-visual-html-truncation-shared-stage-optimization.md`（本文档）
- `docs/claude-cli-skill-profiles.md`（同步最新 Profile 表、截断返回码 `125` 与默认 Debug 开关说明）

---

## 5. 验证结果

- **Skill 镜像一致性**：`diff -ru` 校验 `hyperframes-core`、`aiflow-build-frame-html`、`hyperframes-animation` 三目录零差异。
- **Python 单元测试**：`python3 -m unittest discover -s /app/test -p 'test_*.py'` → **277 tests OK**。
- **Node 测试套件**：`node /app/test/test_frame_packets.mjs` + `node --test /app/test/test_verify_check_parse.mjs /app/test/test_known_failures.mjs` → **全部通过（0 fail）**。
