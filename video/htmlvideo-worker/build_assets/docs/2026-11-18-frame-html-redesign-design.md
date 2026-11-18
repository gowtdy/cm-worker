# aiflow-build-frame-html 重新设计（确定性 fan-out 架构）：≤15m / 0 截断 / 成本受控

> 设计对象：`build_assets/skills/aiflow-build-frame-html`（含其调用链）的重新设计
> 依据：[[thinking-cap-ab-58m-analysis]]（58.3m 事故，cap 一刀切打崩 fan-out）、[[build-frame-html-24m-effort-high-analysis]]（24.2m 基线，马拉松思考）、[[build-frame-html-deadzone-deepdive]]、[[claude-cli-skill-profiles]]
> 生效范围（用户已确认允许全链路改动）：`skills/aiflow-build-frame-html/*`、`skills/hyperframes-core/references/frame-worker-core.md`、`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`、`scripts/frame-packets.mjs`、`html_utility/claude_cli.py`、`html_utility/frame_html.py`、`docs/claude-cli-skill-profiles.md`
> 设计日期：2026-11-18
> 关键词：确定性 fan-out、per-worker cap 64000、合并 dispatch 文档、骨架预生成、filler 契约

## 结论先行

**调度架构从「单 `claude -p` orchestrator + 19 个子代理」改为「Python 确定性 fan-out N 个独立 `claude -p` worker」**：控制回路零 LLM（58m 事故中 orchestrator 的 41 回合 + ~9m 轮间思考 + 全阶段 ~1/3 输出 token 全部归零），每 worker 获得精确到进程的 cap / turns / 墙钟超时。worker 侧靠两件事提速：**输入减负**（三文档并读 30k+ token → 单合并 `.dispatch.md` ≤10k、rule recipe 按需打开）和**骨架预生成**（打包器确定性预写必过 selfcheck 的骨架，worker 从「憋整帧」反转为「Edit 填充」）。cap 只负责不截断——**worker 每进程 64000（默认，128k 一键回退），串行阶段显式固化 32000**。

一句话：**速度靠结构（骨架 + 单文档），成本靠删控制回路与减输入，cap 分级只做防截断。**

---

## 1. 背景与目标

### 1.1 基线

| 指标 | 事故（58.3m，32k 全局 cap） | 基线（24.2m，128k 放开） | 最优（20.4m） |
|---|---|---|---|
| frame-html 阶段墙钟 | 58.3m，**7 帧只落 2** | 24.2m，8 帧全成 | 20.4m |
| 截断回合 | 5/19 worker（3 回合后零 Write） | 0（但 16.1m 单回合马拉松×2） | 0 |
| 阶段成本 | 31.55 USD | 未记录（skill-timing 可回查 cost 字段） | 未记录 |
| orchestrator 输出 | 258,754 token（全阶段输出 799,046 的 ~32%） | — | — |

串行阶段（intent 16.7s / storyboard 3.0m / visual 7.1m）在 32k cap 下已齐踩历史地板，**不在本次范围，只做配置固化**。

### 1.2 三个已证实事实（设计的约束前提）

1. **唯一硬杠杆**：本部署 adaptive 后端剥除 `MAX_THINKING_TOKENS` 的 budget_tokens、effort 档位对思考量无效（api-bodies 实测）；能到达请求体的只有 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`（= `max_tokens`，硬封 thinking + 可见输出）。
2. **cap 的两难**：32k 截断（5/19 worker 死）、128k 放生马拉松（16m 单回合、最后一秒才 Write）。cap 无法同时防截断和防马拉松。
3. **「骨架先行」纪律不自律**：已写进角色文档但仍被违反——纪律必须改成物理事实。

### 1.3 目标与成功标准（用户已确认）

| 指标 | 目标 |
|---|---|
| frame-html 阶段墙钟（7–8 帧） | **≤15m**（基线 24.2m，提速 ~40%；语义 = 标准跑的预期墙钟，A/B 口径。病态上限被结构性兜住：单 worker ≤720s 墙钟断电闸 + 重派 ≤2 轮，最坏 ≈ 2×720s + 轮间开销） |
| 截断回合 | **0**（无「读完就死」、无无声 end_turn） |
| worker 首轮必读输入 | **≤10k token**（现 ~30k+） |
| 阶段成本 | **≤15 USD/run**（A/B 实测校准；不依赖思考压榨，见 §8） |
| 质量门（不降） | frame-selfcheck 一次通过、assemble 成功、verify 终态与像素对比不回归；≤2 轮重派内全帧落地 |

---

## 2. 总体架构

### 2.1 架构决策：Python 确定性 fan-out（用户已拍板，含 assemble 失败补救会话）

| 维度 | A: orchestrator + 子代理（现状） | **B: Python fan-out（选定）** |
|---|---|---|
| worker 并行 | 子代理并行 | Popen 并行池（cap 8，env 可调） |
| 每 worker 独立预算 | 仅 env 继承，无已验证的 per-subagent 口 | **每进程精确注入** cap/effort/turns/timeout |
| 控制回路 | LLM（58m 事故：41 回合、+110s Edit、~9m 轮间思考、~1/3 输出 token） | **零 LLM，确定性状态机** |
| 失败判定 | 事后凭 artifact 反推（19「completed」5 零产物） | 逐 worker：rc + result 文本 `done OK` + 超时信号 |
| 尾部控制 | 无（16m 马拉松只能等） | **per-worker 墙钟超时 + 进程组 kill**（`_kill_process_group` 现成） |
| STORYBOARD 标记 / assemble | orchestrator 顺手做 | Python 正则标记 + 脚本（现成） |
| assemble 失败恢复 | LLM 临场修（58m 事故未救回） | 常规路径报错即停；**失败时起一次性 `repair` 小会话，修完重试一次，再败即 raise** |

### 2.2 数据流（改造后）

```
build_frame_packets():  frame-packets.mjs（Node，构建期，确定性，零 LLM）
  ├─ .hyperframes/frame-packets/<frame_id>.dispatch.md   ← 合并输入：role card + frame block + blueprint + frame.md 节选 + rule 索引
  ├─ compositions/frames/<frame_id>.html（骨架）          ← 预写，必过 frame-selfcheck
  └─ .hyperframes/frame-packets/_role.md + <frame_id>.md ← 全量版，保留为逃生门/回滚
        ↓
build_frame_html():  frame_html.py 确定性状态机（零 LLM 控制回路）
  preflight → 并行 fan-out N 个 run_claude(skill="frame_html", per-worker env)
     worker 进程: claude -p —— 读 dispatch.md → Edit 骨架分块填充（style→body→script）
                  → frame-selfcheck.mjs → 回复 done OK <frame_id> / fail
        ↓
  collect（rc + done OK）→ 失败帧单批并行重派 ≤2 轮（附失败原因）
        ↓
  STORYBOARD.md 状态正则标记 animated → build_index_html() → 失败则 repair 会话一次 → 再败 raise
        ↓
_validate_frame_html_outputs()（既有 gate 保留）
```

### 2.3 组件与职责（隔离边界）

| 组件 | 职责 | 依赖 |
|---|---|---|
| `claude_cli.py::_PROFILES` | 每阶段 cap/effort/turns/timeout 的唯一来源 | 无 |
| `frame-packets-core.mjs` | packet 组装 + **dispatch.md 生成 + 骨架生成**（确定性） | STORYBOARD.md、hyperframes.json、role 源文件 |
| `frame_html.py` | **确定性调度状态机**（fan-out/collect/retry/mark/assemble/repair）+ worker 提示组装 | run_claude、frame-packets 产物 |
| `aiflow-build-frame-html` skill 文档 | **可执行契约**（worker filler 契约 + 驱动实现规范），不再是 orchestrator 的思维流 | 被上面三者共同遵守 |

---

## 3. 组件设计

### 3.1 `claude_cli.py` — 分级 cap 固化

`_PROFILES` 改为（**每个 profile 的 `max_output` 全部显式**，不再依赖环境里的一刀切值）：

| skill | effort | thinking | max_output | max_turns | timeout | deny_agent | 说明 |
|---|---|---|---|---|---|---|---|
| intent | high | 32000 | **32000** | 100 | 1800 | 是 | 固化已验证地板配置 |
| storyboard | high | 64000→clamp | **32000** | 100 | 1800 | 是 | 同上（thinking 被 clamp 到 max_output 约束内） |
| visual | high | 64000→clamp | **32000** | 100 | 1800 | 是 | 同上 |
| **frame_html** | high | 64000→clamp | **64000** | **60** | **720（env 可调）** | **是** | 每帧一个进程；64000 = 防截断档；turns/timeout 是为单帧填充设的尾控：正常填充预期 4–8m，720s 是杀马拉松的断电闸（骨架保证被杀的帧仍有合法内容在盘） |
| **repair**（新增） | high | 40000 | **16000** | 40 | 600 | 是 | 仅 assemble 失败补救会话；小预算 |
| verify | medium | 32000 | **32000** | 100 | 1800 | 是 | 固化 |

要点：

- **B 架构下全部 profile 都禁 `Agent`/`Task`**——没有任何阶段还需要子代理 dispatch（`frame_html` 的 `deny_agent: False` 随 A 架构退役）。
- worker 尾控用现成机制按进程注入：`HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS`（64000/128000 回退档）、`HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_TURNS`、`HTMLVIDEO_CLAUDE_FRAME_HTML_TIMEOUT`（`run_claude` 的 `extra_env` 通道每 worker 独立传）。
- `thinking` 值在与 max_output 冲突时按既有 `_clamp_thinking` 收敛（<64000 的均安全，实为无效参数——后端剥除 budget_tokens——保留仅为 CLI 语义要求，注释如实标注）。
- **M0 验证项**：58m 事故 profile 显示 `max_output:16000` 而 SDK result 是 32000，注入链路存在未闭合歧义——本次实施第一件事以 SDK result 为准复核「profile env → worker 进程」的传导，并把结论写进本表注释。

### 3.2 `frame-packets-core.mjs` — 合并 dispatch 文档 + 骨架预生成

新产物两张（旧产物全保留，作逃生门与回滚）：

**① `.hyperframes/frame-packets/<frame_id>.dispatch.md`（worker 唯一必读输入）**

| 节 | 内容 | 尺寸预算 |
|---|---|---|
| `## Role（可执行条款卡）` | 从 `_role.md` 抽取的全部硬约束 + lint 规则码 + filler 工作流；**不含**大段 HTML 正反示例、不含已被骨架物理保证的条目；明确「规则不清晰 → 打开 `.hyperframes/frame-packets/_role.md` 全量契约」的逃生门 | ≤6KB |
| `## Assigned storyboard block` | 帧的 Scene 序列、duration、focal/roles（原文不动） | 原文 |
| `## Selected blueprint` | 原文不动 | 原文 |
| `## Rule index` | 各 Scene 引用的 recipe 名 + `RULES_DIR/<id>.md` 路径（**不内联全文**） | 每行一条 |
| `## frame.md 节选` | palette / type ramp / components —— 打包器确定性摘取固定小节（不整篇 17KB） | ≤3KB |

- 总预算：**≤40KB（超出打 warning，日志可见）**；首轮必读 token 目标 ≤10k。
- 规则裁剪原则：**凡 `frame-selfcheck.mjs` 覆盖的检查项，条款一条不删**；被删的只有示例与说理。
- 重派轮无需重建 dispatch 文档——同一份文件复用（逃生门文件也早已在盘上）。

**② `compositions/frames/<frame_id>.html`（确定性骨架，零 LLM）**

由打包器在 build 期预写，内容只从 packet 已知事实（frame_id、canvas、duration）出：

```html
<template>
<!-- skeleton-v1: fill in style/body/script per dispatch.md; last write wins -->
<div id="root" data-composition-id="<frame_id>" data-width="1920"
     data-height="1080" data-duration="<packet duration verbatim>"></div>
<style></style>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {};
window.__timelines["<frame_id>"] = gsap.timeline({ paused: true });
</script>
</template>
```

- 骨架满足 `frame-selfcheck.mjs` 全部确定性项（template wrapper、root 三件套、paused+registered timeline）；**零视觉决定**——不含背景 clip、不含布局（renders 到底色 canvas，中途可见空白帧属预期，重派即填充）。
- 契约明确：骨架是**保险不是束缚**——worker 可增删任何节点、可整体重写（shared contract 的 last-write-wins 不变）。
- 实施期验证一点：`assemble-index.mjs` 对「仅骨架的帧」必须正常装配出 index；若它要求非空 body，就在骨架里放一个全时长透明背景 clip（结构性、非视觉决定），以 M3 实测为准。

### 3.3 `frame_html.py` — 确定性调度状态机

`build_frame_html()` 重写为如下状态机（沿用现有 pre-flight 与 `_validate_frame_html_outputs`）：

1. **preflight**：STORYBOARD / frame.md / hyperframes.json 存在性 + 每个 frame packet 都有对应 `.dispatch.md` 与骨架文件（缺 → raise，提示重跑 `build_frame_packets`；不再走「orchestrator 自己补」）。
2. **worker 枚举**：列出 `.hyperframes/frame-packets/NN-*.md`（排除 `_role.md`）即帧清单——不浅析用户内容，帧 id/duration 等已由打包器固化。
3. **fan-out**：每个 frame 一个 `claude -p` 进程（`skill="frame_html"` + `extra_env` 注入 per-worker cap/turns/timeout；并发上限 `HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY` 默认 8，超限分波）。
   - **worker 提示**（`_worker_prompt(frame_id)`，短而封闭）：`PROJECT_DIR`、`frame_id`、唯一输入 `.dispatch.md` 路径、骨架已存在的事实、chunked Edit 顺序（style→body→script，≤20KB/次）、selfcheck 命令、终态回复 `done OK <frame_id>` / `fail <frame_id>: <reason>`、cut-catalog 例外（Scene seam 属 within-scene cut 时打开）、「规则不清晰才打开 `_role.md` / `frame.md`」。
4. **collect**：逐 worker 判定 `done = (rc == 0) ∧ (result 文本含 "done OK" + frame_id)`。**artifact 存在性不再是完成信号**（骨架永远在盘上）——这是与 A 架构的根本差异，`SKILL.md` 同 commit 更新。rc==124（墙钟超时）单独归类，重派时附「上轮超时」提示。
5. **retry**：失败帧**单批并行**重派，≤**2 轮**，附上本轮失败原因；重派提示固定句：「骨架与上次部分内容已在盘上，直接填充，首回合即 Edit，禁止单回合马拉松」。2 轮后仍有败 → raise（带各帧最后失败原因），不兜、不代写。
6. **mark**：对 `done` 帧用现有 `STATUS_RE` 将 STORYBOARD 对应状态行标记为 `animated`。
7. **assemble**：`build_index_html()`（脚本，已有）。失败 → 起**一次性 `repair` 会话**（`skill="repair"`，提示 = assemble stderr + 指向 compositions/frames，任务限定「最便宜的结构性修复」）→ 重试 assemble 一次 → 再败 raise（stderr 原文带出）。
8. **gate**：`_validate_frame_html_outputs()`（既有：全帧 animated + frames 目录非空）。

`_ensure_frame_worker_agent()` 的调用删除（B 架构下无 Agent 工具；worker effort 走 profile env 注入）。`references/frame-html-worker.md` 保留一版说明其历史作用，标注「仅 A 架构回滚路径需要」。

### 3.4 skill 文档重写（`aiflow-build-frame-html/*`）

skill 的角色转变：**从「orchestrator 的思维流」变成「可执行契约」**——Python 驱动与 worker 提示共同遵守的唯一权威。

| 文件 | 改写 |
|---|---|
| `SKILL.md` | 删「You are the orchestrator…」编排指令；重写为：① Prerequisites（新产物清单 + 兼容说明）；② 驱动实现规范（§3.3 状态机的自然语言镜像：枚举/判定/重派/标记/repair 的范围）；③ worker 契约指针；④ 完成信号语义变更（`done OK` 是唯一绿信号，artifact 必要非充分） |
| `sub-agents/frame-worker.md` | 重写为 **filler 契约**：输入 = `.dispatch.md` 唯一必读、骨架已存在（「文件在盘 = 你的骨架或上一轮，填充之，勿从头重写」）、chunked Edit、turn discipline（保留）、selfcheck、`done OK`/`fail`、cut-catalog 例外（保留）；被骨架保证的「骨架先行」条目删除（已物理化） |
| `references/subagent-dispatch.md` | 改写为「驱动调度契约」：DISPATCH/WAIT/Re-dispatch 原语在 B 架构下由 `frame_html.py` 实现，文档描述其语义（rc/done OK/超时三信号、波次规则），不再是 harness Agent 工具映射 |
| `references/frame-html-worker.md` | 标注历史行（A 架构回滚需要），effort 现由 profile env 承担 |
| `references/cut-catalog.md` | 不动 |

`hyperframes-core/references/frame-worker-core.md`（`_role` 的上半）**本次不改**——裁剪放在打包器的 role-card 抽取层，不破坏共享契约的权威性；M3 A/B 若暴露「card 漏了致命规则」，修复点在抽取层而非共享契约。

---

## 4. worker 契约变更要点（与 A 架构的差异）

1. **完成信号**：`done OK <frame_id>`（rc==0 且出现于 result）为唯一绿信号；盘上文件因骨架预写而失去判定力。
2. **输入**：唯一必读 `.dispatch.md`；全量 `_role.md` / `frame.md` / rule recipe 是「不清晰才打开」的逃生门（按需，不预读）。
3. **工作流**：Read dispatch.md → Edit 骨架（style→body→script 分块）→ `node /app/scripts/frame-selfcheck.mjs …` → 修复 → 回复。首个 Edit 应在 1–2 回合内发生（骨架消灭了「先想 16 分钟再落笔」的物理基础）。
4. **turn discipline / GSAP / root / 单位 / 字体 / 轨道密度**等全部 ruled conventions 原样保留（它们是质量门的来源）。

---

## 5. 错误处理

| 场景 | 处理 |
|---|---|
| worker rc!=0 / result 无 `done OK` | 进重派批（附原因；若 output≈cap 则附「疑似截断」提示） |
| worker 墙钟超时（rc 124） | `_kill_process_group` 已杀净进程组；进重派批附「上轮超时」；骨架+部分内容仍在盘 |
| 重派 2 轮后仍有败帧 | raise，带每帧最后失败原因（不兜底代写） |
| preflight 缺 dispatch/骨架 | raise + 明确「重跑 build_frame_packets」 |
| dispatch.md > 40KB | 打包器 warning（不 fail，日志可查） |
| assemble 失败 | stderr 原文 → 一次性 repair 会话 → 重试一次 → 再败 raise |
| 裁剪卡漏规则导致 worker 卡壳 | 逃生门（全量文档按需打开）；最坏 = 旧行为，不会更差 |

---

## 6. 实施里程碑与 A/B（每步独立可回滚）

| 里程碑 | 内容 | 开关 | 验证点 / 预期 |
|---|---|---|---|
| **M0** | 复核 env 注入链路（16000 vs 32000 歧义）；以 SDK result 为准 | — | 结论写入 §3.1 注释；无行为变更 |
| **M1** | 分级 cap：串行四阶段显式 32000；frame_html 64000 | `HTMLVIDEO_CLAUDE_<SKILL>_MAX_OUTPUT_TOKENS`（既有通道） | A 架构下先跑一轮：截断消失（SDK maxOutputTokens=64000 达 worker）；预期墙钟回到 ~24m 档（**立即止血 58.3m 事故**，次日可上线） |
| **M2** | B 架构司机 + 合并 dispatch：`frame_html.py` 状态机、`_worker_prompt`、dispatch.md 生成、skill 文档改写 | `HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER=2` / `HTMLVIDEO_FRAME_HTML_DRIVER=1`（off = 旧 orchestrator 路径） | 控制回路零 token；阶段输出 token 预期 −~1/3；墙钟与 M1 持平或略降；单元测试全绿 |
| **M3** | 骨架预生成 + filler 契约 + done-OK 判定 | `HTMLVIDEO_FRAME_HTML_SKELETON=1` | **收口目标 ≤15m / 0 截断**：骨架过 selfcheck + assemble（fixture）；真实 run A/B（skill-timing --group 对比）；质量三关不回归 |
| **M4** | 收尾：A/B 结论回填本文「实施记录」；`docs/claude-cli-skill-profiles.md` 表格更新；清理 A 架构残留引用（保留 `_role.md` 逃生门） | — | 文档与代码一致 |

回滚粒度：M1 = 单个 env 变量；M2/M3 = 各自的 feature flag（off 回旧路径）；全量 = `HTMLVIDEO_CLAUDE_PROFILE=off`。

---

## 7. 测试策略

| 层 | 内容 |
|---|---|
| 打包器单测 | dispatch.md 必含 5 节、size ≤ 预算、确定性（同输入两次构建产物逐字节一致）；骨架过 `frame-selfcheck`（fixture 项目）；assemble 对骨架帧可装配 |
| 驱动单测 | monkeypatch `run_claude`：全成 / 部分失败→单批重派→收敛 / 2 轮后 raise / rc124 归类 / done-OK 串判定的容错（措辞漂移）；STORYBOARD 标记正则；assemble 失败→repair 一次→再败 raise |
| claude_cli 单测 | `test_claude_cli.py` 扩：各 profile max_output 断言（32000/64000/repair 16000）、extra_env 逐 worker 覆盖、clamp 行为 |
| 端到端 A/B | 真实 run：skill-timing `--group` 墙钟/回合/用量对比；质量三关（selfcheck 一次通过率、assemble、verify 终态 + 像素）不回归；记录 cost 字段校准 §8 |

---

## 8. 成本模型与校准

成本 = 输入 + 输出（thinking + 可见）。改造对每一项的账面影响：

| 项 | 现状 | 改造后 | 依据 |
|---|---|---|---|
| worker 输入（重派同） | 30k+/worker/首轮 | ≤10k 首轮 + 按需 | 合并文档 + rule 索引化 + frame.md 节选 |
| orchestrator 输出 | ~1/3 阶段输出 token | **0** | B 架构删控制回路 |
| 无效回合 | 截断重烧 / 马拉松憋局 | 0（骨架+文案消除物理基础；64k 防截断） | M3 验证 |
| thinking | 无逐回合约束（唯一杠杆是 cap） | 同上，靠 filler 工作流缩短每回合思考 | §1.2 事实 1 |

**绝对目标：≤15 USD/run**（对照 58m 事故 31.55 USD）。A/B 用 skill-timing 的 cost 字段校准；若超限，收紧顺序 = ① `HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS` 下调（观察截断率，业务红线 0）② worker turns 收紧 ③ 重派轮 repair 化（备选模型）。成本目标不靠牺牲质量三关达成。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| R1 角色卡裁剪漏掉致命规则 | 裁剪原则硬性条款（selfcheck 覆盖项一条不删）+ 全量文档逃生门；最坏 = 旧行为 |
| R2 `done OK` 判定脆弱（措辞漂移） | 判定 = rc==0 ∧ 含 "done OK" ∧ 含 frame_id；result 尾部计入重派原因日志 |
| R3 Edit-fill 产出弱于整写 | 骨架零视觉决定 + 契约允许整体重写 + A/B 质量三关把关 |
| R4 64k 仍截断极端回合（基线有 67k 单回合） | 128k 一键回退；同时对 A/B 中剩余马拉松做 skill-timing 剖析，再决定是否结构性加码 |
| R5 驱动并发 bug（僵尸进程/IO 死锁） | 复用 `run_claude` 进程组管束；波次上限默认 8；驱动单测覆盖 |
| R6 repair 会话越界（大改特改） | 提示限定「最便宜结构性修复」+ repair profile 小预算（16k/40 turns/600s）+ 单次 |
| R7 文档与驱动漂移 | 驱动是可执行规格；SKILL.md 与代码同 PR（M2/M3 各自） |

---

## 10. 回滚

- M1：`HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS=32000`（单变量，瞬时）
- M2：`HTMLVIDEO_FRAME_HTML_DRIVER=0` → 回旧 orchestrator 路径（旧产物 `_role.md`/packet 原样保留）
- M3：`HTMLVIDEO_FRAME_HTML_SKELETON=0` → 骨架不预写，done-OK 判定回退到「artifact + rc」（同 driver 内双分支）
- 全量：`HTMLVIDEO_CLAUDE_PROFILE=off`

---

## 附：决策记录（2026-11-18 设计会话）

1. 目标：≤15m 且 0 截断（用户选）；允许全链路改动。
2. 调度架构：**B + assemble 失败补救会话**（用户拍板；A 架构的 orchestrator 开销与盲等被量化否决）。
3. worker 输出预算：**64000 默认、128k 回退**（每 worker 进程精度）。
4. 骨架预生成：**确定性脚本、零 LLM**。
5. 本设计文档路径：`build_assets/docs/`（跟随仓库 docs 约定，替代 superpowers 默认路径）。

---

## 实施记录

- 2026-11-18: M0 注入链路验证与 M1 止血 A/B 记为**待部署后执行**（本仓库无部署环境，无法真实 run；runbook 如下，部署后执行并把结论回填本节与 `html_utility/claude_cli.py` 头部注释（M0 结论填入点））。**M0**：对真实视频项目跑一轮 frame-html（仍走 A 架构 orchestrator 路径），env：`HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS=64000`；跑完核对 worker 子会话 SDK result：`grep -r '"maxOutputTokens":64000' /app/log/claude/projects/*/subagents/ 2>/dev/null | head -5`（/app 路径为部署环境特定，以实际部署日志路径为准）。结论三者取一：a) 64000 到达 worker（继承成立）→ 继续；b) worker 仍是旧值（32000/128000）→ env 不在子代理继承链，改在 frame_html profile 的 max_output 上直接调，并回写本结论到本文档 §3.1；c) api-bodies 显示 max_tokens 与 SDK 不一致 → 抓包模式复现并记录。**M1**：同一次 run 记录 A/B 测量——skill-timing `--group`（frame-html 墙钟 + 各 worker 回合）、截断迹象（worker result 无 `done OK` 且 outputTokens≈64000（128k 回退时≈128000））、阶段 cost 字段；预期对照 M1 里程碑：截断消失（SDK maxOutputTokens=64000 达 worker）、墙钟回 ~24m 档。

- 2026-11-18: M2 驱动与 M3 骨架实现完成，本地全量回归 green：`python3 -m unittest test.test_claude_cli test.test_frame_html_driver -v` → 39/39 OK（claude_cli 33、frame_html_driver 6）；`node test/test_frame_packets.mjs` → card OK（7808 bytes）、dispatch docs OK（01-hook 619 / 02-answer 582）、skeletons frame-selfcheck 全过；两命令 exit 0。失败注入 spot-check（临时脚本复用既有测试 fixture，未入库）：骨架 `.html` 在盘但缺 `skeleton-v1` 标记 → preflight 即刻 raise `RuntimeError: frame file for 01-hook is not a pre-built skeleton while HTMLVIDEO_FRAME_HTML_SKELETON is on`；单测同覆盖（test_skeleton_mismatch_raises_preflight 预检 raise、test_two_rounds_then_raise 两轮重派语义）。真实管线 A/B（墙钟 ≤15m / 0 截断 / cost ≤15 USD / SDK maxOutputTokens=64000 到达 worker）记**待部署后执行**，测量命令：`skill-timing --group`（frame-html 墙钟 + 阶段 cost 字段）、截断核对（worker result 无 `done OK` 且 outputTokens≈64000（128k 回退时≈128000））、SDK 达 worker 核对（`grep -r '"maxOutputTokens":64000' /app/log/claude/projects/*/subagents/ 2>/dev/null | head -5`，/app 路径按实际部署日志位置调整）；执行后结论回填本节。
