# `6b07fd3a95da96bf` 全流程回归复盘与下一阶段完整优化方案（2026-11-26 修订终版）

> **关联任务**：`6b07fd3a95da96bf`（2026-11-26 09:34:50–10:01:20 UTC，即 17:34–18:01 CST；前置意图任务 `6b07dadcdd1d48c0` 耗时 `7.5s`；项目目录 `/app/videos/2026-11-26/userclm17919893695531772/6b07fd3a95da96bf`）  
> **对照基线**：`6b07a9c110ac8428`（详见 `docs/2026-11-26-6b07a9c1-pipeline-review-and-optimization-plan.md`）  
> **核验依据**：`log/htmlvideo_worker.log`（L1945–L2761）、`log/claude-debug.log`、21 个 Claude Code 会话 JSONL、本轮 21 组 `.request.json` / 16 组 `.response.json` 及历史全量 4,265 份 `/app/log/api-bodies/*.request.json` 出站抓包、`snapshots/contact-sheet.jpg` 成片抽帧，以及源码与单测（`test_frame_packets.mjs` / `test_visual_driver.py` / `test_claude_cli.py`）交叉审查。

---

## 1. 总体结论

上一轮针对 `6b07a9c1` 的 P0/P1/P2 优化重新构建并重启服务后的首次端到端回归（**7 帧 / `57.6s` 成片**，较上次 6 帧 / `37.9s` 帧数 +1、时长 +52%）：

- **符合预期的部分（稳定性、零返工与核心视觉门禁全面达标）**：
  - **零截断 + 零网关限流 + 端到端一次出片**：全流程 `truncations=0`（16/16 已完成响应均为 `stop_reason=end_turn`），`claude-debug.log` 中 HTTP `429`/`529`/5xx 报错全部归零（上次为 19×`429` + 15×`529`）；成功产出 `57.6s` / `10.8 MB` MP4（渲染 `53.0s`，QUIC 上传 `3.89s`）。
  - **`verify` 首次扫描直接通过（`0` 轮模型修复）**：从上次 `351.8s`（3 轮修复）降至 **`64.6s`（直省 `287.2s` / 4.8 分钟）**；7/7 帧首轮 `frame-selfcheck=PASS`，无 JS 语法错、无元素重叠、无低对比度告警。
  - **P0-1（根治 `— figure —` 占位符）完全生效**：F4（`04-gate-two-the-price`）使用定性标签 `Steep` / `Fair` + 比例条呈现对比，零字面占位符上屏。
  - **P0-3（对冲居中/语法微护栏）完全生效**：F1 居中问句卡片与 F6 对冲胜出帧（`06-when-you-sell#1-hedge`，仅 **`35.7s` / `12` thinking tokens**）均精准居中、排版干净。
  - **P1-1（共享舞台双态图标 + 阴影色对齐）在 F2–F5 完全生效**：顶部状态栏 `THE THREE GATES | 01 Fundamentals | 02 Valuation | 03 Exit Plan` 跨帧像素锁死，圆点与勾选切换清晰；F3/F4/F5 均在 `3.5–5.1m` 内一次通过。
  - **端到端总耗时下降 4.0 分钟**：在成片规模扩大 52% 的前提下，总墙钟从 `1826.2s`（30.4m）降至 **`1585.7s`（26.4m）**。
- **未达预期的部分（两大耗时尖峰尚未削峰 + F7 成片瑕疵）**：
  - **`frame_html` 关键路径仍达 `826.7s`（13.8m），且共有 3 帧主进程突破 500s**：除最慢的 **F7（`820.8s` / `38.2k` thk）** 复现“汇总帧槽位重演 vs 骨架内联背景色”冲突外，**F1（非共享舞台帧，`550.3s` / `25.2k` thk）** 与 **F2（槽位全 `dim`，`509.9s` / `23.0k` thk）** 同样耗时 8.5–9.2m，仅修 F7 无法将阶段墙钟压入 `240–360s`。
  - **对冲命中率降至 `1/5`（3 次 150s 超时 + 槽位拥堵）**：`01/02/07#1-hedge` 仍在 `effort="medium"` 下陷入长思考，在 `150s` 超时被杀，白烧 ~`450s` 算力并占满 `MAX_HEDGES=2` 槽位，导致 F5/F6/F7 拖到 `305–317s` 才启动对冲。
  - **`visual_design` Phase A 未提速**：耗时 **`437.2s`（`20,618` thinking，占比 93.2%）**，与上次 `439.7s`（`20,578` thinking）几乎持平。
  - **F7 成片存在 2 处明显视觉瑕疵**：收尾场景（`55.847s`）`#stage-card` 被设为 `autoAlpha: 0` 淡出，导致画面正中央大片空白；四周飘浮 `VISION` / `FUTURE` / `FLOW` / `NEXT` / `PACE` / `SIGNAL` 6 个与股票投资主题无关的模板词胶囊。

---

## 2. 全流程耗时与 Token 实测总账（对比 `6b07a9c1`）

| 阶段 | 本次墙钟 (`6b07fd3a`, 7 帧 / 57.6s) | 上次 (`6b07a9c1`, 6 帧 / 37.9s) | 调用 / 轮次 | Input | Cache Read | Output | Thinking (占比) | 结果 |
|---|---:|---:|---|---:|---:|---:|---:|---|
| `intent`（前置独立任务） | 7.5s (`6b07dadc`) | - | - | - | - | - | - | 成功 |
| `init` | 3.6s | 3.6s | - | - | - | - | - | 成功 |
| `storyboard` | 68.0s | 65.5s | 1 call / 1t | 5,747 | 0 | 3,065 | 1,499 (48.9%) | 成功 |
| `audio`（并行） | 85.7s | 69.3s | - | - | - | - | - | 成功 |
| `visual` Phase A (`visual_design`) | **437.2s** | 439.7s | 1 call / 1t | 4,862 | 0 | **22,124** | **20,618 (93.2%)** | 成功（恒定 ~20.6k 过思考） |
| `visual` Phase B (`visual` × 7) | 123.1s (最慢 F5) | 122.1s (6帧) | 7 calls / 均 1t | 38,877 | 4,352 | 28,138 | 25,076 (89.1%) | 成功（阶段总计 `565.3s`） |
| `packets` + `captions` | 0.2s | 0.2s | - | - | - | - | - | 成功 |
| `frame_html`（7 主 + 5 对冲） | **826.7s** (最慢 F7 `820.8s`) | 806.7s (6帧) | 12 calls（7成 / 2杀 / 3超时） | 54,100* | 45,056* | **144,218*** | **118,266 (82.0%)*** | 7/7 `selfcheck=PASS`；对冲 1 胜 3 超时 |
| `verify` | **64.6s** | 351.8s (3轮) | **0 calls** | 0 | 0 | 0 | 0 | **首扫即 GATE PASS** |
| `render` + `sync` | 56.3s + 3.9s | 30.7s + 1.8s | - | - | - | - | - | 成功产出并上传 MP4 |
| **流水线总计** | **1585.7s (26.4m)** | 1826.2s (30.4m) | 21 sessions / 21 API req | **103,586*** | **49,408*** | **197,545*** | **165,459 (83.8%)*** | **全流程一次过检** |

### `frame_html` 逐帧明细（含隐藏算力成本）

| 帧 / 角色 | Session | 墙钟 | Input | Cache Read | Output | Thinking | 结果与耗时特征 |
|---|---|---:|---:|---:|---:|---:|---|
| `01-the-wrong-question#1`（主） | `152cb6b5` | **550.3s** | 12,802 | **1,024** | 28,616 | 25,184 | **胜出**（非共享舞台；手搓 `masterUpdate` 状态机同步三层残影耗时长） |
| `01-the-wrong-question#1-hedge` | `8b581213` | 150.0s | - | - | - | - | 超时（`rc=124`，`effort=medium` 下思考未完成） |
| `02-three-gates#1`（主） | `ce01953b` | **509.9s** | 7,226 | 7,168 | 26,864 | 22,984 | **胜出**（槽位全 `dim`；纠结 SVG 噪点滤镜编码 + 卡片内重复画三槽位） |
| `02-three-gates#1-hedge` | `b32eb70d` | 150.0s | - | - | - | - | 超时（`rc=124`，`effort=medium` 下思考未完成） |
| `03-gate-one-the-business#1` | `dd313831` | 266.6s | 7,793 | 7,168 | 14,089 | 10,737 | 一次通过 |
| `04-gate-two-the-price#1` | `0f334bed` | 210.7s | 7,896 | 7,168 | 11,466 | 8,114 | 一次通过（定性对比，零占位符） |
| `05-gate-three-the-exit#1`（主） | `be5ca547` | 306.3s | 8,653 | 7,168 | 16,683 | 13,015 | **胜出**（排队至 `305s` 才起对冲，2s 后主进程即完成） |
| `05-gate-three-the-exit#1-hedge` | `6f7fb90b` | ~12s | - | - | - | - | 被主进程取消（`rc=130`） |
| `06-when-you-sell#1`（主） | `6a13125b` | ~357s | - | - | - | - | 被对冲取消（`rc=130`，排队至 `310s` 才起对冲） |
| `06-when-you-sell#1-hedge` | `ed9010a3` | **35.7s** | 2,008 | 8,192 | 2,088 | **12** | **胜出**（极速返回，排版居中、无语法/对比度问题） |
| `07-the-whole-checklist#1`（主） | `2ba2583e` | **820.8s** | 7,790 | 7,168 | **44,412** | **38,214** | **胜出**（关键路径；与骨架内联背景色冲突 + 结尾淡出 `#stage-card`） |
| `07-the-whole-checklist#1-hedge` | `87ead676` | 150.0s | - | - | - | - | 超时（`rc=124`，排队至 `317s` 才启动，落入同一冲突） |

> **\*统计口径与隐藏成本说明**：表中显性 `Input=103,586` / `Cache Read=49,408` / `Output=197,545` 仅统计 **16 个正常结束的响应**；其余 **5 个中途被取消/超时的 `frame_html` 请求**（`01#1-hedge` 150s + `02#1-hedge` 150s + `05#1-hedge` 12s + `06#1` 357s + `07#1-hedge` 150s = 合计服务端运行约 **`819s`**）因进程被终止未落盘 `.response.json`，按实测吞吐 `~50 tok/s` 测算，上游实际额外消耗了约 **~55k 输入（含缓存）tokens + ~35k–42k 思考 tokens**（较上次 `1,012s` 隐性空烧下降约 19%）。

---

## 3. 深度根因与源码交叉核验结论

### 3.1 `frame_html` 三帧突破 500s 的真实根因（F7 `820.8s` + F1 `550.3s` + F2 `509.9s`）
分段核查 `2ba2583e.jsonl`（F7，133k 思考字符）、`152cb6b5.jsonl`（F1，85k 思考字符）与 `ce01953b.jsonl`（F2，81k 思考字符），定位到两类不同根因：

1. **F7（`820.8s` / `38.2k` thk）：汇总帧“帧内槽位重演”与骨架内联样式冲突 + 收尾退场留白**
   - `STORYBOARD.md`（L31、L224–L227）给 F7 分配了 `fixed-anchor-cycle`，要求在单帧内将 3 个槽位从 `dim → active:coral → done:lime` 重新扫一遍；
   - 但 `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（L1218）已将 F7 的 3 个槽位预置为 `data-state="done"` **并硬写了内联 `style="background:#C4D94E;"`**。模型被迫耗费数万 token 推演如何在 `t=0` 用 GSAP 逆向覆写内联背景色与 `data-state` 而不破坏 `.hf-stage-*` 禁改令。
   - **视觉副作用**：F7 在 `#stage-card` 内塞入 3 个轮播面板，到 Scene 3（`5.71–9.509s`）槽位全部变绿后不知卡片该如何收尾，直接将 `#stage-card` 设为 `autoAlpha: 0`，造成成片结尾（`contact-sheet` 第 9 格 `55.847s`）画面中央整片空白。
2. **F1（`550.3s` / `25.2k` thk）与 F2（`509.9s` / `23.0k` thk）：自建复杂状态机与高成本微装饰推演**
   - **F1 并非共享舞台帧**，其 25k 思考主要花在编写 `tl.to(drive, { onUpdate: masterUpdate })` 逐帧状态机来手动同步 `chromatic-glitch` 三层残影、`__hf.type` 与 `t=0` 预渲染状态；
   - **F2 槽位全程 `dim` 无状态切换**，其 23k 思考主要耗在：① 手工拼装 `<svg><feTurbulence>` 噪点滤镜与 URL 编码 Data-URI 字符串（F7 同样耗费大量思考写该滤镜，而在 `opacity: 0.05` 下肉眼几乎不可见）；② 在 `#stage-card` 中又重复绘制了一排 `01 FUNDAMENTALS | 02 VALUATION | 03 EXIT PLAN` 胶囊，与上方 `.hf-stage-lockup` 语义重叠、反复权衡。

### 3.2 对冲 `3/5` 超时、槽位拥堵与 `effort="low"` 网关未验证风险
1. **为何 `150s` 超时率高达 `3/5`**：
   - 5 份对冲 `.request.json` 证实对冲仍运行在 `output_config.effort="medium"` + `thinking: {"type": "adaptive", "display": "omitted"}`（`MAX_THINKING_TOKENS` 在网关侧被剥离不生效，`effort` 是唯一控制思考深度的旋钮）。
   - 按 `deepseek-v4-pro-0813` 单流 `~50 tok/s` 测算，输出 `2.5k–3k` HTML 正文就需要 `50–60s`，`HEDGE_TIMEOUT=150s` 留给思考的预算不足 `90s`（~4,500 tokens），一旦在 `medium` 档进入中等思考即必然超时。
   - 同时 `DEFAULT_MAX_HEDGES=2` 被 F1/F2 的两个慢对冲占满整整 `150s`，导致 F5/F6/F7 被迫排队到 `305–317s` 才启动对冲（此时 F5 主进程已在 2s 后跑完，F6 主进程已多空烧 160s）。
2. **关键风险核查（`effort="low"` 历史零实测）**：
   - 扫描本地全部 **4,265 份**历史 `/app/log/api-bodies/*.request.json` 发现：`deepseek-v4-pro-0813` 历史上仅发过 `max`（1,525 次）、`high`（2,438 次）、`medium`（241 次），**`effort="low"` 的历史请求数为 `0`**。
   - 虽然 `claude_cli.py`（L943–947）已支持通过 `extra_env["CLAUDE_CODE_EFFORT_LEVEL"] = "low"` 覆盖 profile 默认值，但在未确认千帆网关对 `effort="low"` 的兼容性（是否返回 HTTP 200、是否会退化到破坏 HTML 结构）前，**必须先做单次冒烟验证并保留环境变量一键回退开关**。

### 3.3 `visual_design` Phase A `437.2s` 过思考根因 & Phase B KV Cache 真相
1. **Phase A 连续两次恒定 `~20.6k` 思考的三重原因**：
   - **代码启发式提示误导**：`html_utility/visual.py`（L400–436）`_shared_stage_pairs()` 仅凭单词 `"same"` 就输出 `Shared-stage hints: - F1→F2: scene says "same"`（源于 F2 大纲 *"Same cream stage"*），直接诱发模型在思考尾段反复推敲“F1 单卡问句到底算不算共享舞台”；
   - **边界决策歧义**：模型在概览帧（F2）该标 `dim` 还是 `active`、以及 `top-stepper-center-card` 与 `center-scorecard` 选型之间来回摇摆；
   - **输出体量与档位基线**：`visual.py` 虽已写有双段骨架与 `done` 状态不回退说明，但在 `effort="medium"` 下要求单会话一次性生成 ~5.5KB 的 6 段式创意散文 + Blueprint 分配 + Shared stage spec，模型天然倾向于展开 ~20k tokens 的全局推演（思考:正文比高达 **14:1**）。
2. **Phase B KV Cache 命中率波动真相（已抓包证伪代码前缀问题）**：
   - 逐字节比对本轮 7 份 Phase B `.request.json`，前 **13,695 字符（连同 system 共约 `4,352` tokens）100% 字节级一致**；且 `claude-debug.log` 证实暖机帧 F1 在 `+1.1s` 已收到首包，F2–F7 在 `+6.1s`（5s 暖机窗口后）才收到首包；上一轮 `6b07a9c1` 完全相同的代码 6 帧中有 **5 帧命中 `3840` 缓存**。
   - **结论**：本轮仅 F2 命中 `cache_read=4352`、其余 5 帧为 `0`，完全是因为千帆后端多副本集群无会话粘性路由（F1 仅预热了其中 1 个副本），**`visual.py` 的前缀构造与暖机时序无缺陷，无需无效折腾**。

### 3.4 F7 无关英文胶囊词（`VISION` / `FUTURE` / `FLOW` / `NEXT` / `PACE` / `SIGNAL`）源头
- 全量扫描 `skills/hyperframes-creative/frame-presets/*/FRAME.md` 确认：**仅 `capsule` 预设**（L263，同步 `.claude/skills`）硬编码了：
  > `Floating-pill words are neutral atmospheres ("VISION", "FUTURE", "NEXT"), never content-specific.`
- 在财经/专业题材中渲染此类无关英文词会产生明显模板违和感。

---

## 4. 完整修改建议（按执行顺序与优先级编排）

### Step 0：上线前 1 分钟网关冒烟验证（必做前置检查）

| 验证项 | 验证方式与判定标准 |
|---|---|
| **`deepseek-v4-pro-0813` `effort="low"` 网关兼容性** | 用 `claude_cli.run_claude_result`（传 `extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "low", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "24000"}`，复用 `6b07fd3a` 任一慢帧如 `02` 或 `07` 的 hedge prompt）跑 1 次单帧冒烟：<br>1. 核查 `/app/log/api-bodies/*.request.json` 确认 `output_config.effort == "low"` 且网关返回 HTTP 200（无 400 参数报错）；<br>2. 观察响应 `thinking_tokens` 与耗时（预期 `30–90s`），并确认产出通过 `node scripts/frame-selfcheck.mjs`。<br>- 若通过：P0-2 默认启用 `HEDGE_EFFORT = "low"`，同时可对 `visual_design` 做 `HTMLVIDEO_CLAUDE_VISUAL_DESIGN_EFFORT=low` A/B 验证；<br>- 若网关拒收 `low`：P0-2 保持 `medium`，仅落地 `MAX_HEDGES=3` + `HEDGE_TIMEOUT=180s` + 对冲提示词极简化。 |

---

### P0：消除 `frame_html` 500s–820s 极长尾、对冲超时空烧与收尾留白

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P0-1** | `html_utility/visual.py`<br>`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills`）<br>`test/test_frame_packets.mjs` | **根治汇总帧“槽位重演 vs 骨架内联样式”冲突与收尾留白（兼容任意调色板强调色）**：<br>1. **`visual.py`（L508–543 `_build_design_prompt`、L593–630 `_frame_shared_stage_note`）**：明确规定——汇总/收尾帧（如 `[done, done, done]`）的 `.hf-stage-slots` 保持静态终态，**严禁在单帧内设计将槽位重置为 `dim` 再逐个重演的动画**；回顾与收尾视觉一律放在 `#stage-card` 内完成，且 **`#stage-card` 在收尾 Scene 必须保持可见（展示总结清单/核心结论锁版），严禁淡出为空白**。<br>2. **`frame-packets-core.mjs`（L1210–1260）**：移除 `.hf-stage-slot` 上的内联 `style="background:${fill};"`，改为输出属性 `data-state="${st.status}" data-accent="${st.color \|\| ''}"`；同时在骨架 `<style>` 中基于 `tokens.colors` 生成 `.hf-stage-slot[data-state="dim"] { background: ${c.white}; }`、默认 `active`/`done` 背景色及每个调色板命名的 `.hf-stage-slot[data-accent="<name>"]:not([data-state="dim"]) { background: <hex>; }`。既 100% 保留 `test_frame_packets.mjs` 中 `[done:coral]` / `[done:lime]` / `[active:sky]` 等任意配色能力，又彻底消除内联 `style=` 与 GSAP / `data-state` 切换的冲突。 | 直接消除 F7 类 `820s` / `38k` 思考死结，根治成片结尾中央空白问题。 |
| **P0-2** | `html_utility/frame_html.py`<br>`test/test_claude_cli.py` | **提升对冲速度、并发槽位与超时余量（可配置、可一键回退）**：<br>1. 在 `frame_html.py`（L348–364、L1145–1165、L1217–1223）新增 `HEDGE_EFFORT_ENV = "HTMLVIDEO_FRAME_HTML_HEDGE_EFFORT"`（默认 `"low"`，白名单校验同 `_ALLOWED_EFFORTS`，设 `medium` 可一键回退），在 `mode == "hedge"` 时注入 `extra_env["CLAUDE_CODE_EFFORT_LEVEL"] = hedge_effort`（主进程维持 `medium` 保成片质量上限）。<br>2. 将 `DEFAULT_MAX_HEDGES` 从 `2` 调至 **`3`**，避免前 2 个慢帧占满槽位把后续慢帧（如 F5/F6/F7）卡到 `305s+` 才起对冲。<br>3. 将 `HEDGE_TIMEOUT` 从 `150` 微调至 **`180`**（保持 `DEFAULT_HEDGE_AFTER = 150`、`HEDGE_MAX_OUTPUT = "24000"`），扣除 `2.5k–3k` HTML 流式输出所需的 `50–60s` 后留足思考安全垫。 | 对冲返回时间压回 **`30–75s`**，大幅提升对冲命中率，消除 `3×150s` 超时空烧与对冲排队饥饿。 |
| **P0-3** | `html_utility/frame_html.py` | **砍掉非舞台/常规帧 500s+ 思考黑洞（直击 F1 `550s` 与 F2 `510s`）**：<br>1. 在 `rich_note`（L626–639）补充：当顶部/侧边 `.hf-stage-lockup` 已展示槽位标签时，`#stage-card` 聚焦本帧核心主张或视觉图形，**不要在 `#stage-card` 内再重复画一排相同的槽位胶囊**，且最后一个 Scene 禁止将 `#stage-card` 设为 `autoAlpha: 0`。<br>2. 在 `Hard no-ops`（L700–716）新增两条硬减负令：<br>   - **禁止自建逐帧主循环**：严禁编写 `onUpdate` 状态机或 `gsap.ticker` 手动驱动文字/残影/状态切换，一律使用声明式 `tl.set` / `tl.to` / `tl.fromTo` 与预置 `__hf.type` / `__hf.draw`；<br>   - **禁止手搓内联 SVG 滤镜纹理**：严禁编写 `<feTurbulence>` 噪点滤镜或手拼 URL 编码 SVG Data-URI 背景纹理（背景氛围仅用纯色与轻量 CSS `radial-gradient`）。 | 直接砍掉 F1/F2/F7 在逐帧状态机和 SVG 噪点编码上的上万思考 tokens，将常规主进程墙钟压回 **`180–320s`**。 |

---

### P1：压缩 `visual_design` Phase A 过思考与清理模板视觉噪点

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P1-1** | `html_utility/visual.py`<br>`test/test_visual_driver.py` | **三管齐下压缩 Phase A（`437s` / `20.6k` thk）无谓推敲**：<br>1. **修正启发式误报（L400–436 `_shared_stage_pairs`）**：匹配 `_SAME_STAGE_CUE_RE` 时排除仅指画布底色一致的短语（如 `same cream stage/canvas/field/background`），避免误产 `F1→F2: scene says "same"` 误导模型反复纠结 F1 是否入共享舞台。<br>2. **固化两条零歧义默认规则（L508–545 `_build_design_prompt`）**：① 概览/引入共享舞台帧（尚未聚焦单个具体槽位）一律填 `[dim, dim, ...]`，无需纠结 `active`；② 凡属“共享清单/步骤条 + 逐帧详情内容”结构，默认直接选用 `top-stepper-center-card`，无需与其他布局反复权衡。<br>3. **收紧第 1 节散文篇幅**：明确要求 `Video direction — creative body` 每个加粗子项控制在 **1–2 句精炼规则（总长 ≤ 250 词）**，把输出与思考重心放在结构化契约而非长篇修辞。<br>4. *（可选 A/B）* 若 Step 0 验证 `effort="low"` 表现稳健，可通过环境变量 `HTMLVIDEO_CLAUDE_VISUAL_DESIGN_EFFORT=low` 实测对比 Phase A 耗时与契约质量（无需改代码）。 | 消除启发式误导与布局权衡拉锯，预计将 Phase A 墙钟从 **`437s` 压降至 `200–300s`**（若 `low` 档 A/B 通过可进一步降至 `<150s`）。 |
| **P1-2** | `skills/hyperframes-creative/frame-presets/capsule/FRAME.md`（同步 `.claude/skills`）<br>`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（同步 `.claude/skills`） | **根治 `capsule` 预设无关英文氛围词胶囊（模板源头 + 摘录层双保险）**：<br>1. 将 `capsule/FRAME.md`（L263）中的 `Floating-pill words are neutral atmospheres ("VISION", "FUTURE", "NEXT"), never content-specific.` 改写为：**背景漂浮胶囊仅作为无文字的纯色几何色块装饰（严禁在漂浮背景胶囊内填写 `"VISION"`、`"FUTURE"`、`"NEXT"` 等无关英文氛围词）**。<br>2. 在 `frame-packets-core.mjs` `frameMdExcerpt()` 中同步对该旧句式做替换兜底，确保已初始化的存量项目目录同样生效。 | 100% 消除金融/专业视频边缘出现 `VISION` / `FUTURE` / `FLOW` 等无关词胶囊的违和感。 |

---

### P2：工程卫生清理（低风险顺手项）

| 序号 | 目标文件 | 具体改造内容 | 预期收益 |
|---|---|---|---|
| **P2-1** | `/app/log/claude/plugins/installed_plugins.json` | 将 `plugins` 字段清空为 `{"version": 2, "plugins": {}}`（当前已通过 `--settings` 禁用 `superpowers@claude-plugins-official`），消除每次 `claude --print` 启动读取插件目录的调试日志噪音与 ~100ms IO 开销。 | 保持 `claude-debug.log` 干净，微降 20+ 次 CLI 冷启动开销。 |

---

## 5. 预期综合收益与回归验收标准

落地上述 P0 + P1 改造后，同规模 7 帧（~55–60s）任务的预期表现：

| 指标 | 本次实测 (`6b07fd3a`) | 优化后预期 | 核心驱动项 |
|---|---|---|---|
| `visual` Phase A 墙钟 | `437.2s` (7.3m) | **`200–300s`** (3.3–5.0m)<br>*（`effort=low` A/B 若通过可达 `90–150s`）* | P1-1（修 `_shared_stage_pairs` 误导 + 固化默认规则 + 限散文篇幅） |
| `frame_html` 关键路径 | `826.7s` (13.8m) | **`240–360s`** (4.0–6.0m) | P0-1（解 F7 冲突）+ P0-3（禁状态机/SVG 滤镜）+ P0-2（`low` 对冲兜底） |
| `verify` 总耗时 | `64.6s` (0 轮) | **`60–90s`** (0–1 轮) | 维持首扫即过门禁基线 |
| **端到端总耗时** | **`1585.7s` (26.4m)** | **`720–980s` (12–16m)** | `visual` Phase A 与 `frame_html` 双峰同步削减 |
| **隐性算力空烧** | `~819s`（3 次对冲超时） | **`< 250s`** | P0-2 消除对冲 `150s` 超时空烧与排队饥饿 |
| **成片视觉质量** | F7 结尾中央留白 + 飘 `VISION/FUTURE` | **收尾卡片饱满、无无关模板词、槽位零冲突** | P0-1 + P0-3 + P1-2 |

### 回归验收 Checklist
1. **单测全绿**：`node test/test_frame_packets.mjs`、`node test/test_verify_check_parse.mjs`、`pytest test/test_visual_driver.py test/test_claude_cli.py` 全部通过，且新增断言覆盖 `data-accent` 无内联背景色与 `_shared_stage_pairs` 过滤。
2. **网关冒烟通过**：Step 0 确认 `effort="low"` 返回 HTTP 200 且单帧产出通过 `frame-selfcheck.mjs`。
3. **下一次端到端回归观测点**：
   - `frame-html stage summary` 中 `truncations=0`、`timeouts=0`（对冲不再成批超时），`critical_path_s <= 360s`；
   - `contact-sheet.jpg` 收尾帧 `#stage-card` 内容饱满无留白，全片无 `VISION`/`FUTURE` 类无关胶囊词，无 `— figure —` 占位符。
