# 2026-11-25 视频生成全链路复盘与效果/耗时/成本协同优化

基于最新一轮视频生成任务（`intent`: `6b06de3e6352176b` + `create`: `6b06dfa0a2a2d105`，全阶段统一使用 `deepseek-v4-pro-0813`）的 Worker 日志、HTTP 请求/响应抓包、Claude Code JSONL thinking 轨迹及 7 帧成品快照复盘，在不切换模型（全阶段保持 `deepseek-v4-pro-0813`）且暂不改动 `music/sfx` 的前提下，落地以下工程与契约优化。

## 一、核心瓶颈归因

### 1. 耗时与成本归因（总耗时 `1562.0s = 26.0m`，总输出 `282.6K` tokens 中 `90.6%` 为 thinking）
1. **自适应推理长尾失控**：网关侧固定 `thinking: {"type": "adaptive"}`，当 `frame_html` 上限设为 `64000`、`visual` 设为 `high × 32000` 时，`deepseek-v4-pro-0813` 在矛盾输入下出现单帧 `37K–41K` thinking（`592–664s`）与 `22.6K` thinking（`425s`）。
2. **`HTMLVIDEO_FRAME_VISUAL_LEDGER=1` 负向收益**：强制 `visual` Phase B 逐帧手算坐标表导致慢帧膨胀至 `425s`；同时 `audio.mjs sync-durations` 仅缩放了 `Scene N (a–b s)` 时间窗而未同步缩放 `## Motion ledger` 中的 `dur` 列，诱发 `frame_html` worker 在 Scene、Ledger 与 `Resolved conflicts` 之间反复仲裁空转。
3. **并发冷启动前缀缓存踩踏（Cold-Cache Stampede）**：`visual` Phase B 与 `frame_html` 的 7 个 worker 同一毫秒并发发出请求，首请求尚未完成 prefill，导致 ~24KB 跨帧共享前缀命中率仅 `10.9%`。
4. **`storyboard` 双轮工具往返与 `intent` Slash/Hook 注入**：`storyboard` 使用 `Write` 工具产生冗余第 2 轮（多付 `16.7K` 输入 tokens）；`intent` 以 `/aiflow-identify-intent` 开头且未禁用 `Skill`，额外注入 `SKILL.md` 与 `superpowers` hook。
5. **常驻 Worker 环境变量泄漏 `ANTHROPIC_LOG=debug`**：父进程残留 `ANTHROPIC_LOG=debug` 被子进程继承，导致每次调用全量打印 HTTP 报文并向 `log/api-bodies` 持续写入 GB 级日志。

### 2. 画面与动效质量归因（7 类实测缺陷）
1. **帧内重复底栏字幕（02 / 05 / 06）**：`visual.py::_build_design_prompt` 误要求 Phase A 设计 `caption band`，导致帧内绘制 `.caption-band` / `.f5-caption`，与根轨 `compositions/captions.html` 双重叠加。
2. **跨帧共享舞台（Shared Stage）漂移（02–07）**：Phase A 未锁定共享卡片尺寸与完整槽位短标签，导致各帧卡片宽度（`48cqw → 92cqw`）及未激活槽位文案（空白 / `—` / 改名）不一致。
3. **打字机末字符截断（02 `"one rul"`）**：`discrete-text-sequence.md` 中浮点数累加 `t`（如 `2.1290000000000004 > 2.129`）导致 `time >= SEQUENCE[i].t` 在终点漏掉最后一帧文本。
4. **白底黄字不可读（05 `"FAIR PRICE"` 对比度 `1.44:1`）与胶囊字号过小（`0.75cqw`）**：浅色糖果色被误用于白底文字，`capsule/FRAME.md` 阅读字号低于视频可读下限。
5. **卡片内部 `position: absolute` 重叠与贴边（04 / 07）**：手写百分比绝对定位导致图表遮挡槽位、标题贴死左边框、`cta-morph-press` 残留 `opacity: 0.3` 鬼影。
6. **`MOTION_BANS` 3D 正则误伤**：`frame-packets-core.mjs` 中 `\b3d\b` 将 `"no 3D extrude"`（禁止 3D 浮雕材质）误判为禁止一切 CSS `rotateY` 卡片翻转。
7. **质检门禁盲区**：非持续态 `contrast_aa_failure`（即使 `1.44:1`）仅记为 `warning` 放行；未碰撞采样点的帧内 `.caption-band` 未清理；`verify-pixels.py` 将底部 17% 字幕区计入全画幅非空比例，掩盖了主舞台空白。

---

## 二、落地改动清单

### 1. 耗时与成本优化（全阶段保持 `deepseek-v4-pro-0813`）
- **`html_utility/claude_cli.py`**：
  - `intent`：禁用 `"Skill"`（自动开启 `--disable-slash-commands` 与 `disableAllHooks`），收敛为 `effort="medium", max_output="12000", max_turns=4`。
  - `storyboard`：`thinking` 对齐 `"16000"`。
  - `visual`：收敛为 `effort="medium", thinking="16000", max_output="16000", max_turns=12`（保留 `HTMLVIDEO_CLAUDE_VISUAL_*` 环境变量一键回滚）。
  - `frame_html`：首轮 `max_output` 从 `"64000"` 收敛至 `"28000"`（正常帧实测总输出 `9.5K–18.5K`；若遇极端截断 `TRUNCATED_RC=125`，驱动器自动提额至 `64000` 重试）。
  - `_child_env()`：当未显式开启 `HTMLVIDEO_CLAUDE_DEBUG_REQUEST=1` 时，自动剥离父进程泄漏的 `ANTHROPIC_LOG` 与 `--preload=...claude_inspect_full.js`。
- **`html_utility/intent.py`**：移除 `/aiflow-identify-intent` 斜杠指令前缀，改为自包含精简 JSON 提取提示词。
- **`html_utility/storyboard.py` & `storyboard_contract.py`**：默认开启单轮直出模式（`HTMLVIDEO_STORYBOARD_TEXT_OUT=1`，`--tools ""`，`max_turns=2`），Python 直接提取 Markdown 落盘并派生 `SCRIPT.md`，消除第 2 轮工具确认开销（失败或 `=0` 自动回退 `Write` 模式）。
- **`html_utility/visual.py` & `html_utility/frame_html.py`**：
  - 新增跨帧共享前缀预热（`HTMLVIDEO_PREFIX_WARMUP_SECONDS`，默认 `1.8s`）：首个 worker 先行提交并等待 `wait([first], timeout=1.8s)` 预热网关 KV Cache 后再扇出其余帧（单测 mock 立即返回，零等待）。
  - `frame_html.py` 将尾帧对冲阈值 `DEFAULT_HEDGE_AFTER` 从 `660s` 下调至 `300s`。

### 2. 视觉契约、共享舞台与版式质量修复
- **`html_utility/visual.py` & `scripts/storyboard-gate.mjs` & `scripts/audio.mjs`**：
  - `HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认改回 **OFF (`False`)**（保留 `=1` 可选开启，并在 `audio.mjs` `scaleSceneWindows` 中同步缩放 `## Motion ledger` 的 `dur` 列）。
  - `_build_design_prompt()` 删除 `"caption band"` 设计要求，明确根轨 `captions.html` 独占底部 17% 禁区；强化 **Shared stage** 规范（锁定共享卡片尺寸、网格及完整编号槽位短标签列表，禁止槽位留空/`—`/改名）；强制浅底文字使用深色 `ink`（浅糖果色仅作胶囊填充）。
- **`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`**（及 `.claude` 镜像）：修复 `MOTION_BANS` 中 `3d` 正则负向先行断言，避免 `"no 3D extrude/bevel/render/mesh"` 误禁 CSS `rotateY` 翻牌动效。
- **`skills/hyperframes-animation/rules/discrete-text-sequence.md`**（及 `.claude` 镜像）：`textAt(time)` 增加 `+ 1e-4` 浮点容差并补充 `onComplete` 终态兜底，彻底消除打字机漏字。
- **`skills/hyperframes-creative/frame-presets/capsule/FRAME.md`**（及 `.claude` 镜像）：将 `body`/`subtitle`/`pill-text` 从 `0.85/0.95/0.75cqw` 提升至 `1.25/1.15/1.05cqw`，并在色彩规范中禁止白/米底使用浅糖果色文字。
- **`frame-worker-card.md` / `frame-worker.md` / `frame-worker-core.md` / `frame_html.py`**（及 `.claude` 镜像）：强制卡片与双栏内部使用 CSS `flex`/`grid` + `padding` + `gap` + `box-sizing: border-box`（严禁手写百分比 `position: absolute` 堆叠行/列）、主视觉占画幅 `35–55%`、morph 退场完全淡出至 `autoAlpha: 0`、SVG 折线图 X 轴置于底部。

### 3. 质检门禁加固（`verify`）
- **`scripts/known-failures.mjs`**：
  - 新增 `in-frame-caption-band`（`mode: "autofix"`）：当存在 `compositions/captions.html` 时，在 `prepare` 阶段自动为帧内残留的 `.caption-band` / `.fN-caption` 注入幂等 CSS 隐藏规则，零 DOM/GSAP 副作用地根除双重底栏。
  - 新增 `contrast-aa-failure`（`mode: "model"`）：为严重低对比度文本提供明确的深浅底改色指引。
- **`scripts/lib/verify-check-parse.mjs` & `scripts/verify-report.mjs`**：保留 `hyperframes check --json` 的 `ratio`/`fg`/`bg`/`suggestedColor` 字段；在不影响 `declaredErrorCount` 一致性校验的前提下，将非字幕、非半透明渐变中 `ratio < 2.5:1` 的严重低对比度警告（如白底黄字 `1.44:1`）升级为阻断错误（`HTMLVIDEO_VERIFY_SEVERE_CONTRAST=0` 可关闭）。
- **`scripts/verify-pixels.py` & `html_utility/verify.py`**：新增顶部 `83%` 主舞台独立像素占比检测（`stage_content_pct`），防止底部 `17%` 字幕条掩盖空白主舞台。
