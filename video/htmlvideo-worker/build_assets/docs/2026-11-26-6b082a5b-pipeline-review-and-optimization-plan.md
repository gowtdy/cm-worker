# `6b082a5be89ed160` 全流程回归复盘与下一阶段完整优化方案（2026-11-26 修订终版）

> **关联任务**：`6b082a5be89ed160`（2026-11-26 12:47:23–13:04:36 UTC，即 20:47–21:04 CST；9 帧 / `52.9s` 成片 / `7.77 MB` MP4；项目目录 `/app/videos/2026-11-26/userclm17919893695531772/6b082a5be89ed160`）  
> **对照基线**：`6b07fd3a95da96bf`（7 帧 / `57.6s`，详见 `docs/2026-11-26-6b07fd3a-pipeline-review-and-optimization-plan.md`）  
> **核验依据**：`log/htmlvideo_worker.log`（L2773–L3767）、`log/claude-debug.log`、27 个 Claude Code 会话 JSONL、27 组 `/app/log/api-bodies/*.request.json` / 20 组 `.response.json`、`STORYBOARD.md`、9 份帧 HTML、`snapshots/contact-sheet-1.jpg` & `contact-sheet-2.jpg` 成片抽帧、`verify-report.mjs` / `npx hyperframes check . --json` 实跑输出，以及 `html_utility/frame_html.py` / `html_utility/visual.py` / `scripts/frame-selfcheck.mjs` / `scripts/verify-report.mjs` / `scripts/known-failures.mjs` / `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` 源码交叉审查。

---

## 1. 总体结论

上一轮针对 `6b07fd3a` 的优化（`data-accent` 去内联色与禁收尾淡出、`effort="low"` 对冲 + `MAX_HEDGES=3` + `HEDGE_TIMEOUT=180s`、禁状态机/SVG 噪点、压缩 Phase A、清理 `capsule` 漂浮英文词）重新构建上线后的首次端到端回归（**9 帧 / `52.9s` 成片**，较上次帧数 +2）：

- **超预期达标项（稳定性、总耗时与 `visual` 阶段全面提速）**：
  - **零网关错误 + 零截断 + 端到端一次出片**：`claude-debug.log` 中 HTTP 4xx/5xx 与重试全部为 `0`；20/20 已完成响应均为 `stop_reason=end_turn`（`truncations=0`）；渲染 `42.1s`、QUIC 上传 `2.91s`。
  - **端到端总耗时再降 9.3 分钟（-35%）**：在帧数增加 2 帧的前提下，总墙钟从 `1585.7s`（26.4m）降至 **`1029.9s`（17.2m）**，逼近 `12–16m` 目标区间。
  - **`visual` 阶段直接腰斩（-50%）**：Phase A（`visual_design`）从 `437.2s`（`20,618` thk）直降至 **`179.6s`（`8,722` thk，-59%）**，优于 `200–300s` 预期；Phase B 9 帧并行仅 `104.1s`（**7/8 非暖机帧命中 KV Cache** `3,072–3,584` tokens），阶段总计从 `565.3s` 降至 **`283.7s`**。
  - **`effort="low"` 网关兼容性验证通过**：7/7 对冲请求正常以 `output_config.effort="low"` 返回 HTTP 200，对冲胜率从 `1/5`（20%）跃升至 **`6/7`（85.7%）**；除 F6 外其余 **8 帧在 `318s`（5.3m）内全部完工**。
  - **历史成片顽疾根除**：全片零 `VISION`/`FUTURE`/`FLOW`/`NEXT` 无关英文胶囊词，收尾帧（F6/F9）饱满无空白，零 `— figure —` 字面占位符。
- **未达预期 / 新暴露的问题**：
  1. **单帧极长尾仍在（F6 `596.4s`）**：`06-check-3-the-exit-plan` 主进程耗时 **`590.7s`（`27,486` thk）**，且其 `low` 对冲排队至 `+229s` 启动后在 **`180s` 超时**（墙钟 `190s`），单帧将整个流水线拖慢近 **4.6 分钟**（未达 `critical_path_s <= 360s` 验收线）。
  2. **隐性算力空烧翻倍（`819s` → `1,701s` 墙钟）**：6 帧 `low` 对冲胜出后取消了 6 个已运行 `190–313s`（合计 `1,511s`）的 `medium` 主进程，加上 F6 对冲超时 `190s`。（*注：当前阶段对冲调度策略暂保持现状继续观察。*）
  3. **成片出现 4 处明显视觉瑕疵且 `verify` 漏放行**：F2 右侧文字溢出画布 `111.7px` 被裁切、F4 捏造 `+28%`/`26%` 假数据且双 `+28%` 同屏重影、F3 顶部槽位误变薄荷绿且触发 GSAP `dataState` 无效属性警告、F8 左侧出现悬空错位的红色竖线括号——**4 处全部落在 `low + slim_fallback` 对冲胜出帧**。

---

## 2. 全流程耗时与 Token / 成本实测总账（对比 `6b07fd3a`）

| 阶段 | 本次 (`6b082a5b`, 9 帧 / 52.9s) | 上次 (`6b07fd3a`, 7 帧 / 57.6s) | 调用 / 档位 | Input | Cache Read | Output | Thinking (占比) | 结果与评价 |
|---|---:|---:|---|---:|---:|---:|---:|---|
| `init` | 3.7s | 3.6s | - | - | - | - | - | 持平 |
| `storyboard` | **52.5s** | 68.0s | 1 (`high`) | 5,745 | 0 | 2,512 | 943 (37.5%) | 提速 23% |
| `audio`（与 visual 并行） | 101.9s | 85.7s | - | - | - | - | - | 被 `visual` 掩盖 |
| `visual` Phase A (`visual_design`) | **179.6s** | 437.2s | 1 (`medium`) | 5,051 | 0 | **9,352** | **8,722 (93.3%)** | **大降 59%（P1-1 生效）** |
| `visual` Phase B (`visual` × 9) | **104.1s** (最慢 F1 `103.2s`) | 128.1s (7帧) | 9 (`medium`) | 19,667 | **24,064** | 29,595 | 26,254 (88.7%) | **7/8 命中缓存，总计 `283.7s`** |
| `packets` + `captions` | 0.2s | 0.2s | - | - | - | - | - | 持平 |
| `frame_html`（9 主 + 7 对冲） | **596.6s** (最慢 F6 `590.7s`) | 826.7s (7帧) | 16 (9成 / 6杀 / 1超时) | 56,973\* | **46,080**\* | **79,110**\* | **49,938 (63.1%)**\* | 8 帧 `≤318s`，F6 拖垮关键路径 |
| `verify` | **50.2s** | 64.6s | **0** | 0 | 0 | 0 | 0 | 首扫 `GATE PASS`（`canvas_overflow` 仅作 `info` 漏放行） |
| `render` + `sync` | 42.1s + 2.9s | 56.3s + 3.9s | - | - | - | - | - | 渲染提速 25% |
| **端到端总计** | **1029.9s (17.2m)** | 1585.7s (26.4m) | 27 req / 20 resp | **87,436**\* | **70,144**\* | **120,569**\* | **85,857 (71.2%)**\* | 显性 Output -39%，隐性空烧需关注 |

### `frame_html` 逐帧交互明细（含隐藏算力成本与成片核查）

| 帧 / 胜出角色 | Session | 胜出耗时 | Input | Cache Read | Output | Thinking | 落败/超时方墙钟 | 抽帧与运行时核查 |
|---|---|---:|---:|---:|---:|---:|---|---|
| `01-the-question-you-keep-asking#1-hedge` (`low`) | `1b92c748` | **29.0s** | 8,974 | 0 | 1,806 | **19** | primary 杀于 `190s` | 正常（居中打字卡片） |
| `02-the-rule-before-you-buy#1-hedge` (`low`) | `edb8b008` | **36.4s** | 9,151 | 0 | 2,255 | **192** | primary 杀于 `197s` | **缺陷：`span.tail` `right=2031.7 > 1920` 溢出裁切** |
| `03-three-checks-before-you-buy#1-hedge` (`low`) | `7c7e94f5` | **78.9s** | 4,363 | 6,912 | 5,087 | **397** | primary 杀于 `240s` | **缺陷：顶部 Slot 1 误变薄荷绿 + GSAP `dataState` 无效属性警告** |
| `04-check-1-fundamentals#1-hedge` (`low`) | `ce14204a` | **73.9s** | 5,156 | 6,144 | 4,832 | **55** | primary 杀于 `260s` | **缺陷：捏造 `+28%`/`26%` + 旧 `#hd-num1` 未隐藏致双 `+28%` 重影** |
| `05-check-2-valuation#1-hedge` (`low`) | `5c0576da` | **115.0s** | 5,014 | 6,144 | 6,565 | **3,195** | primary 杀于 `313s` | 基本正常（卡片内字号偏克制） |
| `06-check-3-the-exit-plan#1` (`medium`) | `dcab5786` | **590.7s** | 8,535 | 6,912 | **31,535** | **27,486** | **hedge 超时 `190s`** (`a9770fed`) | 正常（排版干净、三槽位全珊瑚红） |
| `07-when-to-sell#1` (`medium`) | `41a05c87` | **171.6s** | 4,102 | 6,912 | 9,089 | **6,634** | 无对冲（一次过） | 正常（双卡对称、无漂浮词） |
| `08-the-line-that-ties-it-all#1-hedge` (`low`) | `6a40a1de` | **50.4s** | 3,005 | 6,144 | 3,094 | **214** | primary 杀于 `311s` | **缺陷：`90cqw` 宽全屏括号悬空套 `34cqw` 竖排胶囊，左侧露孤立红线** |
| `09-write-it-down#1` (`medium`) | `d69552c5` | **279.2s** | 4,673 | 6,912 | 14,847 | **11,746** | 无对冲（槽位排队未启） | 正常（居中 CTA 点击波纹） |

> **\*统计口径与隐性算力成本精算**：表中显性数据仅计入 **20 个正常落盘响应**。其余 **7 个被取消/超时的 `frame_html` 请求**共运行 **`1,701s` 墙钟**（6 个被杀主进程 `190+197+240+260+313+311 = 1,511s` + `06#1-hedge` `190s` 含回收）：
> - **隐性 Output**：本轮主进程实测流式速率稳定在 `53.2 tok/s`（如 F6 `31,535 / 590.7s`、F9 `14,847 / 279.2s`）；`06#1-hedge` JSONL 无 assistant 块（疑似卡在上游首包前），故隐性 Output 主要来自 6 个被杀主进程，约 **`75k–80k` thinking/output tokens**。
> - **隐性 Input**：7 个请求合计约 **`85k–90k` prompt tokens**，其中约一半（**`~42k` tokens**）命中低单价 **KV Cache Read**，未缓存净输入约 **`43k–48k` tokens**。

### `visual` Phase B 逐帧明细（9 帧并发与 KV Cache 命中率）

| 帧 | Session | 墙钟 | Input | Cache Read | Output | Thinking (占比) | 备注 |
|---|---|---:|---:|---:|---:|---:|---|
| `F1`（5s 暖机帧） | `479cba34` | 103.2s | 5,339 | 0 | 5,358 | 5,076 (94.7%) | 预热共享前缀缓存 |
| `F2` | `acc9e03c` | 56.4s | 1,587 | **3,584** | 2,841 | 2,459 (86.6%) | 命中缓存 |
| `F3` | `19a6f84a` | 86.7s | 1,863 | **3,584** | 4,386 | 3,947 (90.0%) | 命中缓存（违背 `[dim,dim,dim]` 提前点亮 Slot 1） |
| `F4` | `12a244c7` | 84.6s | 5,549 | 0 | 4,280 | 3,812 (89.1%) | 路由至未预热副本；**编造 `+28%`/`26%` 假数据** |
| `F5` | `b5106652` | 97.7s | 1,968 | **3,584** | 4,859 | 4,531 (93.2%) | 命中缓存 |
| `F6` | `1319965c` | 57.9s | 1,970 | **3,584** | 2,819 | 2,454 (87.1%) | 命中缓存（**写出 `01/02 dim` 矛盾分镜 + 四重动效**） |
| `F7` | `bc9a30bb` | 77.9s | 1,665 | **3,072** | 4,018 | 3,628 (90.3%) | 命中缓存 |
| `F8` | `ffe35528` | **9.9s** | 1,688 | **3,584** | 379 | **0 (0.0%)** | 命中缓存，零思考直出 |
| `F9` | `fbc72632` | **13.1s** | 2,038 | **3,072** | 655 | **347 (53.0%)** | 命中缓存，极速返回 |

---

## 3. 深度根因与源码交叉核验

### 3.1 对冲胜出帧为何集中出现视觉/语法瑕疵？（`effort="low"` + `slim_fallback` 三重降级）
- 核查 `html_utility/frame_html.py`（L417–L444、L1217–L1218）发现：当 `mode == "hedge"` 时，除注入 `CLAUDE_CODE_EFFORT_LEVEL="low"` 外，默认开启的 `slim_fallback=True` 还会调用 `_minimal_dispatch_text()` **把 `## Selected blueprint` 全文与 `## Rule recipes (snippets)` 代码参考片段一并裁掉**。
- 由于本轮 **6/9 帧由对冲胜出交付**，意味着三分之二的成片实际上是在 **“极低思考（19–397 tokens）+ 无 Blueprint 构图约束 + 无标准动效代码片段”** 三重降级下盲写的：
  - **F3**（`03-three-checks-before-you-buy.html` L151）：手写出 `tl.set(".check-row[data-row='1']", { dataState: "done" })`，触发运行时警告 `Invalid property dataState set to done Missing plugin?`（GSAP 必须写 `attr: { "data-state": "done" }`）；
  - **F4**（`04-check-1-fundamentals.html` L103–L104）：上半句 `"Is it growing?"`（`#hd-q1`）已在 Scene 1（`0–0.82s`）正常显示并淡出，`18.733s` 抽帧点位于 Scene 2（`frame t≈3.0s`），显示下半句 `"and profitable?"` 属正常时序；**真正的 DOM/JS Bug** 是因丢失 `counting-dynamic-scale` / `stat-bars-and-fills` 片段，在 Scene 2 将旧 `#hd-num1`（红字 `+28%`）缩至 `scale: 0.42` 却**漏写 `autoAlpha: 0`**，同时又弹出 `#hd-hold1`（黄底胶囊 `+28%`），造成两个 `+28%` 并排重影；
  - **F8**（`08-the-line-that-ties-it-all.html`）：丢失了 `logo-assemble-lockup` 与 `svg-path-draw` 范式，给 `34cqw` 宽的竖排胶囊外空套了一个 `width: 90cqw; height: 58cqh`、`preserveAspectRatio="none"` 的全屏大 U 型括号（左右各悬空 `28cqw`），在 `44.525s` 抽帧点（`frame t≈3.0s`，`__hf.draw` 起始阶段）仅画出 `x≈4cqw` 处的左竖边，形如孤立红线。

### 3.2 F2 右侧越界为何绕过了 `verify`？（`canvas_overflow` 已被检出但仅归为 `info`）
- 对存量项目实跑 `node /app/scripts/verify-report.mjs` 输出为 `exit=0 parse=json ✗=0 ⚠=4 ℹ=30`（4 条非阻塞 `⚠` 分别为：F3 `Invalid property dataState` 运行时警告、F9 `t=49.2–49.3s` 变形交接瞬间 `0.1s` `content_overlap`、F7 `"Sell"` `2.78:1` 与 F9 `"Write it down"` `3.44:1` 轻度对比度告警；30 条 `ℹ` 为 `text_box_overflow×25, canvas_overflow×1, content_overlap×4`）。
- 核查 `npx hyperframes check . --json` 原始报文证实：**底层检查器已经精准捕获了 F2 右侧越界**：
  ```json
  {"code": "canvas_overflow", "severity": "info", "time": 8.816, "selector": "span.tail", "message": "Text extends outside the composition canvas.", "rect": {"left": 1761.03, "top": 434.59, "right": 2031.69, "bottom": 540.59, "width": 270.66, "height": 106}}
  ```
- 漏放行原因：`hyperframes check` 将 `canvas_overflow` 标记为 `severity: "info"`，被 `verify-report.mjs`（L284–L288）折叠进 `infos (count only)` 直接放行；同时需注意 **`text_box_overflow×25` 含大量打字机动态宽度容器、固定宽度单词槽位及字幕词块假阳性，绝不能一刀切提级为错误**，仅精准提级 `canvas_overflow` 即可零额外耗时拦截真越界。

### 3.3 F6 (`06-check-3-the-exit-plan`) 独扛 `590.7s` 且 `low` 对冲超时的根因
拆解 `dcab5786-4135-4515-8bd4-ccdeba8819de.jsonl`（`94,684` 思考字符），定位到两处分镜冲突：
1. **Phase B 分镜与 Shared Stage 契约自相矛盾**：`STORYBOARD.md`（L53）规定 `F6: [done:coral, done:coral, done:coral]` 且已完成槽位永不重置，但 Phase B F6 worker 在 L203 写了 *"slot-03's coral check brightens in the lockup while completed 01/02 dim"*，诱发模型反复推演是否覆写骨架（思考流中 `slot` 115 次、`done` 43 次、`dim` 41 次）。
2. **单个关键词叠加 4 重易冲突微交互**：L203–L204 对 `"sell"` 同时要求逐字打字 + 跟随光标 + 同步画珊瑚红下划线 + 硬切为 `"coral fill"`。模型陷入死循环（`caret` 229 次、`coral` 181 次、`type` 169 次、`underline` 125 次、`Actually` 79 次），反复权衡 `__hf.type` 写 `textContent` 会清空子元素下划线以及 `"coral fill"` 是字体色还是背景色。

### 3.4 F3 顶部槽位变薄荷绿与 F4 虚构数字根因
1. **F3 变薄荷绿**：Phase B 违背 `F3: [dim, dim, dim]` 提前在 Scene 2 将 Slot 1 切为 `done`；而 `frame-packets-core.mjs`（L1227–L1233）对初始 `dim` 槽位未输出 `data-accent="coral"`，F3 JS 仅切了 `data-state="done"`，回退命中 `.hf-stage-slot[data-state="done"] { background: #A8E6CF; }`（薄荷绿）。
2. **F4 虚构 `+28%` / `26%`**：口播原文 *"First — the fundamentals. Is the business actually growing, and profitable?"* 无任何数值，但 `visual.py` 仅禁止了 `— figure —` 字面占位符，未禁止凭空编造具体统计数字。

---

## 4. 完整修改建议（按执行优先级编排）

> **策略保留前提与中长期降本储备**：
> - `frame_html` 的“主进程 (`medium`) + 150s 启动 `low` 对冲（`MAX_HEDGES=3`、`HEDGE_TIMEOUT=180s`）”调度架构**当前阶段保持现状不变**；以下 P0/P1/P2 改造在不改变该竞速拓扑的前提下，补齐质量门禁、消除 F6 类 590s 冲突并拉升对冲帧成片下限。
> - **后续降本备选储备（待下轮验证 P0/P1 质量门禁稳固后再评估）**：针对当前 6/9 帧因对冲胜出而取消 `medium` 主进程产生的 `~1,511s` 隐性空烧，后续可二选一收敛——**备选方案 A**：默认 `effort="low"`（配完整 `snippets`）主跑，仅在超时或 `selfcheck`/`verify` 未过时单帧升级 `medium` 重试（预计隐性空烧归零、阶段降至 `120–180s`）；**备选方案 B**：提高 `DEFAULT_HEDGE_AFTER` 至 `240s` 或将 `medium` 主进程超时收敛至 `300s` 后串行 fallback 至 `low`。

### P0：零耗时堵住画布越界漏检 + 根除虚构数字

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P0-1** | `scripts/lib/verify-check-parse.mjs`<br>`scripts/verify-report.mjs`<br>`scripts/known-failures.mjs`<br>`html_utility/frame_html.py`<br>`test/test_verify_check_parse.mjs` | **将真画布越界 `canvas_overflow` 提级为阻塞错误 + 禁止整句 `nowrap`（零额外扫描耗时）**：<br>1. **`verify-check-parse.mjs`（L61–L129）**：对 `code === "canvas_overflow"` 同步保留 `f.rect` 并在 `msg` 中格式化几何坐标 `[t=…s (frame t=…s) box(left,top,WxH)]`（与 `overlap` 一致），供修复模型直接定位越界像素。<br>2. **`verify-report.mjs`（L248 附近，在 `checkParseMismatch`/`checkContradiction` 校验之后）**：受环境变量 `HTMLVIDEO_VERIFY_CANVAS_OVERFLOW`（默认 `"1"`，设 `0` 可一键回退）控制，扫描 `chk.infos` 中的 `canvas_overflow`——排除字幕选择器（`#caption-word`/`.caption-line`）、转场滑屏窗口（复用现成 `transitionWindows(indexHtml)`，避免 `push-slide` 进出画布瞬间误报）以及 `< 8px` 的亚像素边缘溢出；其余真越界（如 F2 `right=2031.7 > 1920`）从 `chk.infos` 移入 `chk.errors`（`mark: "✗"`）。**注意：绝不提级 `text_box_overflow`（含 25 处打字机/槽位假阳性）**。<br>3. **`known-failures.mjs`**：新增 `id: "canvas-overflow"`（`mode: "model"`）词条，命中 `checkErrors` 中 `code === "canvas_overflow"` 时给出精准修复提示：移除多词长句上的 `white-space: nowrap`、允许自然换行并收紧 `max-width` / `font-size`。<br>4. **`frame_html.py`（L715–L720 `layout & visual guardrails`）**：明确补充——**严禁对超过 4 个词的句子或标题容器设置 `white-space: nowrap`**（仅短徽章/胶囊标签可用），长句必须设 `max-width`（如 `≤ 84cqw`）并允许换行或合理控字号。 | **0ms 额外开销**精准拦住 F2 类画布裁切并触发单帧自动修复，且零 `push-slide` / `text_box_overflow` 误报。 |
| **P0-2** | `html_utility/visual.py`<br>`test/test_visual_driver.py` | **在 Phase B `visual` 补齐“零虚构统计数字”硬约束**：<br>在 `visual.py` `_build_frame_prompt()` `VISUAL GUARDRAILS`（L1018–L1026）新增第 (5) 条：**若 `BRIEF.md` 与本帧 `voiceover` 未出现具体数字/百分比/倍数，严禁在 `- focal:` 或 `Scene` 中编造 `+28%`、`26%`、`3.2x`、`$10M` 等虚假定量数据**（同时严禁写 `— figure —` 占位符）；无源数据时一律采用定性标签（如 `Growing` / `Profitable`、`Steep` / `Fair`）、无数字比例条或勾选状态表达。 | 从源头杜绝 F4 类财经/专业科普视频出现无依据假数据的硬伤。 |

---

### P1：消除 F6 级 590s 思考死结 + 修复共享舞台变色 + 拉升对冲帧质量下限

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P1-1** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills`）<br>`test/test_frame_packets.mjs` | **按槽位列（per-slot）跨帧继承 `data-accent`（兼容同台多色槽位）**：<br>在 `frame-packets-core.mjs` `buildSharedStageMarkupAndCss()`（L1220–L1234 & L1270）中：<br>1. 对每个槽位索引 `idx`，若当前帧 `entry.states[idx].color` 为空（如 F3 初始 `dim`），按帧序纵向扫描 `stageSpec.frames` 中同一列 `fe.states?.[idx]?.color` 首次声明的有效调色板色名，写入 `data-accent="<slotColor>"`（因 CSS 选择器为 `.hf-stage-slot[data-accent="..."]:not([data-state="dim"])`，初始 `dim` 态仍保持白底半透明，一旦帧内动态切为 `active`/`done` 即自动呈现该槽位专属强调色，且 100% 保留 `[done:coral, done:sky, done:lime]` 同台异色能力）；<br>2. 若某槽位列在全表均未声明 `:color`，但其他列声明了单一同质强调色，则回退该全局强调色；全表均无 `:color` 声明时仍保持原默认 `mint`/`sky` 兜底，确保既有单测零回归。 | 彻底根除 F3 类动态切 `done` 误变薄荷绿的问题，同时完整兼容多色共享舞台单测。 |
| **P1-2** | `html_utility/visual.py`<br>`test/test_visual_driver.py` | **锁死 Phase B 帧级槽位状态契约 + 限制单元素微交互叠加（直击 F6 `590.7s` 根因）**：<br>1. **`_frame_shared_stage_note()`（L637–L651）**：强化双向锁死——本帧 `slot_states` 即终态契约：若本帧为 `[dim, dim, dim]`（概览引入帧），**严禁在 Scene 中提前将任一 `.hf-stage-slot` 点亮为 `done`**；若前置槽位已为 `done`（如 `[done:coral, done:coral, done:coral]`），**严禁在 Scene 中描述将已完成的槽位变暗（`dim`）或重置重演**。<br>2. **`_build_frame_prompt()`（L1018–L1026）**：新增微交互复杂度上限——单个词/短语最多绑定 **1–2 个**动效（例如“打字入场 + 颜色切换”或“弹入 + 下划线绘制”二选一），**严禁在同一词上同时叠加逐字打字、跟随光标、同步下划线与底色翻转四重动效**。 | 直接消除 F6 类分镜自相矛盾与四重微交互推演死循环，将 `frame_html` 关键路径压回 **`240–360s`**，同时降低对冲超时率。 |
| **P1-3** | `scripts/frame-selfcheck.mjs`<br>`html_utility/frame_html.py`<br>`test/test_claude_cli.py`（或新增 selfcheck 单测） | **毫秒级静态拦截 GSAP `dataState` 误写 + 收紧绝对定位 SVG 装饰框与替换残影护栏（直击 F3 / F4 / F8）**：<br>1. **`frame-selfcheck.mjs`（L254 附近）**：在剥离注释的 `<script>` 中检测 GSAP 调用里裸写 camelCase `data[A-Z]\w*\s*:`（如 `{ dataState: "done" }`），直接判 `FAIL` 并提示：`GSAP sets JS properties, not data-* HTML attributes — use attr: { "data-state": "…" } (gsap_camelcase_data_prop)`，在 `frame_html` 阶段 0.1s 内自愈 F3 类静默失效。<br>2. **`frame_html.py`（L715–L725 `layout & visual guardrails`）**：补充两条明确布局铁律：<br>   - **替换态必须互斥隐藏**：当元素 A 变形/缩退为徽章元素 B（如大数字转为小持仓胶囊）时，必须在同一时间点对旧元素 A 设 `autoAlpha: 0`（杜绝 F4 双 `+28%` 同屏重影）；<br>   - **禁止全屏悬空 SVG 括号/线框**：包裹一组卡片/胶囊的括号或边框必须放进与该组元素同宽高的紧凑父容器内（或直接用 CSS `border`），**严禁在 `#root` 层放置 `width > 70cqw` + `preserveAspectRatio="none"` 的巨型绝对定位 `<svg>` 去套局部元素**（杜绝 F8 左侧孤立红线）。 | 毫秒级拦截 `dataState` Bug，消除 F4 双重影与 F8 悬空错位线。 |
| **P1-4** | `html_utility/frame_html.py`<br>`test/test_claude_cli.py` | **对冲模式保留 `## Rule recipes (snippets)` 代码片段（不改对冲触发策略，拉升胜出帧质量）**：<br>在 `frame_html.py` `_minimal_dispatch_text()`（L417–L444）中区分裁剪粒度（新增环境变量 `HTMLVIDEO_FRAME_HTML_SLIM_KEEP_SNIPPETS`，默认 `"1"`，设 `"0"` 可回退全裁）：<br>- 继续裁掉长篇散文式的 `## Selected blueprint` 与冗余 `## Rule index`（省去架构推敲负担）；<br>- **当原本是紧凑的 `## Rule recipes (snippets)`（通常仅 1.5–3KB 核心 GSAP/DOM 范式）时予以保留**（仅裁剪超长的 `(full)` 全文）。因 `effort="low"` 下模型本身几乎不展开长思考，保留 2KB 代码片段仅增加 `<0.3s` prefill，却能让 6/9 对冲胜出帧直接照抄标准 DOM/GSAP 写法而非盲写。 | 在完全保持“150s `low` 对冲”架构不变的前提下，显著消除对冲胜出帧的代码与排版退化。 |

---

### P2：工程卫生清理（低风险顺手项）

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P2-1** | `/app/log/claude/plugins/installed_plugins.json` | 将 `plugins` 字段清空为 `{"version": 2, "plugins": {}}`（当前仍残留 `superpowers@claude-plugins-official`，本轮在 `claude-debug.log` 中产生 86 行扫描日志）。 | 保持 `claude-debug.log` 干净，微降 27 次 CLI 冷启动 IO。 |

---

## 5. 预期综合收益与回归验收标准

在**保持当前 `frame_html` 对冲竞速策略不变**的前提下，落地上述 P0 + P1 + P2 后，同规模 9 帧（~50–60s）任务的预期表现：

| 指标 | 本次实测 (`6b082a5b`, 9 帧) | 改造后预期 | 核心驱动项 |
|---|---|---|---|
| `visual` 总耗时（Phase A + B） | `283.7s` (4.7m) | **`240–300s`** (4.0–5.0m) | 维持 Phase A ~180s + Phase B 高缓存命中基线 |
| `frame_html` 关键路径 | `596.6s` (9.9m，F6 拖尾) | **`200–320s`** (3.3–5.3m) | P1-2 消除 F6 分镜矛盾与四重微交互死结 |
| `verify` 总耗时 | `50.2s` (0 轮，漏放行 F2 越界) | **`50–110s`** (0–1 轮) | P0-1 零开销提级 `canvas_overflow`（有真越界时精准单帧修复） |
| **端到端总耗时** | **`1029.9s` (17.2m)** | **`620–820s` (10.3–13.7m)** | 削去 F6 ~280s 极长尾 |
| **成片视觉与运行时质量** | F2 裁切 / F3 变色+GSAP 警告 / F4 假数据+重影 / F8 悬空红线 | **零画布越界、零虚构数字、零 `dataState` 警告、跨帧槽位同色、对冲帧构图稳健** | P0-1 + P0-2 + P1-1 + P1-2 + P1-3 + P1-4 |

### 回归验收 Checklist
1. **单测全绿并覆盖新增断言**：
   - `node test/test_frame_packets.mjs`：验证 `dim` 槽位按列继承 `data-accent`（含单色 `[done:coral, ...]` 与多色 `[done:coral, done:sky, done:lime]` 两种场景）；
   - `node test/test_verify_check_parse.mjs`：验证 `canvas_overflow` 几何信息解析、非转场窗口提级为 `error`、转场窗口与字幕豁免；
   - `pytest test/test_visual_driver.py test/test_claude_cli.py`：验证零虚构数字护栏、帧级共享舞台双向锁死、`_minimal_dispatch_text` 保留 `snippets` 及 `gsap_camelcase_data_prop` 自检拦截。
2. **对存量 `6b082a5be89ed160` 目录做离线回放验证（零大模型开销，10 秒内可验）**：
   - 跑 `node /app/scripts/frame-selfcheck.mjs .../03-three-checks-before-you-buy.html` 应精准报出 L151 `gsap_camelcase_data_prop`；
   - 跑 `node /app/scripts/verify-report.mjs --videodir .../6b082a5be89ed160` 应精准将 F2 `span.tail` `canvas_overflow`（`right=2032`）列为阻塞 `✗` 并给出 `canvas-overflow` 修复指引，而 25 条 `text_box_overflow` 仍保持 `ℹ` 不误报。
3. **下一次端到端回归观测点**：
   - `frame-html stage summary` 中 `truncations=0`、`timeouts=0`、`critical_path_s <= 320s`；
   - `contact-sheet` 逐帧核查：无右侧文字裁切、无口播外编造的百分比/金额、共享舞台顶部槽位颜色跨帧严格一致、无元素替换重影与悬空装饰线。
