# `6b0a4a1a520a7d3d` 全流程复盘、上线回归审计与下一轮落地执行记录（2026-11-28）

> **关联任务**：`6b0a4a1a520a7d3d`（意图任务 `6b08f93e617a8674`；2026-11-28 03:27:23–03:40:24 UTC；**9 帧 / `66.3s` 成片 `8.2 MB` / `verify` 首扫零模型直过**；项目目录 `/app/videos/2026-11-28/userclm17919893695531772/6b0a4a1a520a7d3d`）  
> **同题对照基线**：`6b096712c5efbc8f`（11-27 11:18，8 帧 / `verify` 3 轮失败，详见 `docs/2026-11-27-6b096712-pipeline-review-and-optimization-plan.md`）、`6b0941c6196ef01f`（11-27 08:39，9 帧 / `59.1s` 成片，`1191.0s`）  
> **核验依据**：`log/htmlvideo_worker.log`（L1–L929）、29 份 Claude Code 会话 JSONL、29 份 `/app/log/api-bodies/*.request.json` 与 21 份 `.response.json`（精确对应 21 完成 + 8 取消）、全量 9 份帧 HTML、`node /app/scripts/verify-report.mjs` 重放、`snapshots/contact-sheet-1.jpg` / `contact-sheet-2.jpg` 与高清单帧截图。

---

## 1. 上轮改造（`6b096712` P0-1 ~ P1-2）线上回归核验

| 阶段 / 指标 | 上轮 `6b096712` (8帧) | 同 9 帧基线 `6b0941c6` | 上轮方案预期 | **本次 `6b0a4a1a` (9帧) 实测** | 判定 |
|---|---:|---:|---:|---:|---|
| **`init`** | `3.6s` | `3.6s` | `~3.6s` | **`3.6s`** | **✅ 符合** |
| **`storyboard`** | `50.3s` | `74.6s` | `~50s` | **`52.6s`** (`1,366` thk) | **✅ 符合** |
| **`audio` (TTS, 并行)** | `90.4s` | `102.9s` | 掩盖于 Phase A 内 | **`101.4s`**（完全掩盖） | **✅ 符合** |
| **`visual_design` (Phase A)** | `303.8s` / `18.4k` thk | `150.4s` | `120–150s` / `6–7k` | **`184.9s` / `10.8k` thk**（-39.1%） | **🟡 明显改善，略超预期** |
| **`visual` Phase B** | `127.9s` (8帧) | `163.4s` (9帧) | `~120–140s` | **`70.7s`**（单帧 `19.2–65.0s`） | **✅ 远超预期（-56.7%）** |
| **`packets` + `captions`** | `0.3s` | `0.4s` | `~0.3s` | **`0.2s`** | **✅ 符合** |
| **`frame_html`** | `554.2s` | `654.1s` | `220–280s` | **`366.6s`**（关键路径 F9 `366.4s`） | **🟡 大幅改善，未达理想态** |
| **`verify`** | `190.0s` (FAIL) | `101.0s` (PASS) | `35–60s` (PASS) | **`51.0s`（首扫直过，0 模型调用）** | **✅ 达标** |
| **`render`** | 未执行 | `45.7s` | `~46s` | **`47.5s`**（`1989` 帧 / `44.3s` 渲染） | **✅ 符合** |
| **端到端总耗时** | 未完赛（折算 `~21.2m`） | `1191.0s` (`19.9m`) | **`11.5–14.0m`** | **`778.0s` (`12.97m`)** | **✅ 精准达标（提速 34.7%）** |
| **显性 Output / Thinking** | `173.1k` / `130.1k` | — | `115–135k` | **`118,070` / `72,624` (61.5%)** | **✅ 达标（thk -44.2%）** |
| **被取消进程空烧时长** | `746s` (5 进程) | `1,173s` | `<400s` | **`1,669.0s`（8 进程，约 `~102k` 隐性 out）** | **❌ 严重超预期** |

- **P0-1 / P1-2（Verify 防死循环 + 分级软放行）**：已上线，本次首扫直接 `GATE: PASS ✓`（`lint errors=0`、`check ✗=0`），未触发修复与降级兜底。
- **P0-2（前置写盘清洗 + `gsap_non_transform_motion`）**：已上线，全部 9 帧 0 次 `left/top` tween 违规、0 次数字开头选择器或冗余 CSS `scale`；`07#1` 主进程（`222.8s`）因 `gsap_fromto_delayed_leak` 被 `selfcheck` 拦截（后审计确认为**退场动画误杀**），8 秒后由 `07#1-hedge` 接管通过。
- **P1-1（`visual_design` `done:coral` 豁免 + `frame-worker-card` 防印章压字）**：API 请求体实证已生效；`visual_design` 不再纠结 `done:coral`，F7 `Sell` 印章彻底无上轮“胶囊压黑字”重影。

---

## 2. 逐帧竞速与新暴露问题审计（含对初版建议的关键纠偏）

### 2.1 `frame_html` 9 帧竞速账本（9/9 触发对冲，7/9 对冲胜出）

| 帧 ID | Primary (`medium`) 耗时 / Out (Thk) | Hedge (`low`) 耗时 / Out (Thk) | 胜出方 | 取消空烧时长 |
|---|---|---|---|---:|
| `01-the-guess` | **`173.0s`** / `10,692` (`8,266`) | `23.0s` 被取消 | Primary | `23.0s` |
| `02-three-signals` | `299.0s` 被取消 | **`148.3s`** / `9,834` (`6,007`) | Hedge | `299.0s` |
| `03-check-one-value` | `205.0s` 被取消 | **`54.4s`** / `3,945` (`182`) | Hedge | `205.0s` |
| `04-check-two-quality` | `206.0s` 被取消 | **`55.1s`** / `4,114` (`22`) | Hedge | `206.0s` |
| `05-check-three-timing` | `208.0s` 被取消 | **`57.4s`** / `4,335` (`127`) | Hedge | `208.0s` |
| `06-all-green-buy` | **`259.7s`** / `16,169` (`12,262`) | `110.0s` 被取消 | Primary | `110.0s` |
| `07-one-flips-red-sell` | `222.8s` / `13,917` (`8,930`) **被误杀** | **`61.5s`** / `4,607` (`214`) | Hedge | （沉没 `13.9k` out） |
| `08-the-checklist-recap` | `257.0s` 被取消 | **`51.7s`** / `3,775` (`61`) | Hedge | `257.0s` |
| `09-run-the-checklist` | `361.0s` 被取消 | **`154.2s`** / `9,561` (`5,655`) | Hedge | `361.0s` |
| **合计** | 2 胜 / 6 取消 / 1 误杀 | **7 胜 / 2 取消** | — | **`1,669.0s`（按 `62.2 tok/s` 折算约 `~102k` out）** |

### 2.2 源码与截图交叉审计发现（6 项精准结论）

1. **视觉瑕疵（4 处绕过首扫 PASS）**：
   - **F1（[01-the-guess.html](file:///app/videos/2026-11-28/userclm17919893695531772/6b0a4a1a520a7d3d/compositions/frames/01-the-guess.html#L95-L102)）**：`.underline` 用 `position: absolute; top: 60%` 横穿 `"guess"` 字母腰部成删除线（`<svg aria-hidden="true">` 被 `check` 视作纯装饰漏检）。
   - **F3（[03-check-one-value.html](file:///app/videos/2026-11-28/userclm17919893695531772/6b0a4a1a520a7d3d/compositions/frames/03-check-one-value.html#L81-L108)，`low` hedge 产出）**：`.bar-group` `left: 10%` 盖住 `"CROWDED"` 尾部变成 `"CROWD"`；且 `tl.fromTo(".zone-crowded::before", ...)` / `.zone-cheap::before` 触发 `GSAP target not found` 告警。
   - **F4（[04-check-two-quality.html](file:///app/videos/2026-11-28/userclm17919893695531772/6b0a4a1a520a7d3d/compositions/frames/04-check-two-quality.html#L147)，`low` hedge 产出）**：`tl.fromTo(label, { textDecorationLine: "line-through" }, { textDecorationLine: "line-through", duration: 0.01 }, st + 0.2)` 因 `fromTo` 默认 `immediateRender: true`，导致未勾选的第 3 行 `"Manageable debt"` 在 `t=0` 就提前画上删除线。
   - **F7（[frame-06-at-48.792s.png](file:///app/videos/2026-11-28/userclm17919893695531772/6b0a4a1a520a7d3d/snapshots/frame-06-at-48.792s.png)）**：主卡内 `Quality Fading` 为 `✕`，但顶部 `.hf-stage-lockup` 的 `data-state="done" data-accent="coral"` 槽位因 `frame-packets-core.mjs` 写死 `.hf-stage-check`，仍显示 `✓ 02 Quality`。
2. **`07#1` 主进程被拒实为 `gsap_fromto_delayed_leak` 误杀**：`07#1` 写的是合法退场动画 `tl.fromTo("#row-quality .sig-icon.check", { scale: 1, autoAlpha: 1 }, { scale: 0, autoAlpha: 0 }, t2)`；**严禁对所有延时 `fromTo` 盲补 `immediateRender: false`**（会破坏全片 30+ 处依赖 `immediateRender: true` 在 `t=0` 隐藏元素的 `0 → 1` 入场动画），应改为在 `frame-selfcheck` 豁免纯 `1 → 0` 退场动画，并在 `_normalize_frame_html` 仅将 `from === to` 的退化延时 `fromTo` 安全改写为 `tl.set`。
3. **严守策略红线**：保持 `frame_html=medium/48000`、`hedge=low/23999` 不降档（本轮 F3/F4 瑕疵均来自 `low` hedge 仓促直出，印证主进程不能直接降为 `low`）。
4. **`visual_design` 纠结 F1 的代码源头**：`visual.py` `_shared_stage_pairs()` 的 `\bsame\b` 正则误匹配了 F2 的 `"The same central card steadies and splits into a three-slot top row..."`，主动注入 `F1→F2: scene says "same"` 伪提示导致模型反复推翻。
5. **`frame_html` 排队与草稿长尾**：`DEFAULT_MAX_HEDGES=6` 使 9 帧任务的 F7 晚起对冲 `18s`、F8/F9 晚起 **`55s`**（F9 成为 `366.4s` 关键路径）；同时 `medium` 主进程仍在 thinking 内逐行手写完整 CSS/HTML/JS 草稿（150–171 段）。

---

## 3. 本轮已落地改造清单（2026-11-28 全绿验证）

> **策略红线守卫**：全链路各阶段 `effort` 与 `max_output` / `MAX_THINKING_TOKENS` 保持不变；`frame-worker-card.md` 保持 `7,938 B <= 8,000 B` 且 `skills/` 与 `.claude/skills/` 双副本字节一致。

### P0-1：静态门禁精准修复 + 确定性退化 `fromTo` 归一化
- **[scripts/frame-selfcheck.mjs](file:///app/scripts/frame-selfcheck.mjs)**：
  1. 修复 `gsap_fromto_delayed_leak`：当延时 `fromTo` 为纯退场（`from` 可见、`to` 为 `autoAlpha/opacity: 0` 且同目标未在脚本中先做 `0 → 1` 隐藏入场）时豁免放行（消除 `07#1` 误杀）；同时新增延时 `textDecoration*: "line-through"` 泄漏拦截（捕获 F4 缺陷）。
  2. 新增 `gsap_pseudo_element_target` 静态门禁：拦截 `tl/timeline/gsap.(to|from|fromTo|set)` 选择器中的 `::before`/`::after`（`:before`/`:after`），消除 F3 无效动画与 `GSAP target not found`。
- **[html_utility/frame_html.py](file:///app/html_utility/frame_html.py)**：
  - 在 `_normalize_frame_html()` 新增 `_rewrite_degenerate_delayed_fromto()`：仅当延时 `tl.fromTo(target, fromObj, toObj, pos)` 的 `fromObj` 与 `toObj`（忽略 `duration`/`ease`/`immediateRender`/`delay`）键值完全相同时，确定性改写为 `tl.set(target, fromObj, pos)`，绝对不触碰正常 `0 → 1` 入场 `fromTo`。

### P0-2：共享舞台告警图标修复 + Worker 布局规则卡片微调
- **[skills/hyperframes-core/scripts/lib/frame-packets-core.mjs](file:///app/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs)**（同步 `.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`）：
  - 在每个 `.hf-stage-slot` 预置 `.hf-stage-warn` (`✕`) SVG，CSS 增加 `.hf-stage-slot[data-state="done"][data-accent="coral"]` 自动隐藏 `.hf-stage-check` 并显示 `.hf-stage-warn`，彻底消除 F7 顶栏红底黑勾的语义割裂。
- **[skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md](file:///app/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md)**（同步 `.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`，总长 **`7,938 B <= 8,000 B`**）：
  - 在 L34 等量精简并补入：下划线保持在文字下方正常 flex 流（禁 `position: absolute` 百分比偏移穿字）；图表标签置于独立 flex/grid 列避开实心柱体。

### P1：上游共享舞台启发式消歧 + 对冲并发/触发与近因防草稿约束
- **[html_utility/visual.py](file:///app/html_utility/visual.py)**：
  - 在 `_shared_stage_pairs()` 新增 `_STAGE_INTRO_SPLIT_RE`（`splits into` / `introduces` / `transforms into` / `opens a three-slot`），当 `F1→F2` 仅因 `same` 命中且 F2 描述舞台首次拆分建立时，不再生成 `F1→F2: scene says "same"` 伪提示。
- **[html_utility/frame_html.py](file:///app/html_utility/frame_html.py)**：
  - `DEFAULT_MAX_HEDGES` 由 `6` 调至 **`9`**（消除 9 帧任务 F7–F9 的 `18–55s` 排队延迟）；
  - `DEFAULT_HEDGE_AFTER` 由 `150` 温和下调至 **`120`**（保留正常 `medium` 帧 2 分钟完赛窗口，同时削减马拉松主进程空烧）；
  - 在 `_worker_prompt()` `target_block` 末尾（近因效应位）追加硬禁令：`plan layout & timing in <=15 short bullets — NEVER draft raw CSS rules, HTML markup, or GSAP code blocks inside thinking`。

---

## 4. 单元测试与历史样本回放实测结果

1. **`6b0a4a1a520a7d3d` 真实样本回放**：
   - **F3 (`03-check-one-value.html`)**：`frame-selfcheck.mjs` 精准检出 `gsap_pseudo_element_target` (`rc=1`)；
   - **F4 (`04-check-two-quality.html`)**：原始 HTML 检出 `gsap_fromto_delayed_leak` (`rc=1`) → 经 `_normalize_frame_html()` 触发 `degenerate-fromto-to-set=1` 改写为 `tl.set` 后 `frame-selfcheck.mjs` **`PASS (rc=0)`**；
   - **F7 主进程 (`afec7c8f.jsonl`)**：此前被误杀的 `1 → 0` 退场 `fromTo` 现已 **`PASS (rc=0)`**；
   - **历史帧全量回归（`2026-11-26` + `2026-11-27`）**：0 误报。
2. **全量单元测试套件**：
   - `node /app/test/test_frame_packets.mjs`（含 `.hf-stage-warn`、`frame-packets-core.mjs` & `frame-worker-card.md` 双副本一致、`7,938 B <= 8,000 B`、`gsap_pseudo_element_target`、F4 泄漏与 F7 退场豁免）：**PASS**；
   - `node /app/test/test_verify_check_parse.mjs`：**PASS**；
   - `python3 -m unittest test_frame_html_driver.py test_visual_driver.py test_verify.py test_claude_cli.py test_storyboard_contract.py`（含 `test_normalize_frame_html_rewrites_degenerate_delayed_fromto_only`、`test_shared_stage_pairs_ignores_f1_split_into_stage`、`DEFAULT_MAX_HEDGES=9` / `DEFAULT_HEDGE_AFTER=120`）：**全绿**。
