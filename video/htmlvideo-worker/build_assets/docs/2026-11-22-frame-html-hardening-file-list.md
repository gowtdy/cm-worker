# frame-html 关键路径硬化 — 修改文件清单

> 日期:2026-11-22  
> 范围:P0 + P1 + P2（均已代码落地）  
> 规格主文档:`docs/2026-11-22-frame-html-critical-path-hardening.md`  
> 进度记忆:`log/claude/projects/-app/memory/htmlvideo-frame-html-visual-p0p1-progress.md`

共 **20** 个路径（含 `.claude/skills` 同步副本与新建文档/测试）。

---

## 0. P1 / P2 落地摘要（含默认开的开关）

### P1（5 项，全部 ✅）

| ID | 做什么 | 默认 |
|---|---|---|
| P1-1 | selfcheck `skeleton_unfilled` | 规则常开（无独立 env） |
| P1-2 | dispatch 末尾 `## Resolved conflicts` | `RESOLVE_CONFLICTS=1` |
| P1-3 | `skill-timing.py` 按 `message.id` 去重 + 截断等指标 | 统计脚本行为 |
| P1-4 | `frame-html stage summary …` 一行汇总 | 常开 |
| P1-5 | recipe `snippet` / `full` / `off` | **`INLINE_RECIPES=snippet`**（默认开；设 `off` 关） |

**P1 主要改动文件**

```
scripts/frame-selfcheck.mjs
skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
html_utility/frame_html.py
stat/skill-timing.py
skills/aiflow-build-frame-html/SKILL.md
skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
.claude/skills/aiflow-build-frame-html/SKILL.md
.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
test/test_frame_packets.mjs
test/test_frame_html_driver.py
test/test_skill_timing.py          # 新建
```

### P2（5 项，全部 ✅；三开关已默认打开）

| ID | 做什么 | 默认 | 关闭方式 |
|---|---|---|---|
| P2-1 富骨架 | bg clip + Scene labels + 正确 timeline 注册 | **`RICH_SKELETON=1`** | `=0` |
| P2-2 两阶段 | attempt1：style+body → script | **`TWO_PHASE=1`** | `=0` |
| P2-3 对冲 | 末帧无 Write 心跳时 hedge | **`HEDGE=1`**（阈值 660s） | `=0` |
| P2-4 cap/effort | 环境覆盖 worker 参数 | 未设（= profile 48000/high） | 设值即覆盖 |
| P2-5 降级 | 临近 deadline 简化重派 | **`DEGRADE=1`**（窗口 600s） | `=0` |

一键回滚「本次打开的三项」：

```bash
export HTMLVIDEO_FRAME_HTML_RICH_SKELETON=0
export HTMLVIDEO_FRAME_HTML_TWO_PHASE=0
export HTMLVIDEO_FRAME_HTML_HEDGE=0
```

**P2 主要改动文件**

```
skills/hyperframes-core/scripts/lib/frame-packets-core.mjs   # 富骨架
.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
html_utility/frame_html.py                                    # 两阶段/对冲/降级/cap·effort
html_utility/claude_cli.py                                    # cancel_event（hedge）
skills/aiflow-build-frame-html/SKILL.md                       # 默认开说明
.claude/skills/aiflow-build-frame-html/SKILL.md
test/test_frame_packets.mjs
test/test_frame_html_driver.py
test/test_claude_cli.py
docs/2026-11-22-frame-html-critical-path-hardening.md
docs/2026-11-22-frame-html-hardening-file-list.md
```

### 打开默认开关时额外改动的文件（相对「初落地默认关」）

仅改默认值与文档/单测断言，无新功能文件：

```
html_utility/frame_html.py                  # TWO_PHASE/HEDGE default=True
skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
                                            # RICH default true；INLINE_RECIPES default snippet
test/test_frame_html_driver.py              # fixture 显式关；断言生产默认开
test/test_frame_packets.mjs                 # 断言 rich / snippet 默认开
skills/aiflow-build-frame-html/SKILL.md
skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
.claude/skills/aiflow-build-frame-html/SKILL.md
.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
docs/2026-11-22-frame-html-critical-path-hardening.md
docs/2026-11-22-frame-html-hardening-file-list.md
log/claude/projects/-app/memory/htmlvideo-frame-html-visual-p0p1-progress.md
```

---

## 1. 按类别（P0+P1+P2 全量）

### 核心逻辑（6）

| 文件 | 阶段 | 改动摘要 |
|---|---|---|
| `html_utility/frame_html.py` | P0–P2 | 逐帧流水线、截断重派升 cap、salvage、阶段汇总、两阶段/对冲/降级/cap·effort 覆盖、prompt 文案 |
| `html_utility/claude_cli.py` | P0–P2 | `--session-id`、`_TruncationWatcher`（rc=125）、`cancel_event`（hedge 取消 rc=130）、`max_turns`/`timeout` 覆盖 |
| `scripts/audio.mjs` | P0 | `sync-durations` 等比缩放 `Scene N (a–b s)` |
| `scripts/frame-packets.mjs` | P0 | 接线 packet core 新选项 |
| `scripts/frame-selfcheck.mjs` | P1 | 新增 FAIL `skeleton_unfilled` |
| `stat/skill-timing.py` | P1 | 按 `message.id` 去重；`api_requests`/`truncations`/`max_req_s`/`first_write_req` |

### Packet / 骨架（2 + 1 副本）

| 文件 | 阶段 | 改动摘要 |
|---|---|---|
| `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` | P0–P2 | `selectBlueprint`、Scene drift、`Resolved conflicts`、recipe snippet/full、富骨架 |
| `.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` | 同步 | 与上同内容 |

### 文案 / 契约（3 + 3 副本）

| 文件 | 阶段 | 改动摘要 |
|---|---|---|
| `skills/aiflow-build-frame-html/SKILL.md` | P0–P1 | recipe 未全文内联、禁自跑 selfcheck、优先级、冲突/snippet 说明 |
| `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md` | P0–P1 | 同上（dispatch 内嵌卡，保持 ≤8KB） |
| `skills/aiflow-build-frame-html/sub-agents/frame-worker.md` | P0 | legacy 路径标注；与 driver 口径对齐 |
| `.claude/skills/aiflow-build-frame-html/SKILL.md` | 同步 | |
| `.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md` | 同步 | |
| `.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker.md` | 同步 | |

### 测试（4）

| 文件 | 阶段 | 改动摘要 |
|---|---|---|
| `test/test_frame_packets.mjs` | P0–P2 | dual blueprint、scene drift、conflicts、snippets、rich skeleton |
| `test/test_frame_html_driver.py` | P0–P2 | 流水线/125 升 cap/salvage/deadline/wave 回滚/两阶段/hedge/降级/汇总 |
| `test/test_claude_cli.py` | P0–P2 | TruncationWatcher、session-id、cancel_event |
| `test/test_skill_timing.py` | P1 | **新建**；message.id 去重与截断指标 |

### 文档 / 记忆（2）

| 文件 | 说明 |
|---|---|
| `docs/2026-11-22-frame-html-critical-path-hardening.md` | **新建**；根因、P0–P2 规格、环境变量、验收与限制 |
| `docs/2026-11-22-frame-html-hardening-file-list.md` | **新建**；本清单 |
| `log/claude/projects/-app/memory/htmlvideo-frame-html-visual-p0p1-progress.md` | 进度记忆更新（P0+P1+P2 状态） |

> 上表「共 20 路径」= 核心 6 + packet 2 + 文案 6 + 测试 4 + 文档 2（hardening + 本清单）；progress memory 另计为关联更新。

---

## 2. 纯路径列表（便于 diff / 备份）

```
html_utility/frame_html.py
html_utility/claude_cli.py
scripts/audio.mjs
scripts/frame-packets.mjs
scripts/frame-selfcheck.mjs
stat/skill-timing.py
skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
skills/aiflow-build-frame-html/SKILL.md
skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
skills/aiflow-build-frame-html/sub-agents/frame-worker.md
.claude/skills/aiflow-build-frame-html/SKILL.md
.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker.md
test/test_frame_packets.mjs
test/test_frame_html_driver.py
test/test_claude_cli.py
test/test_skill_timing.py
docs/2026-11-22-frame-html-critical-path-hardening.md
docs/2026-11-22-frame-html-hardening-file-list.md
```

关联更新（非代码）:

```
log/claude/projects/-app/memory/htmlvideo-frame-html-visual-p0p1-progress.md
```

---

## 3. 按阶段归属速查

| 阶段 | 主要触及文件 |
|---|---|
| **P0** | `frame_html.py`、`claude_cli.py`、`audio.mjs`、`frame-packets.mjs`、`frame-packets-core.mjs`、aiflow 三份文案（+`.claude` 副本）、三份既有测试 |
| **P1** | `frame-selfcheck.mjs`、`frame-packets-core.mjs`、`stat/skill-timing.py`、`frame_html.py`（汇总/prompt）、`test_skill_timing.py`、文案微调 |
| **P2** | `frame-packets-core.mjs`（富骨架）、`frame_html.py`（两阶段/hedge/降级/env cap）、`claude_cli.py`（cancel） |

---

## 4. 环境变量（改动引入，详见 hardening 文档 §5）

**P0:** `HTMLVIDEO_FRAME_HTML_WAVE_MODE`、`STAGE_DEADLINE`、`TRUNC_KILL`、`SALVAGE`、`HTMLVIDEO_SYNC_SCALE_SCENES`、`HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT`

**P1:** `HTMLVIDEO_FRAME_PACKETS_RESOLVE_CONFLICTS`（默认 1）、`HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES`（默认 **`snippet`**；`off`/`0` 关）

**P2:** `HTMLVIDEO_FRAME_HTML_RICH_SKELETON`（默认 **1**）、`TWO_PHASE`（默认 **1**）、`HEDGE`（默认 **1**）、`HEDGE_AFTER`、`MAX_OUTPUT`、`EFFORT`、`DEGRADE`、`DEGRADE_WINDOW`  
> 2026-11-22：富骨架 / 两阶段 / 对冲 / recipe snippet 默认打开；有问题设对应变量为 `0`/`off` 关闭。

---

## 5. 验证命令

```bash
python3 -m unittest test.test_frame_html_driver test.test_claude_cli test.test_skill_timing
node test/test_frame_packets.mjs
```

2026-11-22 本会话结果：Python **84** OK；packet 套件 OK。缺真实 `claude` 端到端回放样本 `6b01e0245007d730`。
