# build-frame-html 死区深挖:模型 32k 思考截断 × end_turn 假完成 × 无心跳编排

> 分析对象:`b4c8974f` 会话(63 分钟 build-frame-html)中的 **09:00–09:08 全队列零活动死区**
> 分析方法:主转录 + 14 份子代理转录 + 共享 debug 日志 + api-bodies 请求/响应体交叉比对
> 分析日期:2026-11-14
> 结论先行:**死区不是基础设施故障、不是队列卡死、也不是 worker 进程僵死——是后端模型在 `effort:max + adaptive thinking + max_tokens=32000` 下进行了 8–11 分钟/回合的"马拉松思考",被 32k token 上限截断,而截断被网关报告为 `stop_reason: end_turn`(正常结束),客户端据此把"未完成"当成"已完成";两个重建 worker 各烧两轮、主 agent 无心跳等待,拼出这 8 分钟零活动。**原报告"共享基础设施滞阻"的推测被本次证据推翻。

---

## 一、死区的精确解剖

### 1. 三个"钢琴黑键":三次 32k 无声截断回合

| # | worker | 请求派发 | 首字节 | 响应落地 | 生成时长 | output_tokens | stop_reason | 产出 |
|---|---|---|---|---|---|---|---|---|
| T1 | 03 重建 | 08:47:21.807 | +1.5s | 08:58:23.526 | **11.0 分钟** | **32000** (thinking 31999) | end_turn | **零**(无文本、无工具调用) |
| T2 | 04 重建 | 08:53:04.139 | +1.2s | 09:03:13.252 | **10.2 分钟** | **32000** (thinking 32002) | end_turn | **零** |
| T3 | 03 重建(nudge 后) | 08:58:23.554 | +1.0s | 09:09:21.746 | **11.0 分钟** | **32000** (thinking 32001) | end_turn | **零** |

> 数据源:`api-bodies/*.response.json`(`fb9167cf`@08:58:23、`e1436e0a`@09:03:13、`53e69111`@09:09:21)。

**每一条都指向同一个事实:模型生成了整整 32000 个 output token——刚好撞上请求里的 `"max_tokens": 32000`——全程只有 thinking,没有任何可见输出。**

### 2. 全程首字节 1.0–2.8 秒:API 通道从未生病

对 08:47–09:16 全部 55 次 API 派发做了首字节时延统计:

- 08:47:21.807(马拉松回合)→ 首字节 1.47s
- 08:53:04.139(马拉松回合)→ 首字节 1.19s
- 08:58:23.554(马拉松回合)→ 首字节 1.03s
- 最慢一次首字节 3.06s(08:50:31)

**服务器每次都立即受理并开流。** 慢的不是网络、不是排队——是模型生成。按 32000 tokens / 660 秒反推,生成速率 ≈ **48–52 tok/s,持续 11 分钟一步没停**。这不是卡顿,是"匀速吐思考"。

### 3. 死区的三层结构

```
08:57:42 主 agent 标记 frame 11 完成,进入"Waiting"(合理等待,但无超时)
   │
   ├─ 08:58:23.5  03-worker 回合#1 落地:32k 截断,无声,end_turn
   ├─ 08:58:23.5  SDK 注入 nudge("no visible output, please continue")重派 → 03 回合#2 在途(11 分钟)
   ├─ 09:03:13.3  04-worker 回合#1 落地:32k 截断,无声,end_turn
   ├─ 09:03:13.3  nudge 重派 → 04 回合#2 在途(8.2 分钟)
   │
   ├─ 09:04:00 – 09:08:59  共享 debug 日志【0 行】← 原报告所称"8 分钟死区"
   │       (两份 32k 回合同时在途,thinking 被网关整包缓冲、不逐块下发,毫无可观测信号)
   │
09:09:21.7  03 回合#2 落地:又烧了一整个 32k、还是无声 end_turn
09:09:21.773  SDK 判定子代理"完成"退出(exitPath=completed, turns=6, finalStopReason=end_turn)
09:09:21.779  父进程才收到完成通知 → 主 agent 醒来,发现磁盘上根本没有 03/04 的产物
```

所谓"09:09:21 的恢复",**不是基础设施修好了,而是第二份截断响应落地 + 子代理带着假完成状态退出**。

### 4. nudge 的铁证:请求体重发 diff

同一逻辑回合的两次请求(`73cf0b51`@08:53:04 vs `bcc745a5`@09:03:13)相似度 99.97%,唯一差异是后者注入了一条系统消息:

```
[Your previous response had no visible output. Please continue and produce a user-visible response.]
```

即:回合无声结束后,客户端把"上一回合只思考不产出"当成需要纠正的异常,自动补一条指令重跑。**但 thinking 不会带进下一回合的上下文——模型从零重新想一遍,再次烧满 32k。** 03-worker 连续撞了两次(第一轮 08:47:21→08:58:23 无声、nudge 后 08:58:23→09:09:21 又无声);第二次无声后 SDK 放弃循环、agent 直接退出。

### 5. 思考块被截断的现场

三个思考块的结尾全部断在半截(T1 末 300 字符):

| 回合 | 断点内容 |
|---|---|
| T1 (08:58:23) | `...container-type: size;\n        --cream:` ← CSS 变量定义到一半 |
| T3 (09:09:21) | `chips left 1022 + w 340 → span 102` ← 布局坐标换算到一半 |

模型正在思考中途被 32k 上限一刀切断,之后的内容(大概率是"那么我现在 Write")永远没有生成。

---

## 二、死区形成的五环机制

```
① 决策歧义(字体交付约定矛盾:determinism"禁联网"规则 vs 兄弟帧 Google Fonts @import)
      ↓ 触发
② 模型进入"自适应+最大努力"深度思考,决定性证据:单回合 thinking 高达 105KB/103KB
      ↓ 32k 上限
③ 生成被 max_tokens=32000 截断,网关把截断映射成 stop_reason=end_turn(而非 max_tokens)
      ↓ 客户端无法区分"想完了"与"没想完"
④ SDK 子代理循环:无声回合 → nudge 一次重跑 → 仍无声 → agent 以 completed 退出并通知父进程
      ↓ 假完成通知
⑤ 主 agent 的编排逻辑只有"等完成通知",没有磁盘/mtime 心跳,也没有超时 → 8 分钟零活动
```

- **为什么 03/04 特别严重**:这两个帧是数据/表格密集帧,且它们恰好在猜"字体约定"这个本可一句话裁定的问题。首次失败的重建 prompt 又使用模棱两可的表述,模型把全部预算花在"重新推导规范 + 完整草拟设计"上,直到被截断都没轮到"调用 Write"。SendMessage 直接把字体答案告诉它之后,它立刻收敛并在下一回合写盘(09:20:48 起)——**决策消歧的杠杆被这次事故反向实证了。**
- **为什么其他 worker 没炸**:全批共 **7 次** 32k 无声截断(08:40:00、08:40:30、08:45:54、08:51:58、08:58:23、09:03:13、09:09:21),6 个子代理被 nudge 过。前 4 次 nudge 后的回合较快产出工具调用(思考较短),所以只是拐了个弯;03/04 的 nudge 回合仍深陷歧义,第二轮又烧满一个 32k——累计 22 分钟打水漂。
- **为什么"零日志"**:`thinking: {type: adaptive, display: omitted}` + 网关整包缓冲。生成期间既不下发思考块,也没有任何工具活动,debug 日志 09:04–09:08 一分钟一行都没有。这是观测盲区,不是系统冻结(对比:应诊期间的 API 请求一直能正常进出)。

---

## 三、对原报告疑点的逐条修正

| 原报告的说法 | 现在的结论 |
|---|---|
| "共享基础设施(模型端点/任务队列)滞阻恢复,建议结合网关日志复核" | **推翻。** 本会话所有请求首字节 1–3s,模型以恒定 ~50 tok/s 连续生成 11 分钟,没有任何滞阻特征 |
| "两个独立会话 09:09:13/09:09:21 相隔 8 秒同步恢复" | **巧合。** 09:09:13.617 是并行 Q&A 会话(de78f2ca)自己的一次 Bash 工具在等了 754s 权限审批后放行(其转录 TU 创建于 08:56:39.288,该会话是交互式的,工具在等**人类用户**审批);09:09:21.7 是 03-worker 回合#2 的截断响应落地。两个进程写同一个 `claude-debug.log.1`,原分析误把它们拼成了一个故事 |
| "09:03:13 的请求体是 exit code 2 的失败 Bash 结果" | **部分对。** 那是 04-worker 的 nudge 重发请求,其上下文里还挂着 08:52:40 的 exit-2 `ls` 结果——因为中间只发生过一个无声回合,没有新消息可加。exit code 2 本身无碍(只是 ls 列出的字体目录不存在) |
| "worker 卡死在字体研究循环" | 主 agent 的诊断方向对:模型确实在围着字体约定打转——但机制不是"循环调用",而是**单回合内马拉松思考 + 截断 + 重想再截断** |

---

## 四、优化建议(按杠杆排序)

### 1. 决策消歧前置(最高杠杆,已被事故反向实证)

在 frame packet / 派发 prompt 里**直接裁定**所有已知歧义,而不是让 worker 自行判断:
- 字体约定:"本项目无本地字体文件,一律使用 Google Fonts `@import`(与兄弟帧一致),禁用在思考中反复权衡";
- 模板边界、GSAP 引入方式、单位体系(cqw vs px)等全部给出唯一答案。
- 遇到未预期歧义时,要求 worker **先以最短思考给出一句问题或先落盘骨架文件**,禁止原地长考。

实证:09:09:53 SendMessage 给出字体答案 → 09:20:48 文件落盘,思考立刻收敛。若 08:46 派发时就带这个裁定,03 重建可省 ~32 分钟。

### 2. 思考预算治理(把"任意长思考"变成"受限思考")

- `output_config.effort: max` + adaptive thinking + 32k 上限是病态组合。对 frame worker 这种"按规范执行"的子代理,建议 **effort 降到 medium/high 起步**,或为 thinking 单独设预算(≈8k tokens → 回合时长封顶 ~3 分钟);
- 在 worker 角色文档写死行为模式:**先 Write 骨架文件(1–2 分钟内),再用 Edit 迭代完善**。骨架既提供磁盘心跳,又保证即使回合被截断仍有产物;
- 实测常数可用来做调度预算:本后端 ~50 tok/s,一个满额 32k 回合 ≈ 11 分钟。任何"预计很快"的轮次如果越过 3 分钟没有产物,就可以判定它走进了思考黑洞。

### 3. 无声截断的快速失效(需网关 + 客户端配合)

- **网关**:max_tokens 截断时应返回 `stop_reason: max_tokens`(或等效标志),不要映射成 end_turn。当前映射让客户端把"没写完"当成"正常收工";
- **客户端/SDK**:检测 `stop_reason=end_turn` 且 `thinking_tokens ≥ max_tokens` 且无 tool_use/无文本 → 判定为异常回合;
- **编排器**:对异常回合的补救不是裸 nudge 重跑(这会再烧 11 分钟,正是本次死循环),而是**带裁定/带断点摘要重派**,或直接判该帧失败进入批量重派流程。

### 4. 心跳巡检 + 假完成防护(替换"等通知"模型)

- 等 agent 完成通知不可靠:通知只在子代理**彻底退出**时才发(本次 03-worker 通知延迟了 22 分钟才到,且内容是假完成)。SKILL.md 已有"全部返回后统一扫描"批量重派,仍建议:
  - 主 agent 每 2–3 分钟巡检一次**磁盘产物 mtime**(比 ListAgents 更直接——没有新产物 mtime 就说明 worker 在长考或死循环);
  - 对"agent 已退出但产物缺失"的帧,进入既有的批量重派通道并附上失败原因(注明"模型思考超预算被截断,已裁定歧义,请直接写骨架")。

### 5. 截断思考延续(进阶,为第二轮省钱)

把被截断回合的 thinking 摘要(或原文)回注到重发回合的上下文,让模型从断点继续而不是从零重想。03-worker 两次无声回合的内容("重新推导兄弟帧规范")高度重叠,属于 22 分钟里的重复劳动。此项需 SDK/网关支持,可作为与后端团队的跟进项。

### 6. 保留原报告的有效项

- 拆帧/专用模板(03/04 表格密集帧);
- 并发会话隔离(本次是不同机制,但共享日志确实误导了诊断,建议日志打上 session-id 前缀);
- 原报告"验收下沉、批量重派"已落地,与本次建议互补。

---

## 五、收益估算

| 场景 | 现状 | 预期 |
|---|---|---|
| 03 重建(关键路径) | ~36 分钟(3 个马拉松回合 ~32 分钟) | 裁定歧义 + 预算治理后 ≤ 10 分钟 |
| 04 重建 | ~23.5 分钟(2 个马拉松回合 ~18.5 分钟) | ≤ 8 分钟 |
| 死区 | 8 分钟零活动 + 主 agent 空等 22 分钟 | 心跳 2–3 分钟即触发重派,死区归零 |
| 全批无声截断损耗 | 7 次 × 2–11 分钟(并行摊薄后仍多次压线) | 预算治理 + 骨架先行后基本消除 |

**若本次已生效以上 1+2+4,63 分钟的关键路径可压缩到 ~25–30 分钟**(首轮 12 帧 ~18–20 分钟 + 困难帧重建 ~8–10 分钟),与原报告"~20 分钟以内"的目标一致。

## 实施记录(2026-11-14)

落地"effort 降档"的第 2 项优化,采用 **`CLAUDE_CODE_SUBAGENT_EFFORT`**(而非改 `CLAUDE_EFFORT` / `CLAUDE_CODE_EFFORT_LEVEL`),原因:

- 该变量只作用于**子代理**(frame workers),主 orchestrator 回合的 effort 不受影响;
- 语义为封顶:per-call 显式指定的 effort 只能更低档,不能超过该值(CLI 二进制中 `$cs` → `Lzt()` 注入 `agentDefinition.subagentEffort`;Agent 工具官方描述同步说明 "omit to use the configured subagent default");
- skill 无法自身设置 env,且 settings.json 的 `env` 按目录生效(5 个管道 skill 共用 cwd)——而管道每个 skill 本来就是独立 `claude --print` 进程,在启动点按 skill 注入即可精准隔离;
- 取值理由:二进制里未知模型(deepseek-v4-pro-0813 走通用默认分支)的 `default_effort` 即为 `"high"`,事故时的 `max` 是管道 env 强加的偏差;`high` = 恢复模型默认档。

**改动文件(2 个,其余 4 个 skill 零改动):**

1. `/app/html_utility/claude_cli.py` — `run_claude()` 新增可选 `extra_env` 参数,以 `{**os.environ, **extra_env}` 合并传入 `subprocess.run(...)`(默认为 None,不传行为不变);
2. `/app/html_utility/frame_html.py` — 仅 build-frame-html 的调用点注入:
   ```python
   rc = run_claude(
       project_dir, prompt, logger,
       extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
   )
   ```
   两文件均已 `py_compile` 通过。

**下次构建的验证步骤:**

1. 构建期间/结束后,查 `/app/log/api-bodies/*.request.json`:
   - worker 请求(体里带 `_role.md` / frame packet 内容)的 `output_config.effort` 应为 `"high"`(事故基线:全部 `"max"`);
   - 主 orchestrator 请求(含 STORYBOARD 语料)的 `output_config.effort` 应仍为 `"max"`;
2. 用本报告同款方法复查:32k 截断响应数量(`output_tokens ≈ 32000` 且 `stop_reason=end_turn` 且无 tool_use)、每回合时长(是否再现 8–11 分钟无声回合);
3. 若 `high` 下仍出现马拉松回合,把注入值降为 `"medium"` 对比(改动点仅 `frame_html.py` 中一个字符串);
4. 补充前提:该开关保证 CLI 侧发出的 effort 字段正确;qianfan/deepseek 后端对该档位的实际解释(思考量下降幅度)以本轮实测为准。

**实施记录(2026-11-14 二:决策消歧前置落入 worker 角色与派发约定)**

- 在 `sub-agents/frame-worker.md`(双副本)新增 **"## Ruled conventions — settled, do not re-derive"** 一节,把事故中诱发马拉松思考的决策点全部裁定封闭:字体交付(Google Fonts `@import`,显式 **Override** 共享契约中"fonts ship as files"的旧条款与 `.claude` 副本同款自检条目——该条款与项目现实直接矛盾,正是歧义源头)、GSAP 引脚 3.14.2 置 template 内、裸 template + 全 data-* 于 #root、单位以 cqw/cqh 为主(px 限固定装饰)、回合纪律(禁纯思考收尾、歧义当回合定案)、骨架先行(1–2 回合必落盘最小骨架)、大文件分块 Write(>20KB 易截断为 InputValidationError)。
- 两份 `SKILL.md` 派发段各加一句:约定裁定已闭合,派发 prompt 只陈述项目事实,不得复述或重新争论。
- `frame-packets.mjs` 确认 `SKILL_DIR=/app/hyperframes/skills/aiflow-build-frame-html`、`deltaPath=sub-agents/frame-worker.md`——新项目的 `_role.md` 自动携带本裁定;已在途项目的旧 packet 需重建才生效。
- **副本漂移已解决(2026-11-14,用户裁定)**:hyperframes 副本为 canonical,已用 `cp -a` 整体覆盖 `.claude` 副本(含补齐仅存在于 hyperframes 的 `references/subagent-dispatch.md`),两树逐文件哈希一致;其余 aiflow 技能(build-frame-visual / build-storyboard / verify-frame)双副本核对无漂移;`aiflow-identify-intent` 仅存在于 `.claude`(无 hyperframes 对应目录,未动)。

**实施记录(2026-11-14 三:字体交付机制查实 + 裁定修正)**

查实(源:`packages/producer/src/services/deterministicFonts.ts`、`fontData.generated.ts`、`packages/lint/src/rules/fonts.ts`、`packages/parsers/src/fontAliases.ts`):帧内 `@import` 只服务于静态 lint(`font_family_without_font_face` 接受 @font-face / Google import / bundled 名;另有 **warning 级** `google_fonts_import` 规则);实际字体交付由渲染编译器完成——内嵌 data-URI(零网络)/ Google Fonts 实抓(需联网,缓存 `~/.cache/hyperframes/fonts`,分布式/Lambda fail-closed)/ 系统字体捕获。旧版内嵌集(18 族 42 面)不含 Bodoni Moda / Space Grotesk,但**据用户确认:新的 build docker 已把 frame-presets 用到的全部字体打进内嵌包**,该缺口已在运行时层关闭。据此:①曾尝试的"preset 字体迁移到内嵌族"方案**已完整回滚**(hyperframes-creative 双树恢复一致);②skill 的 Ruled conventions 字体条改写为**构建无关**版本(编译器负责交付、内嵌集随 CLI build 变化不硬编码、@import 只照 frame.md 家族清单过 lint、禁找本地字体/禁纠结 URL),两副本已同步;③四个 aiflow 技能(build-frame-visual / build-storyboard / verify-frame / identify-intent)经全量 grep 确认**零字体引用**,字体面经 frame.md ← build-frame.mjs ← hyperframes-creative presets 流入,无需调整。④此后 Ruled conventions 字体条再按新 docker 翻转一次指令:预设坡内家族**省略 @import**(bundled 名 lint 直接放行、免 `google_fonts_import` warning),仅 frame.md 坡外家族(品牌覆盖)补 import;省略/补写的判定是机械对比而非权衡。原则部分(消歧前置、禁长考)不变。

**补充验证(2026-11-14,`CLAUDE_CODE_MAX_OUTPUT_TOKENS` 能否放 settings 目录):**

- **可以。** settings.json 的 `env` 块会在会话启动时注入 `process.env`(二进制中 `Object.assign(process.env, dGr(e.env).kept)`),而 `cYn` 在每次请求构建时读 `process.env.CLAUDE_CODE_MAX_OUTPUT_TOKENS`;已实测:在测试项目 `.claude/settings.json` 写 `{"env":{"CLAUDE_CODE_MAX_OUTPUT_TOKENS":"16000"}}` 后,用本地 HTTP 捕获器收到该会话真实出站请求 `max_tokens: 16000`(对照组 32000);
- `f_n` 只钳上限(upperLimit 128000),**设小合法**;该变量**没有**子代理专属版本(`CLAUDE_CODE_SUBAGENT_*` 全枚举仅 8 个:`CACHE_EVICT / CONFIG_WARNING / EFFORT / HISTORY / MODEL / MODEL_FORCE / PROMPT_CACHE_TTL / PROMPT_SNAPSHOT`),所以它作用于会话内**全部**请求(主 agent + worker 一起)——想"只 cap worker"只能走 `CLAUDE_CODE_SUBAGENT_EFFORT`;
- 作用域注意:项目级 settings 按目录生效,管道 5 个 skill 共用视频项目 cwd,项目级 env 会全体命中(storyboard/visual 等长输出阶段需留意);按 skill 精准注入仍走 launcher `extra_env` 通道(与 effort 方案同入口)。

## 附录:关键证据索引

- 截断响应:`api-bodies/fb9167cf-*.response.json`(T1)、`e1436e0a-*.response.json`(T2)、`53e69111-*.response.json`(T3)
- nudge 注入 diff:`73cf0b51`(08:53:04) vs `bcc745a5`(09:03:13) request.json 相似度 99.97%
- agent 假完成:`claude-debug.log.1` 09:09:21.773 `[Stall] agent_completion agentId=a263b95673093ac7e ... finalStopReason=end_turn turns=6`
- 思考块原文:子代理转录 `agent-a263b95673093ac7e.jsonl` 08:58:23.526 / 09:09:21.746(105KB / 103KB)
- de78f2ca 权限等待:其会话转录中 TU Bash 创建于 08:54:50.803、08:56:39.288,后者 754s 后于 09:09:13.617 派发