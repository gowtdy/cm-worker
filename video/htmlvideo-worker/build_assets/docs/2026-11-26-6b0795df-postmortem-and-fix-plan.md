# `6b0795df06afcc27` 全流程复盘与 `frame-visual` / `frame-html` / `verify` 修复方案（2026-11-26）

> **关联任务**：`6b0795df06afcc27`（2026-11-26 02:13Z–02:51Z，项目目录 `/app/videos/2026-11-26/userclm17919893695531772/6b0795df06afcc27`）  
> **对照基线**：`6b06ff057bb54345`（详见 `docs/2026-11-26-frame-visual-html-truncation-shared-stage-optimization.md`）  
> **依据来源**：`log/htmlvideo_worker.log`、`/app/log/api-bodies/*.json`、Claude Code 会话 JSONL、`npx hyperframes check . --json` 原始输出。

---

## 1. 总体结论

上一轮 P0/P1/P2 优化上线后的首次完整回归中：

- **符合预期的部分**：
  - `frame-visual` 与 `frame-html` 实现 **0 次满额截断（`truncations=0`）**，所有 worker 单轮（`num_turns=1`）一次性完成。
  - `frame-html` 8/8 帧首轮 `selfcheck=PASS`，总耗时从上次 `28m+` 卡死压至 **`779.2s`（13.0m）**；主进程与 4 次对冲 **100% 稳定命中 KV 前缀缓存（`cache_read=7168`）**；瘦身对冲 4 战 2 胜（`01-two-buttons#1-hedge` 仅用 `24.7s / 64 thinking tokens` 击败主进程）。
  - `__hf` 微运行时生效（`02/06/07` 使用 `__hf.type`，`03/04/06/07` 使用 `__hf.draw`）；`verify` 扫描中 `lint / runtime / contrast / motion` 全部 0 报错。
- **不符合预期的部分（导致任务中断）**：
  - 全流程在 **`verify` Round 2 失败中断**（触发 `TRUNCATED_RC (125)`：`output=32000, thinking=31999`，耗时 `603.3s`）。
  - `frame-html` 仍存在 **`02-one-framework-two-directions` 耗时 `773.0s`（12.9m）** 的单帧极长尾（吞掉整个阶段 99% 墙钟）。
  - 根因集中在 **P1 共享舞台（`center-scorecard`）引发的跨阶段冲突与布局缺陷** 以及 **`verify` Profile 自身的 `16k` 截断失忆漏洞**。

---

## 2. 全流程耗时与 Token 实测数据

| 阶段 | 墙钟耗时 | 调用/轮次 | Input | Cache Read | Output | Thinking (占比) | 结果 |
|---|---:|---:|---:|---:|---:|---:|---|
| `init` | 3.6s | - | - | - | - | - | 成功 |
| `storyboard` | 56.5s | 1 call / 1t | 5,751 | 0 | 2,787 | 1,368 (49.1%) | 成功 |
| `tts`（并行） | 89.6s | - | - | - | - | - | 成功 |
| `frame-visual` Phase A (`visual_design`) | 280.5s | 1 call / 1t | 4,795 | 0 | 13,667 | 12,543 (91.8%) | 成功（散文与 spec 冲突） |
| `frame-visual` Phase B (`visual` × 8) | 194.1s (最慢 F5) | 8 calls / 均 1t | 22,372 | 23,296 | 37,883 | 34,414 (90.8%) | 成功（阶段总计 480.3s） |
| `frame-html`（8 帧 + 4 对冲） | 779.2s (最慢 F2 773.0s) | 12 attempts / 均 1t | 45,471 | 57,344 | 165,241 | 138,523 (83.8%) | 8/8 `selfcheck=PASS`；对冲 2 胜 1 超时 1 取消 |
| `verify` Round 1 | 284.8s (+26s scan) | 1 call / 3t | 23,927 | 7,168 | 14,915 | 14,522 (97.4%) | `fixed 2`，但错误未消且 `firstSeen` 提前 |
| `verify` Round 2 | 603.3s (+24s scan) | 1 call / 2t | 5,829 | 10,240 | 32,000 | 31,999 (100.0%) | **2×16k 失忆空烧 → `TRUNCATED_RC (125)` 失败** |

---

## 3. 重点步骤根因分析

### 3.1 `frame-visual`（480.3s）

1. **Phase A 散文 `**Shared stage**` 与结构化 `## Shared stage spec` 布局自相矛盾**：
   - `html_utility/visual.py`（L508–L534）同时要求输出散文 `**Shared stage**`（允许模型自由写 `56cqw` / Flex/Grid 等描述）和末尾 `## Shared stage spec`（强限 3 套预设）。
   - 本次 Phase A 在 `STORYBOARD.md` 中：
     - 散文描述：**单个 `76cqw` 两列白卡**（左列 `BUY TESTS 01·02`、右列 `SELL TESTS 03·04`）；
     - `## Shared stage spec` 却选择：`layout: center-scorecard`（`frame-packets-core.mjs` 实际生成**上下两个 `60cqw` 卡片**：上方 `.hf-stage-lockup` 4 列横排槽位，下方独立 `#stage-card`）。
   - Phase B 仍沿用散文写了 `76cqw white #stage-card`、`two-column checklist card`，将矛盾原样传递给 `frame-html`。
2. **Slot 标签过长，缺少长度硬约束**：
   - `slots: 01 Explainable in one sentence | 02 Growing, not priced for perfection | 03 Original reason, broken | 04 Better home elsewhere` 中前两项达 30 / 37 字符，超出 `center-scorecard` 4 列单格宽度（~250px）。
3. **Prompt 指令小矛盾**：Phase A 开头写 `"Produce ONLY two sections"` 但正文列出 3 项，在 `d518631c` 思考开头引发额外自检；Phase A 思考仍达 `12,543` tokens（`280.5s`）。

### 3.2 `frame-html`（779.2s）

1. **散文 vs 骨架冲突直接引爆 F2 单帧 773s 长尾**：
   - `02-one-framework-two-directions#1` 耗时 `773.0s`，烧掉 `38,907` thinking tokens（91%），其 24k 对冲 `02#1-hedge` 也在 `360s` 超时（`rc=124`）。
   - `0ff46fec.jsonl` 思考原文直接证实原因：模型一上来就发现 `STORYBOARD.md` 的“单张 76cqw 双列卡片”与骨架的“`.hf-stage-lockup` + `#stage-card` 双卡结构”矛盾，花了几万 token 反复权衡如何调和。
   - 同样冲突也拖慢了 `07 (566.1s, 27.4k thk)`、`06 (422.9s, 19.9k thk)`、`05 (417.4s, 19.3k thk)`。
2. **`center-scorecard` 骨架 CSS 存在布局隐患**（`frame-packets-core.mjs` L1208–L1225）：
   - `.hf-stage-lockup` 宽 `60cqw`（内宽 ~1060px），`.hf-stage-slots` 硬写 `repeat(4, minmax(0, 1fr))`（每列 ~250px），且 `.hf-stage-label` 缺 `white-space: nowrap`；即便加了 `nowrap`，未裁切文本盒宽度（`320px / 388px`）仍大于列宽，直接埋雷给 `verify`。
3. **对冲槽位占用**：`02#1-hedge` 占坑 `360s` 直到超时，导致 `07` 等到 `562s` 才拿到对冲槽位（4 秒后主进程已完成）。

### 3.3 `verify`（944s，Round 2 截断失败）

`verify-all` 全量扫描仅报出 3 条 `content_overlap` error（全部归因到 `02-one-framework-two-directions.html`），但构成**无解死循环**：

1. **死循环根因 A — 跨帧共享舞台 `crossfade` 必然重叠 + 错误归因到 F2**：
   - `npx hyperframes check . --json` 原始数据：
     - `div.hf-stage-title overlaps div.hf-stage-title "THE INVESTOR'S CHECKLIST"`，`occurrences: 2`，`firstSeen: 26.569s`，`lastSeen: 41.538s`。
   - 对照 `index.html` 转场时间轴：
     - `26.569s` = **F4 → F5 `crossfade`（26.262–26.762s）中点**；
     - `41.538s` = **F7 → F8 `crossfade`（41.251–41.751s）中点**。
   - 因为相邻共享舞台帧的 `.hf-stage-lockup` 像素锁定在同一坐标，`crossfade` 期间两帧同时可见，同名 `.hf-stage-title` 必然 100% 重叠；而 `hyperframes check` 按选择器去重时，将 `sourceFile` 归因给了**首个包含该选择器的帧 `02-one-framework-two-directions.html`**（F2 在 `26.6s` 与 `41.5s` 并不在画面中）。
   - `verify` 仅让模型编辑 F2，**无论如何修改 F2 都永远无法消除 F4→F5 和 F7→F8 的跨帧重叠**。
2. **死循环根因 B — `center-scorecard` 4 列宽度不足 + Round 1 越修越糟**：
   - `span.hf-stage-label` `"01 Explainable in one sentence"`（`box(487,191,320x27)`，右边界 `807`）与 `"02 Growing, not priced for perfection"`（`box(755,191,388x27)`，左边界 `755`）在 4 列 ~250px 网格下产生横向文本盒重叠。
   - Round 1 给 F2 补了 `white-space: nowrap; min-width: 0;`，原本 F2 换行未触发横向重叠，补完后 F2 自身在 `t=8.9s` 也触发单行文本盒溢出，`firstSeen` 从 `26.6s` 提前至 `8.9s`（`occurrences: 74`）。
3. **截断机制漏洞 — `verify` 复发“千帆 16k 失忆 Turn-2 空烧”**：
   - `html_utility/claude_cli.py`（L109–L116）中 `verify` profile 仍为 `max_output="16000"`、`thinking="32000"`（输出上限小于思考预算），且因带有 `Read,Write,Edit` 工具，`trunc_kill_streak=2`。
   - Round 2（`c1d85e04`）Turn 1 烧满 `16,000` thinking tokens 零输出，千帆返回 `stop_reason: "end_turn"` + `display: "omitted"`；watcher 放行 `auto-resume 1/2`，Claude Code 丢弃全部 Turn 1 思考并在**同一 `16,000` 上限**下从头重算，Turn 2 再次烧满 `16,000`（合计 `31,999` thinking tokens / `603.3s`），最终被 `_log_parsed_result` 升级为 `TRUNCATED_RC (125)`。
   - `html_utility/verify.py`（L753–L758）收到 `TRUNCATED_RC` 且无文件变更时无提额重试，直接 `raise RuntimeError` 终止任务。

---

## 4. 修复方案

### P0：消除 `verify` 死循环与截断崩溃（必做）

| 序号 | 目标文件 | 修改内容 | 预期效果 |
|---|---|---|---|
| P0-1 | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills` 镜像） | 1. 在 `center-scorecard` 预设中，当 `slots.length >= 4` 时将 `.hf-stage-slots` 改为 **2×2 网格**（`grid-template-columns: repeat(2, minmax(0, 1fr))`），`.hf-stage-lockup` 宽放宽至 `68cqw`（`left: 16cqw; top: 8cqh`），`#stage-card` 调整至 `top: 40cqh`（底边仍 `<= 80cqh < 83cqh`）。<br>2. 在预生成的 `.hf-stage-title` 上直接添加 `data-layout-allow-overlap="true"`（单帧内唯一标题节点，不可能帧内自撞，彻底消除跨帧 `crossfade` 误报）。 | 每格宽度由 ~250px 增至 ~600px，长标签不再横向碰撞；彻底消除 `div.hf-stage-title` 跨帧误报。 |
| P0-2 | `scripts/lib/verify-check-parse.mjs` / `scripts/verify-report.mjs` | 对 `content_overlap` 增加跨帧共享舞台 `crossfade` 过滤/降级：当 `selector === container` 且命中 `.hf-stage-*`，且所有命中时间点均落在相邻帧 `crossfade` 0.5s 过渡区间内时，降级为 `info`（不阻塞门禁、不错误归因到首帧）。 | 即使未来有其他共享舞台预设或模板样式，也不再触发跨帧 `crossfade` 死循环。 |
| P0-3 | `html_utility/claude_cli.py`<br>`html_utility/verify.py` | 1. `verify` profile 的 `max_output` 从 `"16000"` 提升至 **`"32000"`**（与 `thinking="32000"` 对齐）。<br>2. `verify` 的 `trunc_kill_streak` 默认设为 **`1`**（在千帆 `omitted` 模式下避免 5 分钟失忆 Turn-2 空烧）。<br>3. `verify.py` 在 `_apply_mechanical_fix` 捕获 `TRUNCATED_RC (125)` 且无落盘时，自动提额至 `48000` 重试 1 次（仅限单次升级），失败再抛错。 | 消除 `verify` 16k 失忆空烧与单次截断直接炸任务的脆弱性。 |

### P1：消除 `frame-visual` → `frame-html` 布局冲突，砍掉 F2 773s 长尾（必做）

| 序号 | 目标文件 | 修改内容 | 预期效果 |
|---|---|---|---|
| P1-1 | `html_utility/visual.py` | 1. Phase A prompt 明确规定：**`## Shared stage spec` 是共享舞台几何布局的唯一权威（SSOT）**；散文 `**Shared stage**` 仅描述语义与状态演进，**禁止**发明与所选 `layout` 预设冲突的宽度/列数/单卡双卡结构。<br>2. 约束 `slots:` 每个标签 **≤ 20 字符（2–4 个短词）**。<br>3. 修正 `"Produce ONLY two sections"` → `"Produce ONLY three sections"`。 | 从源头消除 `STORYBOARD.md` 散文与骨架 DOM 冲突，降低 Phase A 与 `frame-html` 思考消耗。 |
| P1-2 | `html_utility/frame_html.py` | 在 `target_block` 的共享舞台指引中增加硬规则：*当 `STORYBOARD.md` 散文描述的卡片宽度/列数与预生成 `.hf-stage-lockup` / `#stage-card` 骨架不一致时，**骨架几何绝对优先**，严禁尝试重构或调和两者。* | 直接阻断 `frame-html` worker（尤其是 F2）在布局矛盾上消耗 30k+ thinking tokens。 |

### P2：对冲与长尾调度微调（建议）

| 序号 | 目标文件 | 修改内容 | 预期效果 |
|---|---|---|---|
| P2-1 | `html_utility/frame_html.py` | 将 `HEDGE_TIMEOUT` 从 `360s` 下调至 **`240s`**。 | 24k 瘦身对冲若 4 分钟未完成基本已卡死，提早释放并发对冲槽位给其他慢帧（如 `07`）。 |

---

## 5. 验证计划

1. **单测与镜像校验**：
   - `diff -ru skills/hyperframes-core .claude/skills/hyperframes-core` 零差异；
   - `python3 -m unittest discover -s /app/test -p 'test_*.py'` 全部通过（补充 `verify` 32k/streak=1/截断重试单测与 `visual.py` prompt 断言）；
   - `node /app/test/test_frame_packets.mjs` + `node --test /app/test/test_verify_check_parse.mjs` 全部通过（补充 `center-scorecard` 4 槽位 2×2 网格与 `data-layout-allow-overlap` 断言，以及 `crossfade` 共享舞台误报过滤单测）。
2. **线上复跑验收指标**：
   - `frame-visual`：0 截断、`STORYBOARD.md` 散文与 `Shared stage spec` 无几何冲突、`slots` 单项 ≤ 20 字符；
   - `frame-html`：0 截断、单帧关键路径 `<= 420s`（消除 F2 773s 长尾）；
   - `verify`：跨帧 `crossfade` 不再误报 `hf-stage-*` 重叠，机械门禁 0 轮或 1 轮通过，无 `TRUNCATED_RC (125)` 中断，全流程产出最终视频。
