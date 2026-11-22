# frame-html / frame-visual 优化设计(P0 · P1 · P2)

> 设计对象:`html_utility/frame_html.py`(确定性 fan-out,已落地)+ `html_utility/visual.py`(单会话,待并行化)
> 依据:[[build-frame-html-deadzone-deepdive]]、[[build-frame-html-24m-effort-high-analysis]]、[[thinking-cap-ab-58m-analysis]]、[[htmlvideo-skill-optimization-playbook]]、`2026-11-18-frame-html-redesign-{plan,design}.md`
> 设计日期:2026-11-20

## 0. 总览与依赖

| 项 | 目标 | 基线 | 预期 | 关键关系 |
|---|---|---|---|---|
| **P0** frame-html 骨架度量+锁定 | 拿量化基线 | 24.2m(11-17 无骨架) | ~16m(骨架已实测) | 给 P2 当「压前基线」 |
| **P1** frame-visual 并行化 | 7.3m→3m 级 | 441s(7.3m) | **-50%+** | 产出紧 design contract → **喂 P2** |
| **P2** frame-html 思考收紧 | 16m→10-12m | ~16m | -30% | 吃 P1 的紧 dispatch + 独立杠杆 |

**依赖/协同**:P1 的 `design contract`(每帧 blueprint + palette + 元素意图定死)就是 frame-html `.dispatch.md` 的输入——P1 做紧,P2 自动见效。顺序:P0(基线)→ P1 → P2。

---

## 1. P0 — frame-html 骨架:度量 + 锁定(几乎零代码)

现状已落地:骨架默认 ON(`switchOn("HTMLVIDEO_FRAME_HTML_SKELETON", defaultOn=true)`)、`frame-packets.mjs` 预生成 `<template>`+`#root`+`skeleton-v1`、worker fill 契约、实测 ~16m(≈ 11-18 重设计目标 ≤15m)。

要补:
1. **度量 harness**:`test_parallel_caption_frames.py --log-file` 跑一次,per-worker 的 `num_turns/duration_ms/input/output/thinking` 落盘(上次 stderr 丢了)。
2. **基线锁进 memory**:16m 的 per-worker 拆解。
3. **回滚文档化**:4 开关(`HTMLVIDEO_FRAME_HTML_DRIVER/SKELETON/MAX_CONCURRENCY` + `HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER`)。

质量门:`frame-selfcheck` 逐帧 PASS + assemble 成功 + `_validate_frame_html_outputs` 全 animated + verify 不回归。

---

## 2. P1 — frame-visual 并行化(核心)

### 现状与约束
`build_frame_visual()` 单个 `claude` 会话:读 ~9 文件 → 给 8 帧写 shot sequences + `## Video direction` → 单 Write 整份 STORYBOARD,~7.3m。硬约束:① narrative 字段 **byte-exact**;② `## Video direction` video-wide;③ 跨帧一致性靠「一个大脑」。

### 架构:三阶段(照搬 frame-html 的确定性 fan-out)

```
Phase A 串行 : 1 claude → .hyperframes/visual/_design.md
               (## Video direction 全文 + 每帧 blueprint 分配 + palette/motion 契约 + transition 定死)
Phase B 并行 : N worker(cap 16) → compositions/frames/NN.visual.md
               (该帧 enriched block:append shot sequences,narrative 不动)
merge 确定   : 脚本并回 STORYBOARD + 顶部插 ## Video direction
gate         : storyboard-gate.mjs + byte-exact diff
```

### 关键决策
1. `_design.md` = 跨帧一致性锚:palette/type/motion grammar/负面清单 + 每帧 blueprint + transition 顺序,所有 worker 注入**同一份**。
2. 每帧 fragment **独立文件**(零写冲突),merge 复用 `_mark_animated_storyboard` 的「按 `## Frame NN` 块替换」。
3. byte-exact 兜底:packet = 抽出原 block,worker 只 append;gate 加 narrative 逐字符 diff。
4. `_build_frame_visual_driver` 复制 `_build_frame_html_driver`(preflight→并行 pool→collect→重派≤2→标记→assemble),跑通后再抽公共 base。

### 文件改动
| 文件 | 改动 |
|---|---|
| `html_utility/visual.py` | 加 driver + worker prompt + 三阶段编排 + merge 调用 |
| `scripts/build-visual-packets.mjs`(新) | 抽 frame block → packet + `_design.md` |
| `scripts/merge-visual.mjs`(新) | inject fragments → STORYBOARD + prepend ## Video direction |
| `html_utility/create_pipeline.py` | `_run_visual` 改调 driver(`build_frame_visual()` 接口不变) |
| `skills/aiflow-build-frame-visual/*` | SKILL 拆「design」+「per-frame filler」两份 |

### 质量门(不降)
`storyboard-gate.mjs` 整份 PASS + byte-exact diff 零漂移(`scene/voiceover/duration/transition_in/status/src/type/beat`)+ 下游 verify/frame-html 不回归。

### 回滚/预期/风险
回滚 `HTMLVIDEO_FRAME_VISUAL_DRIVER=0`;预期 7.3m→2.5-4m(-45~65%);风险 byte-exact 漂移(diff gate 兜)、转场一致性(Phase A 定死)。

---

## 3. P2 — frame-html 思考收紧(与 P1 协同)

杠杆(按优先级,越后越险):
1. **吃 P1 紧 dispatch**(协同,零 cap 风险):P1 `_design.md` 定死 blueprint/palette/元素意图 → `frame-packets-core.mjs` 并入 `.dispatch.md` → worker 从「推导 HTML」降成「按配方翻译」。
2. **`max_turns` 60→40**(独立低险):填骨架+selfcheck+修 ≤40 足够,超了早断早重派。
3. **cap 64000→48000**(高险):骨架分段写已压小单回合 Write,48k 大概率不截断,但 58m 教训是 32k 截、48k 未知 → A/B 实测。
4. **worker prompt 再强调**(零成本):「dispatch 含完整配方,禁重推导;每回合思考 ≤1 段;首 2 回合先写 style block」。

文件改动:`claude_cli.py`(max_turns 60→40)、`frame_html.py`(`_worker_prompt`)、`frame-packets-core.mjs`(并入 design contract)。

质量门同 P0;预期 16m→10-12m。