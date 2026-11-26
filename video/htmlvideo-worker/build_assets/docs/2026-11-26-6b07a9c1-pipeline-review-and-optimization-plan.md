# `6b07a9c110ac8428` 全流程日志复盘与效果 / 耗时 / 成本最终优化方案（2026-11-26 修订版）

> **关联任务**：`6b07a9c110ac8428`（2026-11-26 03:38:41–04:09:09，项目目录 `/app/videos/2026-11-26/userclm17919893695531772/6b07a9c110ac8428`）  
> **对照基线**：`6b0795df06afcc27`（详见 `docs/2026-11-26-6b0795df-postmortem-and-fix-plan.md`）与 `6b06ff057bb54345`（详见 `docs/2026-11-26-frame-visual-html-truncation-shared-stage-optimization.md`）  
> **依据来源**：`log/htmlvideo_worker.log`（L1051–L1929）、`log/claude-debug.log(.1)`、22 个 Claude Code 会话 JSONL、26 组 `/app/log/api-bodies/*.request.json` 出站抓包、`snapshots/contact-sheet.jpg` 成片抽帧，以及源码与预设模板交叉审查。

---

## 1. 总体结论

上一轮针对 `6b0795df` 的 P0/P1/P2 修复重新构建并重启服务后的首次端到端回归：

- **符合预期的部分（稳定性底线全部守住）**：
  - **0 次满额截断（`truncations=0`）并端到端出片**：全流程跑通并经 QUIC 上传最终视频（`6b07a9c110ac8428_20261126033841_426.mp4`，`37.9s` / `4.8 MB` / 渲染 `30.7s` / 上传 `1.8s`），彻底解决了上次在 `verify` Round 2 触发 `TRUNCATED_RC (125)` 中断的问题。
  - **P0-1 / P0-2（`center-scorecard` 2×2 网格 + `data-layout-allow-overlap` + `crossfade` 过滤）完全命中**：4 个共享槽位标签全部 ≤ 16 字符（`01 Business` / `02 Price` / `03 Story breaks` / `04 Price outruns`），`contact-sheet` 展示清晰对齐，跨帧 `crossfade` 误报与槽位横向重叠全部归零。
  - **P0-3（`verify` 提额 `32000` + `trunc_streak=1`）完全命中**：所有 `verify` 调用均以 `max_tokens=32000` 发出，0 次 16k 失忆空烧，`verify` 耗时从上次 `944s + 失败` 降至 **`351.8s`（5.9m）通过**。
  - **P2-1（`HEDGE_TIMEOUT=240s`）生效**：2 次卡死对冲在 `240s` 准时终止释放槽位。
- **未达预期的部分（极长尾未消 + 画面瑕疵）**：
  - **端到端总耗时仍达 `1826.2s`（30.4 分钟）**（本次仅 6 帧，较上次 8 帧少 2 帧）。
  - **`frame-visual` Phase A 反而变慢**：从上次 `280.5s`（`12,543` thinking）升至 **`439.7s`（7.3m，`20,578` thinking，占比 95.2%）**。
  - **`frame-html` 极长尾未消除**：总计 **`806.7s`（13.4m）**，未达 `<= 420s` 目标；**F5（`800.6s`, `40,608` thinking）** 与 **F2（`745.8s`, `36,870` thinking）** 吞掉全阶段 99% 墙钟，且两者的 24k 对冲双双在 `240s` 超时。
  - **成片视觉存在 3 处明显瑕疵**：F1 `"walking away"` 卡片严重偏右未居中；F4 左右书页直接渲染字面占位符 `— figure —`；F5 下方 `#stage-card` 偏空且 F2 对 `.hf-stage-lockup` 施加了非预期的整体缩放/浮动动画。

---

## 2. 全流程耗时与 Token 实测总账（对比 `6b0795df`）

| 阶段 | 本次墙钟 (`6b07a9c1`, 6帧) | 上次 (`6b0795df`, 8帧) | 调用 / 轮次 | Input | Cache Read | Output | Thinking (占比) | 结果 |
|---|---:|---:|---|---:|---:|---:|---:|---|
| `init` | 3.6s | 3.6s | - | - | - | - | - | 成功 |
| `storyboard` | 65.5s | 56.5s | 1 call / 1t | 5,753 | 0 | 3,558 | 2,377 (66.8%) | 成功 |
| `audio`（并行） | 69.3s | 89.6s | - | - | - | - | - | 成功 |
| `visual` Phase A (`visual_design`) | **439.7s** | 280.5s | 1 call / 1t | 3,984 | 768 | **21,620** | **20,578 (95.2%)** | 成功（严重过思考） |
| `visual` Phase B (`visual` × 6) | 122.1s (最慢 F6) | 194.1s (8帧) | 6 calls / 均 1t | 13,420 | 20,224 | 18,398 | 15,790 (85.8%) | 成功（阶段总计 `566.8s`） |
| `packets` + `captions` | 0.2s | 0.2s | - | - | - | - | - | 成功 |
| `frame_html`（6 主 + 4 对冲） | **806.7s** (最慢 F5 `800.6s`) | 779.2s (8帧) | 10 calls（6成 / 2杀 / 2超时） | 37,688* | 36,864* | **117,078*** | **99,849 (85.3%)*** | 6/6 `selfcheck=PASS`；对冲 2 胜 2 超时 |
| `verify` Round 1（修 F4 JS 语法） | 50.0s (+12s scan) | 284.8s (+26s) | 1 call / 6t (`fixed 5`) | 10,265 | 8,192 | 3,148 | 2,513 (79.8%) | 解除语法阻断 |
| `verify` Round 2（并行 F4+F5） | 73.0s (+23s scan) | 603.3s (失败) | 2 calls / 均 2t | 15,065 | 19,456 | 4,324 | 3,849 (89.0%) | F5 通过；F4 重叠未消 |
| `verify` Round 3（再修 F4 重叠） | 134.6s (+55s scan) | - | 1 call / 2t | 12,659 | 10,240 | 7,588 | 7,382 (97.3%) | **GATE PASS**（阶段总计 `351.8s`） |
| `render` + `sync` | 30.7s + 1.8s | 未到达 | - | - | - | - | - | 成功产出并上传 MP4 |
| **端到端总计** | **1826.2s (30.4m)** | 中断 | 22 sessions / 26 API req | **98,834*** | **95,744*** | **175,714*** | **152,338 (86.7%)*** | **全流程成功** |

### `frame-html` 逐帧明细（含隐藏算力成本）

| 帧 / 角色 | Session | 墙钟 | Input | Cache Read | Output | Thinking | 结果 |
|---|---|---:|---:|---:|---:|---:|---|
| `01-the-hard-part#1`（主） | `059f8b3e` | ~240s | - | - | - | - | 被对冲取消（`rc=130`，上游已空烧） |
| `01-the-hard-part#1-hedge` | `87edb3a7` | **29.6s** | 2,201 | 7,168 | 1,617 | **70** | **胜出**（漏 `translateX(-50%)` 卡片偏右） |
| `02-the-framework#1`（主） | `52b443e3` | **745.8s** | 6,954 | 7,168 | **40,152** | **36,870** | **胜出**（槽位动画 vs 骨架禁改令纠结） |
| `02-the-framework#1-hedge` | `57737fa9` | 240.0s | - | - | - | - | 超时（`rc=124`，落入同一矛盾空烧） |
| `03-buy-rule-one-the-business#1` | `4115eafc` | 215.5s | 6,714 | 7,168 | 10,861 | 8,034 | 一次通过 |
| `04-buy-rule-two-the-price#1`（主） | `e2f369e8` | ~292s | - | - | - | - | 被对冲取消（`rc=130`，上游已空烧） |
| `04-buy-rule-two-the-price#1-hedge` | `ab6466ab` | **55.8s** | 3,776 | 7,168 | 3,597 | **57** | **胜出**（带 JS 语法错 + `— figure —` + 重叠） |
| `05-sell-rule-the-story-breaks#1`（主） | `fdf88c65` | **800.6s** | 13,218 | **1,024** | **43,856** | **40,608** | **胜出**（关键路径 + KV Cache 穿透） |
| `05-sell-rule-the-story-breaks#1-hedge` | `bffee0c8` | 240.0s | - | - | - | - | 超时（`rc=124`，落入同一矛盾空烧） |
| `06-trade-the-rules#1` | `f9a79659` | 333.1s | 4,825 | 7,168 | 16,995 | 14,210 | 一次通过 |

> **\*隐藏成本说明**：4 个中途被取消/超时的 `frame-html` 进程合计在服务端运行约 **`1,012s`**；按本批次实测单流吞吐 `50.4–54.8 tok/s` 测算（扣除网关 `429`/`529` 退避时间后），上游实际额外消耗了约 **~45k–50k 思考 tokens**（因进程被强杀未计入本地 JSONL）。

---

## 3. 深度根因与源码交叉核验结论

### 3.1 `frame-html` F2（`745.8s`）与 F5（`800.6s`）40k 思考长尾 + 对冲双超时的真正根因
对 `52b443e3.jsonl`（F2，1,167 行思考）与 `fdf88c65.jsonl`（F5，1,453 行思考）按 0%–100% 进度分段核查，定位到 **三层叠加矛盾**：
1. **核心死结（占 80%+ 思考时间）—— Storyboard 要求对槽位做动态演进动画 vs 骨架严禁改动 `.hf-stage-*` DOM**：
   - `html_utility/frame_html.py`（L628–L631）注入硬指令：`KEEP .hf-stage-* styles and DOM intact (never move, resize, or re-label the stage slots)`；
   - 但 F2 Storyboard 要求对标题执行打字机动画（必须往 `.hf-stage-title` 内拆分 `<span class="hf-stage-title-text">` 与光标）并逐个弹入 4 个槽位；
   - F5 更严重：`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（L1190–L1206）只为 `done` 槽位生成 `<svg class="hf-stage-check">`，为 F5 的 `active:coral` 槽位仅生成圆点 `.hf-stage-dot`；而 F5 Scene 2 却要求**槽位 03/04 从白色 `dim` 翻转为珊瑚色并画出勾（`__hf.draw`）**。模型被迫耗费数万 token 推演如何篡改 `.hf-stage-slot` 补塞 SVG 勾、并在 `t=0` 用 GSAP 逆向覆写状态而不破坏契约。
   - **对冲双超时原因**：`_minimal_dispatch_text()` 仅剥离了 blueprint 与 rule recipes，**原封不动保留了 Storyboard Scene 与 `.hf-stage-*` 禁改令的冲突**，导致 `02#1-hedge` 与 `05#1-hedge` 掉进同一个思考死结，在 `240s` 超时。
   - **连锁副作用**：F2 在 `t=0` 将整个 `.hf-stage-lockup` 设为 `scale: 0, autoAlpha: 0` 并在结尾加 `y: -4` 浮动，且未清理 `data-skeleton="stage"`（`rich_note` 仅交代清理 `bg` 与 `#stage-card`），破坏了共享舞台跨帧像素锁定的初衷。
2. **单双卡语义残留冲突（占 ~15% 思考时间）**：`STORYBOARD.md` 仍将 F2 描述为 *"a single checklist card carrying all four slots"*，与 `center-scorecard` 上下双卡骨架（`.hf-stage-lockup` + `#stage-card`）语义不一致，导致 F2 不知 `#stage-card` 该放什么、F5 `#stage-card` 仅放一颗胶囊显得空旷。
3. **骨架生成器阴影色值不一致（代码级小冲突）**：`frame.md`（L8/L177）规定阴影色为 `shadow: rgba(26,26,26,0.08)`，而 `frame-packets-core.mjs`（L1210–L1225）硬写了 `box-shadow: 6px 6px 0 ${c.outline}`（`#1E1E1E`），引发 F2 尾段思考反复纠结。

### 3.2 F4 渲染字面占位符 `— figure —` 的真实源头（非对冲偷懒，而是 `frame.md` 硬规则冲突）
- **源码根因**：项目 `frame.md`（L265–L269，源自 `skills/hyperframes-creative/frame-presets/*/FRAME.md` 全部预设模板）写有硬规则：
  > `## Numerals & Claims (hard rule)`  
  > `Never invent figures, stats, or counts at frame scale. Render slots as — figure —, {metric}, N%.`
  且 `frame-packets-core.mjs`（L585–L590）在 `FRAME_MD_EXCERPT_HEADINGS` 中显式白名单抽取了 `## Numerals & Claims (hard rule)` 内联进每个 `.dispatch.md`。
- 当 F4 口播无具体数字却被 Phase A 分配了 `comparison-split`（左右数值对比面板）时，worker 是**严格遵守 `frame.md` 硬规则**才输出了 `— figure —`。
- **关键警示**：若仅在 `frame-selfcheck.mjs` 中拦截 `— figure —` 而不剥离/改写 `frame.md` 的该条 PPT 模板遗留规则，会导致 worker 陷入“遵守 `frame.md` 写占位符 ↔ 被 `selfcheck` 打回”的死锁。

### 3.3 `visual_design`（Phase A）`439.7s` 过思考与 `max_tokens` 调参禁区
- **出站协议事实**：26 份 `/app/log/api-bodies/*.request.json` 证实网关全部运行在 `thinking: {"type": "adaptive", "display": "omitted"}`，`MAX_THINKING_TOKENS` 被剥离不生效。
- **严禁靠压低 `max_output` 控思考**：在千帆 `adaptive + omitted` 模式下，压低 `max_tokens` **不会**让模型按比例缩短思考，只会让模型在原思考节奏下撞墙触发 `TRUNCATED_RC (125)` 并从零重跑（`6b06ff05` 中 `visual_design=12000`、`frame_html=28000` 已实测导致 50% 帧首轮撞墙雪崩）。
- 因此，`visual_design` 保持 **`effort="medium"` + `max_output="24000"` 不变**（不降低 `effort`，也不下调 `max_output`），仅通过精简 Phase A 提示词中的蓝图/共享舞台决策歧义来自然减少无谓反复推敲。

### 3.4 对冲“零思考”引发的代码缺陷与 `frame-selfcheck.mjs` / `verify` 链路放大
1. **对冲缺少微护栏 + `selfcheck` 漏检 JS 语法**：
   - `01#1-hedge`（`70` thinking tokens）写出 `.pill-card { position: absolute; left: 50%; top: 42cqh; }` 漏掉 `transform: translateX(-50%)`，导致卡片右偏；
   - `04#1-hedge`（`57` thinking tokens）在 GSAP 对象字面量中写出 5 处未加引号的 `y: 2.2cqh`（非法 JS 数字后缀），`scripts/frame-selfcheck.mjs` 未对内联 `<script>` 执行 JS 语法解析而直接放行，白白消耗 `verify` Round 1（`50s` + 扫描）且推迟了布局/对比度检查。
2. **`verify` Round 2 修 F4 失败的直接原因——全局时间戳未换算为帧内局部时间**：
   - 报错 `[t=21.1-26.3s box(1008,495,83x28)]` 输出的是 `index.html` 全局时间轴秒数（F4 起点 ~`19.6s`，`21.1s` 实为帧内 `t_local ≈ 1.5s` Scene 1，且只打印了单个碰撞区域 box 而非两个元素的各自 box）。
   - `8c11889c.jsonl`（R2）与 `117d95d4.jsonl`（R3）均因 `21.1s > 6.7s（帧长）` 产生误判，Round 2 改错了 `1.8s` 的动画，多耗费一整轮 Round 3（`168.6s`）。
3. **`verify.py` 单文件过滤不一致**：`_split_scan_by_file()`（L438）在 `len(order) <= 1` 时返回空字典回退 `_build_fix_prompt`，导致单文件报错未复用 `_filter_summary_for_file` 剔除无关提示项，且日志 `label=-` 丢失目标文件名可观测性。
4. **Phase B 自违背对比度规则**：`STORYBOARD.md` L176 中 F5 直接写出 `violet text on a sky pill fill`（`1.72:1`），违背 Phase A `Color roles`（浅底必须用 `ink`），触发 `verify` Round 2 对比度修复。

### 3.5 并发网关限流与 CLI 启动开销（系统侧补充）
- `claude-debug.log` 在本次运行中记录了 **`429`（19 次）与 `529`（15 次）** 重试：`frame_html` 高峰期 6 主 + 2 对冲 = **8 路长连接并发**打向千帆网关，引发限流退避与跨后端节点路由，这也是 F5 发生 KV Cache 穿透（`cache_read=1024` vs 同批其余帧 `7168`）的诱因。
- 全部 22 次 `claude --print` 启动均执行了 `Loaded 1 installed plugins from /app/log/claude/plugins/installed_plugins.json` + `installPluginsForHeadless: starting` 的无用插件加载流程。

---

## 4. 最终优化建议（安全、无死锁、按 ROI 排序）

### P0：零风险止血项（消除视觉硬伤、JS 语法返工与 Phase A 决策歧义）

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P0-1** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills`）<br>`scripts/frame-selfcheck.mjs` | **根治 `— figure —` 占位符（先改规则，再加门禁，防死锁）**：<br>1. 在 `frameMdExcerpt()` 中剥离或覆写 `## Numerals & Claims (hard rule)` 中的 `Render slots as — figure —, {metric}, N%` 指令，明确声明：**autonomous 视频直接渲染成片，脚本无具体数字时必须使用定性短标签（如 `Low` / `Intrinsic`）或纯图形比例条，严禁将 `— figure —`、`{metric}`、`N%` 渲染为可见文本**。<br>2. 在 `frame-selfcheck.mjs` 中对可见 DOM 文本新增占位符 FAIL 门禁（匹配 `—\s*(?:figure|metric|value|number|placeholder)\s*—` 或 `\{metric\}`）。<br>3. 在 `frame-selfcheck.mjs` 中对每个内联 `<script>`（非外链）执行 `new vm.Script(code)` 语法校验，直接拦截 `y: 2.2cqh` 类 `SyntaxError`。 | **100% 根治 `— figure —` 上屏与死锁风险**；**0.1s 本地拦住 JS 语法错**，直接省去 `verify` Round 1（~75s）。 |
| **P0-2** | `html_utility/visual.py` | **减少 Phase A（`visual_design`）决策歧义（保持 `effort="medium"`、`max_output="24000"` 不变）**：<br>1. 在 `_build_design_prompt()` 中明确：无具体脚本数字的帧不强配数值对比模板（或明确使用定性对比），同时明确共享舞台 `active` / `done` 状态规则，减少模型在标题/槽位状态上的反复自检。 | 保留 `medium` 档设计质量，同时缓解 Phase A 无谓发散思考，源头避免 F4 类占位符错配。 |
| **P0-3** | `html_utility/frame_html.py`<br>`scripts/frame-selfcheck.mjs` | **消除对冲“零思考”居中漏写与低级错误**：<br>1. 在 `_hedge_suffix()` 末尾追加 3 条极简硬护栏：① `left: 50%` 居中元素必须配 `transform: translateX(-50%)`（或 flex 父容器居中）；② GSAP 含单位属性必须加引号（如 `y: "2cqh"`）；③ 禁止任何占位符文本。<br>2. `frame-selfcheck.mjs` 对“独立块（无 `right`、非极窄分割线）使用 `left: 50%` 且全文件无 `translateX(-50%)` / `translate(-50%` / `xPercent` / 负 `margin-left`”输出明确告警（或精准 FAIL），避免误杀合法右半屏分栏布局。 | 修复 F1 卡片偏右问题，守住 30–55s 极速对冲的代码与排版下限。 |

### P1：攻克 `frame-html` 13 分钟极长尾与 `verify` 多轮返工（核心提速降本）

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P1-1** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills`）<br>`html_utility/visual.py`<br>`html_utility/frame_html.py` | **彻底解开“共享舞台槽位动画 vs `.hf-stage-*` 禁改令”死结**：<br>1. **骨架预置双态图标**：在 `frame-packets-core.mjs` 的每个 `.hf-stage-slot` 中同时渲染 `.hf-stage-dot` 与 `.hf-stage-check`（通过 CSS `[data-state="done"]` / `[data-state="active"]` 控制显隐），并将骨架 `box-shadow` 改为使用 `c.shadow || c.outline` 与 `frame.md` 对齐。<br>2. **明确槽位动画边界**：规定 `.hf-stage-lockup` 外框严禁做整体缩放/位移（跨帧像素静止）并统一保留/忽略 `data-skeleton="stage"`；槽位状态切换仅允许通过 GSAP 切换 `data-state` / 背景色或对预置 `.hf-stage-check path` 调 `__hf.draw`，禁止在 Phase B 设计篡改 `.hf-stage-title` / `.hf-stage-slot` 结构的复杂打字机或翻转动画。<br>3. **明确双卡分工**：Phase A / B 提示词明确 `center-scorecard` 为“上方 `.hf-stage-lockup` 进度栏 + 下方 `#stage-card` 本帧主视觉”双卡结构，每帧必须为 `#stage-card` 指定充实的主视觉内容（解决 F2 纠结与 F5 空旷）。 | 直击 F2（36.9k thk）与 F5（40.6k thk）80%+ 的思考消耗源头，同时解决 `02/05-hedge` 超时问题；`frame-html` 关键路径预计从 **`806.7s` → `220–320s`**，省 **~60k+ 思考 tokens**。 |
| **P1-2** | `scripts/verify-report.mjs`<br>`scripts/lib/verify-check-parse.mjs`<br>`html_utility/verify.py` | **提升 `verify` 一轮修复命中率（消除时间戳误导 + 统一单文件精简模式）**：<br>1. 在 `verify` 错误摘要中根据 `index.html` 各帧 `data-start` 自动换算并同时输出**帧内局部时间**（如 `[global t=21.1-26.3s | frame t=1.5-6.7s]`），并打印重叠双方的两个 `box(...)` 坐标。<br>2. `verify.py` `_split_scan_by_file()` 将 `if len(order) <= 1:` 放宽为单文件报错（且非 `__other__`）同样生成过滤后的单文件 fragment 与明确 `label=<rel_path>`。 | 消除 Round 2 因误判时间轴改错动画导致的整轮返工（本次可省 `168.6s`），单文件修复输入与耗时再降 ~30%。 |
| **P1-3** | `html_utility/frame_html.py` | **优化并发度与对冲窗口（减少网关 429/529 与 1,012s 隐性空烧）**：<br>1. **保持主进程 `max_output="48000"` 不下调**（严防首轮撞墙）。<br>2. 将 `DEFAULT_HEDGE_AFTER` 从 `210s` 下调至 **`150s`**，`HEDGE_TIMEOUT` 从 `240s` 下调至 **`150s`**；并在对冲后缀中明确指示：共享舞台帧在 `#stage-card` 内做简单静态/弹入排版，严禁触碰 `.hf-stage-lockup`。<br>3. 可选限制 `frame_html` 最大并发 worker 数为 `4–6`（或拉长错峰启动间隔 `0.5–1s`），缓解 8 路并发触发的千帆 `429/529` 与 KV Cache 穿透。 | 削减 ~40% 被取消/超时进程的隐性算力浪费，提高 KV Cache 稳定命中率。 |

### P2：前置护栏与工程卫生（持续打磨）

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P2-1** | `html_utility/visual.py` | Phase B 合并后对场景描述做轻量低对比度词组扫描/替换（如 `violet/coral/yellow/lime/mint/peach text on sky/lavender/mint/peach/yellow` 自动修正为 `ink text`），防止 Phase B 违背 Phase A 配色契约。 | 从源头消除 F5 类 `contrast_aa_failure` 返工。 |
| **P2-2** | `html_utility/claude_cli.py` | 检查并清理 `/app/log/claude/plugins/installed_plugins.json` 中流水线不需要的 headless plugin（或通过 `--settings` 禁用插件加载），省去每次 `claude --print` 冷启动插件初始化开销。 | 降低 20+ 次 CLI 子进程启动开销与上下文干扰。 |

---

## 5. 预期综合收益汇总

落地上述修正后的 P0 + P1 方案后，6 帧同规模任务的预期表现：

| 指标 | 本次实测 (`6b07a9c1`) | 优化后预期 | 核心驱动项 |
|---|---|---|---|
| `visual` Phase A 墙钟 | `439.7s` (7.3m) | **`200–350s`** (3.3–5.8m) | P0-2（保持 `effort="medium"` + `24k` 不变，仅做 prompt 消歧） |
| `frame-html` 关键路径 | `806.7s` (13.4m) | **`220–320s`** (3.7–5.3m) | P1-1（双态槽位预置 + 解开 `.hf-stage-*` 死结）+ P1-3 |
| `verify` 总耗时 | `351.8s` (3 轮) | **`35–90s`** (0–1 轮) | P0-1（JS 语法自检）+ P1-2（帧内局部时间 + 单文件过滤） |
| **端到端总耗时** | **`1826.2s` (30.4m)** | **`700–950s` (12–16m)** | `frame-html` 与 `verify` 两大长尾同步削峰 |
| **总输出 + 隐性思考 Token** | ~225k（175.7k 显性 + ~50k 隐性） | **~120k–145k（下降 ~35%–45%）** | 消除 F2/F5 40k 思考空烧与无效对冲/返工 |
| **成片视觉质量** | F1 偏右 / F4 `— figure —` / F5 偏空 | **全部根治且无死锁风险** | P0-1 + P0-3 + P1-1 |
