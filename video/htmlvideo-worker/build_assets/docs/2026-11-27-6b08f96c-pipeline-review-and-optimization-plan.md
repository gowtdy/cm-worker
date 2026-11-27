# `6b08f96ccf960c67` 全流程回归复盘与下一阶段优化落地记录（2026-11-27）

> **关联任务**：`6b08f96ccf960c67`（意图任务 `6b08f93e617a8674`；2026-11-27 03:30:06–03:52:19 UTC；**9 帧 / `61.1s` 成片 / `7.4 MB` MP4**；项目目录 `/app/videos/2026-11-27/userclm17919893695531772/6b08f96ccf960c67`）  
> **对照基线**：`6b082a5be89ed160`（9 帧 / `52.9s`）、`6b083ee7d7c36f88`（6 帧 / `34.6s`，详见 `docs/2026-11-26-6b083ee7-pipeline-review-and-optimization-plan.md`）  
> **核验依据**：`log/htmlvideo_worker.log`（L1–L1173）、`log/claude-debug.log*`、30 份 `/app/log/api-bodies/*.request.json` / 23 份 `.response.json`、`STORYBOARD.md`、全量 9 份帧 HTML、`snapshots/contact-sheet-1.jpg` / `contact-sheet-2.jpg` 及 45 份历史帧 HTML（2026-11-26 ~ 27）。

---

## 1. 上轮改造（Stage 1 + Stage 2 + Stage 3）线上回归逐项核验

| 上轮改造项 | 线上实证结果 | 关键证据 |
|---|---|---|
| **S1-1 `frame-packets-core.mjs` 单一真源 + `__hf.type` 自动收光标 + `dim` 继承 `data-accent`** | **完全符合预期** | 全片 8 个共享舞台帧 `dim` 槽位均带 `data-accent="lime/lavender/sky"`；全片 9 帧注入 `if (caret) tl.set(caret, { autoAlpha: 0 }, at + d + 0.15);`，5 个打字帧**零光标常驻**。 |
| **S1-2 `frame-selfcheck.mjs` 4 条静态拦截** | **超预期，线上真实拦错 2 次** | `01-stop-guessing#1-hedge`（39 thk）与 `03-value-check#1-hedge`（281 thk）均因漏写 `immediateRender: false` 被 `gsap_fromto_delayed_leak` 毫秒级拒收，转由 `medium` 主进程兜底。 |
| **S1-3 `verify` 防消音守卫 + `escaped_container` 提级** | **符合预期（0 次 LLM 修复）** | 第 1 轮仅报 F7 2 条 `designed_overlay`，经 `verify_overlap_autofix.py` 补标 `div.f7-stamp` 后第 2 轮直接 `GATE: PASS`（零模型调用）。*注：复核发现 F7 `div.f7-stamp` 盖住了不透明副标题 `.f7-sub`，本轮 P0-3 已收紧。* |
| **S1-4 `verify-pixels.py` `#stage-card` 内部像素探针** | **已生效** | 11 张抽帧全绿 `PASS`（0 空白卡、0 左壁坍缩）。 |
| **S1-5 消除 10s 取消死等 + 关 Auto-Memory + 清插件** | **核心项极佳，插件残留因 `settings.json` 未清** | ① 5 次进程取消全部 **`<1s` 完成**（此前硬卡 `10.3s`），零取消后跑完计费；② **Auto-Memory 彻底关闭**（`0/30` 请求含 `# Memory`，省 ~19.5k 输入 tokens）；③ `/app/log/claude/settings.json` 仍残留 `"superpowers@claude-plugins-official": true` 导致重启写回 `installed_plugins.json`（本轮 P1-2 已根治）；④ 容器环境变量仍显式设置 `HTMLVIDEO_CLAUDE_DEBUG_REQUEST=1` / `ANTHROPIC_LOG=debug`（代码默认已关闭，部署侧按需关闭即可）。 |
| **S2-1 `SLIM_PRIMARY=1` + 默认开 `VISUAL_LEDGER` + Phase A 统一 `(Adapt)`** | **效果分化：`VISUAL_LEDGER=True` 引发严重思考通胀** | **正向**：Phase A 从 `191.6s` 降至 **`143.6s`**（thk `11.9k → 6.7k`）；`SLIM_PRIMARY` 已裁除主进程 Blueprint，KV Cache 命中 `6,144–6,656`。<br>**负向反噬**：全量 5 次历史任务（含 9 帧 `6b082a5b`）均未开 `VISUAL_LEDGER`；本轮首次默认开启后，**Visual Phase B 耗时从 `104.1s` 暴涨至 `227.0s`（+118%，思考 `26.3k → 61.9k`）**，且盲写的 `x%/y%` 与 `cqw` 坐标直接与 `#stage-card` Flex 骨架及护栏打架。 |
| **S3-1 `tts ‖ visual Phase A` → `sync_duration` → `visual Phase B`** | **完全符合预期（0s 额外等待）** | `tts` 于 `103.9s` 完成（早于 Phase A `39.7s`），`pre-phase-b sync_duration` 仅耗时 **`0.07s`**，Phase B 100% 按真实 TTS 秒数编排。 |
| **S3-2 分镜时长预算 + 词数门禁** | **完全符合预期** | 同一句 13 词输入 1 轮直出 9 帧 / `152 words`（`2.49 wps`，0 重跑），成片 **`61.1s`**，相对目标 `60s` 误差仅 `+1.8%`。 |

---

## 2. 耗时与成本总账（对比 `6b082a5b` 9 帧基线 & `6b083ee7` 6 帧基线）

| 阶段 | 本次 (`6b08f96c`, 9帧/61.1s) | `6b082a5b` (9帧/52.9s) | `6b083ee7` (6帧/34.6s) | 本次 Output / Thinking (占比) | 关键观察 |
|---|---:|---:|---:|---|---|
| `init` | 3.9s | 3.7s | 3.5s | - | 正常 |
| `storyboard` | 114.1s | 52.5s | 25.0s | `5,534` / `3,891` (70.3%) | 预算配平 9 帧词数，1 轮直过 |
| `audio`（与 Phase A 并行） | 103.9s | 101.9s | 69.1s | - | 完全掩盖在 Phase A 内 |
| `visual` Phase A | **143.6s** | 179.6s | 191.6s | `7,437` / `6,718` (90.3%) | 较上轮提速 25% |
| `visual` Phase B | **227.0s** | 104.1s | 63.6s | **`69,277`** / **`61,936`** (89.4%) | **退化 +118%**：开启 `VISUAL_LEDGER` 所致 |
| `packets` + `captions` | 0.2s | 0.2s | 0.3s | - | 正常 |
| `frame_html` | **672.3s** | 596.6s | 323.8s | 11 落盘 `160,283` / `119,487`；F6 `608.8s` (`30.3k` thk)、F8 `666.4s` (`34.9k` thk) | 9/9 触发对冲，2 对冲 `180s` 超时；吞吐正常（`50–66 tok/s`） |
| `verify` | 77.1s | 50.2s | 72.6s | **0 次 LLM 调用** | 2 轮扫描 + 自动修复即过 |
| `render` + `sync` | 44.1s + 2.8s | 42.1s + 2.9s | 29.1s + 1.8s | - | 16 workers `40.9s`，QUIC `2.62 MB/s` |
| **总计（显性 23 响应）** | **`1283.3s` (21.4m)** | `1029.9s` (17.2m) | `710.4s` (11.8m) | `in=116,767`, `cr=79,872`, `out=242,836`, `thk=192,200` (79.1%) | 另有 7 个被取消/超时进程共空烧 `1,238s`（估 `~65k–70k` 隐性 output） |

---

## 3. 成片质量深度复核（视觉瑕疵与源码根因）

整体质感较 `6b083ee7` 显著提升（音画严丝合缝、零左壁坍缩、零光标残留、零提前显影粉框、零虚构数字），但仍检出 3 处真实视觉问题：

1. **F1（`01-stop-guessing.html`）`"GUESS"` 偏移裁切（高优）**：`.card` 为 `display: flex; align-items: center; justify-content: center; overflow: hidden`，`.wordmark` 为 `position: relative`（已被 flex 居中），但 JS L210 误加 `tl.set(".wordmark", { autoAlpha: 0, xPercent: -50, yPercent: -50 }, 0)`，将 `12cqw` 文字向左上偏移半个自身尺寸，直接撞出 `.card` 左上边界被裁。
2. **F5（`05-quality-check.html`）下半场卡片上半部 55% 留白空洞（中优）**：`#stage-card` 为 `flex-direction: column`，Scene 2 仅对 `#quality-hero` 设 `autoAlpha: 0`（`visibility: hidden` 仍占 Flex 高度），导致 3 颗芯片偏下、上方大片全白。
3. **F7（`07-when-to-sell.html`）尾段红章压住未淡出副标题（中优）**：`t=6.3s` 居中弹出的 `.f7-stamp` 盖在 `opacity: 1` 的 `.f7-sub`（`"Sell when any signal breaks."`）上，`verify` 第 1 轮已检出，但 `verify-check-parse.mjs` 因类名含 `stamp` 误判为可自动放行，直接贴了 `data-layout-allow-overlap="true"`。

---

## 4. 本轮 P0 + P1 优化落地清单（不改动任何模型 `effort` 与 `MAX_THINKING_TOKENS`）

> **策略边界**：保持全链路各阶段 `effort` 档位与 `MAX_THINKING_TOKENS` 上限（含 `storyboard=high/16000`、`frame_html=medium/48000`、`hedge=low/23999`）完全不变。

| 编号 | 状态 | 目标文件 | 精确修改内容 |
|---|---|---|---|
| **P0-1** | **已落地** | `html_utility/visual.py`<br>`html_utility/frame_html.py`<br>`test/test_visual_driver.py`<br>`test/test_frame_html_driver.py` | **回退 `VISUAL_LEDGER` 默认值 + 消除 Shared Stage 重复画槽冲突**：<br>1. `visual.py` 将 `HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认改回 `False`（保留 `=1` 可随时开启），确定性省回 Phase B `~110–125s` 及 `~35k` tokens；<br>2. `frame_html.py` 仅当 `dispatch_text` 实际含 `## Layout ledger` / `## Motion ledger` 时才注入抄表指令，消除与 Flex/Grid 护栏冲突；<br>3. `visual.py` `_build_worker_prompt()` 补强 Shared Stage 护栏：`.hf-stage-lockup` 已持有编号进度槽，`#stage-card` 内只放本帧单一 Hero 视觉，严禁重画第二套编号清单或将 `done` 槽位重置回 `dim`（根治 F8 类死锁思考）。 |
| **P0-2** | **已落地** | `scripts/frame-selfcheck.mjs`<br>`skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（同步 `.claude/skills`）<br>`test/test_frame_packets.mjs` | **精准拦截非绝对定位元素误用 `xPercent/yPercent: -50`（根治 F1 裁切，零误报）**：<br>1. `frame-selfcheck.mjs` 新增 `flow_element_xpercent_center`：按 `<template>` DOM 元素聚合其 `id` 与全部 `class` 的 CSS 声明，仅当目标节点声明为 `position: relative/static`（或聚合后无 `position: absolute/fixed` 且无 `left/top/inset`）却施加 `xPercent/yPercent: -50` 时判 `FAIL`；<br>2. `frame-worker-card.md` `GSAP idioms` 显式警示禁止对 flex/grid 子项或 `position: relative` 元素使用 `xPercent/yPercent: -50`，并在 `test_frame_packets.mjs` 增加双副本字节一致断言。 |
| **P0-3** | **已落地** | `scripts/lib/verify-check-parse.mjs`<br>`skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（同步 `.claude/skills`）<br>`test/test_verify_check_parse.mjs` | **卡片切幕叠槽规范 + 收紧印章覆盖正文自动放行（根治 F5 空洞 & F7 印章压字）**：<br>1. `frame-worker-card.md` 明确 `#stage-card` 内两幕交替元素须共处同一 `grid-area: 1 / 1` 或定位锚点（严禁 `autoAlpha: 0` 在 Flex 列占空），且居中印章/徽章落地时底层正文/副标题须同步 `autoAlpha: 0` 退场；<br>2. `verify-check-parse.mjs` 收紧 `triageContentOverlap`：当 `stamp`/`seal` 覆盖含 `TEXT_TOKENS` 且词数 `>= 3` 的多词文本块（如 `.f7-sub` `"Sell when any signal breaks."`）时，`autofix` 降级为 `false`（`mark_side: null`），交由 `verify` 处理退场。 |
| **P1-1** | **已落地** | `html_utility/frame_html.py` | **补齐 `frame_html` 竞速子进程 `selfcheck` 拦错详情日志**：`on_race_result()` 在竞速成员失败时不再仅打印 `failed rc=%s`，而是同步输出 `done_ok`、`selfcheck=PASS/FAIL` 及 `sc_out` 中的 `[FAIL]` 规则摘要，便于直接从 `htmlvideo_worker.log` 定位被拦截的对冲帧。 |
| **P1-2** | **已落地** | `/app/log/claude/settings.json`<br>/app/log/claude/plugins/installed_plugins.json | **彻底静默 `superpowers` 插件**：将 `settings.json` 中 `"superpowers@claude-plugins-official"` 置为 `false`，并清空 `installed_plugins.json` 为 `{"version": 2, "plugins": {}}`。 |

---

## 5. 单元测试与 45 份历史帧回放验证结果（2026-11-27 实测）

1. **45 份历史帧回放（`2026-11-26` + `2026-11-27` 全量）**：
   - 运行 `frame-selfcheck.mjs` 扫描 45 份帧 HTML：`flow_element_xpercent_center` **精准命中 `1/45`**（仅命中 `6b08f96ccf960c67/compositions/frames/01-stop-guessing.html` 的 `.wordmark`），其余 44 份合法帧（含 `<div class="seal" id="check-seal">` 类选择器定位写法）**0 误报**。
2. **单元测试套件**：
   - `node /app/test/test_frame_packets.mjs`：**PASS**（含 `flow_element_xpercent_center` 正反例、`frame-worker-card.md` 双副本字节一致）；
   - `node /app/test/test_verify_check_parse.mjs`：**PASS**（含 `stamp` 覆盖多词 `.f7-sub` 拒绝自动放行 + 短词保留自动放行）；
   - `python3 -m unittest /app/test/test_visual_driver.py /app/test/test_claude_cli.py /app/test/test_verify.py /app/test/test_storyboard_contract.py`：**全绿**；
   - `test_frame_html_driver.py` 中 `test_worker_prompt_transcribes_ledger_when_present` 已同步通过（注：该文件另有 2 个与上轮 P1-4 `keep_snippets=True` 相关的旧断言未触及）。
