# `6b096712c5efbc8f` 全流程复盘、上轮结论审计与完整执行方案（2026-11-27）

> **关联任务**：`6b096712c5efbc8f`（意图任务 `6b08f93e617a8674`；2026-11-27 11:18:42–11:39:13 UTC；**8 帧 / 目标 `60s` / `verify` 第 3 轮后失败未出片**；项目目录 `/app/videos/2026-11-27/userclm17919893695531772/6b096712c5efbc8f`）  
> **同题对照基线**：`6b0941c6196ef01f`（同日 08:39，9 帧 / `59.1s` 成片，详见 `docs/2026-11-27-6b0941c6-pipeline-review-and-optimization-plan.md`）、`6b08f96ccf960c67`（同日 03:30，9 帧 / `61.1s` 成片，详见 `docs/2026-11-27-6b08f96c-pipeline-review-and-optimization-plan.md`）  
> **核验依据**：`log/htmlvideo_worker.log`（L2250–L3171）、27 份 Claude Code 会话 JSONL、31 份 `/app/log/api-bodies/*.request.json` 与 26 份 `.response.json`、`STORYBOARD.md`、全量 8 份帧 HTML、`node /app/scripts/verify-report.mjs` 实测重放及 `verify.py` / `verify-check-parse.mjs` / `known-failures.mjs` / `frame_html.py` / `frame-selfcheck.mjs` 源码交叉审计。

---

## 1. 对初版分析结论的逐项 Review（准确性审计与关键纠偏）

经源码、真实渲染几何及历史改造约束（特别是 `6b08f96c` S1-3 / P0-3“印章防消音”）交叉复核，初版分析中**现象还原与耗时/Token 账本准确**，但**存在 1 项方向性误判与 3 项工程细节偏差**，必须在落地方案前予以纠正：

| 审计维度 | 初版结论判定 | 复核结论与精确纠偏说明 |
|---|---|---|
| **1. F7 `content_overlap` 的定性与修复方向** | **❌ 方向有误（重大纠偏）** | **初版误判**：认为 `#f7-sell-pill` 压在 `span.f7-verdict-word` 上属于合法 `designed_overlay`，建议放宽 `verify.py` `_detect_and_revert_blanket_allow()` 放行 `designed_overlay` 贴标。<br>**复核真相**：检查 `07-the-sell-signal.html`（L38–41, L89–92, L150）可知，`.f7-verdict-stamp` 仅 `101×82px`，不透明珊瑚红胶囊 `#f7-sell-pill`（`"SELL"`）以 `top: -1.1cqh; right: -0.9cqw` 直接盖在全程未淡出（`opacity: 1`）的黑色衬线底字 `"Sell"` 正上方，遮挡约 80% 面积且底部/左侧露出半截残缺笔画。**这是典型的“印章压字 / 叠字重影”真实视觉缺陷**，`verify.py` 防消音护栏（S1-3）拦截纯贴标完全正确！若按初版建议放宽 `verify.py`，会直接导致 P0-3 视觉质量回退。<br>**真正根因**：**上游分诊与防消音守卫契约断裂**——安全可贴标的叠加（`autofix: true`）已在调模型前由 `verify_overlap_autofix.py` 自动写盘清零；**凡流到模型的 `content_overlap`（`autofix: false`）在架构上均严禁仅贴 `data-layout-allow-overlap`**。但 `verify-check-parse.mjs` 将命中弱装饰词 `"pill"` 的碰撞仍归入 `kind: "designed_overlay"`（`autofix: false`），导致 `known-failures.mjs` L610 指令模型去贴 `data-layout-allow-overlap="true"`（必被 `verify.py` L631 回滚），形成 100% 必现死循环。 |
| **2. F8 Lint 规则码与 P0-2 时序错位** | **⚠️ 规则名笔误 + 漏检时序根因** | ① **规则名修正**：Round 1 `08-check-before-you-guess.html` `#cursor` 触发的真实 lint 错误码为 **`gsap_non_transform_motion`**（`hyperframes/packages/lint/src/rules/gsap.ts` L1976），初版误写为 `gsap_layout_property_motion`；其短路 `hyperframes check` 浏览器采样的结论属实（Round 1 扫描仅 `9s`，Round 2 lint 通过后完整扫描 `27s`）。<br>② **新增关键根因（P0-2 时序错位）**：11:11 上线的 P0-2 同时在 `frame-selfcheck.mjs` 加了 `gsap_css_transform_conflict` 拦截、在 `known-failures.mjs` 加了纯 `scale` CSS transform 自动剥离；但 **`frame-selfcheck` 在 `frame_html` 阶段先跑，而 `known-failures` 在 `verify --phase prepare` 后跑**——导致 `05-step-3-timing/hedge`（`79.5s`）写的 `.volume-bar { transform: scaleY(0) }` 直接被 `selfcheck` 枪毙，根本走不到 `verify prepare` 自动剥离，白白多等主进程 `285s`！ |
| **3. `frame_html` 对冲帧低级错误的自动修复落点** | **⚠️ 落点需修正** | **初版粗糙点**：建议“在 `selfcheck` 阶段做轻量自动修复”。`frame-selfcheck.mjs` 被 `frame_html.py`、`verify-report.mjs` 及测试套件多处复用，必须保持**纯只读无副作用**。<br>**正确落点**：在 `frame_html.py` `_extract_and_write_frame_html()` **写盘前、`selfcheck` 前**执行纯函数确定性归一化（重写选择器上下文中的 `#[0-9]` / `.[0-9]` 为属性选择器 + 剥离已被 GSAP 接管的纯 `scale` CSS `transform`）。 |
| **4. 跨轮耗时/成本对比与 KV Cache 归因** | **⚠️ 需补帧数归一化与后端归因** | ① **帧数归一化**：本次为 **8 帧**，前两轮为 **9 帧**。按帧均计，`visual` Phase B 为 `74.5s/帧`（`4,324` out/帧），较上轮 `72.7s/帧`（`3,608` out/帧）基本持平略增，阶段墙钟缩短（`127.9s` vs `163.4s`）主要得益于少 1 帧及最慢帧从 `157.3s` 降至 `120.7s`。<br>② **Hedge `cache_read=0` 归因**：核查 `frame_html.py` L1292 确认 `text_out=True` 下所有 hedge 均保持 `cli_cwd = project_dir`，与命中缓存的 `06/08` hedge（`cr=6,656/6,144`）共享同一前缀；`04/05/07` hedge 的 `cr=0` 系后端多实例负载均衡路由至冷副本所致，**客户端前缀无缺陷，无需改动**。 |
| **5. 全片其余帧健康度与日志总账** | **✅ 完全准确** | 实跑 `verify-report.mjs` 确认：全片 `frame-selfcheck 8/8 PASS`、`transitions 6/6 verified`、`lint errors=0`，`check` 全场仅 F7 该 1 条 `content_overlap` 错误；26 份响应体 Token 总账与 5 个被取消进程空烧时长（`746s`）核对无误。 |

---

## 2. 11:11 上轮改造（P0-1 ~ C-5）线上回归核验

| 上轮改造项（11:11 上线） | 线上实证结果 | 关键证据 |
|---|---|---|
| **P0-1 `__hf.draw()` + `non-scaling-stroke` CTM 缩放修复** | **生效** | 全量帧运行时均已注入 `getScreenCTM()` 轴向缩放补偿（如 `07-the-sell-signal.html` L125–128），无半截断框。 |
| **P0-2 `type_caret_before_text` + `gsap_css_transform_conflict`** | **部分生效（存在时序错位）** | 打字光标顺序全片 0 违规；`frame-selfcheck` 拦截了 `05/hedge` 的 `.volume-bar` `scaleY(0)`，但因自动剥离仅放在下游 `verify prepare`，反而使 `05/hedge`（`79.5s`）被误杀、被迫等待 `05#1`（`364.3s`）。 |
| **P1-1 `frame-worker-card.md` 解锁 Bodoni 大小写与静态 `rotate`** | **生效** | `01-the-question#1` 耗时从上轮 `551.8s`（`26.6k` thk）暴降至 **`78.7s`**（`2,260` thk，**-85.7%**）！ |
| **P1-2 `storyboard` 与 Shared Stage 契约对齐** | **分镜生效，但引发 `visual_design` 思考尖峰** | `storyboard` 耗时降至 **`50.3s`**（`1,176` thk）；但 F7“绿转红”使 `visual_design` 花费 `68k` 字符反复论证是否违反 `"NEVER reset completed slots back to dim"`，耗时翻倍至 `303.8s`。 |
| **P2-1 `DEFAULT_MAX_HEDGES = 6`** | **显著生效** | 7 个慢帧在 `150s` 线零排队触发对冲，6/8 帧在 `216s` 内全部收工，被取消进程空烧时长从 `1,173s` 降至 **`746s`**（**-36.4%**）。 |
| **C-1 ~ C-5 清除 `Selected blueprint` 与 `text_out` 诱导语** | **生效** | 0 次非法工具调用尝试，0 次 `negative-list-orbit/spin` 幽灵冲突。 |

---

## 3. `6b096712c5efbc8f` 全链路效果、耗时与成本精确复盘

### 3.1 阶段耗时与 Token 总账（对比同日同题两轮 9 帧基线）

| 阶段 | 本次 `6b096712` (8帧) | 上轮 `6b0941c6` (9帧) | 基线 `6b08f96c` (9帧) | 本次显性 Input / CacheRead / Output / Thinking (占比) | 是否符合预期 |
|---|---:|---:|---:|---|---|
| `init` | `3.6s` | `3.6s` | `3.9s` | - | **符合** |
| `storyboard` | **`50.3s`** | `74.6s` | `114.1s` | `5,679` / `0` / `2,702` / `1,176` (43.5%) | **优于预期**（较上轮 `-32.6%`） |
| `tts`（与 Phase A 并行） | `90.4s` | `102.9s` | `103.9s` | 完全掩盖在 `visual` Phase A 内 | **符合** |
| `visual` Phase A (`design`) | **`303.8s`** | `150.4s` | `143.6s` | `4,547` / `0` / **`19,057`** / **`18,375`** (96.4%) | **严重超预期（+102%）** |
| `visual` Phase B | **`127.9s`** | `163.4s` | `227.0s` | 8 帧：`17,358` / `23,808` / `34,593` / `31,384` (90.7%) | **基本符合**（帧均 `74.5s` vs `72.7s`） |
| `packets` + `captions` | `0.3s` | `0.4s` | `0.4s` | - | **符合** |
| `frame_html` | **`554.2s`** | `654.1s` | `672.3s` | 10 落盘：`78,988` / `39,936` / `110,998` / `74,120` (66.8%) | **改善但未达理想态**（被 F7 `548.2s` 单点卡死） |
| `verify` | **`190.0s` (FAIL)** | `101.0s` | `77.1s` | 3 轮 6 请求：`18,813` / `32,768` / `5,791` / `5,071` (87.6%) | **不及预期**（规则冲突死循环失败） |
| `render` + `sync` | 未执行 | `45.7s` | `46.9s` | - | — |
| **总计（显性 26 响应）** | **`1231.0s` (20.5m)** | `1191.0s` (19.9m) | `1283.3s` (21.4m) | **`in=125,385`, `cr=96,512`, `out=173,141`, `thk=130,126` (75.2%)** | **整体未达预期** |

### 3.2 `frame_html` 逐帧竞速与算力拆解（揭示 `9.2m → 3.8m` 的确定性压缩空间）

| 帧 ID | 主进程耗时 / Out (Thk) | 对冲进程耗时 / Out (Thk) | 最终胜出方与原因 |
|---|---|---|---|
| `01-the-question` | **`78.7s`** / `4,795` (`2,260`) | 未触发（`<150s`） | **主进程胜**（P1-1 生效，较上轮 F1 `551.8s` 快 7 倍） |
| `03-step-1-value` | **`175.2s`** / `11,212` (`7,354`) | `22.0s` 被取消 | **主进程胜** |
| `02-the-three-signals` | **`201.5s`** / `12,672` (`8,949`) | `48.0s` 被取消 | **主进程胜** |
| `04-step-2-quality` | `205.0s` 被取消 | **`49.6s`** / `3,804` (`0`) | **Hedge 胜**（`150 + 49.6 ≈ 200s` 收工） |
| `08-check-before-you-guess` | `216.0s` 被取消 | **`38.9s`** / `2,813` (`53`) | **Hedge 胜**（但漏出 `#cursor` `left/top`，触发 `verify` R1） |
| `06-the-buy-signal` | `255.0s` 被取消 | **`99.8s`** / `6,945` (`3,607`) | **Hedge 胜**（`150 + 99.8 ≈ 250s` 收工） |
| `05-step-3-timing` | **`364.3s`** / `23,115` (`19,223`) | `79.5s` / `5,416` (`1,939`) **被拒** | Hedge 因 `.volume-bar { transform: scaleY(0) }` 被 `selfcheck` 拒收，**多等 `135s`** |
| `07-the-sell-signal` | **`548.2s`** / `35,218` (`30,591`) | `65.5s` / `5,008` (`144`) **被拒** | Hedge 因 `.07-sell-spread` 数字开头类名被 `selfcheck` 拒收，**多等 `333s`（全场关键路径）** |

> **关键算力洞察**：
> 1. **隐性空烧**：5 个被中途 `kill` 的进程（`22s + 48s + 205s + 216s + 255s = 746s`）约产生 `40k–42k` 未计入响应体的流式 output tokens（较上轮 `1,173s` 已降 `36.4%`）；外加 2 个被 `selfcheck` 拒收的 hedge 沉没 `10,424` output tokens。
> 2. **长尾集中度**：仅 `visual_design`（`18.4k` thk）+ `07#1`（`30.6k` thk）+ `05#1`（`19.2k` thk）**3 个请求就占了全流程 `52.4%` 的思考开销（`68.2k / 130.1k`）**。只要在写盘阶段用纯确定性规则救活 `05/hedge` 与 `07/hedge`，`frame_html` 墙钟即可从 **`554.2s` 直降至 `~250s`**，并砍掉 `05#1`/`07#1` 后半程约 `30k+` 流式 tokens！

---

## 4. 完整执行方案（P0 / P1 / P2 落地规范）

> **策略红线**：严格维持全链路各阶段 `effort` 档位与 `max_output` / `MAX_THINKING_TOKENS` 上限（`storyboard=high/16000`、`visual_design=medium/24000`、`visual=medium/20000`、`frame_html=medium/48000`、`hedge=low/23999`、`verify=medium/32000`）不变。

### P0-1：根治 Verify 死循环——对齐分诊分类、修复 Prompt 与防消音反馈闭环（最高优 / 阻断修复）

- **目标文件**：
  1. `scripts/lib/verify-check-parse.mjs`
  2. `scripts/known-failures.mjs`
  3. `html_utility/verify.py`
  4. `test/test_verify_check_parse.mjs`、`test/test_verify.py`
- **精确修改内容**：
  1. **在 `verify-check-parse.mjs` 建立严格不变量：`kind === "designed_overlay"` 仅限 `autofix === true`**：
     - 修改 `triageContentOverlap(item)`（L378–400）：
       - **双侧装饰词碰撞**（L378，`selDecor.length && conDecor.length`，如 `.pill` × `.badge`）：改为返回 `verdict("ambiguous", 'decorative tokens on both sides (...)')`；
       - **`stamp` / `seal` 压多词文本**（L394，`otherIsText && wordCount >= 3`）：改为返回 `verdict("real_collision", 'stamp/seal over multi-word text (...)')`；
       - **弱装饰词碰撞**（L399，`pill` / `badge` / `tag` / `chip` / `dot` 等不在 `AUTOFIX_DECOR_TOKENS` 的词）：若另一侧命中 `TEXT_TOKENS`（如本次 `span.f7-verdict-word` × `#f7-sell-pill`），返回 `verdict("real_collision", 'badge/pill overlaps text block (...)')`；否则返回 `verdict("ambiguous", 'weak decorative token "..."')`。
     - **效果**：所有 `autofix: true` 的真装饰叠加仍由 `verify_overlap_autofix.py` 零模型开销自动打标；**任何流到模型的 `content_overlap` 100% 落入 `real_collision` 或 `ambiguous`，绝不再触发 `known-failures.mjs` 的 `add data-layout-allow-overlap="true"` 指令**。
  2. **在 `known-failures.mjs` 补齐“印章/胶囊压底字”的具体 CSS/GSAP 修复指引**：
     - 在 `id: "content-overlap"`（L611–612）的 `real_collision` 指引中补充：`if a .pill / .badge / .stamp overlaps an underlying word/headline (e.g. verdict pill over verdict word), either anchor the pill on the outer card corner/flex row away from the text OR fade the underlying text out (`tl.to(wordSel, { autoAlpha: 0, duration: 0.2 }, label)`) when the pill enters — NEVER add data-layout-allow-overlap`。
  3. **在 `verify.py` 消除回滚后的空转重扫，并向下一轮注入防消音拒绝提示**：
     - 修改 `_apply_model_fix()` 与 `_require_changed_and_rescan()`（L829–941）：
       - 当 `_detect_and_revert_blanket_allow()` 触发回滚且 `after_digest == before_digest` 时，**跳过无意义的 `_run_verify()` 调用**（文件已完全还原，省 `~24s/轮`），直接复用本轮 `scan_output` 进入下一轮；
       - 在 `verify_frame()` 循环中维护 `reverted_files: set[str]`，当下一轮构建修复 Prompt（`_build_fix_prompt*`）命中曾被回滚的文件时，在 Prompt 顶部插入醒目硬约束块：
         ```text
         [PREVIOUS EDIT REJECTED BY ANTI-SILENCING GUARD] Your prior attempt added `data-layout-allow-overlap` / `data-layout-allow-occlusion` without fixing `<style>` or `<script>`, and was automatically reverted. You MUST modify CSS positioning/spacing or GSAP timeline (`autoAlpha: 0` fade-out on covered text) — adding `data-layout-allow-*` alone is blocked.
         ```

---

### P0-2：前置 `frame_html` 写盘确定性归一化 + 补齐 `gsap_non_transform_motion` 静态门禁（预计省 `~330s` `frame_html` + `~99s` `verify`）

- **目标文件**：
  1. `html_utility/frame_html.py`
  2. `scripts/frame-selfcheck.mjs`
  3. `test/test_frame_html_driver.py`、`test/test_frame_packets.mjs`
- **精确修改内容**：
  1. **`frame_html.py` 新增 `_normalize_frame_html(html: str) -> tuple[str, list[str]]` 并在写盘时调用**：
     - 在 `_extract_and_write_frame_html(frame_path, text)`（L484，以及非 `text_out` 模式下 `run_attempt` 调用 `_run_frame_selfcheck` 之前）对落盘 HTML 执行两项零副作用、幂等的确定性清洗（记录一条 `frame-html normalized <fid>: ...` 日志）：
       - **规则 A（数字开头选择器重写）**：仅在 `<style>` 规则选择器与 `<script>` 字符串字面量（跳过注释、`#` 十六进制色值如 `#1E1E1E`、`getElementById(...)`、URL 与小数）中，匹配 `frame-selfcheck.mjs` 同款模式 `([#.])([0-9]{1,2}[-_][A-Za-z][A-Za-z0-9_-]*)`：
         - CSS 块内：`#07-foo` → `[id="07-foo"]`，`.07-foo` → `[class~="07-foo"]`；
         - JS 字符串字面量内（当整个选择器或片段含 `#07-foo` / `.07-foo` 且引号类型不冲突时）：转为 `[id="07-foo"]` / `[class~="07-foo"]`（单双引号自适应，避免破坏外层 JS 字符串语法，转换后可通过 `frame-selfcheck` 内置 `vm.Script` 语法校验兜底）。
       - **规则 B（前置剥离冗余纯 `scale` CSS `transform`，修复 P0-2 时序错位）**：完整移植 `known-failures.mjs` L352–395 的 `stripRedundantCssScaleTransforms` 判定——当 `<style>` 中简单选择器 `#id`/`.class` 含纯 `transform: scale|scaleX|scaleY(...)`（不含 `translate/rotate/skew/matrix`），且 `<script>` 中 `tl/gsap.(set|to|fromTo)` 已控制同一选择器的 `scale/scaleX/scaleY` 时，在 `frame-selfcheck` 执行前剥离该 CSS `transform` 声明。
  2. **`frame-selfcheck.mjs` 新增 `gsap_non_transform_motion` 静态门禁**：
     - 对齐 `hyperframes/packages/lint/src/rules/gsap.ts`（L1866–1934）：在 `frame-selfcheck.mjs` 内联 `<script>` 扫描段中（豁免 `<canvas layoutsubtree>` 内部元素及瞬时 `.set()`），检查 `tl/timeline/gsap.(to|from|fromTo)` 的 `vars` 对象键名（对字符串字面量与注释做掩码，避免字符串内容误报）：
       - 若含位移布局属性 `left` / `right` / `top` / `bottom` / `margin` / `marginLeft` / `marginRight` / `marginTop` / `marginBottom`、字排重排属性 `fontSize` / `letterSpacing` / `wordSpacing` 或 `roundProps`，立即输出 `[FAIL] ... (gsap_non_transform_motion)` 并提示改用 `x` / `y` / `scale`。
     - 同步在 `frame_html.py` `_hedge_suffix()`（L1058）追加短护栏：`(5) never tween left/top/right/bottom/fontSize in tl.to/from/fromTo — use x/y/scale only`。

---

### P1-1：消除 `visual_design` 5.1 分钟思考尖峰与印章叠字源头诱导

- **目标文件**：
  1. `html_utility/visual.py`
  2. `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（字节级同步 `.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`）
  3. `test/test_visual_driver.py`、`test/test_frame_packets.mjs`
- **精确修改内容**：
  1. **`visual.py` `_build_design_prompt()` 消除“绿转红 vs 禁止重置 `dim`”语义歧义**：
     - 在 L528–530 与 L560–562 补充明确豁免说明：`keep completed slots in done:<accent> on later frames instead of resetting to dim (re-tinting completed slots to done:<warning_accent> e.g. coral on a reversal/sell frame is valid and NOT a dim reset — decide immediately)`，彻底切断模型耗费 `18.4k` thinking tokens 反复自证合规的思考回路。
  2. **`frame-worker-card.md` L37 强化印章/胶囊不压同词底字（严守 `< 8000 B` 预算）**：
     - 将 L37 精炼更新为：`Fade outgoing morph cards to autoAlpha: 0 (no 0.3 ghost); when a stamp/seal/verdict-pill lands over text, either place it on the card corner clear of text OR fade the underlying word/sub-line to autoAlpha: 0 (never stack duplicate words).`（保持文件总长 `< 7980 B` 且双副本字节一致）。

---

## 5. 单元测试与 62 份历史帧 / 故障样本离线回放实测结果（2026-11-27 已落地全绿）

1. **`6b096712c5efbc8f` 真实故障样本回放（全部实测通过）**：
   - **Verify 分诊与 Prompt 回放**：运行 `node /app/scripts/verify-report.mjs --videodir /app/videos/2026-11-27/userclm17919893695531772/6b096712c5efbc8f`，确认 F7 `span.f7-verdict-word overlaps #f7-sell-pill` 分诊为 `real_collision×1`（`designed_overlay=[]`、`autofix_ok=false`），`── actionable summary ──` 明确要求重定位胶囊或用 `tl.to(..., { autoAlpha: 0 })` 淡出底字，**彻底消除 `add data-layout-allow-overlap="true"` 冲突指令**。
   - **`frame_html` 归一化对冲挽救回放**：直接从 Claude Code 转录提取被拒收的 `07-the-sell-signal/hedge` 与 `05-step-3-timing/hedge` 原始 `<template>`：
     - `07-the-sell-signal/hedge`：`_normalize_frame_html()` 重写 `digit-led-selectors=39`，`frame-selfcheck` 由 `rc=1` 转为 **`rc=0 (PASS 07-the-sell-signal)`**；
     - `05-step-3-timing/hedge`：`_normalize_frame_html()` 剥离 `css-scale-strip=1`，`frame-selfcheck` 由 `rc=1` 转为 **`rc=0 (PASS 05-step-3-timing)`**。
   - **62 份历史帧全量扫描（`2026-11-26` + `2026-11-27`）**：`gsap_non_transform_motion` 对全部 62 份历史合法帧 **0 误报**；对还原的 `tl.to("#cursor", { left: "51.5cqw", top: "43cqh" })` 精准拦截，对 `tl.set` + `tl.to({ x, y })` 放行。
2. **全量单元测试套件（全部 PASS）**：
   - `node /app/test/test_verify_check_parse.mjs`：**PASS**；
   - `node /app/test/test_frame_packets.mjs`：**PASS**（含 `gsap_non_transform_motion` 正反例、`frame-worker-card.md` `7922 B < 8000 B` 及双副本字节一致）；
   - `python3 -m unittest /app/test/test_verify.py /app/test/test_visual_driver.py /app/test/test_claude_cli.py /app/test/test_storyboard_contract.py` + `test_normalize_frame_html_rewrites_digit_selectors_and_strips_pure_scale`：**全绿**。

---

## 6. 预期上线收益估算（同规模 8–9 帧任务）

| 指标 | 本次 `6b096712` 实测 | 方案落地后预期 | 核心驱动项 |
|---|---:|---:|---|
| **Verify 通过率** | 失败（3 轮死循环） | **1 轮直过或 1 次有效修复 PASS** | P0-1 消除指令与防消音守卫自相矛盾 |
| **`visual_design` 耗时 / Thinking** | `303.8s` / `18.4k` | **`120–150s` / `6–7k`** | P1-1 消除 `done:coral` 语义反转思考死锁 |
| **`frame_html` 阶段墙钟** | `554.2s` (`9.2m`) | **`220–280s` (`3.7–4.7m`)** | P0-2 前置清洗直接救活 `65–80s` 完赛的 hedge |
| **`verify` 阶段墙钟** | `190.0s` (FAIL) | **`35–60s` (PASS)** | P0-2 前置拦截 `left/top` + P0-1 消除回滚空转重扫 |
| **全流程端到端总耗时（含 render）** | 未完赛（折算 `~21.2m`） | **`11.5–14.0m`** | 三阶段长尾同步削峰（累计省 **`~450–550s`**） |
| **显性 + 隐性 Output Tokens** | `173k` + 约 `41k` 空烧 | **`115k–135k` + `<20k` 空烧** | 提前由合格 hedge 终止 `30k+` 思考马拉松主进程 |
