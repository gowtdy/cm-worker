# build-frame-visual 单次调用 13.6 分钟耗时分析报告

> 分析对象:`/aiflow-build-frame-visual` 的一次 `claude -p` 调用
> 会话:`c2a6d40d-7247-4056-b278-4eacd934d2fa`
> 时间:2026-11-12 04:32:17 → 04:45:52(总耗时 **13.6 分钟**;统计行:5.2s 首响应 / 13.6m / 0 子代理 / 24 个 assistant 回合)
> 视频项目:`/app/videos/2026-11-11/userclm17919893695531772/6af47d10f984844e/`(12 帧,VO 72.2s,captions 启用)
> 分析日期:2026-11-15

## 数据来源

- 会话转录:`.../6af47d10f984844e/c2a6d40d-*.jsonl`(118 行,含逐块 token usage)
- skill 定义:`/app/.claude/skills/aiflow-build-frame-visual/SKILL.md`(49 行)+ references 两个(157+146 行)

## 一、时间线分解

| 阶段 | 时间 | 耗时 | 内容 |
|---|---|---|---|
| 1. 上下文收集 | 04:32:17–04:33:30 | ~1.2m | 12 Reads + 3 Bash(项目文件、skill 参考、3 个 animation blueprint、caption 时序);期间 2 次失败:`jq: command not found`(exit 127)→ python 语法错误 → 第三次才成功 |
| 2. **单块超长思考/生成** | 04:33:30–04:42:57 | **10.4m** | 零工具调用,模型一次性在 thinking 里完成全部 12 帧视觉设计(**3 个 32,000 token 上限输出块 ≈ 96k tokens**) |
| 3. 逐帧写回 | 04:42:59–04:45:17 | ~2.3m | **15 次 Edit**(14 次成功:12 帧逐帧一次 + 若干;1 次 InputValidationError 失败白跑) |
| 4. gate 复核 + 总结 | 04:45:18–04:45:52 | ~0.6m | 全文件 Read 复核 + 总结 |

**关键结论:13.6m ≈ 1.2m 收集 + 10.4m 一次性生成 + 2.3m 冗余转写 + 0.6m 收尾。工具执行合计 ~0.02m(全本地瞬时操作)——这纯粹是一个"生成任务"的时间分布,不是 I/O 或修复消耗。**

## 二、根因分析(按影响排序)

### 1. 10.4 分钟单块生成是主体,而且只该发生一次(决定性)

证据:04:42:55–04:42:59 连续 3 个 content block 各 output_tokens=32,000(该模型单块输出上限)→ 模型在 thinking 里写完了**全部设计草稿 ~96k tokens**,耗时 624 秒(≈154 tok/s,含推理开销)。

- 这说明 skill 的创作量本身是 10 分钟级的——**这部分不可被纪律压缩**,只能靠输出瘦身(P2)或并行化(P1);
- 但紧接着的 15 次 Edit(≈9k tokens 再输出 + 15 个回合 × ~10s 固定延迟)**是把 thinking 里的草稿再转写一遍**,纯冗余:~2.3m 墙钟 + 重复 tokens + 一次 InputValidationError 白跑。

### 2. 设计草稿被二次转写(2.3m,可完全消除)

模型"先想完、再逐帧贴"的工作模式:15 次 Edit 回合,每次输出仅 548–673 tokens,回合固定延迟 ~10s 占大头。若改为**一次 Write 全量写回**,2.3m → ~0.1m;且 Write 不存在 old_string 失配(InputValidationError 那一回合的来源)。

SKILL.md 现写"Edit STORYBOARD.md in place"——没有禁止逐帧追加,给"逐帧 Edit"留了空间,模型又恰好选了最慢的贴法。

### 3. 时序检查的两次失败往返(~0.5m)

caption 节奏核对:`jq` 未安装(127)→ 手写 python 语法错误(f-string 引号)→ 第三次才成功。skill/参考里没有预置解析片段,模型现场发明工具。

### 4. 回合固定延迟累积(~1m 量级)

24 个回合,收集阶段每回合 6–16s。收集本身合理(12 个 Read 分 4 批已经很批量化),但仍有 3 次"读一个、想一下、再读一个"的零散往返(04:32:31/04:32:35/04:32:52 TXT 之间的停顿),批量纪律可再收紧 ~0.5m。

### 5. 输入 token 膨胀(次要)

总输入 757k tokens:基线 23.5k × 回合数为主;一次异常放大到 **66,394**(04:43:04,InputValidationError 之后的回合,harness 把大段文件/错误上下文塞了回去)。失败回合不仅白花 10s,还抬高后续输入成本。

## 三、优化建议(按收益排序)

### P0. 强制"一次 Write 写回"+ 预置节奏脚本(零结构风险,立即做)

SKILL.md 增加终态动作规则:

1. **设计定稿后用一次 `Write` 全量写回 STORYBOARD**——禁止逐帧 Edit 追加(把"Edit in place"改成"Write once,全文件一次落盘");Edit 仅允许用于单处事后修正,且必须带精确 old_string;
2. gate 复核 Reading 保留一次(全文件通读);
3. 节奏核对改为预置片段:caption_groups.json 的解析用 skill 参考里给定的 python3 单行/小脚本(禁 `jq`——环境确认未安装),失败即视为信号缺失,继续但不阻塞(时序表只用于 pacing 参考)。

**预期:13.6m → ~10.6m**(消除 2.3m 转写 + ~0.5m 失败往返 + ~0.2m 收紧批量)。

### P1. 扇出并行化(结构性收益 ~35%,需先定契约)

沿用 build-frame-html 已验证的模式,但增加一致性契约层:

1. orchestrator 一次生成**全局设计契约**(≈ `## Video direction` + 每帧一句 brief:blueprint 选择、调色板角色、动效规则名、VO pacing 表)——这一步仍是 ~4–5m 的大生成,但只做一次;
2. **12 个 worker 并行**,各自持有契约 + 本帧 brief,**只返回 markdown 文本块、不写文件**(避免同文件并发写冲突);
3. orchestrator 汇总一次 Write;失败帧批量重派,最多 2 轮(同 build-frame-html 的既有模式)。

**预期:13.6m → ~8–9m**。代价:跨帧一致性从"同一思考里天然一致"变成"契约约束"(契约要写足:typography 层级、间距系统、动效命名清单);增加 ~4–6 个编排回合与失败处理路径。

### P2. 输出瘦身(压 P1 的前提,也是独立杠杆)

96k thinking + 126k 总输出对 12 帧而言偏肥(~10.5k tokens/帧)。给 shot sequence 定 budget:每帧 ≤4 个 Scene、每 Scene inline 布局/动效各 ≤2 行、禁大段叙述;`visual-design.md` 的方法里加长度示例。输出 126k → ~80–90k,thinking 时长按比例 ~10.4m → ~7–8m,**即使不做 P1 也有 ~2.5–3m 收益**。

### 四、预期效果

| 方案 | 预期 |
|---|---|
| 仅 P0 | 13.6m → **~10.6m**(零风险) |
| P0 + P2 | 13.6m → **~8m** |
| P0 + P1 + P2 | 13.6m → **~6.5–7.5m**(并行 + 瘦身叠加;±一致性风险) |

该 skill 的硬地板是"12 帧连贯视觉设计的生成本身":本会话实测 ~10 分钟 generation 无法靠调度纪律消除,只能靠契约并行或削减输出规模;若该模型支持更高吞吐/或换更快模型,收益另计。

## 附:双副本注意

`aiflow-build-frame-visual` 存在 `.claude/skills` 与 `hyperframes/skills` 双副本(与 verify-frame 同约定),任何 SKILL.md/references 修改需 `cp -a` 同步 + diff 验证;P0 若落地即涉及两处。

## 实施记录(2026-11-15:P0 已落地)

**1. SKILL.md 双副本**(canonical hyperframes 先行写,`cp -a` 同步、逐字节一致,49→54 行):

- **一次 Write 全量落盘**:替换原"Edit STORYBOARD.md in place"为强制终态动作——全部 shot sequences + `## Video direction` 一次写完、单次 `Write` 全文件落盘;禁止逐帧 Edit 追加(明确引用 15 次 Edit ≈ 2.3m 的观察数据);Edit 仅限写后单点修正且必须精确 `old_string`;
- **批量读纪律**:全部输入 ≤3 个批量回合(项目文件一批、四个 reference/index 一批),禁止 read-one-then-ponder 循环;
- **节奏解析**:新增 `caption_groups.json` → `node /app/scripts/caption-pacing.mjs --videodir ${videodir}` 单次执行,明令**禁 jq(本环境未安装)、禁手写内联脚本**(并在文中记录观察到的失败链 command-not-found → heredoc 语法错误);
- **Gate**:单次 Write 后全文件 Read 回读一次验证三要素,仅对实证缺口做定点 Edit;
- Quick Reference 增补 caption-pacing.mjs 行。

**2. 新增 `/app/scripts/caption-pacing.mjs`**:确定性 per-frame VO 节奏表脚本(纯 node、零依赖,与 frame-midpoints.mjs 同风格;caption_groups.json 缺失时 exit 2 且明确"非阻断");已在事故项目实测输出 12 帧 VO 时间表。

预期:P0 落地后该 skill 13.6m → ~10.6m(消除 2.3m 转写 + ~0.5m 失败往返 + ~0.2m 批量收紧),下次真实 build-frame-visual 会话以转录实测回合数闭环。

## 整体复查 + F1/F2/F3 落地(2026-11-15)

复查发现三个新风险并全部落地:

**F1. 重跑守卫(新发现,与 transitions inject 漂移同族)**:已 build 项目(任一帧 `status: animated`)重跑 visual 会重新生成设计并全量写回,静默使 STORYBOARD 与磁盘帧/`.hyperframes/frame-packets` 背离。落到新增脚本 `storyboard-gate.mjs` 的 preflight 段 + SKILL.md "Re-run guard":animated → BLOCKER 停止上报;已完成(all frames 有 Scene + `## Video direction`)→ verify-only 幂等跳过不重写;否则按序设计。**实战验证:事故项目 12/12、fourth-video 7/7 均正确阻断(exit 1)。**

**F2. 一次 Write 的保留字段漂移风险**:全文件重写会重打 narrative/transition_in/status/src/duration。SKILL.md 增补"保留字段逐字复现"条款 + gate 复核时比对;`storyboard-gate.mjs` 机检 status=outline、transition_in 合法值、src 落盘存在、duration 有效,兜住最危险的字段。

**F3. 确定性结构 gate(新脚本 `/app/scripts/storyboard-gate.mjs`)**:替代纯模型自检——每帧 ≥2 Scene、窗口单调(首幕 0.0 起、禁止重叠/越界)、覆盖 ≥90% 帧时长(禁 front-load)、focal/roles 存在、`## Video direction` 在、未知反引号 motion 名(对照 rules/blueprints 文件名 + transitions registry + ease 正则)仅 WARN。五态测试全过:已 build 阻断(exit 1)/ 完成干净(exit 0,验完仍跑全查)/ 完成带坏(7 findings:缺 src、非法 transition、缺 focal、Scene 重叠、front-load、未知 motion)/ 纯 outline 前设计板(notice + 预期 findings)。期间修一个解析 bug:帧块分割的 lookahead 正则漏掉末帧,改用 `split(/^(?=## Frame)/gm)` 方案。

**SKILL.md 双副本**:F1 守卫、F2 条款、gate 步骤、Quick Reference 行;canonical hyperframes 先行写,`cp -a` 同步逐字节一致(现 66 行)。

**剩余开放项**:P1(12 worker 契约并行,~35% 结构性收益,需先设计契约格式)、P2(输出瘦身预算)。

## 2026-11-16 落地:gate 必然误报降级 + 验证条文更新

来源:2026-11-16 新一次 build-frame-visual 实跑复盘——8 帧全部命中 `src does not exist on disk`,`GATE: FAIL` × 8,模型被迫 ~70s 读 gate 源码自行确认这在本阶段是 verify-only 可忽略项(结论正确但纯属浪费);另有小偏差:SKILL 写的"Write 后全文件 Read 回读"被模型替换为 gate + 定点 grep(质量等同甚至更硬核)。

**1. `/app/scripts/storyboard-gate.mjs` F3:src 落盘缺失降级为 ⚠**

- 本阶段 `src` 指向的 `.html` 是下一阶段 `/aiflow-build-frame-html` 的产物,缺失是必然稳态而非本次 Write 的缺陷;只有 `status: animated`(已被消费)才应当有落盘文件,而该类板子已被 F1 blocker 提前 exit 1。故:`status ≠ animated` 时缺失 → ⚠ "expected at this stage";缺行/格式非法 `- src:` 仍为硬 ✗;animated 分支保留 ✗ 兜底(F1 之后不可达)。
- 头注释 F3 描述与 PASS 文案同步更新(明示 src-not-on-disk ⚠ 为本阶段预期、禁止为"修"而去建 HTML)。
- 四态 fixture 验证:src 缺失板 → exit 0 + 2 ⚠;src 在盘 → exit 0 无警告;缺 src 行 → exit 1"missing - src"仍致命;animated → F1 blocker 仍 exit 1。

**2. SKILL.md 双副本**(canonical hyperframes 先行写,`cp -a` 同步、diff 逐字节一致):

- Gate 段预注解两个"预期出现、非缺陷"的 finding:`src not on disk` ⚠(HTML 属下一阶段产物,严禁建 HTML 去"修")、未知 motion 名 ⚠(review)——未来运行时不必再花 ~70s 读 gate 源码重新推导;
- 验证条文:"Read the full file back once"降为**非强制**——认可 gate + 定点 grep(vO 逐字引用、scene-to-cue 对齐、保留字段逐字一致)为实测最优模式,同等严谨且更省;
- Quick Reference 的 gate 行同步标注"`src not on disk` ⚠ is expected at this stage"。