# `6b0941c6196ef01f` 全流程回归复盘与 P0/P1/P2 优化落地记录（2026-11-27）

> **关联任务**：`6b0941c6196ef01f`（2026-11-27 08:39:35–08:59:29 UTC；**9 帧 / `59.1s` 成片 / `7.92 MB` MP4**；项目目录 `/app/videos/2026-11-27/userclm17919893695531772/6b0941c6196ef01f`）  
> **对照基线**：`6b08f96ccf960c67`（同日 03:30，9 帧 / `61.1s`，详见 `docs/2026-11-27-6b08f96c-pipeline-review-and-optimization-plan.md`）  
> **核验依据**：`log/htmlvideo_worker.log`、27 份 Claude Code 会话 JSONL、28 份 `/app/log/api-bodies/*.request.json` 与 23 份 `.response.json`、`STORYBOARD.md`、全量 9 份帧 HTML、11 张全分辨率快照及 54 份历史帧回放（`2026-11-26` + `2026-11-27`）。

---

## 1. 08:09 上轮改造（P0-1 ~ P1-2）线上回归核验

| 上轮改造项 | 线上实证结果 | 关键证据 |
|---|---|---|
| **P0-1 回退 `VISUAL_LEDGER=False` + Shared Stage 护栏** | **显著生效** | `visual` Phase B 墙钟由 `227.0s` 降至 **`157.3s`**（`-31%`），9 个 worker 累计耗时从 `1490.1s` 降至 **`654.6s`**（`-56%`），output tokens 从 `69,277` 降至 **`32,469`**（`-53%`）。 |
| **P0-2 `flow_element_xpercent_center`** | **生效，0 误报** | F1 零左上偏移裁切，全片 9 帧无误报。 |
| **P0-3 切幕 `grid-area: 1 / 1` + 印章防消音** | **生效** | F6（`.gauges-layer` / `.merged-layer`）与 F9（`.token-row` / `.decide-wrap`）均规范共处 `grid-area: 1 / 1`，零留白空洞与印章压字。 |
| **P1-1 竞速子进程 `selfcheck` 详情日志** | **生效** | `htmlvideo_worker.log` 直接捕获 `01-the-guessing-trap/hedge` 拒收原因 `abs_zero_xpercent_center`。 |
| **P1-2 静默 `superpowers` 插件** | **彻底根治** | `settings.json` 与 `installed_plugins.json` 保持清空，零插件扫描日志。 |

---

## 2. 耗时与 Token / 成本总账（对比 03:30 基线 `6b08f96c`）

| 阶段 | 本次 (`6b0941c6`, 9帧/59.1s) | 基线 (`6b08f96c`, 9帧/61.1s) | 变化 | 本次显性 Input / CacheRead / Output / Thinking (占比) |
|---|---:|---:|---:|---|
| `init` | 3.6s | 3.9s | -0.3s | - |
| `storyboard` | **74.6s** | 114.1s | **-39.5s (-35%)** | `5,608` / `0` / `3,708` / `2,004` (54.0%) |
| `audio`（与 Phase A 并行） | 102.9s | 103.9s | -1.0s | 完全掩盖在 `visual` Phase A 内（非关键路径） |
| `visual` Phase A | 150.4s | 143.6s | +6.8s | `4,682` / `0` / `7,729` / `7,075` (91.5%) |
| `visual` Phase B (9 帧) | **163.4s** | 227.0s | **-63.6s (-28%)** | `18,059` / `27,392` / `32,469` / `28,853` (88.9%) |
| `packets` + `captions` | 0.2s | 0.2s | 0s | - |
| `frame_html` | **654.1s** | 672.3s | -18.2s (-2.7%) | 10 落盘 `64,037` / `52,224` / `139,214` / `101,238` (72.7%) |
| `verify` | 101.0s | 77.1s | +23.9s | 1 次修 lint（`04-the-value-test.html`）：`11,400` / `7,168` / `2,115` / `1,964` (92.9%) |
| `render` + `sync` | 42.8s + 2.9s | 44.1s + 2.8s | -1.3s | 16 workers `39.5s`，QUIC `2.72 MB/s` |
| **总计（显性 23 响应）** | **`1191.0s` (19.9m)** | `1283.3s` (21.4m) | **-92.3s (-7.2%)** | **`in=103,786`, `cr=86,784`, `out=185,235`, `thk=141,134` (76.2%)** |

> **隐性算力空烧**：5 个被中途终止的 `frame_html` 进程（`03#1` 杀于 `209s`、`05#1` 杀于 `271s`、`08#1` 杀于 `333s`、`02#1-hedge` 与 `09#1-hedge` 各 `180s` 超时）共空烧 **`1,173s`**（按 `~55 tok/s` 估算约 `60k–65k` 未计入 `claude skill done` 的流式 output tokens），加上 `01#1-hedge`（`148s`, `7,752` out）被 selfcheck 拒收，`frame_html` 约 35%–40% 的 output 算力被浪费。

---

## 3. 成片质量与长尾根因深度复核

### 3.1 成片视觉缺陷（源码级根因）
1. **F2（`02-three-lenses.html`）外框永久断成半截（高优，内置运行时缺陷）**：`<svg id="f2-frame" viewBox="0 0 680 79">` 渲染为 `68cqw × 15cqh`（缩放 ~`1.92×`）且 `<path>` 声明 `vector-effect="non-scaling-stroke"`。`__hf.draw()` 用 `el.getTotalLength()` 获取 viewBox 坐标系周长 `1491`，但 Chromium 对 `non-scaling-stroke` 按屏幕 CSS 像素周长（`~2908px`）解析 `stroke-dasharray`，导致动画结束后底边与左边永久缺失（`frame-01-at-8.354s.png`）。
2. **F4（`04-the-value-test.html`）打字光标反置左侧（中优）**：`#pe-verdict-caret` DOM 节点位于 `#pe-verdict-text` 之前，打字期间呈现 `| Cheaper o`。
3. **F9（`09-decide-with-confidence.html`）按钮文字偏左跳变（中优）**：`.decide-pill` 为 Flex 行，右侧 `.check-badge`（`3.4cqw` + `gap 1.2cqw`）在 `t=1.6s–3.4s` 仅设 `autoAlpha: 0, scale: 0` 仍占 Flex 宽度，导致 `"DECIDE"` 在红胶囊内明显偏左 1.8 秒。
4. **F3（`03-step-one-value.html`）对冲帧打勾抢跑（低优）**：`.vc-check` 在 `t=0.5s` 绘制而 `#vc-label` 在 `t=2.71s` 才显现。

### 3.2 耗时与成本长尾根因（思维切片实证）
1. **`frame_html` F1（551.8s, `26.6k` thk）与 F9（484.7s, `22.4k` thk）规则死锁**：
   - `frame.md` 禁止 *"uppercase Bodoni"*，而 `STORYBOARD.md` 指定全大写短词 `"BUY?" / "SELL?" / "GUESS?"` 与 `"DECIDE"`，模型耗费 30%–40% 思考反复摇摆；
   - 反复审计非动画背景装饰（`.fpill`）的静态 CSS `transform: rotate(...)` 与骨架自带 `.hf-stage-lockup` 样式是否误触 `gsap_css_transform_conflict` / `nowrap` 门禁。
2. **`visual` F8（157.3s, `7.5k` thk）上下游契约冲突**：`storyboard` 先于 `visual_design` 运行，在 `STORYBOARD.md` L236 写下 *"The three tokens dim one by one; the 'Value' token visibly breaks apart"*，与 `P0-1` Shared Stage 护栏正面冲突。
3. **`verify`（101.0s）因冗余初态触发整轮模型修复**：`04-the-value-test.html` 在 CSS 写了 `.pe-fill { transform: scaleX(0); }` 且 JS 已有 `tl.set(".pe-fill", { scaleX: 0 }, 0)`，触发 `gsap_css_transform_conflict`（`tl.set` 不在 `from/fromTo` 豁免名单内），消耗 `41.4s` 模型修复 + `47s` 重扫。
4. **`DEFAULT_MAX_HEDGES = 3` 导致对冲排队**：`05`/`08`/`09` 排队至 `209s`/`271s`/`295s` 才启动对冲，使 `05`/`08` 主进程白跑约 `240s`。

---

## 4. 本轮 P0 + P1 + P2 优化落地清单（保持 `effort` 与 `max_output` 上限不变）

> **策略边界**：严格保持全链路各阶段 `effort` 档位与 `max_output` 上限（`storyboard=high/16000`、`visual_design=medium/24000`、`visual=medium/20000`、`frame_html=medium/48000`、`hedge=low/23999`、`verify=medium/32000`）不变，避免千帆 `adaptive` thinking 截断失忆与 `low` 档良率退化。

| 编号 | 状态 | 目标文件 | 精确修改内容 |
|---|---|---|---|
| **P0-1** | **已落地** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`<br>（同步 `.claude/skills/...`）<br>`test/test_frame_packets.mjs` | **修复 `__hf.draw()` + `vector-effect="non-scaling-stroke"` 断框 Bug**：检测 `el.getAttribute("vector-effect") === "non-scaling-stroke"` 时，按 `el.getScreenCTM()` 提取最大轴向缩放比 `Math.max(1, hypot(a,b), hypot(c,d))` 放大 `len`，根治缩放 SVG 描边半截缺失。 |
| **P0-2** | **已落地** | `scripts/frame-selfcheck.mjs`<br>`scripts/known-failures.mjs`<br>`test/test_frame_packets.mjs` | **打字光标顺序门禁 + `gsap_css_transform_conflict` 双重闭环**：<br>1. `frame-selfcheck.mjs` 新增 `type_caret_before_text`（拦截 `__hf.type` 光标节点位于文本节点之前）与 `gsap_css_transform_conflict`（拦截简单选择器 CSS `scale/translate` 与 GSAP `set`/`to` 冲突，放行非动画 `rotate` 装饰）；<br>2. `known-failures.mjs` 新增 prepare 阶段确定性 `autofix` 条目 `gsap-css-scale-transform-conflict`：当 CSS 仅声明纯 `scale/scaleX/scaleY(...)`（严格排除 `translate/rotate/skew/matrix`）且 JS 已有 `tl/gsap.set|to|fromTo` 接管同属性时自动剥离冗余 CSS `transform`，零模型调用直接过 `verify`（省 `~88s`）。 |
| **P1-1** | **已落地** | `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`<br>（同步 `.claude/skills/...`） | **消除 `frame_html` 三处规则死锁与排版隐患**（保持 `<8000 B` 预算，`7936 B`）：<br>1. 明确全大写 1–2 词短标统一用 `Space Grotesk` 粗体大写（解除 `Bodoni` 大小写死锁）；<br>2. 明确非动画背景装饰的静态 CSS `transform: rotate(…)` 与未改动 `.hf-stage-lockup` 骨架样式免审计；<br>3. 明确 `__hf.type` 光标须置于文本之后，且后续 Scene 才显现的同行图标/对勾须绝对定位或共槽，禁止 `autoAlpha: 0` 占位导致居中标题偏左。 |
| **P1-2** | **已落地** | `skills/aiflow-build-storyboard/references/contract-card.md`<br>（同步 `.claude/skills/...`） | **对齐 `storyboard` 与 Shared Stage 上下游契约**：在 `## Continuity` 明确步骤/清单类弧线一旦前面步骤完成，后续对比/反转/CTA 帧保持顶部进度条完整，新对比与收尾在中央卡片内完成，严禁写出“把已完成步骤逐个变暗/拆碎”的 `scene` 描述。 |
| **P2-1** | **已落地** | `html_utility/frame_html.py`<br>`test/test_visual_driver.py` | **解除对冲排队饥饿**：将 `DEFAULT_MAX_HEDGES` 从 `3` 提升至 `6`（仍支持 `HTMLVIDEO_FRAME_HTML_MAX_HEDGES` 覆盖），避免 9 帧任务多帧同时越过 `150s` 线时后序帧排队至 `209–295s` 才发车。 |

---

## 5. 单元测试与 54 份历史帧回放验证结果（2026-11-27 实测全绿）

1. **54 份历史帧离线回放（`2026-11-26` + `2026-11-27` 全量）**：
   - `frame-selfcheck.mjs` 扫描全部 54 帧：`type_caret_before_text` / `gsap_css_transform_conflict` **精准命中 `1/54`**（仅命中 `6b0941c6196ef01f/04-the-value-test.html` 的 `#pe-verdict-caret` 顺序缺陷），其余 53 帧 **0 误报**。
2. **`known-failures.mjs` `gsap-css-scale-transform-conflict` 确定性回放**：
   - 还原 `6b0941c6196ef01f/04-the-value-test.html` 修复前状态（`.pe-fill { transform-origin: 0% 50%; transform: scaleX(0); }`）：精准剥离 `transform: scaleX(0);` 且保留 `transform-origin`，二次扫描 0 命中（幂等）；
   - 反向用例（`transform: translateX(-50%) scaleX(0)`）：严格保留不误删。
3. **全量自动化测试套件**：
   - `node /app/test/test_frame_packets.mjs`：**PASS**（含 `__hf.draw` CTM 缩放、`type_caret_before_text`、`gsap_css_transform_conflict`、静态 `rotate` 放行、`frame-worker-card.md` `7858 B` 预算及双副本字节一致、`frame-packets-core.mjs` 双副本字节一致）；
   - `node /app/test/test_verify_check_parse.mjs`：**PASS**；
   - `python3 -m unittest /app/test/test_visual_driver.py /app/test/test_claude_cli.py /app/test/test_verify.py /app/test/test_storyboard_contract.py`：**230/230 全绿**（含 `contract-card.md` 双副本一致、`DEFAULT_MAX_HEDGES == 6`、`SLIM_PRIMARY_ENV` 已彻底移除、`onUpdate` 边界放行）。

---

## 6. 全流程 Prompt / Skill 自相矛盾排查与 `SLIM_PRIMARY` 源头收敛（2026-11-27 追加落地）

对全部 5 个大模型阶段（`intent` / `storyboard` / `visual A+B` / `frame_html` / `verify`）的实际拼接 Prompt 做交叉审计后，进一步清除了 **5 项真实自相矛盾**，并彻底移除 `SLIM_PRIMARY` / `SLIM_FALLBACK` 历史开关，从 `frame-packets` 源头切断 `frame_html` 对 Blueprint 散文的耦合：

| 编号 | 状态 | 目标文件 | 根因与精确修改内容 |
|---|---|---|---|
| **C-1** | **已落地** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills/...`）<br>`html_utility/frame_html.py`<br>`skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（同步 `.claude/skills/...`） | **从源头移除 `## Selected blueprint` 与 `SLIM_PRIMARY` / `SLIM_FALLBACK` 开关，根治幽灵冲突**：<br>1. `frame-packets-core.mjs` `buildDispatchDocs()` 不再向 `.dispatch.md` 拼入 `## Selected blueprint`，且调用 `detectConflicts()` 时传入 `blueprintText: ""`（仅在内部传给 `frameRules()` 辅助规则推断），彻底消灭因未内联 Blueprint 引发的 `negative-list-orbit/spin (blueprint ...)` 幽灵冲突；<br>2. `frame_html.py` 彻底删除 `SLIM_PRIMARY_ENV` / `SLIM_FALLBACK_ENV` 及 `_worker_prompt()` 中所有 `selected blueprint body` / `## Selected blueprint is authoritative` 悬空描述；<br>3. `frame-worker-card.md` 移除 `inlined blueprint` / `keep blueprint signature move`（体积进一步压至 `7858 B`）。 |
| **C-2** | **已落地** | `html_utility/frame_html.py` | **修正 `_hedge_suffix()` 居中护栏与 `gsap_css_transform_conflict` 正面冲突**：区分动静场景——被 GSAP 动画的 `left: 50%` 元素必须用 `xPercent: -50`（禁混写 CSS `transform`），纯静态无 GSAP 动画的元素才用 CSS `transform: translateX(-50%)`（优先父容器 Flex/Grid 居中）。 |
| **C-3** | **已落地** | `html_utility/frame_html.py`<br>`test/test_visual_driver.py` | **收窄 `onUpdate` 禁令边界（消除 F1 `chromatic-glitch` 思考死锁）**：禁止 `gsap.ticker` / `requestAnimationFrame` 及全帧单一 `onUpdate` 大状态机（瞬间换词用 `tl.set`，打字用 `__hf.type`），明确放行内联 Recipe 中的有界代理 `onUpdate`（如 `chromatic-glitch` 振幅包络、`sine-wave-loop` 相位）。 |
| **C-4** | **已落地** | `html_utility/frame_html.py`<br>`skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（同步 `.claude/skills/...`） | **消除零工具 `text_out=True` 模式下的 `Read` 诱导语**：`_worker_prompt()` 在 `text_out=True` 时自动将内联 snippet 中的 `Read a full RULES_DIR/<id>.md...` 与 `// … truncated — Read the full recipe for the rest` 替换为无工具自足说明（磁盘 `.dispatch.md` 与 `TEXT_OUT=0` 回退模式保持兼容）。 |
| **C-5** | **已落地** | `scripts/known-failures.mjs`<br>`html_utility/verify.py` | **消除 `verify` 误导性 `x: -1000` hint 与内联 `Read` 措辞冲突**：<br>1. `known-failures.mjs` 在 `other-lint-errors` 之前新增 `id: "gsap-css-transform-conflict"`（`mode: "model"`），指导仅删 CSS `transform` 并迁移真实初态、忽略 CLI hint 中的 `x: -1000` 模板数字；<br>2. `verify.py` `_FIX_HARD_CONSTRAINTS` 补充 `(skip Read when the file is already inlined below)`。 |

