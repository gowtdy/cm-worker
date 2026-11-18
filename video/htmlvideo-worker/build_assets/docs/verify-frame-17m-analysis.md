# verify-frame 单次调用 17.0 分钟耗时分析报告(P3/P4 落地后首个闭环数据)

> 分析对象:`/aiflow-verify-frame` 的 `claude` 会话(流水线 step 7)
> 会话:`c25e6fb1-81fb-4105-b5db-37c1093a65ce`
> 时间:2026-11-17 02:27:34 → 02:44:37(总耗时 **17.0 分钟**;72 个含工具的 assistant 回合、72 次工具调用、0 子代理)
> 视频项目:`/app/videos/2026-11-16/userclm17919893695531772/6afb004009d47552/`(8 帧,6 转场)
> 对照基线:`verify-frame-41m-analysis.md`(2026-11-14,P0–P5 优化落地于此后的 11-14/15)
> 分析日期:2026-11-17

## 结论先行

**方向全对,速度比预期慢 ~40%。** 41.5m 事故后落地的所有机制本次全部生效且行为正确(guard→inject→verify、verify-report 门禁、机械 playbook、verify-pixels 免 vision、cwd 零失败、零 rabbit-hole),但 17.0m 超出 P0+P2+P3 预期的 10–12m。超出部分 100% 可归因于三件事:一次全新环境故障的诊断(4.5m,已入记忆不会复发)、两次 adaptive deep-think 尖峰(3.2m,thinking cap 未做)、以及 72 个回合零批量(新 CLI 下代价已低,~1m 级,不再痛)。

## 一、总账:钱花在哪了

| 桶 | 数值 | 占比 |
|---|---|---|
| 模型侧(思考 + TTFB + 流式,72 回合) | **~12.9m** | **76%** |
| 工具执行(72 次,含 check/lint/snapshot/report) | ~4.1m | 24% |
| 回合开销结构 | 中位 ~4s、<10s 占多数;尖峰 101s、94s、74s、55s | — |
| 令牌 | input 789K、output 186K、cache_read 7.64M(全上下文每回合重发) | — |

**与 41.5m 事故对比:模型/工具比 73%/27% → 76%/24% 几乎没变——模型侧仍是大头,但绝对耗时腰斩(30.4m → 12.9m)。回合单价从均值 23.3s 降到 ~10s(中位 4s),缓存+快速首字节已把"一次一问"的税率压到很低。**

## 二、时间线分解

| 阶段 | 时间窗 | 耗时 | 内容 | 对照预期 |
|---|---|---|---|---|
| 1. 装载+preflight+转场守卫 | 02:27:34–02:28:35 | ~1.0m | preflight OK(8/8 帧、index 已装配);transitions guard 报无注入 → **inject 一次 → verify exit 0**(6 转场) | ✓ P2+幂等 inject 生效,不再有"未装配"侦查 |
| 2. #3 字体修复 | 02:28:35–02:30:43 | ~2.1m | `font_family_without_font_face` ×9:grep 定位 + 8 帧读 + 9 处 Edit,补 pipeline 标准 @import | ✓ 机械 playbook,#3 预算 3–5m 内 |
| 3. report 复跑 | 02:30:43–02:31:38 | ~0.9m | verify-report(35s,grep 内容增多) | ✓ 单回合复跑 |
| 4. **check 环境诊断** | 02:31:38–02:36:09 | **~4.5m** | `npm run check`(钉版 `npx --yes hyperframes@0.7.76`)下载的 registry 副本在本机静默 exit 1(连 `--version` 都不行);16 回合逐个排除(doctor/json/--version/node 版本/lint/读 verify-report 源码)→ 定位:可用的是本地 symlink `/app/hyperframes/packages/cli`(plain `npx hyperframes check .`) | ⚠ **全新环境故障,playbook 外**;结论已进 summary+自写 memory,下次应 ~0 |
| 5. #4 overlap + 对比度修复 | 02:36:09–02:41:21 | ~5.2m | check findings 分 3 片读入 → 帧 02 三层叠字判定为设计分层 → `data-layout-allow-overlap` ×18;帧 01 pill 文字/帧 03 "Price first" sticker 对比度 4 处 Edit;11+11 处 Edit(每处一个回合) | ✓ 归因正确走 #4;含 2 次深思考尖峰(101s/94s),比纯机械版多 ~3m |
| 6. 终态门禁+视觉 | 02:41:21–02:43:31 | ~2.1m | verify-report(62.6s)→ **GATE PASS exit 0**;midpoints+snapshot(25s);**verify-pixels 10/10 PASS 0 FAIL**(6.4s);contact-sheet 两页 glance | ✓ P4 生效:免 vision 视觉闭环 40s 内,无 PIL 手写 |
| 7. 记忆+总结 | 02:43:31–02:44:37 | ~1.1m | 2 次 Write(项目 memory)+ 总结 | ✓ |

## 三、P3 闭环指标对比(41.5m 报告"后续验证点")

| 指标 | 目标 | 实测 | 判定 |
|---|---|---|---|
| 含工具回合数 | ≤12 | **72**(72 工具调用 = 72 回合,**零批量**) | ✗ 6×(新 CLI 下单回合已是低税,严重度降级) |
| cwd 漂移失败 | 0 | **0**(全绝对 --videodir) | ✓ |
| 模型等待 | 4–6m | ~12.9m | ✗(基数=回合数×单价,尖峰再加码) |
| 子代理 | 0 | 0 | ✓ |
| rabbit-hole | 禁止 | 无(第 4 阶段是真实环境故障,非假设绕路) | ✓ |
| 总耗时带修复 | 10–12m | 17.0m | ✗ ~+40%(构成见下) |

## 四、超预期的 ~5–7m 去哪了(按大小)

1. **check 环境故障诊断 ~4.5m(一次性)**:钉版 registry 副本静默死掉是 11-14 落地的 report 脚本没覆盖到的环境断点(脚本本体内部用本地 CLI 不受影响,是元组的 `npm run check` 入口坏)。这次交了学费并把结论写进项目 memory;建议下次若再触雷直接跳过钉版入口(→ SKILL.md 加一行预案)。**本次属于"新知识获取"而非退步。**
2. **两次深思考尖峰 ~3.2m(结构性,与 other 阶段同族)**:读单帧后 101s 一次性归因 18 条 overlap(结论正确、走 #4,但要价高);94s 手工推演 WCAG 相对亮度公式给 sticker 选色(checker 已给出 verified 4.51:1 建议,模型选择重算并取了更暗的 #1A1A1A——严格上正确但属于 over-verification)。与 storyboard 65%、identify-intent 63–86% 的 adaptive-thinking 特征完全同族。`CLAUDE_CODE_MAX_OUTPUT_TOKENS`(thinking cap;`MAX_THINKING_TOKENS` budget_tokens 在 adaptive 后端被剥除、本部署实测无效)在记忆中仍是"待做/待 A/B"——**它是 verify 阶段剩余的最大杠杆**(预期直接吃掉尖峰 ~3m,长尾回合再压 ~2m)。
3. **零批量 ~1m(税已降,不再痛)**:8 帧文件 8 个回合、22 处 Edit 22 个回合。旧 CLI 单价 20–30s 时这是 30m 级主税;现在缓存命中+快速首字节下单价 ~3–4s,批量纪律收益优先级大幅下移——**不建议再为 batching 改 skill,先做 thinking cap。**

## 五、下一步(优先级)

1. **thinking cap A/B(与 storyboard/identify-intent 的 ① 合并做)**:`CLAUDE_CODE_MAX_OUTPUT_TOKENS`(如 16000)+ run_claude 注入,封 max_tokens;若生效,verify 预期 17m → 12–13m(与 10–12m 预期带对齐)。
2. **check 入口预案进 SKILL.md**:`npm run check` 钉版入口失败 → 直接 `npx hyperframes check .`(本地 symlink),免 16 回合诊断。
3. P3 回合预算口径更新:≤12 的旧口径按"逻辑阶段"计(本次逻辑阶段 ≈7,达标);"工具回合"口径应在新 CLI 下重估为 ≤40(单价低)。

## 附录:本次会话留下的资产

- 项目级 memory(aiflow-verify-pitfalls 系,会话收尾 2×Write):check 钉版入口环境断点已记录,下次同一项目/同类环境直接命中。
- 修复明细:字体 @import ×9(#3)、`data-layout-allow-overlap` ×18(#4 设计分层)、对比度 ×4(01 pillow 墙词/03 sticker);28 条 `id_requires_css_escape` warning 确认为 tell-only(结构门干净),9 条 google_fonts_import warning 为已知非阻断。