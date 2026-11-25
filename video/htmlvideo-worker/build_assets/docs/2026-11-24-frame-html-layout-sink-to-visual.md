# frame-html → visual 布局下沉设计(方案1)

> 设计对象:`aiflow-build-frame-visual` 产出契约 + `frame-packets-core.mjs`(dispatch 并入)+ `html_utility/frame_html.py`(worker prompt 收缩)
> 依据:本次 17点 完整跑(`6b04…` hedge-07)的 frame-html 实测逐 worker thinking 拆解
> 设计日期:2026-11-24
> 状态:**已落地**(L-1~L-6 代码+测试绿)。env `HTMLVIDEO_FRAME_VISUAL_LEDGER` **默认 OFF**,按 §9 先 A/B 再翻默认 ON。是 `2026-11-20-frame-html-visual-optimization-design.md` P2 杠杆#1「吃 P1 紧 dispatch」的续篇——上一版把 `_design.md` 的 palette/type/easing 定「具体」了,但**每帧布局仍是散文**,本次就是把「每帧布局/动效数值」也往前推一层。

## 0. 一句话

frame-html worker 那 17–51K token 的 thinking 里,约有 **1/3 是在把 visual 已经用散文写定的布局意图「翻译成精确坐标/tween 值」**。这部分是「真·决策」,但属于写代码阶段的、高放大率的决策(思考放大 ~5:1,spec 阶段只有 ~1:1)。把它从 frame-html 的 thinking 上移到 visual 的产出,用**可读散文(带具体数值的 layout ledger)**而非裸 YAML 承载,让 frame-html 从「按配方推导」降成「按数值誊写」。

## 1. 背景与基线(17点实测)

整条线 `total 1500.4s`,frame-html **1010.5s = 67%** 是唯一大头;visual 仅 272.7s。

| 阶段 | 每帧 thinking | 每帧耗时(关键路径) | 每帧产出 | 放大率(thinking/产出) |
|---|---|---|---|---|
| visual(7 worker) | 0.9–3.2K token | 30–70s(F7=70s) | ~2KB F*.md | ~1:1 |
| frame-html(7 worker) | **17–51K token** | 377–999s(F7=999s) | 7–12KB HTML | ~3–5:1 |

frame-html 逐 worker 官方 `thinking_tokens`:01=17,494 / 05=34,766 / 02=38,357 / 06=39,184 / 03=41,312 / 04=43,356 / 07=50,875。`duration/thinking ≈ 19.6s/1K`(极稳,无隐藏开销)——**砍一半 thinking = 砍一半时长**。有用输出(output−thinking)仅 3.5–5.2K token(含 Read+Write 的 HTML 本体)。

## 2. 问题定义:frame-html 的 thinking 里有什么

采样 frame-07 的 thinking(开头/25%/50%/75%/结尾)可拆三层:

| 层 | 内容 | 估占比 | 性质 | 处理归属 |
|---|---|---|---|---|
| ① 重算已定全局 token | 字体/配色/缓动,`_design.md` 已定死(hex/cqw/ease)却仍在想 | ~30% | **纯浪费** | 归 frame-html worker 纪律(本方案**不做**)|
| ② 把 F*.md 散文 → 精确坐标/tween | "scorecard 缩成一个 pill"→ 具体 `data-*`、scale、grid 位置 | ~30% | **真·决策,高放大率** | **本方案主体:上移 visual** |
| ③ 在 thinking 里起草整段 CSS/HTML/JS + 自我审查 | `<div class="clip">…`、`.f07-brand-inner: flex column…` | ~40% | 违约(合同明令"代码只在 Write/Edit 参数里") | 分级 cap / skeleton 补全(方案2,**不做**)|

> ①②③ 占比是从 thinking 采样估的,非精确度量;但 ② 的存在和方向是硬的:visual 契约(`visual-design.md` L70/L86/L146)明文「**never write px / scale / shadow recipes (the worker writes those)**」——这是我方的设计决策把「具体值」留到了 frame-html thinking。

**本方案只覆盖 ②。** ① 是 frame-html worker 提示词纪律问题(visual 已经具体、且已 inline 进 dispatch,worker 视而不见);③ 是模型抗拒合同行为。若不讲清楚,会把「方案1 无法单独压到 ~160s 以下」误读为方案1 失败。

## 3. 目标与非目标

**目标**:把「每帧元素的精确布局 + 动效数值」从 frame-html thinking 上移到 visual 产出,**用可读散文承载**;frame-html 关键路径 999s → ~160s(投影),visual 关键路径 70s → ~90–120s(仅 +1–2K thinking)。

**非目标(本方案不做,做进兄弟方案)**:
- ①「worker 用已 inline 的具体全局值、禁重推导」→ frame-html worker 纪律(方案2 前的低成本项)。
- ③「thinking 里不起草代码」→ 分级 cap + skeleton 补全(方案2)。
- 确定性代码生成器(路径 B,见 §5)→ 架构级,单独设计。

## 4. 关键决策

### D1:visual 只下称「值」,不下称「代码」;且产「可读散文」

项目已有硬教训(见 [[htmlvideo-frame-html-visual-p0p1-progress]]):**确定性下沉要产「可读散文」,不是裸 YAML**。所以 visual 的新产出是**逐帧 layout ledger——自然语言 + 表格,带精确数值**,不是结构化 schema。理由:
- visual 是低放大率 spec 写手,写散文顺手;一旦逼它写 YAML/JSON,错误率和 thinking 都会反升。
- frame-html 是誊写方,给「散文里嵌精确值」它最容易照抄;给嵌套 YAML 它反要二次解析。

### D2:新增的 concrete 值 = 场景级元素表 + tween 表,不越界到 CSS/JS

visual 多写两类(见 §6 字段),**不下笔任何 CSS 选择器 / JS 语句 / GSAP API**——那仍是 frame-html 的职责:
1. **元素表**:本帧有哪些 invented 元素,各自的语义 + 位置(命名区/`x%, y%`)+ 尺寸(`cqw`)+ 颜色(直接引用 `_design.md` 的 role)→ 序。
2. **tween 表**:逐场景哪个元素动、from→to 数值、ease(引用 motion grammar 名字)、时长(引用 scene 窗口)、挂哪个 `sceneN` label。

### D3:复用 `_design.md` 已有的「具体」,逐帧只写 delta

`_design.md` 已经把 palette(hex)、type(cqw)、easing(expo.out/power2.inOut)、role-mapping 定成具体值了。逐帧 ledger **只补 delta**:元素位置/尺寸/序 + tween 编排;颜色/字体/缓动**引用**全局 role 名,不逐帧重抄(避免重抄漂移,也避免 ① 的源头)。

### D4:下沉走「值」,不碰代码生成(路径 B 另评)

本方案落地路径 A(§5)。路径 B(确定性从 spec 生成 CSS/HTML/JS)能把 frame-html 模型压到 ~0,但它要求 spec 结构化到机器可解析,与 D1「可读散文」冲突、有回归风险,单独立项。

## 5. 路径权衡(只落地 A)

| 路径 | visual 关键路径 | frame-html 关键路径 | 两阶段合计 | 改动量 | 风险 |
|---|---|---|---|---|---|
| 现状 | 70s | 999s | 1070s | — | — |
| **A(本方案)** visual 出「带数值散文 ledger」,frame-html 誊写 | ~90–120s | ~160s | **~250–280s** | 中 | 低(纯增量,可回滚) |
| B(后续)spec 结构化 + 确定性代码生成 | ~90–120s | ~0–80s | ~150–200s | 大 | 高(换架构) |

选 A 的理由:与 D1 一致、增量可回滚、且先验证「值上移后 frame-html thinking 真塌缩」这个假设;若 A 实测 thinking 仍不降(说明 ② 占比估高,③ 才是大头),再决定要不要上 B 或转投方案2。

## 6. visual 新增产出的字段设计

逐帧 F*.md 在现有 `blueprint / focal / roles / sfx / rules / Scene` 之后,追加一段(load-bearing,gate 校验存在性):

```
## Layout ledger (concrete — transcribe, do not re-derive)

| element | role | place | size | color | order |
|---------|------|-------|------|-------|-------|
| wordmark | focal | center x=50% y=42% | 8.5cqw | violet  | 2     |
| tagline  | supporting | center y=54%       | 1.9cqw | ink    | 3     |
| bg       | background | full-bleed         | —      | cream  | 0     |

## Motion ledger

| scene | element | from → to | ease | dur |
|-------|---------|-----------|------|-----|
| scene1 | wordmark | opacity 0 → 1; y +0.5cqh → 0 | power2.inOut | 1.2s |
| scene2 | scorecard | scale 1.0 → 0.32 | power2.inOut | 1.4s |
```

约束:
- `place`/`size` 给数值或命名区(套用 `visual-design.md` 的 Layout 词汇 + `_design.md` 的 role),**不写 px-precision 的任意值,不写 CSS 属性名**(`opacity 0→1` 是语义,不是 `style` 语句)。
- `color` 只引用 `_design.md` role 名(cream/ink/coral/lime/sky/mint/lavender/violet/yellow/peach/shadow)。
- `ease`/`dur` 引用 motion grammar 与 scene 窗口(scene window 已在 Scene 行,ledger 不复算)。
- 这是给 frame-html 的**誊写源**,不是替代 Scene prose——Scene 行仍保留"what + when + why + rule id",ledger 补"具体多少"。

## 7. 落地项对照

| ID | 内容 | 默认 | 落点 |
|---|---|---|---|
| L-1 | visual worker 追加 `## Layout ledger` + `## Motion ledger` 输出契约(字段见 §6) | OFF → `=1` 激活(`HTMLVIDEO_FRAME_VISUAL_LEDGER`) | `visual.py` `_build_worker_prompt` + `aiflow-build-frame-visual` 文案 |
| L-2 | ledger 存在且非空、color/ease 只含已知 role/grammar 名 → 否则重派 | 随 L-1 | `visual.py` `_diagnose_worker_reply` |
| L-3 | gate 校验逐帧「ledger 缺失/空表」→ ✗(结构校验;白名单留 L-2) | OFF → `=1`(默认 OFF) | `storyboard-gate.mjs` |
| L-4 | dispatch 内联 ledger(随 frame block verbatim,零新代码) | 免费 | 验证测试 `test_frame_packets.mjs` |
| L-5 | frame-html worker prompt:ledger 是誊写源,「照抄数值」 | 常开(无条件,无 ledger 时无害) | `frame_html.py` `_worker_prompt` |
| ~~L-6~~ | 逐帧日志 `ledger_present` 标记(度量回验) | **未做**(A/B 时再补) | — |

## 8. 文件改动

```
html_utility/visual.py                      # L-1 输出契约 + L-2 验收重派 + driver 接线
scripts/storyboard-gate.mjs                 # L-3 gate 校验(ledgerOn + ledgerDataRowCount)
html_utility/frame_html.py                  # L-5 誊写句(无条件)
skills/aiflow-build-frame-visual/references/visual-design.md      # 「never px」→ ledger 例外 + checklist 项
.claude/skills/aiflow-build-frame-visual/references/visual-design.md   # 同步
skills/aiflow-build-frame-visual/SKILL.md                           # fast-path 提及 ledger 表
.claude/skills/aiflow-build-frame-visual/SKILL.md                   # 同步
test/test_visual_driver.py                 # +LedgerValidationTest(6) +LedgerPromptTest(2) +3 gate 测试
test/test_frame_packets.mjs                # +testDispatchInlinesLedger(L-4 验证)
test/test_frame_html_driver.py             # +test_worker_prompt_transcribes_ledger_when_present
docs/2026-11-24-frame-html-layout-sink-to-visual.md  # 本设计文档
```

**未改动**(与初版 §8 的差):`frame-worker-card.md`(L-5 冗余,且卡 8000 字节预算)、`frame-packets-core.mjs`(+`.claude` 同步,L-4 免费)。

## 9. 验证与质量门

- **单元**:`test_visual_driver.py`(ledger 契约/验收重派)、`test_frame_packets.mjs`(dispatch 并入)、`test_frame_html_driver.py`(prompt 含 ledger 段落)。
- **端到端一次完整跑**:比较同 project 前后——frame-html 关键路径 ≤160s(现值 999s),逐帧 `thinking_tokens` 均值 ≤15K(现值 ~38K),visual 关键路径 ≤120s。
- **不回归**:`frame-selfcheck` 逐帧 PASS、assemble 成功、verify/render 不回归、storyboard-gate 整份 PASS、byte-exact 无漂移。
- 先跑 **1 个无 caption-cruft 的简单项目**做 A/B 基线,再批量。

## 10. 风险与回滚

| 风险 | 缓解 |
|---|---|
| ② 占比估高——值上移后 thinking 不降 | A/B 时补 L-6 度量回验;不降则证 ③ 是大头,转投方案2,回滚 |
| visual 写「精确值」反致 thinking 涨、错更贵 | D2:只值不代码、只 delta 不重抄;L-2 校验 role/ease 只许已知名 |
| ledger 与 Scene prose 两处数值漂移 | D3 唯 delta 原则;gate + L-2 校验 role/ease |
| dispatch 变大(多两张表) | ledger 是表、非散文,膨胀可控 |

回滚:`HTMLVIDEO_FRAME_VISUAL_LEDGER=0`(visual 不产 ledger、gate 不要求、frame-html 无誊写句)→ 退回到现在的散文流水。

## 11. 相关文档

- `2026-11-20-frame-html-visual-optimization-design.md` — P1/P2 前序,本方案是 P2 杠杆#1 的续
- `2026-11-22-thinking-reduction-and-visual-rules.md` — V-1..V-4(visual rule 强制)本方案前序
- `2026-11-22-frame-visual-p0p1p2-hardening.md` — visual 硬化前序
- `2026-11-22-frame-html-critical-path-hardening.md` — frame-html 关键路径硬化
- 上方未落地的 ①③ 归:「frame-html worker 纪律(①) + 分级 cap/skeleton 补全(③)」,需另起一篇方案2 设计

## 12. 落地记录(2026-11-24,与 §7 的偏差)

1. **env 默认 OFF,非 ON**。§7 L-1 写「默认 ON」,但 §9 的「先 A/B 再批量」意味着应默认 OFF 激活式落地。改 `HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认 OFF(driver + gate 一致读;`visual.py _env_on(VISUAL_LEDGER_ENV, default=False)`,gate 只认显式 `1/true/on/yes`)。存量 board 与既有测试(编码旧契约)零扰动;A/B 通过后在 pipeline env 翻 `=1`。
2. **L-4 是验证测试,非新增内联代码**。ledger 随 frame block 原样进入 `buildDispatchDocs` 的 `## Assigned storyboard block`,无需 `HTMLVIDEO_FRAME_PACKETS_INLINE_LEDGER` 开关。补了 `testDispatchInlinesLedger` 断言。
3. **L-5 不落 card,只落 `_worker_prompt`**。card(`frame-worker-card.md`)已贴 8000 字节预算(7993B),加词越界;且 L-5 誊写指令已在 `frame_html._worker_prompt` 直接送达 worker(card 是 dispatch 里的第二处内联,冗余)。故 card 原文不动。
4. gate L-3 只做「存在 + 非空表」的结构校验;color/ease 白名单留在 Python `_diagnose_ledger`(fragment 级),gate 不重复(legacy 路径 gate 是唯一 backstop,但白名单属 nice-to-have)。

测试:248 Py unittest + `node test/test_frame_packets.mjs` 全绿。