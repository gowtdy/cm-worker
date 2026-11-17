# build-frame-html 单次调用 63 分钟耗时分析报告

> 分析对象:`/aiflow-build-frame-html` 的一次 `claude -p` 调用
> 会话:`b4c8974f-41d6-4112-9dbb-8d36fee04c44`
> 时间:2026-11-12 08:20:09.565Z → 09:23:26.902Z(总耗时 **63.3 分钟**)
> 视频项目:`/app/videos/2026-11-11/userclm17919893695531772/6af47d10f984844e/`(12 帧)
> 分析日期:2026-11-13

## 数据来源

- 会话转录:`/app/log/claude/projects/-app-videos-2026-11-11-userclm17919893695531772-6af47d10f984844e/b4c8974f-41d6-4112-9dbb-8d36fee04c44.jsonl`(344 行)
- debug 日志:`/app/log/claude-debug.log.1`(分钟级 API/工具活动密度)
- API 请求体:`/app/log/api-bodies/*.request.json`(工具调用内容交叉验证)

## 一、63 分钟时间线分解

| 阶段 | 时间 | 耗时 | 内容 |
|---|---|---|---|
| 主 agent 开场探索 | 08:20:09–08:24:49 | ~4.5m | 加载 skill、检查 STORYBOARD/caption 契约/frame 包结构(~30 次 Bash/Read) |
| 扇出准备 | 08:24:49–08:25:44 | ~1m | TaskCreate ×3(fan-out / WAIT gate / flip status) |
| 派发 12 个 frame worker | 08:25:44–08:27:09 | ~1.5m | `Agent:Build frame 01..12`,每 6~8 秒串行派发一个 |
| worker 并行高峰 | 08:26–08:29 | — | 顶峰 48 次工具调用/分钟、35 次 API/分钟(Read/Bash 为主) |
| 主 agent 串行验收 | 08:35:15–08:45:54 | ~19m(与 worker 并行) | 每到一个完成通知:Bash 验证产物 + Edit 修复,逐个处理 10 个帧 |
| 重建 frame 03 | 08:46:23 派出 | **重建耗时 ~37m** | `Agent:Rebuild frame 03-the-business`;08:46:29 取消 03 首轮 worker |
| 重建 frame 04 | 08:52:20 派出 | **重建耗时 ~23m** | `Agent:Rebuild frame 04-the-balance-sheet` |
| 死区 | 09:00–09:08 | **8m** | 全队列 0 API 请求、0 工具调用,无人活动 |
| 拖尾收尾 | 09:09–09:23 | ~14m | 主 agent 检查 03/04 磁盘状态、SendMessage 询问 worker、最终 gate check |
| 结束 | 09:23:26 | — | gate 通过:`count animated vs outline status` |

**关键结论:63m ≈ 4.5m 开场 + 1.5m 扇出 + ~50m 等待/串行验收。问题帧 03/04 把关键路径从 ~20 分钟拉到了 57 分钟。**

## 二、根因分析(按影响排序)

### 1. 两个"困难帧"拖尾 + 两次重建(决定性因素)

frame 03(the-business)与 frame 04(the-balance-sheet)首轮均失败:

- 03 的首个 worker 到 08:46:29 才被 cancel(`queue-operation remove`),重建耗时 **~37 分钟**;
- 04 重建耗时 **~23 分钟**;
- 其他帧首轮只需 **10–18 分钟**,说明这两帧的复杂度(数据/表格密集)与 worker 能力不匹配。

这两帧的两次重建成为全程关键路径,直接决定了总时长。

### 2. 重建 worker 出现卡死/失败重试循环

证据链:

- **09:00–09:08**:全队列连续 8 分钟零活动(0 API、0 工具调用);
- **09:03:13** 的 API 请求体:内容为 exit code 2 的失败 Bash 结果 + system 消息,worker 没有向前推进;
- 主 agent 直到 **09:09:53** 才用 `SendMessage` 询问 worker 状态——被动等待,被拖了近 4 分钟才采取行动。

**更正(2026-11-13 复查)**:此前将「08:57:01 WebFetch github.com 重试循环」归因于 frames 03/04 的重建 worker 是**误归因**。经 api-bodies 交叉比对,该 WebFetch 循环出自并行的 `de78f2ca` 会话(用户问题:「如何查看另一个 claude code 进程是否启动了并行?」)在 08:51:27 派出的子代理:它在 **don't ask mode** 下 Bash/WebSearch 被自动拒绝、WebFetch 被域名校验拦截,工具全线瘫痪后仍逐个换工具重试,空转约 5 分钟后被父会话 cancel。补充观察:08:58–09:09 两个独立会话**同步静默、且均在 09:09:13/09:09:21(相隔 8 秒)恢复**,更可能指向该时段共享基础设施(模型 API 端点/任务队列)的一次滞阻恢复,而非 worker 自身卡死——建议结合网关侧日志复核。

### 3. 主 agent 是串行验收瓶颈

15 个完成通知逐个处理,每个通知需要 2~4 次模型往返 + Bash 验证 + Edit 修复,累计 ~15–20 分钟主会话耗时。期间:

- 已完成 worker 的通知在排队,等待主 agent 处理;
- 缺陷发现晚(03/04 的重建在首轮派发 20+ 分钟后才派出);
- 两次重建先后派出相隔 6 分钟,而不是并行。

### 4. 验收职责放错位置

每帧的"验证产物存在 + 修模板边界"都在主 agent 单线程里做,且 03/04/05/11 等帧被重复验证多次。这些工作本应下沉到每个 frame worker 内部完成。

### 5. 并发会话竞争(次要)

同时段(08:50–09:13)有另一个队列会话 `de78f2ca` 并行运行,同样在 09:09:13 cancel 了一个挂起任务。两者共享任务队列/模型端点,~09:00 前后队列疑似出现过集体卡顿,可能与池资源争抢有关。

## 三、优化建议

### 1. 把验收下沉到 worker(预期收益最大)

在 per-frame worker 的提示词中增加自检 gate:产物写入后自行执行 lint/check + 模板边界检查,通过后只回"done/OK"。主 agent 仅在全部完成后做一次汇总 gate。

- **预期收益:省 ~15–20 分钟**(消除串行验收)。

### 2. 增加 watchdog + 快速失败

- 派发后主 agent 每 **2–3 分钟** 用 `ListAgents`/`SendMessage` 巡检 worker 状态;
- 为每个 worker 设置硬 deadline(**~10 分钟**),超时立即 cancel 并重派。

- **预期收益:消除 8 分钟死区,避免 03 号 worker 拖 20 分钟才被取消。**

### 3. 失败重派批量并行化

对所有未完成/破损的帧一次性并行重派,而不是逐个发现、逐个重派。

- **预期收益:03/04 的重建可并跑,缩短关键路径 4–6 分钟。**

### 4. 针对性处理"困难帧"

the-business / the-balance-sheet 是数据/表格密集帧,两轮均失败:

- 为表格类帧提供专用模板;
- 或把这类帧拆成两个简单帧(帧数增加、单帧复杂度下降)。

### 5. 封堵 WebFetch 问题

worker 构建 HTML 帧不应依赖联网——所有规则/蓝图已内联在 packet,联网是纯风险(澄清:当时 github.com 域名校验失败引发重试循环的并非 frame worker,而是并行 Q&A 会话的子代理,见"更正"注。**本条未实施、已回滚**,保留供参考):

- 在 skill 提示词中明确"禁止 WebFetch";
- 所需参考资料提前下载进项目目录;
- 或在 settings 中将必要域名加入 WebFetch 白名单。

### 6. 队列并发管理

避免两个构建任务在同一队列上撞车:若并发构建是常态,考虑 worker 池上限隔离,或错峰调度。

## 四、预期效果

| 方案 | 预期收益 |
|---|---|
| 仅做 1(验收下沉) | 63m → ~40m |
| 1 + 2 + 3(验收下沉 + watchdog + 批量重派) | 63m → **~20m 以内** |

正常 12 帧视频的 build-frame-html 应控制在 **~20 分钟**:首轮完成 ~15–18 分钟(API 首字节 p50 仅 1.78s,模型响应不是瓶颈)+ 批量收尾 gate ~2–3 分钟。

## 附录:历史 `claude -p` 调用响应速度参考(2026-11-07 ~ 11-13,共 12 次)

| 指标 | 数值 |
|---|---|
| 队列等待 | ~0ms |
| claude 进程启动(出队→首条 user 消息) | ~0.1s |
| 首次模型响应(出队→第一条 assistant 输出) | min 3.0s · **p50 11.7s** · 均值 14.9s · max 36.8s |
| API 首字节延迟(431 次请求样本) | **p50 1.78s** · 均值 1.91s · p90 2.68s · p99 4.11s · max 5.65s |
| 会话总耗时(12 次) | min 17.5s · p50 45.5s · 均值 11.0m · p90 38.7m · max 63.3m |

模型 API 延迟稳定且在毫秒到秒级,长任务的耗时瓶颈在编排(扇出、验收、重试),不在模型响应速度。

## 实施记录(2026-11-13,优化 1 / 3;优化 5 已回滚)

落地了 1(验收下沉)、3(失败重派批量并行)两项;5(禁 WebFetch)一度落地,后因证据误归因(该次 WebFetch 重试循环实为并行 Q&A 会话的子代理所为,与 frame worker 无关,见根因更正注)**已回滚**:

**1. 新增单帧自检脚本 `/app/scripts/frame-selfcheck.mjs`**

worker 端结构自检 gate,覆盖尚未接入 index.html 的单帧可判定的确定性规则(对照 `hyperframes lint` 规则码):

- 文件存在且非空;
- 单一 `<template>…</template>` 包裹;
- `#root` 在 template 内、带 `data-composition-id` 与正数 `data-duration`;
- **模板边界**:所有 `<style>/<script>`(含 GSAP 加载)必须位于 template 内;
- 每个 `class="clip"` 带 `data-start`/`data-duration`/`data-track-index`;
- 同轨 clip 时间窗不得重叠(`timeline_track_too_dense`);
- 存在 `gsap.timeline({ paused: true })` 且注册于 `window.__timelines["<id>"]`;
- WARN(不阻断):CSS transition 使用、`#root` 上画背景、id 与文件名不一致。

已验证:对已完成视频 12 个真实帧全部 PASS;人为破坏(去掉 data-duration / 模板外 style / 同轨重叠)的副本均正确 FAIL。

**2. 改造 worker 角色 `sub-agents/frame-worker.md`(两份副本)**

- 终态动作:写文件 → 跑 `frame-selfcheck.mjs` → 修复至 `PASS` → 回复 `done OK <frame_id>`;无法通过则回复 `fail <frame_id>: <原因>`;
- 编排器信任 `done OK` + 磁盘产物,不再代 worker 复验或修改。

**3. 改造编排器 `SKILL.md`(两份副本)**

- **信任 worker gate**:逐帧验收/编辑循环移除,`done OK` 即标记 `animated`;
- **批量并行重派**:全部 worker 返回后做一次扫描(产物清单 vs STORYBOARD 全部帧),把所有失败/缺失帧汇总成一个列表,**整批并行**重派(附上次失败原因);最多再重派两轮,仍失败即停止并上报,禁止循环,禁止编排器手工修补掩盖问题;
- Quick Reference 增加 `frame-selfcheck.mjs` 条目。

**同步范围**:`/app/.claude/skills/aiflow-build-frame-html/` 与 `/app/hyperframes/skills/aiflow-build-frame-html/` 两份副本已逐字节同步(diff 验证一致);`frame-packets.mjs` 构建 `_role.md` 时读取 hyperframes 副本,新项目自动继承新角色。注意:执行 `npx skills add … --full-depth` 刷新 skill 时会覆盖本地改动,若上游无同款修改需另行保留。