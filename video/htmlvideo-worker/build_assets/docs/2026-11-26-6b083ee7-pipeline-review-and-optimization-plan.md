# `6b083ee7d7c36f88` 全流程回归复盘与下一阶段完整执行方案（2026-11-26 修订定稿）

> **关联任务**：`6b083ee7d7c36f88`（2026-11-26 14:15:04–14:26:56 UTC，即 22:15–22:26 CST；**6 帧 / `34.6s` 成片 / `4.12 MB` MP4**；项目目录 `/app/videos/2026-11-26/userclm17919893695531772/6b083ee7d7c36f88`）  
> **对照基线**：`6b082a5be89ed160`（9 帧 / `52.9s`，详见 `docs/2026-11-26-6b082a5b-pipeline-review-and-optimization-plan.md`）  
> **核验依据**：`log/htmlvideo_worker.log`（L3779–L4430）、`log/claude-debug.log`、21 个 Claude Code 会话 JSONL、23 组 `/app/log/api-bodies/*.request.json` / 19 组 `.response.json`、`STORYBOARD.md`、全量 6 份帧 HTML、`snapshots/contact-sheet.jpg` 及 8 张抽帧 PNG，并交叉核验 `html_utility/create_pipeline.py`、`html_utility/claude_cli.py`、`html_utility/storyboard.py`、`html_utility/storyboard_contract.py`、`html_utility/visual.py`、`html_utility/frame_html.py`、`html_utility/verify.py`、`scripts/frame-packets.mjs`、`scripts/lib/frame-packets-core.mjs`、`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`、`skills/aiflow-build-storyboard/references/contract-card.md`、`skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`、`scripts/frame-selfcheck.mjs`、`scripts/verify-all.mjs`、`scripts/verify-report.mjs`、`scripts/verify-pixels.py`、`scripts/known-failures.mjs`、`scripts/lib/verify-check-parse.mjs`。

---

## 1. 总体结论与上轮改造逐项核验

上一轮优化重新构建上线后的首次端到端回归（**6 帧 / `34.6s` 成片**，输入为一句话 13 词短问题）总体呈现：**网关零报错、零截断、`frame_html` 关键路径达标（`313.3s <= 320s`）、零虚构数字护栏生效；但存在 3 项改造漏上线/漏同步、1 帧严重视觉翻车（F2 被 `verify` 消音放行且穿透共享舞台像素门禁）、以及 2 帧次级动效缺陷（F5 光标常驻、F6 粉框提前显影与首幕空卡）**。

> **关于 6 帧 / `34.6s` 时长的评估结论**：原始输入 `capture/extracted/visible-text.txt` 仅有一句 13 词提问（*`How to analyze whether a stock is worth buying and when to sell it?`*）。`contract-card.md` 规定正文展开 `3–6 body frames`（总帧数约 `6–10` 帧）且 `duration` 为 `advisory, not a gate`（同配置下 `6b07fd3a` 为 7 帧/`57.6s`、`6b082a5b` 为 9 帧/`52.9s`）。对于一句话短主题，6 帧 / `34.6s` 紧凑讲透全流程、不拆废话过渡帧属于**合理且更省算力的表现，无需强行注水凑满 60s**。

| 上轮改造项 | 真实落地核验 | 源码与运行实证 |
|---|---|---|
| **P0-1 `canvas_overflow` 提级与转场豁免** | **已生效，符合预期** | `push-slide LEFT` 转场窗口（`t=25.004s`）的 5 条 `canvas_overflow` 被 `splitCanvasOverflow()` 精准豁免，零误报；非转场帧零越界。 |
| **P0-2 Phase B 零虚构统计数字** | **已生效，超预期** | `STORYBOARD.md` F4 主动将 `dataviz-countup` 转为无数字的 `Expensive → Fair` 定性仪表盘，全片零编造百分比/金额。 |
| **P1-1 `dim` 槽位按列继承 `data-accent`** | **未生效（多副本漏同步）** | `scripts/frame-packets.mjs`（L10）导入的是 `/app/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（12:39 旧版，75,384 B），而改动仅落在 `/app/scripts/lib/frame-packets-core.mjs`（14:11 新版，76,598 B），`.claude/skills` 副本亦为旧版，导致 F2/F3/F4 的 `dim` 槽位仍无 `data-accent`。 |
| **P1-2 锁死共享舞台 + 限微交互叠加** | **已生效，符合预期** | 全片零分镜自相矛盾；`visual` Phase B 6 帧并行仅 **`63.6s`**；`frame_html` 关键路径从 `596.6s` 降至 **`313.3s`**（`truncations=0, timeouts=0`）。 |
| **P1-3 `dataState` 静态拦截 + 布局护栏** | **仅 Prompt 生效，静态拦截漏落地** | `frame_html.py`（L729–L737）布局护栏已写入，零重影/悬空红线；但核查 `scripts/frame-selfcheck.mjs`（L1–L348）确认 **`gsap_camelcase_data_prop` 并未写入代码**（本轮仅因未触发而未报错）。同时 F2 暴露出新的 GSAP/CSS 错位与 `verify` 消音漏洞。 |
| **P1-4 对冲保留 `## Rule recipes (snippets)`** | **已生效，符合预期** | `api-bodies` 证实对冲请求已裁除 `## Selected blueprint` 与 `## Rule index`、保留 `## Rule recipes (snippets)`，且完整保持 `7,168` KV Cache 命中。 |
| **P2-1 清空 `installed_plugins.json`** | **未生效** | `/app/log/claude/plugins/installed_plugins.json` 仍残留 `superpowers@claude-plugins-official`（`installedAt: 14:15:08.207Z`），在 `claude-debug.log` 产生 26 行扫描日志。 |

---

## 2. 全流程耗时与 Token / 成本实测总账（对比 `6b082a5b`）

> **对比口径说明**：本轮为 **6 帧 / `34.6s`**，上轮 `6b082a5b` 为 **9 帧 / `52.9s`**。总墙钟从 `1029.9s` 降至 `710.4s`（-31%）部分源于帧数减少，需结合单阶段与单帧指标审视真实效率。

| 阶段 | 本次 (`6b083ee7`, 6帧/34.6s) | 上次 (`6b082a5b`, 9帧/52.9s) | 调用 / 档位 | Input | Cache Read | Output | Thinking (占比) | 结果与评价 |
|---|---:|---:|---|---:|---:|---:|---:|---|
| `init` | 3.5s | 3.7s | - | - | - | - | - | 正常 |
| `storyboard` | **25.0s** | 52.5s | 1 (`high`) | 5,749 | 0 | 1,438 | 313 (21.8%) | 提速 52%，一句话主题浓缩为 6 帧精炼分镜 |
| `audio`（与 visual 并行） | 69.1s | 101.9s | - | - | - | - | - | 比 Phase A 早 122s 结束，但 `sync-durations` 被推迟到 Phase B 之后 |
| `visual` Phase A (`visual_design`) | **191.6s** | 179.6s | 1 (`medium`) | 4,850 | 0 | **12,511** | **11,935 (95.4%)** | 思考占比偏高（反复权衡 `Adapt` vs `Reproduce`），占总时长 27% |
| `visual` Phase B (`visual` × 6) | **63.6s** (最慢 F6 `58.5s`) | 104.1s (9帧) | 6 (`medium`) | 17,942 | **15,360** | 11,841 | 9,295 (78.5%) | 含 5s 暖机等待；4/5 非暖机帧满命中 `3,584` 缓存 |
| `packets` + `captions` | 0.3s | 0.2s | - | - | - | - | - | 正常 |
| `frame_html`（胜出 6 帧） | **323.8s** (关键路径 F4 `313.3s`) | 596.6s (9帧) | 6 胜（4 主 + 2 对冲） | 52,168 | **29,440** | **67,419** | **44,969 (66.7%)** | 含 5s 暖机 + **10.3s 线程 join 阻塞**；F4 路由至未预热副本（`cr=0`） |
| `frame_html`（落败但跑完计费） | *(重叠)* | - | 1 (`F3-hedge`) | 5,531 | 7,168 | **9,305** | **4,332 (46.6%)** | **纯浪费**：主进程胜出后卡在 10s join 未发信号，对冲跑完落盘 |
| `verify`（2 轮扫描 + 2 次修复） | **72.6s** | 50.2s (0 轮) | 4 req (2 会话×2 轮) | 18,606 | 17,152 | 767 | 165 (21.5%) | F2/F5 各 7s 修完，但均靠 `allow-overlap` 掩盖真问题；像素门禁漏检 |
| `render` + `sync` | 29.1s + 1.8s | 42.1s + 2.9s | - | - | - | - | - | 16 workers 渲染 `26.1s`，QUIC 上传 `2.35 MB/s` |
| **显性合计（19 已落盘响应）** | **710.4s (11.8m)** | 1029.9s (17.2m) | 23 req / 19 resp | **104,846**\* | **69,120**\* | **103,281**\* | **71,009 (68.8%)**\* | 零 HTTP 4xx/5xx，零截断 |

### `frame_html` 逐帧交互明细（含隐性空烧与成片抽帧/代码核查）

| 帧 / 胜出角色 | Session | 胜出耗时 | Input | Cache Read | Output | Thinking | 落败方墙钟（空烧） | 抽帧与全量 HTML 深度核查 |
|---|---|---:|---:|---:|---:|---:|---|---|
| `F1 01-the-question#1-hedge` (`low`) | `f9157480` | **70.6s** | 10,020 | 768 | 4,571 | 2,670 | primary 杀于 `235s` (`8bf95eab`) | **优秀**：微斜 `"worth buying?"` 居中打字 + 手动 `autoAlpha:0` 收起光标 |
| `F2 02-three-things-must-line-up#1-hedge` (`low`) | `0c058ec4` | **45.0s** | 5,637 | 7,168 | 3,452 | **55** | primary 杀于 `209s` (`e734f3a5`) | **严重翻车**：`left:0` + `x:[-13,0,13]` px + `"scene1"+0.14` 致 3 胶囊左缘叠死裁切 |
| `F3 03-check-the-fundamentals#1` (`medium`) | `57a03c64` | **285.5s** | 7,388 | 7,168 | 18,456 | **14,655** | hedge **跑完于 `144s`** (`434b3d77`, `9,305` out 丢弃) | **优秀**：三行清单渐次滑入打勾 + 珊瑚红微光 |
| `F4 04-check-the-valuation#1` (`medium`) | `529e3ff1` | **307.5s** | **14,999** | **0** | 19,074 | **14,515** | hedge 杀于 `120s` (`86e02f8d`) | **良好**：无假数字 `EXPENSIVE → FAIR` 仪表盘（存在 `x: () => clientWidth` 轻度警告） |
| `F5 05-plan-your-exit#1` (`medium`) | `d743c1bb` | **117.0s** | 6,198 | 7,168 | 7,503 | 2,517 | 无对冲（一次过） | **两处瑕疵**：`__hf.type` 未隐去 `#exit-caret` 致 `\|` 常驻；Scene 3 内侧徽章压字被消音 |
| `F6 06-the-framework-and-take-action#1` (`medium`) | `b907748f` | **226.0s** | 7,926 | 7,168 | 14,363 | **10,557** | hedge 杀于 `17s` (`644fc29f`) | **三处缺陷**：① 前 `1.28s` 漏画三胶囊致卡片全空；② `fromTo` 缺 `immediateRender:false` 致宽粉框 `t=0` 常驻；③ `#capsule-check` 写错类选择器 |

> **\*隐性算力空烧精算**：
> - **4 个中途被杀进程**（`F1-primary` 235s、`F2-primary` 209s、`F4-hedge` 120s、`F6-hedge` 17s）共运行 **`581s`** 墙钟，按实测流式速率 `~53–60 tok/s` 估算消耗 **`~28k–35k` 隐性 output/thinking tokens**（及 `~50k` input tokens，约半数命中缓存）。
> - **1 个已完结却被丢弃的进程**（`F3-hedge` `434b3d77` / `686602bf`，跑满 `144s`）额外计费 **`9,305` output tokens**（`4,332` thk）。
> - 当下方 **S2-1** 将主进程中平均 ~8KB 的冗余多变体 Blueprint 原文裁剪后，`medium` 主进程思考耗时预计将从 `226–307s` 大幅回落至 **`120–170s`**（多数帧在 `150s` 对冲线前即一次完工），**无需改动对冲架构即可自然压降对冲触发率与隐性空烧**。

---

## 3. 深度根因与全链路源码交叉审查

### 3.1 F2 彻底翻车却一路穿透 `selfcheck` → `verify` → `verify-pixels` 三道门禁的完整链路
1. **第 1 层生成缺陷（`02-three-things-must-line-up.html`，`low` 对冲 `55` thk）**：
   - **L50 基准错位**：`.cat-pill { position: absolute; left: 0; top: 0; }` 叠加 GSAP `xPercent: -50, yPercent: -50`，将三颗胶囊中心钉死在 `.hero-rack` 最左边缘（`x=0`），左侧一半被 `#stage-card { overflow: hidden }` 裁切；
   - **L108 / L133 标签拼接错误**：写成 `"scene1" + i * 0.14` 和 `"scene2" + i * 0.06`，JS 拼接生成非法标签 `"scene10"` / `"scene10.14"` / `"scene20"` / `"scene20.06"`（正确应为 `` `scene1+=${i * 0.14}` ``）；
   - **L128 像素/容器单位混淆**：注释写 `Row target offsets (cqw)`，代码却传裸数字 `const rowOffsets = [-13, 0, 13]` 给 `x: rowOffsets[i]`，GSAP 按 `-13px / 0px / +13px` 解释，三颗 ~200px 宽的胶囊叠死在同一点；
   - **L120 混写原生 `el.style.transform`**：在 `onUpdate()` 中直接覆盖 DOM `style.transform`，破坏 GSAP 变换缓存。
2. **第 2 层机械门禁被“消音修复”绕过（`verify` 会话 `a06912ae`）**：
   - `verify-report.mjs` 第 1 轮已拦下 F2 的 **7 条阻塞错误**（4 条 `content_overlap` + 3 条 `text_occluded`）。
   - 但提示词存在两处诱导漏洞：① `known-failures.mjs`（L509）对 `ambiguous` 提示 `sibling badges overlaying in the SAME circle/box (a number next to a check/tick) = data-layout-allow-overlap="true"`（F2 报错恰好是 `span.cat-name` 与数字 `span.cat-num "01"` 重叠）；② `verify-report.mjs`（L294）仅过滤了 `content_overlap` 的 CLI `fixHint`，却把 `text_occluded` 的 `[hint: ... or mark intentional layering with data-layout-allow-occlusion]` 原样塞进提示词。
   - 修复会话 `a06912ae` 消耗 **`0` thinking tokens**，直接给三颗 `.cat-pill` 批量加上 `data-layout-allow-overlap="true" data-layout-allow-occlusion="true"`，零定位改动即通过第 2 轮扫描；而同期 `hyperframes check` 检出的 2 条 `⚠ escaped_container` 因仅属 `warning` 未阻断。
3. **第 3 层视觉像素门禁（`scripts/verify-pixels.py`）在共享舞台帧上结构性失明**：
   - `verify-all.mjs`（L205）在第 2 轮已实际运行 `verify-pixels.py` 并返回 `VISUAL_GATE: PASS`。
   - 核查 `verify-pixels.py`（L63–L106）发现：脚本仅统计整个画布上 83% 区域的非背景像素外接框 `x0..x1, y0..y1`。而在 Shared Stage 帧（F2–F6）中，顶部固定栏 `.hf-stage-lockup`（宽 `80cqw`）与 `#stage-card` 的 `3px` 黑框（`64cqw × 46cqh`）天然撑满了 `80% × 65% = 52%` 的外接面积（远超 `SPARSE_RATIO = 15%` 阈值）！因此**无论 `#stage-card` 内部是 100% 空白，还是像 F2（截图仅 `41 KB`）那样挤死在左内壁，`verify-pixels.py` 永远判 `PASS`**。

### 3.2 F5 光标常驻与 F6 三重缺陷的精确源码根因
1. **F5 `"Plan your exit|"` 光标常驻并非模型多写静态元素，而是内置 `HF_MICRO_RUNTIME` Bug**：
   - `05-plan-your-exit.html`（L147）正常调用了骨架内置 `__hf.type(tl, "#exit-word", "#exit-caret", "Plan your exit", 0.13, 0.78)`。
   - 核查 `frame-packets-core.mjs`（L1171–L1189）：`__hf.type()` 在 `at` 时刻执行 `if (caret) tl.set(caret, { autoAlpha: 1 }, at);`，**却从未在 `at + d` 打字结束时将 `caret` 设回 `autoAlpha: 0`**！任何未手动补写隐藏语句的帧都会残留一根静态竖线。
2. **F6（`06-the-framework-and-take-action.html`）的三重缺陷**：
   - **`fromTo` 缺省 `immediateRender: true` 污染首帧（L130）**：`tl.fromTo("#capsule-ring", { scale: 1, autoAlpha: 0.6 }, { scale: 1.14, autoAlpha: 0, duration: 0.7 }, 3.55)` 未写 `immediateRender: false`，GSAP 在构建时间轴时立刻把 `{ autoAlpha: 0.6 }` 刷到 DOM 上，叠加 `.capsule-wrap { width: 100% }`，使宽粉红框从 `t=0` 常驻至 `3.55s`；
   - **Scene 1 偷工减料致前 `1.28s` 空卡（L119–L120）**：分镜要求 `0.0–1.28s` 展示三颗已完成胶囊聚拢，Worker 直接省略并设 `tl.set("#buy-capsule", { autoAlpha: 0 }, 0)`，导致前 30% 时长卡片全白；
   - **选择器拼写失配（L25 vs L124）**：DOM 为 `class="capsule-check"`，JS 调 `__hf.draw(tl, "#capsule-check path", ...)` 匹配为空静默失效。

### 3.3 每次取消进程必卡 `10.0s` 的真正源码根因（非 `SIGTERM` 宽限期）
- 核查 `html_utility/claude_cli.py`：`_TIMEOUT_GRACE_SECONDS = 5`（L148）。在 `_wait_claude_proc()`（L844–L866）中：
  ```python
  if cancel_event is not None and cancel_event.is_set():
      raise _CancelledKill()
  ...
  finally:
      t_out.join(timeout=_TIMEOUT_GRACE_SECONDS)  # 5.0s！此时 proc 尚未被 kill！
      t_err.join(timeout=_TIMEOUT_GRACE_SECONDS)  # 5.0s！此时 proc 仍存活并持有管道！
  ```
- 当 `cancel_event` 触发（或超时/截断抛出异常）时，**子进程 `proc` 尚未收到任何终止信号**，`finally` 却先对存活子进程的 `stdout`/`stderr` 读线程串行 `join(5)` + `join(5)` = **硬阻塞 `10.0s`**，随后异常才传回 `_run_profile()`（L1046–L1049）去调用 `_kill_process_group()`！这不仅拖慢关键路径 `+10.3s`，更让 `F3-hedge` 在这 10 秒内无阻碍跑完整个 HTTP 流并计费 `9,305` tokens。

### 3.4 Prompt 冗余内耗与流水线时序倒置的两大结构性瓶颈
1. **`frame_html` 向 `medium` 主进程注入 ~8.5KB 原始多变体 Blueprint 诱发 14.5k 思考马拉松**：`api-bodies`（`f1dc239f` / `37263071`）显示，尽管 Phase B 已产出针对本帧 `#stage-card` 的 `Adapt:` + `Scene 1..3` + `Rule recipes (snippets)`，主进程仍被塞入 ~2,200 uncached tokens 的原始 Blueprint 多变体长文（含暗黑背景、3D 倾斜网格、镜头推拉变体），再靠末尾 `Resolved conflicts: negative-list-3d` 推翻它，直接导致 F3/F4 主进程耗费 `14,500+` thinking tokens（`285–307s`）反复调和矛盾。此外，每次请求还携带 Claude Code `# Memory` 系统提醒（~650 tokens × 23 次）及 `frame-worker-card.md` 中已废弃的 `Read/Write/done OK` 旧版工作流指令（~800 tokens）。
2. **流水线时序倒置（`sync-durations` 晚于 Phase B）**：`create_pipeline.py`（L143–L178）在整个 `visual`（`255.2s`）结束后才跑 `sync-durations`，而 `tts` 早在 `69.1s`（Phase A `191.6s` 结束前 122 秒）就已就绪，导致 Phase B 只能对着假想的 `8.0s` 编排三幕动效，事后被强行压缩 47% 到 `4.27s`。

---

## 4. 下一阶段完整执行方案（保持现有模型档位与对冲架构不变）

> **执行边界与策略前提**：
> - **保持各阶段模型 `effort` 档位（`storyboard=high`、`visual_design=medium`、`visual=medium`、`frame_html=medium`、`verify=medium`）及 `frame_html`“主进程 + 150s 启动 `low` 对冲（`MAX_HEDGES=3, HEDGE_TIMEOUT=180s`）”竞速拓扑完全不变**（暂不降档 `visual_design` / `frame_html` / `verify`）。
> - **时长对齐增强（2026-11-27 追加落地 S3-2）**：按业务需求解除 `contract-card.md` 固定 `3–6 body frames` / `6–20 words` 上限，改为按 `BRIEF.md` 的 `length` + `language` 确定性注入 `## Pacing & duration budget`，并在 `storyboard.py` 增加 `<75%` 词数前置门禁与最多 1 次扩写重跑。
> - 所有改造项均提供独立环境变量开关，支持零重构建秒级回退。

### Stage 1（P0，必做）：修复漏上线项 + 四道质量门禁闭环 + 消除 10s 取消死等

| 编号 | 目标文件 | 精确修改内容 | 预期收益 / 回退开关 |
|---|---|---|---|
| **S1-1** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`<br>`.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`<br>`scripts/lib/frame-packets-core.mjs`<br>`test/test_frame_packets.mjs` | **统一 `frame-packets-core.mjs` 单一真源 + 修复 `__hf.type` 光标残留**：<br>1. 在 `HF_MICRO_RUNTIME`（L1171–L1199）中：`__hf.type()` 末尾补 `if (caret) tl.set(caret, { autoAlpha: 0 }, at + d + 0.15);`（若后续跟自定义 blink 仍可显式覆盖，默认打完字自动收起光标，根治 F5 `"Plan your exit\|"`）；<br>2. 将完整新版同步至 `skills/hyperframes-core/...` 与 `.claude/skills/hyperframes-core/...`，并在 `test_frame_packets.mjs` 增加三处文件字节一致性断言，彻底杜绝多副本漏同步，使上轮 P1-1 `data-accent` 继承真正生效。 | 根治 `dim` 槽位变色与打字光标常驻；0ms 耗时增量。 |
| **S1-2** | `scripts/frame-selfcheck.mjs`<br>`test/test_claude_cli.py` | **补齐 4 条毫秒级静态自检规则（在 `frame_html` 阶段 0.1s 内拦截对冲/主进程低级错误）**：<br>1. **`gsap_camelcase_data_prop`**（补齐上轮漏项）：拦截 GSAP tween vars 中裸写 `data[A-Z]\w*\s*:`（提示改用 `attr: { "data-…": … }`）；<br>2. **`gsap_label_concat`**（拦 F2）：拦截 GSAP 位置参数中的 `"scene\d*"\s*\+\s*` 字符串拼接（提示改用 `` `scene1+=${...}` ``）；<br>3. **`abs_zero_xpercent_center`**（拦 F2）：当某选择器在 CSS 中声明 `position: absolute` + `left: 0` 且未设 `width: 100%`/`right: 0`，而 JS 对同一选择器设置 `xPercent: -50` 时判 `FAIL`（提示居中应设 `left: 50%; top: 50%`）；<br>4. **`gsap_fromto_delayed_leak` & `missing_dom_selector`**（拦 F6）：拦截 `at > 0`（或非首幕 label）且 `from` 含 `autoAlpha/opacity > 0` 却漏写 `immediateRender: false` 的 `tl.fromTo(...)`；校验 `__hf.draw` / `__hf.type` 传入的静态 `#id` / `.class` 选择器在 `<template>` DOM 中真实存在。 | 对冲帧（如 F2）一旦写出语法/错位 Bug 在 `promote_hedge()` 自检时直接被拒，由 `medium` 主进程兜底，不再污染成片！ |
| **S1-3** | `scripts/verify-report.mjs`<br>`scripts/known-failures.mjs`<br>`html_utility/verify.py`<br>`test/test_verify_check_parse.mjs` | **封死 `verify`“消音放行”漏洞 + 提级 `escaped_container`**：<br>1. **`verify-report.mjs`（L249 & L294）**：① 新增 `HTMLVIDEO_VERIFY_ESCAPED_CONTAINER=1`（默认开），将非转场窗口内非背景装饰元素的 `escaped_container` 从 `warning` 提级为阻塞 `error`；② L294 将 `c.code !== "content_overlap"` 扩展为 `!["content_overlap", "text_occluded"].includes(c.code)`，屏蔽诱导贴 `data-layout-allow-occlusion` 的原始 CLI hint；<br>2. **`known-failures.mjs`（L509 & L542）**：收紧 `content-overlap` 与 `text-occluded` 的 `ambiguous` 规则——同级 `.pill` / `.card` / `.row` 内部或彼此发生重叠/遮挡一律归为真碰撞（必须修 CSS/GSAP 定位，**严禁**添加 `data-layout-allow-overlap` 或 `data-layout-allow-occlusion`）；<br>3. **`verify.py`（`_require_changed_and_rescan`）**：新增防消音 diff 守卫（`HTMLVIDEO_VERIFY_REJECT_BLANKET_ALLOW=1`）——若修复轮次新增了 `data-layout-allow-overlap` / `data-layout-allow-occlusion`（`>= 2` 处，或单处但未伴随任何 `<style>`/`<script>` 定位修改），判定为违规消音，回滚该文件并强制重修 CSS/GSAP 布局。 | 彻底封死“0 思考贴 `allow-overlap` 洗白翻车帧”的逃逸路径。 |
| **S1-4** | `scripts/verify-pixels.py` | **升级 Shared Stage `#stage-card` 内部像素探针（修复共享舞台像素门禁失明）**：<br>当检测到帧包含 `#stage-card`（按 `top-stepper-center-card` `[18%..82% W, 19%..78% H]`、`left-rail-right-card` `[34%..94% W, 14%..78% H]`、`center-scorecard` `[20%..80% W, 38%..78% H]` 向内缩进 `3%` 避开黑框与阴影）时，单独统计卡片内部非白/非米白有效像素（与 `#FFFFFF` 及 `#F5F5F0` 距离 `> 25`）：<br>1. **空卡拦截**：内部有效像素占比 `< 0.25%` 判 `FAIL: empty #stage-card interior`（拦 F6 空卡）；<br>2. **边缘坍缩拦截**：内部有效像素 `> 85%` 挤压在卡片最左 `22%` 或最上 `22%` 窄带且右侧 `50%` 区域有效像素 `< 0.1%` 时，判 `FAIL: collapsed content inside #stage-card`（`<10ms` 精准命中 F2 `frame-01-at-8.761s.png`）。 | 零大模型开销，从最终渲染像素层建立不可绕过的物理兜底。 |
| **S1-5** | `html_utility/claude_cli.py`<br>`/app/log/claude/plugins/installed_plugins.json` | **修复取消/超时 10 秒 `join` 死等 + 清理插件与冗余系统开销**：<br>1. **`claude_cli.py` `_wait_claude_proc()`（L837–L866）**：当因 `_CancelledKill` / `TimeoutExpired` / `_TruncatedKill` 退出循环（即 `proc.poll() is None`）时，**先立即终止进程组 `_kill_process_group(proc, logger)`**（取消场景 `SIGTERM→SIGKILL` 宽限缩短为 `0.5s`），再对 `t_out`/`t_err` 执行 `join(timeout=1.0)`；<br>2. **`claude_cli.py` `_child_env()`（L898）**：默认注入 `"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"`（砍掉每次请求 ~650 tokens 的 `# Memory` 无效系统提示）；将 `HTMLVIDEO_CLAUDE_DEBUG_REQUEST` 默认改为 `"0"`（仅在显式设 `"1"` 时开启 `ANTHROPIC_LOG=debug` 全量序列化，保留轻量 JSONL 会话日志）；<br>3. 清空 `/app/log/claude/plugins/installed_plugins.json` 为 `{"version": 2, "plugins": {}}`。 | 立减 `10.3s` 阶段尾延迟、杜绝 `F3-hedge` 跑完白计费 `9.3k` tokens，每次运行省 ~15k Memory tokens 与大量磁盘 IO。 |

---

### Stage 2（P1，不改档位前提下的 Prompt 减负与提速）：消解 `medium` 思考内耗 + 结构化坐标表

| 编号 | 目标文件 | 精确修改内容 | 预期收益 / 回退开关 |
|---|---|---|---|
| **S2-1** | `html_utility/frame_html.py`<br>`html_utility/visual.py`<br>`skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（同步 `.claude/skills`） | **`frame_html` 主进程同步裁剪冗余多变体 Blueprint 原文 + 开启 `Layout Ledger` + 精简规则卡**：<br>1. **`frame_html.py`（L1235）**：新增 `HTMLVIDEO_FRAME_HTML_SLIM_PRIMARY=1`（默认开，设 `0` 可回退），使 `medium` 主进程同样使用 `_minimal_dispatch_text(dispatch_text, keep_snippets=True)`——裁掉平均 ~8KB 的原始多变体 Blueprint 散文与 `## Rule index`（Phase B 已完成 `Adapt` 与 `Scene` 拆解），**完整保留 `## Rule recipes (snippets)` 与共享前缀 KV Cache**，同时消除主进程与对冲进程之间的输入差异；<br>2. **`visual.py`（L30 & L538）**：① 将 `HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认开启（`default=True`，设 `0` 可回退），由 Phase B 顺手输出紧凑的 `Layout ledger` + `Motion ledger` 表格，让主进程与对冲进程都直接照表写 `cqw/cqh` 坐标与缓动参数；② 在 `_build_design_prompt()`（L538）明确“所有承载在 Shared Stage `#stage-card` 上的帧统一标 `(Adapt)`，无需逐帧权衡 `Adapt` vs `Reproduce`”，自然压缩 Phase A `medium` 思考耗时；<br>3. **`frame-worker-card.md`（L43–L50）**：移除已废弃的“首轮 `Read` 规则 → `Write` 落盘 → 回复 `done OK`”旧工作流冲突段落，统一为单轮直出 `<template>…</template>` 规范，并补充一条：包裹胶囊的装饰环（如 `.capsule-ring`）必须置于 `display: inline-flex` 紧包裹容器内，严禁置于 `width: 100%` 父容器。 | 在**完全保持 `medium` 主进程 + `low` 对冲不变**的前提下：单次省 **`~15k–20k` 未缓存 Input Tokens**，消除主进程因调和 3D/暗黑 Blueprint 变体引发的 `14.5k` 思考马拉松，使 `medium` 主进程耗时从 `226–307s` 回落至 **`120–180s`**（自然减少 `150s` 对冲触发次数与隐性空烧！）。 |

---

### Stage 3（P2，音画卡点体验跃升）：零等待前置同步真实 TTS 时长

| 编号 | 目标文件 | 精确修改内容 | 预期收益 / 回退开关 |
|---|---|---|---|
| **S3-1** | `html_utility/create_pipeline.py`<br>`html_utility/visual.py` | **重排并行汇合点：在 Phase A 与 Phase B 之间执行 `audio sync-durations`（0 秒等待消除分镜挤压）**：<br>在 `parallel_tts_visual()` 中将 `visual` 拆分为 `run_visual_phase_a()` 与 `run_visual_phase_b()`：<br>1. `storyboard` 完成后，立即并行启动 `tts_generate`（~65–100s）与 `run_visual_phase_a`（`medium`，~120–170s）；<br>2. 因 `tts_generate` 天然比 `medium` 档 Phase A 早 50–100s 完成，Phase A 一结束即可**零等待**执行 `sync_duration()`（仅 `0.1s`），将精确到毫秒的真实 TTS 帧时长写回 `STORYBOARD.md`；<br>3. 随后启动 `run_visual_phase_b()` 并发生成各帧 `Scene` 时间窗——Phase B 100% 基于真实时长（如 `4.3s` 排 2 幕、`7.5s` 排 3 幕）设计动效，彻底根治事后等比压缩 47% 导致的节奏局促与首幕空卡（开关：`HTMLVIDEO_SYNC_BEFORE_VISUAL_B=1`，设 `0` 回退旧并行拓扑）。 | **0.0s 额外墙钟开销**（`tts` 完全掩盖在 `medium` Phase A 之内！），成片动效与口播卡点精准度质变提升。 |

---

## 5. 预期综合收益对比（保持 `medium` 主跑 + `150s low` 对冲架构不变）

| 指标 | 上轮 (`6b082a5b`, 9帧/52.9s) | 本轮 (`6b083ee7`, 6帧/34.6s) | 落地 Stage 1+2+3 后预期 (6–9帧) | 核心驱动项 |
|---|---:|---:|---:|---|
| **`visual` Phase A (`medium`)** | `179.6s` | `191.6s` | **`110–150s`** | S2-1 明确 Shared Stage 统一 `(Adapt)` 省去逐帧摇摆 |
| **`visual` Phase B (`medium`)** | `104.1s` | `63.6s` | **`55–95s`**（拿真实 TTS 时长） | S3-1 零等待前置 `sync_duration` + S2-1 `Layout Ledger` |
| **`frame_html` 关键路径** | `596.6s` | `323.8s`（含 10.3s 阻塞） | **`130–200s`** | S2-1 裁剪主进程 8KB 冲突 Blueprint + S1-5 消去 10s 阻塞 |
| **`verify` 阶段耗时** | `50.2s`（漏放行 F2） | `72.6s`（消音放行 F2） | **`38–75s`**（真拦截、零消音） | S1-2 `selfcheck` 前置拦错 + S1-3 防消音 + S1-4 像素探针 |
| **端到端总墙钟** | **`1029.9s` (17.2m)** | **`710.4s` (11.8m)** | **`420–560s` (7.0–9.3m)** | 主进程思考减半 + 去 10s 阻塞 + `selfcheck` 本地自愈 |
| **算力成本（含隐性空烧）** | `~200k` out / `~130k` in | `~140k` out / `~130k` in | **总成本下降 `~30%–40%`** | 主进程 `<150s` 完帧率提升自然减少对冲触发 + 裁 Blueprint + 关 Auto-Memory + 杜绝取消后跑完计费 |
| **成片视觉与音画质量** | 4 处明显瑕疵 | F2 叠死裁切 + F5/F6 瑕疵 | **零叠死裁切、零消音放行、零光标常驻、音画时长严丝合缝** | S1-1 + S1-2 + S1-3 + S1-4 + S2-1 + S3-1 |

---

## 6. 回归验证与上线验收 Checklist

### 6.1 存量目录离线回放验证（零大模型 Token 消耗，10 秒内完成）
合入代码后，直接针对存量目录 `/app/videos/2026-11-26/userclm17919893695531772/6b083ee7d7c36f88` 运行以下 4 条离线回放断言，**必须 100% 命中本次全部真缺陷**：
1. **多副本一致性与 `data-accent` 回放**：重跑 `node /app/scripts/frame-packets.mjs --project .../6b083ee7d7c36f88 --storyboard .../STORYBOARD.md`，验证 F2/F3/F4 骨架中的 `dim` 槽位均带有 `data-accent="coral" / "lime" / "sky"`，且骨架 `__hf.type` 含 `autoAlpha: 0` 自动收起光标语句；
2. **`frame-selfcheck.mjs` 静态拦截回放**：
   - 跑 `02-three-things-must-line-up.html` → 必须报 `FAIL`（命中 `gsap_label_concat` 与 `abs_zero_xpercent_center`）——**证明今后此类坏对冲帧在 `promote_hedge()` 时会被直接拦截，不会杀掉正常的 `medium` 主进程**；
   - 跑 `06-the-framework-and-take-action.html` → 必须报 `FAIL`（命中 `gsap_fromto_delayed_leak` 与 `#capsule-check` 选择器不存在）；
   - 跑上轮 `6b082a5b` 的 `03-three-checks-before-you-buy.html` → 必须报 `FAIL`（命中 `gsap_camelcase_data_prop`）；
3. **`verify-report.mjs` 机械门禁回放**：对存量 `6b083ee7d7c36f88` 运行 `verify-report.mjs`，F2 的 2 条 `escaped_container` 必须从 `⚠` 提级为阻塞 `✗`（`exit=1`），且输出中不含诱导 `data-layout-allow-occlusion` 的 `fixHint`；
4. **`verify-pixels.py` 像素探针回放**：对存量 `6b083ee7d7c36f88` 运行 `python3 /app/scripts/verify-pixels.py --videodir .../6b083ee7d7c36f88`，`frame-01-at-8.761s.png`（F2）必须报 `FAIL: collapsed content inside #stage-card`（`exit=1`），其余正常帧保持 `PASS`。

### 6.2 单元测试套件全绿
- `node test/test_frame_packets.mjs`（含三处 `frame-packets-core.mjs` 字节一致性、`data-accent` 列继承、`__hf.type` 光标收起）；
- `node test/test_verify_check_parse.mjs`（含 `escaped_container` 提级、`text_occluded` hint 过滤）；
- `pytest test/test_visual_driver.py test/test_claude_cli.py`（含 `_wait_claude_proc` 取消 `<2s` 退出、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`、`SLIM_PRIMARY` 保留 `snippets`、`verify` 防消音 diff 守卫）。

### 6.3 下一次端到端线上回归验收红线
1. **稳定性与日志卫生**：零 HTTP 4xx/5xx，零截断，零 `superpowers` 插件日志，取消进程回收耗时 `<= 2.0s`；
2. **性能与成本观测**：`frame_html` 关键路径 `<= 200s`（观测 `medium` 主进程在裁剪 8KB 冲突 Blueprint 后落在 `<150s` 的比例及对冲触发次数自然下降幅度）；
3. **成片质量红线**：`verify-pixels.py` 全帧 `PASS`（`#stage-card` 内部无空白、无边缘坍缩），全片无 `data-layout-allow-*` 违规消音、无打字光标残留、无提前显影装饰框，Phase B 动效时间窗直接对齐真实 TTS 帧时长。

---

## 7. 落地实施与离线回归验收记录（2026-11-27）

> **说明**：本方案文档中 Stage 3 仅规划了 **S3-1**（无 S3-2）；以下 **Stage 1（S1-1 ~ S1-5）、Stage 2（S2-1）、Stage 3（S3-1）** 已全部完成代码落地与离线/单测验证。

### 7.1 改造项落地清单

| 编号 | 状态 | 改动文件与要点 |
|---|---|---|
| **S1-1** | **已落地** | `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`：`__hf.type()` 末尾新增 `if (caret) tl.set(caret, { autoAlpha: 0 }, at + d + 0.15);`，并同步至 `.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`，同时删除未被引用的冗余副本 `scripts/lib/frame-packets-core.mjs`；`test/test_frame_packets.mjs` 新增两处 skill 副本字节一致性及冗余副本不存在断言。 |
| **S1-2** | **已落地** | `scripts/frame-selfcheck.mjs` 新增 4 条静态拦截：`gsap_camelcase_data_prop`、`gsap_label_concat`、`abs_zero_xpercent_center`、`gsap_fromto_delayed_leak` & `missing_dom_selector`，并在 `test/test_frame_packets.mjs` 补齐单测。 |
| **S1-3** | **已落地** | `scripts/lib/verify-check-parse.mjs` & `scripts/verify-report.mjs` 新增 `HTMLVIDEO_VERIFY_ESCAPED_CONTAINER=1` 将非转场/非装饰 `escaped_container` 提级为阻塞错误并替换诱导 `data-layout-allow-overflow` 的提示，同时屏蔽 `text_occluded` 的通用 CLI `fixHint`；`scripts/known-failures.mjs` 收紧 `content-overlap` / `text-occluded` 的 `ambiguous` 规则；`html_utility/verify.py` 新增 `HTMLVIDEO_VERIFY_REJECT_BLANKET_ALLOW=1` 防消音 diff 守卫（覆盖 `overlap`/`occlusion`/`overflow`）。 |
| **S1-4** | **已落地** | `scripts/verify-pixels.py` 新增 Shared Stage `#stage-card` 内部像素探针（`HTMLVIDEO_VERIFY_STAGE_CARD_PROBE=1`），基于全分辨率黑框边缘自动定位卡片内部有效区域，拦截空卡（`<0.25%`）及边缘坍缩（左/上 22% 窄带占比 `>75%` 且对侧 50% 密度 `<1.0%`）。 |
| **S1-5** | **已落地** | `html_utility/claude_cli.py`：`_kill_process_group()` 支持可配 `grace_seconds`；`_wait_claude_proc()` 在取消/超时/截断时先以 `0.5s` 宽限立即终止进程组再 `join(1.0)` 消除 10s 死等；`_child_env()` 默认注入 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`；`HTMLVIDEO_CLAUDE_DEBUG_REQUEST` 默认改为关闭（显式 `=1` 开启）；清空 `/app/log/claude/plugins/installed_plugins.json` 为 `{"version": 2, "plugins": {}}`。 |
| **S2-1** | **已落地** | `html_utility/frame_html.py` 新增 `HTMLVIDEO_FRAME_HTML_SLIM_PRIMARY=1` 使主进程同样裁剪冗余 Blueprint 与 `## Rule index` 并保留 `## Rule recipes (snippets)`；`html_utility/visual.py` 将 `HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认开启并在 Phase A 明确 Shared Stage 帧统一标 `(Adapt)`；精简 `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`（同步 `.claude/skills`），移除旧 `Read/Write/done OK` 冲突指令并补充装饰环紧包裹容器规范。 |
| **S3-1** | **已落地** | `html_utility/visual.py` 拆分暴露 `run_visual_phase_a()` 与 `run_visual_phase_b()`（Phase B 重新读取磁盘 `STORYBOARD.md` 获取真实 TTS 时长）；`html_utility/create_pipeline.py` 在 `parallel_tts_visual()` 中新增 `HTMLVIDEO_SYNC_BEFORE_VISUAL_B=1`（默认开），实现 `tts ‖ visual Phase A` → `sync_duration` → `visual Phase B`。 |
| **S3-2** | **已落地（2026-11-27 追加）** | **分镜时长预算与前置词数门禁（消除 `contract-card.md` 固定短片上限）**：<br>1. `skills/aiflow-build-storyboard/references/contract-card.md`（同步 `.claude/skills` 副本）：将 `duration` 由 `advisory` 改为目标时长约束，解除 `3–6 body frames` / `usually 6–20 words` 固定上限，改为按目标时长动态匹配帧数与单帧词数；<br>2. `html_utility/storyboard_contract.py`：新增 `HTMLVIDEO_STORYBOARD_DURATION_BUDGET=1`（默认开），按实测有效语速（英文 `2.35 words/s`、中文 `3.9 汉字/s`、日文 `4.8 字符/s`）确定性计算并注入 `## Pacing & duration budget`（`VO_MODE: verbatim` 自动豁免），同时修复 `_CONTRACT_CARD_CANDIDATES` 重复路径；<br>3. `html_utility/storyboard.py`：新增 `HTMLVIDEO_STORYBOARD_DURATION_GATE=1`（默认开），若口播总词/字数低于目标 `75%`（如 `60s` 英文片 `<105 words`），在 TTS/Visual 之前执行最多 1 次快速 `text-out` 扩写重跑并择优保留（软兜底不炸单）。 |

### 7.2 自动化单测与离线回放验证结果（2026-11-27 实测全绿）

1. **单元测试**：
   - `node /app/test/test_frame_packets.mjs`：**PASS**（含两处 skill 副本字节一致、冗余副本不存在、`data-accent` 列继承、`__hf.type` 光标自动收起及 4 条新静态自检规则）；
   - `node /app/test/test_verify_check_parse.mjs`：**PASS**（含 `escaped_container` 提级与转场/装饰豁免）；
   - `python3 -m unittest /app/test/test_storyboard_contract.py /app/test/test_visual_driver.py /app/test/test_claude_cli.py`：**全绿**（含 `contract-card.md` 双副本字节一致、多语言时长预算计算、`VO_MODE: verbatim` 豁免、`<75%` 词数 1 次扩写重跑、取消进程 `<2.0s` 退出、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`、`SLIM_PRIMARY` 保留 snippets、`verify` 防消音回滚守卫、`VISUAL_LEDGER` 默认开启）。
2. **存量目录离线回放（`6b083ee7d7c36f88` & `6b082a5be89ed160`）**：
   - **骨架回放**：对 `6b083ee7` 临时副本重跑 `frame-packets.mjs`，F2 `dim` 槽位均带有 `data-accent="coral" / "lime" / "sky"`，且 `__hf.type` 含 `if (caret) tl.set(caret, { autoAlpha: 0 }, at + d + 0.15);`；
   - **`frame-selfcheck.mjs` 回放**：`6b083ee7` F2 命中 `gsap_label_concat` × 2 + `abs_zero_xpercent_center`（`FAIL`）；`6b083ee7` F6 命中 `gsap_fromto_delayed_leak` + `missing_dom_selector`（`FAIL`）；`6b082a5b` F3 命中 `gsap_camelcase_data_prop` + `gsap_fromto_delayed_leak`（`FAIL`）；
   - **`verify-report.mjs` 回放**：`6b083ee7` F2 的 2 条 `escaped_container` 成功从 `⚠` 提级为阻塞 `✗`（`GATE: FAIL ✗`）；
   - **`verify-pixels.py` 回放**：`6b083ee7` 共 8 张抽帧中 `frame-01-at-8.761s.png`（F2）精准报 `FAIL: collapsed content inside #stage-card (left 22% holds 82.1%, right 50% has 0.77%)`（`exit=1`），其余 7 张正常帧全部 `PASS`。
