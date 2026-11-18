# build-frame-html 24.2 分钟:effort=max→high 降档实测(未改善,反 +19%)

> 分析对象:`/aiflow-build-frame-html` 的一次 `claude -print` 调用,以及该片全链路各阶段与前一次的对比
> 会话(本次):`1836d789-3e35-4d8a-86fc-c1fe15b2e603`;对照(前一次):`6726b54b-ac28-4097-a457-fbe1d5688845`
> 时间(本次):2026-11-17 14:00:08 → 14:24:19(总耗时 **24.2 分钟**;8 子代理、35 工具回合、模型侧 100%)
> 视频项目:本次 `/app/videos/2026-11-17/userclm17919893695531772/6afc5ba47ec041f4/`;前一次 `/app/videos/2026-11-16/.../6afb004009d47552/`(均为 **8 帧**)
> 分析日期:2026-11-17
> 关键词:effort 封顶、marathon thinking、max_tokens 128k、thinking cap

**结论先行(一句话):本轮是 [[htmlvideo-subagent-effort-cap]] 的实测验证——effort 降档(max→high)机械上确实生效了(`agentType=frame-html-worker` + 全部 94 个请求 `effort:high`),但它**没有压住马拉松思考**,本阶段耗时反从 20.4m 涨到 24.2m(+19%,+3.8m),最慢 worker 单回合思考从 8.4m 拉长到 16.1m。降档不产生思考预算约束;真正该上的是 thinking cap,且落点是 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`(设 `max_tokens` 封 thinking+output)——`MAX_THINKING_TOKENS`/`CLAUDE_CODE_MAX_THINKING_TOKENS` 的 budget_tokens 在本部署被 adaptive 后端剥除(实测请求体 `thinking:{"type":"adaptive"}`、0 个请求含 budget_tokens)。**

---

## 数据来源

- 会话转录:`skill-timing.py`(list/group/detail/timeline)对 `/app/log/claude/projects/**/*.jsonl` 的统计;子代理 `agentType` 直接 cat `subagents/agent-*.meta.json` 核对
- API 请求体:`/app/log/api-bodies/*.request.json` 按 mtime 窗口切片,提取 `output_config.effort` 与 `max_tokens`
- 对照口径:两次均为同用户、同管道、**同 8 帧 / 8 worker**,唯一显式变量是 effort(max vs high)

---

## 一、全链路各阶段对比(前一次 vs 本次)

| 阶段 | 前一次(11-16/17) | 本次(11-17) | Δ | 结论 |
|---|---|---|---|---|
| identify-intent | 42.7s | 21.1s | **−50%** | ✓(③ 已落地;免工具 ①/thinking cap ② 仍未做,未到 4–6s 地板) |
| build-storyboard | 4.4m | 2.2m | **−50%** | ✓(②③ 已落地;① thinking cap 未做,距 1–1.5m 差半档) |
| build-frame-visual | 10.4m | 7.3m | **−30%** | ✓(P0 一次 Write 已落地) |
| **build-frame-html** | **20.4m** | **24.2m** | **+19% ✗** | **本阶段唯一退步,本文重点** |
| verify-frame | 17.0m | 1.9m | **−89%** | ✓✓(P3 机制全生效;11 回合、无返修、无故障诊断) |
| **合计(pre-render)** | ~52.9m | ~36.0m | **−32%** | 净进步 |

> identify-intent(12:50)严格说略早于「最近 1 小时」窗口(14:46 − 1h = 13:46),但它是本次视频的前置阶段,一并列入保持链路完整。

**净结论**:全链路 −32% 是净进步,但 build-frame-html 已从「占总链路的 39%」升到 **「占 67%」**,成为绝对瓶颈,且是唯一反向的阶段。

---

## 二、build-frame-html 重点:effort 降档「生效了,但没用」

### 2.1 机械层面:effort=max→high 确认已落地(待验证项 → 已验证 ✅)

api-bodies 按 mtime 窗口切片,`output_config.effort` / `agentType` 三方交叉:

| 指标 | 前一次 | 本次 |
|---|---|---|
| 子代理 `agentType` | `claude`(内置 catch-all) | `frame-html-worker`(项目级定义) |
| 请求 effort(全窗口) | 105 个全 `max` | 94 个全 `high`(含 3 orchestrator + 89 worker) |
| `max_tokens` | 128,000 | 128,000 |

即:[[htmlvideo-subagent-effort-cap]] 里「待验证:`effort:` 是否确为 agent frontmatter 键名」现已闭环——**键名正确、项目级 `frame-html-worker.md` 里的 `effort: high` 被正确注入**,内置 agent 被 BE veto 的问题已绕过。同时可观察到降档不止作用于 worker,**orchestrator 也一并降到 high**(前一次全 max 是管道 env 强加的偏差,现在整体回落)。

### 2.2 效果层面:耗时反增,+3.8m 全在「最慢的一帧」

两次的 8 个 worker(每 worker 一帧)墙钟分布:

| | worker 墙钟(min→max) | 均值 | 最慢 | 次慢 |
|---|---|---|---|---|
| 前一次(max) | 11.9 / 12.3 / 12.7 / 13.2 / 13.3 / 13.6 / 14.8 / 17.6 | 13.7m | 17.6m | 14.8m |
| 本次(high) | 9.1 / 11.0 / 11.5 / 12.6 / 14.8 / 15.3 / 20.4 / 21.4 | 14.5m | **21.4m** | **20.4m** |

- 均值小涨(13.7→14.5,+0.8m),但**尾部显著拉长**:最慢/次慢从 17.6/14.8 涨到 21.4/20.4。
- 前台「首轮等待」反而缩短(父会话最大 Agent 等待 682s→550s),但**总 wall +3.8m 完全由最慢那一帧贡献**——它从 17.6m 涨到 21.4m,与整段 +3.8m 几乎 1:1 对应。

### 2.3 决定性证据:马拉松思考没被压住,反而更重

最慢 worker(`a8517474`,本次)vs 前一次最慢(`af4408ea`)单 worker 剖面:

| | 总时长 | 最大单回合 | thinking 字符 | 可见文本 |
|---|---|---|---|---|
| 前一次(`af4408ea`) | 17.6m | **506s(8.4m)** | 175,111(12 block) | 4,093 chars |
| 本次(`a8517474`) | 21.4m | **967s(16.1m)** | 268,990(8 block) | **164 chars** |

本次最慢 worker 的时间线呈教科书式「马拉松思考」:

```
14:02:42–14:03:34  Read×6 + Bash×3(读帧/环境,~1m)
14:07:09           Bash
14:07:09 ── +967.5s(16.1 分钟,零工具调用)──► 14:23:16  Write(一次性落盘)
14:23:21           收尾 Bash
```

即:该 worker 在 16 分钟里闷头生成 ~269KB(≈67k token)思考、可见文本只有 164 字符,最后一次性 Write。次慢 worker(`a6f33a07`,20.4m)同病:17 回合带 487s + 332s + 216s 三连长发回合,thinking 258KB、可见文本 401 chars。

**对照**:前一次最慢是「多回合迭代收敛」型(14 回合、Bash+Edit 交错、thinking 175KB、可见文本 4,093 chars);本次是「单回合一次性憋完」型,思考更集中、更极端。

### 2.4 根因:effort 档位对这个后端不产生 thinking 约束,且 128k 天花板取消了 fail-fast

1. **effort=high 未改变思考深度**。降档后最坏单回合不降反升(8.4m→16.1m),说明 deepseek-v4-pro-0813 @ qianfan 端点对 max/high 档位的「思考量」解释差异极小或未实现——这正是 [[htmlvideo-subagent-effort-cap]] 预先标注的风险(「后端对该档位的实际解释以本轮实测为准」),现已坐实:*档位开关是这个方向上的死胡同*。
2. **`max_tokens=128,000` 抹掉了 11-14 的「截断 fail-fast」缓冲**。11-14 事故时上限 32k,马拉松回合会在 ~11 分钟被截断并暴露(尽管当时被误报为 end_turn 假完成);现在两次实测上限都是 128k,一个回合可以合法地想满 16+ 分钟而不触发任何截断或错误。旧的「无声截断→假完成→死区」故障链消失,代价是坏回合不再 fail-fast、直接延长关键路径。
3. **「骨架先行」纪律未被遵守**。[[htmlvideo-subagent-effort-cap]]/deadzone 深挖已把「骨架先行(1–2 回合先落最小骨架再迭代)」写进 `frame-worker.md` 的 Ruled conventions,但本次最慢 worker 仍是「憋一整个 16 分钟回合再一次性 Write」——纪律存在但没有被强制执行/触发。

---

## 三、结论与下一步

- **本阶段唯一真实的下一个杠杆是 thinking cap,落点是 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`(`MAX_THINKING_TOKENS` budget_tokens 被 adaptive 后端剥除、实测无效)**,与 verify-frame / storyboard / identify-intent 三篇分析的收敛点完全一致。给 worker 设 thinking 预算(≈8k token → 单回合强制封顶 ~3 分钟),直接对治上述 16.1m / 8.4m 型马拉松回合;它与 effort 档位是正交的(一个封"档",一个封"预算",后者才是硬约束)。
- 若 thinking cap 也实测不生效(该部署需 A/B 验证是否真的透传),退路是把 worker 的 `max_tokens`(输出)单独压低到 ~16k,让马拉松回合重拾「截断→检测→带断点重派」的 fail-fast 通道。
- 顺带两件小事,见下。

---

## 附:两项顺带发现(非本阶段耗时,但与本轮数据质量相关)

1. **skill-timing.py 的 agent 标签 bug**:`detect_identity` 用 `path + ".meta.json"` 找 meta,但实际文件名是 `agent-XXX.meta.json`(不含 `.jsonl`),导致 `--agents` 列表 label 恒为 `agent` 而非 `agent:frame-html-worker`(本文的 agentType 是直接 cat meta 文件得到的)。一行路径修正即可。
2. **verify-frame 1.9m 待核查**:11 回合、out 仅 9,955、无返修、无故障诊断,虽完整跑完 check/lint/snapshot,但相比 P3 预期 10–12m 快得反常。大概率是「本片帧质量尚可 + 目录顺畅」,但建议确认它确实 snapshot 了全部 8 帧而非被某处 GATE 提前短路。

## 实施记录

- 2026-11-17:本次为 effort 封顶(项目级 `frame-html-worker.md` + `effort: high`)落地上线后的首个真实 build-frame-html 闭环实测;结论为「键名正确、降档生效,但降档对 deepseek backend 无思考约束作用,阶段耗时 20.4m→24.2m」。thinking cap 尚未落地。