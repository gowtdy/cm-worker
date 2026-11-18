# thinking cap 落地 A/B:串行阶段齐触地板,frame-html 反被截断崩掉(58.3m)

> 分析对象:2026-11-18 01:03–06:03(UTC,5 小时窗口)内 `claude -p`(`claude --print`)全部调用 —— 恰为一次流水线跑(任务 `6afd1e7426567914`),共 4 次调用
> 会话:identify-intent `89a729b7-…` / storyboard `6cb36a33-…` / frame-visual `4950bc2c-…` / frame-html `543e2eae-63ad-42eb-b283-0c7ad3d03f57`
> 时间:03:38:23(identify-intent)→ 04:50:08(frame-html 报错收尾)
> 视频项目:`/app/videos/2026-11-18/userclm17919893695531772/6afd1e7426567914/`(7 帧)
> 对照基线:`skill-timing.py --group` 全量历史;最接近的一次跑,见 [[build-frame-html-24m-effort-high-analysis]](11-17,会话 `1836d789`)
> 分析日期:2026-11-18
> 关键词:thinking cap、`CLAUDE_CODE_MAX_OUTPUT_TOKENS`、`maxOutputTokens 32k`、fan-out 截断、A/B

## 结论先行

**`CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000` 的 thinking cap 真的落地了**(SDK `result` 里 `maxOutputTokens: 32000`、session 累计 `thinkingTokens: 756,630`),而且对**串行阶段效果极好**:identify-intent 16.7s(历史最优)、storyboard 3.0m(近地板)、frame-visual 7.1m(历史最优),三者齐踩历史最优档。但它把唯一并行 fan-out 阶段 **build-frame-html 打崩了**:32k 输出预算在 worker「先读 ~30K token(_role 32KB + packet 30KB + frame.md 17KB)再 Write」的模式下被提前耗尽,19 个 worker 里 5 个在 3 个工具调用(`role → packet → frame.md`)后即被硬截断、写不出一个 frame;阶段 58.3m(比历史均值 41.5m +40%、比最优 20.4m +186%),且仅 2/7 帧落地、任务失败。

一句话:**cap 是最大杠杆(串行阶段全部兑现),但一刀切 32000 对并行 worker 过紧 —— 需要按阶段分级。**

---

## 一、5 小时窗口总览(4 次调用)

| # | 阶段 | 会话 | 起 | 止 | 耗时 | 工具回合 | 子代理 | 结果 |
|---|---|---|---|---|---|---|---|---|
| 1 | identify-intent | `89a729b7` | 03:38:23 | 03:38:40 | **16.7s** | 1 | 0 | ✓ |
| 2 | build-storyboard | `6cb36a33` | 03:41:44 | 03:44:42 | **3.0m** | 2 | 0 | ✓ |
| 3 | build-frame-visual | `4950bc2c` | 03:44:43 | 03:51:48 | **7.1m** | 25 | 0 | ✓ |
| 4 | build-frame-html | `543e2eae` | 03:51:49 | 04:50:08 | **58.3m** | 41 | 19 | ✗ 2/7 帧 |

合计 wall ≈ 68.4m(其中 frame-html 独占 85%)。窗口内无其他 `claude -p` 调用。

---

## 二、与历史对比

### 2.1 对全量历史(`--group`)

| 阶段 | 本次 | 历史 avg / med / min / max | 判定 |
|---|---|---|---|
| identify-intent | **16.7s** | 29.4s / 27.3s / 16.7s / 48.2s(n=10) | 🟢 平历史最低 |
| build-storyboard | **3.0m** | 3.8m / 3.7m / 2.2m / 5.7m(n=4) | 🟢 快于中位 ~19% |
| build-frame-visual | **7.1m** | 9.6m / 8.9m / 7.1m / 13.6m(n=4) | 🟢 平历史最低 |
| build-frame-html | **58.3m** | 41.5m / 41.2m / 20.4m / 1.1h(n=4) | 🔴 比均值 +40%、失败 |

> 口径提示:`--group` 全量聚合**已包含本次 run**,故三个串行阶段的"历史均值"被本次略拉低,实际提升幅度比表上更大。frame-html 的 n=4 = 1.1h(11-14 崩溃)/ 20.4m(max)/ 24.2m(high)/ 本次 58.3m(thinking cap)。

### 2.2 对最近一次闭环跑(11-17,`1836d789`,唯一变量 = thinking cap)

11-17 那次已具备 effort=high + 契约卡 + P0/P3,唯独没上 cap,是最干净的 A/B 对照:

| 阶段 | 11-17(无 cap) | 本次(+32000 cap) | Δ |
|---|---|---|---|
| identify-intent | 21.1s | 16.7s | **−21%** |
| build-storyboard | 2.2m | 3.0m | +36%(噪声带内,已双写) |
| build-frame-visual | 7.3m | 7.1m | ~0(已 P0 地板) |
| **build-frame-html** | **24.2m(8 帧全成)** | **58.3m(2/7 成)** | **+141% 且失败** |

**净结论**:cap 把 identify-intent 又压低一档,storyboard/visual 维持在地板附近;代价是 frame-html 从「成功但慢(24.2m)」退化成「更慢 + 失败(58.3m,5/7 帧被截断)」。

---

## 三、串行阶段:cap 兑现的证据

**identify-intent 16.7s**(历史 10 次最优):out 仅 1,830 tokens、1 个 thinking block(2,282 chars)、单次 `Write`。ttfb 12.8s 几乎全花在首流式响应上——cap 压的是「总输出」而非首字延迟;免工具模式(单请求 ~4–6s 地板)仍未做,还有 ~10s 空间。

**build-storyboard 3.0m**:out 31,298(历史 5.7m 那次是 88,662)、2 个 thinking block(30,163 chars)、**2 次 Write = 单响应双写 STORYBOARD + SCRIPT** 已生效,共 5 条 assistant 消息。ttfb 2.3m = 文本生成集中在首响应,与「双写」设计一致。out 从 88.7k → 31.3k(-65%)是 cap 的直接体现。

**build-frame-visual 7.1m**(历史最优,与 11-17 的 7.3m 持平):out 83,328、25 工具回合。注意它**仍有一个 +347.7s 的马拉松回合**(03:45:18 一次 Read 后长时间生成)——cap 没在这个阶段完全掐掉 deep-think 尖峰,但 7.1m 是 P0「一次 Write」的地板,cap 的边际贡献在此阶段不显著。

---

## 四、frame-html:cap 把 fan-out 打崩

### 4.1 三轮重派时间线(19 worker)

```
03:54:01–03:54:37  Round 1:spawn 7 worker  ── +1368.7s(22.8m)──► 04:17:25  产出 0 文件
04:17:59–04:18:51  Round 2:spawn 7 worker  ── +1061.3s(17.7m)──► 04:36:33  落 04、06 两帧
04:39:44–04:40:19  Round 3:spawn 5 worker  ── + 533.0s( 8.9m)──► 04:49:12  产出 0(全部被截断)
```

三轮 Agent 等待合计 ≈ 49.4m,加上 orchestrator 轮间思考/Edit(含一个 +110s Edit)≈ 58.3m。SDK `result` 里 `subagent_stats: {spawned:19, completed:19, failed:0, by_type:{frame-html-worker:19}}`——worker 个个「正常完成」,只是输出被截断。

### 4.2 截断的直接证据

worker 日志里 frame-html 的 SDK `result`(该片最终返回):

| 字段 | 值 |
|---|---|
| `maxOutputTokens` | **32,000**(cap 已生效;11-17 及之前是 128,000) |
| `thinkingTokens` | **756,630** |
| `output_tokens` / `num_turns` | 37,029 / 43 |
| `duration_ms` / `total_cost_usd` | 3,499,098(=58.3m)/ 31.55 |

orchestrator 自述结论原文要点:*"all 5 re-dispatched workers returned truncated (3 tool uses each: role → packet → frame.md, no write)... their extended thinking was hard-truncated against the output cap before they ever issued a `Write`."* 即 32k 上限在 worker 读完 3 份大文档(_role ~32KB + packet ~30KB + frame.md ~17KB ≈ 30k+ token 输入)后、还没来得及 `Write` 就被耗尽。

> 附:skill-timing 详情页里 `模型侧 -8857s`(负值)是 fan-out 伪影——Σ(19 个 Agent 的 tool_result − tool_use)= 3.43h 把并行子代理墙钟全部叠加,超出父会话 wall;不要当成「模型侧为负」。frame-html 的 out 258,754 也只统计到 orchestrator 自身消息,不含 worker(与 `modelUsage.outputTokens 799,046` 的差就是这个)。

---

## 五、根因

1. **cap 是全局一刀切,没有按阶段分级**。32000 对串行阶段(单 agent、单响应)是「压思考」的正约束;对 frame-html 的 19 个并行 worker 却是「判死刑」——每个 worker 先读 30k+ token 输入,输出预算在读到一半就烧完,`Write` 永远排不上。
2. **worker 输入过重是放大器**。`_role` + `packet` + `frame.md` 三文档并读 ≈ 30k+ token,这是 [[build-frame-html-24m-effort-high-analysis]] 里「骨架先行」纪律要解决的同一根因——此前 128k 上限放它跑完(代价是 16m/回合的马拉松思考),现在 32k 上限让它直接「读完就死」。
3. **128k → 32k 是「用截断换掉马拉松」,但换错了地方**:本来想封的是思考马拉松,实际封掉的是输出落地。真正的答案既不是无限 128k,也不是全局 32k,而是「分级预算 + 轻量输入」两者一起。

## 六、下一步(优先级)

1. **cap 按阶段分级**:串行阶段(identify-intent / storyboard / visualize)保留 32000;`frame-html-worker` 单独放开输出预算(至少恢复到 128k,或折中 ~64k),避免 truncation。落点仍是 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`,改为在 run_claude 里按阶段注入不同值。
2. **给 worker 减负(更优先、治本)**:把 `_role`/`packet`/`frame.md` 三文档并读收敛为**单个合并 packet + frame.md**(或先骨架后 detail),让 worker 在第一个回合就能 `Write`——这样即便 32k 也能活。这与 [[htmlvideo-subagent-effort-cap]] 的「骨架先行」纪律同源。
3. **重派 5 个断帧**:`01-hook / 02-thesis / 03-buy-business / 05-sell-rule / 07-cta`,用第 1 或第 2 条改造后的 worker 环境补跑。

## 实施记录

- 2026-11-18:thinking cap(`CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000`)上线后首个「串行 + 并行」完整流水线 A/B。结论:串行阶段全兑现(identify-intent 21.1→16.7s、storyboard/visual 维持地板),但 frame-html 从 24.2m/8 帧全成退化成 58.3m/2 帧 + 5 帧截断,失败模式 = worker 输出预算在写出文件前被 32k 上限耗尽。下一步 = 分级 cap + worker 输入减负。