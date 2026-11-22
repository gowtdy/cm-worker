# frame-html 关键路径硬化（54.4m → 目标 10–16m）

> 日期:2026-11-22  
> 触发样本:`6b01e0245007d730`（CST 02:27–03:21，driver cost **3265.4s / 54.4m**）  
> 依据:凌晨流水线转录 + `htmlvideo_worker.log` + Opus 复核（纠正原分析根因排序）  
> 状态:**P0 已落地**；**P1 全部落地**；**P2 以开关落地**（rich skeleton / 两阶段 / 对冲 / cap·effort / deadline 降级，均已单测，未跑真实 A/B）——见 §3/§4/§7

## 0. 一句话

驱动 fan-out / 门禁 / 有界重派本身是对的；54.4m 的关键路径是 **F06 三轮（25.2+25.2+4.05）**，主因是 **dispatch 输入自相矛盾**（错 blueprint + Scene 时间码溢出）把思考顶穿 48k，再被「截断→CLI 续写丢思考→空等 1500s」放大。

## 1. 样本事实（以日志为准）

| 项 | 值 |
|---|---|
| 项目 | `/app/videos/2026-11-21/userclm17919893695531772/6b01e0245007d730` |
| frame-html 起止 | UTC 18:27:00 → 19:21:26 |
| 帧数 | 7；`skeleton=True`；`max_attempts=3`；cap=48000；timeout=1500s |
| 无 F06 时首波 max | F04 ≈ 15.9m（贴近旧 P0 基线） |
| F06 #1 | 18:27→18:52 rc=124；48k 纯思考截断后 CLI 注入 *no visible output* 续写，第 2 轮被超时杀掉 |
| F06 #2 | →19:17 rc=124；写出后 selfcheck PASS，距完成约 16s 被超时杀掉 |
| F06 #3 | 4.05m `done OK`（实质沿用 #2 产物做小修） |
| F01 | done OK + selfcheck FAIL → findings 重派 34s 修好（驱动逻辑正确） |

### 原分析需修正的口径

1. 「F06 拖尾 42.5m」→ **F06 即整条关键路径 54.4m**。  
2. 「截断后进程挂起」→ **CLI 自动续写并丢弃上一轮 thinking**。  
3. 「首落盘大量违反 1–2 回合」→ `skill-timing.py` 按 jsonl **行**计回合，并行 Read 被拆行；按 API 请求多数第 2–3 次已落盘，瓶颈是**单请求思考量**。  
4. 「内联全量 recipe 是头号杠杆」→ 读 recipe 仅 ~1–2s；墙钟收益极小。  
5. 「Rule index 是 Adapt 残留」→ **反了**：Rule index 来自 visual；**内联的 blueprint 才是 storyboard 旧字段**（`field()` 取第一个 `- blueprint:`）。

### 遗漏根因（Opus 补全）

1. **7/7 帧 blueprint 选错**（storyboard 字段 vs visual `(Adapt)` 指派）。  
2. **5/7 帧 Scene 时间码溢出**（`sync-durations` 只改 `duration` 不缩放 Scene）。  
3. **波次屏障**（`pool.map` 等最慢帧才重派；F01 白等 ~14.6m）。  
4. 超时不回收「已 PASS 的半成品」。

## 2. P0 已落地（2026-11-22）

### 2.1 改动文件（P0 当时 15；P0+P1+P2 全量见清单文档）

P0 首批：`frame_html.py`、`claude_cli.py`、`audio.mjs`、`frame-packets.mjs`、`frame-packets-core.mjs`（+`.claude` 副本）、aiflow 三份文案（+`.claude` 副本）、三份既有测试。

**全量路径与阶段归属**以 `docs/2026-11-22-frame-html-hardening-file-list.md` 为准（含 P1 的 `frame-selfcheck.mjs` / `stat/skill-timing.py` / `test_skill_timing.py` 等）。

### 2.2 项对照

| ID | 内容 | 关键落点 |
|---|---|---|
| **P0-1** | `selectBlueprint()`：多 `- blueprint:` 时优先 `(Adapt\|Reproduce)` / 取最后一个；strip 后缀再拼文件；冲突 warning | `frame-packets-core.mjs` |
| **P0-2** | `sync-durations` 等比缩放 `Scene N (a–b s)`；packet 侧 drift warning/strict | `audio.mjs` + packet core |
| **P0-3** | jsonl 截断早杀 → rc=125；重派 `CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000` | `claude_cli.py` `_TruncationWatcher` + driver |
| **P0-4** | 逐帧流水线（失败立即重试）；阶段 deadline；波次回滚开关 | `frame_html.py` |
| **P0-5** | rc∈{124,125} 且文件已变且 selfcheck PASS → salvage 收尾（小 cap/少 turns） | `frame_html.py` |
| **P0-6** | 文案：recipe **未**全文内联；禁自跑 selfcheck；Scene > VD > blueprint > 叙述 | prompt / card / SKILL |

### 2.3 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `HTMLVIDEO_FRAME_HTML_WAVE_MODE` | `0` | `1` = 旧波次 `pool.map` |
| `HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE` | `2400` | 超时后不再发起新尝试（秒） |
| `HTMLVIDEO_FRAME_HTML_TRUNC_KILL` | `1` | `0` = 关闭截断早杀 |
| `HTMLVIDEO_FRAME_HTML_SALVAGE` | `1` | `0` = 关闭 salvage |
| `HTMLVIDEO_SYNC_SCALE_SCENES` | `1` | `0` = sync 只改 duration |
| `HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT` | `0` | `1` = Scene drift 直接报错 |

### 2.4 验收（本会话已跑）

- `python3 -m unittest test.test_frame_html_driver test.test_claude_cli` → 61 OK  
- `node test/test_frame_packets.mjs` → 含 `selectBlueprint` / dual blueprint / scene drift  
- 样本临时副本：7 个 Selected blueprint 与 Blueprint assignment 对齐；sync 后 Scene 尾 = duration，二次 sync 幂等  
- **未做**：真实 `claude` 端到端回放该样本（应用新 packet + 新 driver）

### 2.5 已知限制

- 只缩放 `Scene N (a–b s)`；正文 `At ~3.4s` 等散落时间点不改。  
- 骨架仍可通过旧 selfcheck → salvage 依赖「文件哈希相对 skeleton 已变」；P1-1 补 `skeleton_unfilled` 后更安全。  
- deadline 不杀在途 worker；最坏 ≈ deadline + 单 worker timeout。  
- 早杀放弃 CLI 自续写机会，改为升 cap 重派（可用 `TRUNC_KILL=0` 回滚）。

### 2.6 反事实墙钟（同一样本）

| 假设 | 预期阶段墙钟 |
|---|---|
| 只修输入矛盾（P0-1/2）且 F06 不再截断 | ≈ **16m** |
| 只修截断早杀 + 回收（P0-3/5） | ≈ **28m** |
| P0 全开 | ≈ **16m**（最坏 ≈ 25–28m） |

## 3. P1（本周目标：p50 ≤16m，截断率 ≤10%）——**已落地 2026-11-22**

| ID | 状态 | 内容 | 主文件 |
|---|---|---|---|
| **P1-1** | ✅ | selfcheck `skeleton_unfilled`：仅在含 `skeleton-v1` 时布防；FAIL 需证据 `root-empty`（#root 无非 `data-skeleton` 子元素且无文本）或 `no-tween`（内联 script 无 `.to/.from/.fromTo/.set/.call` / `tl.add(`）；仅 style 为空 → WARN。成品残留标记注释不受影响（样本 7 帧中 2 帧残留，全 PASS） | `scripts/frame-selfcheck.mjs` |
| **P1-2** | ✅ | `detectConflicts()`：blueprint 多字段、blueprint vs Assignment、Scene 尾溢出（给出 ×k 重排后的窗口）/不足、blueprint `**duration**` 范围（容差 max(0.25s,10%)）、负面清单 3D/orbit/spin（含否定窗口：前 4 词含 no/never/without… 不算）、`background always X` vs block `<色> field`。dispatch 末尾写 `## Resolved conflicts` 逐条 Ruling | `frame-packets-core.mjs` |
| **P1-3** | ✅ | `skill-timing.py` 按 `message.id` 合并请求：rounds/usage 去重；新增 `api_requests`、`truncations`、`max_req_s`、`first_write_req` | `stat/skill-timing.py` |
| **P1-4** | ✅ | 阶段结束（成功或失败）一行 INFO：`frame-html stage summary frames= ok= failed= attempts= truncations= salvages= timeouts= degraded= hedges= hedge_wins= critical_path_frame= critical_path_s= total_s=`（pipeline + wave 两条路径） | `frame_html.py` |
| **P1-5** | ✅ | `HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES` = `snippet`（**默认开**）/ `full` / `off`；snippet = `How It Works` 首段 + 首个 JS 代码块，≤2048B；Rule index 保留；driver 按 dispatch 标题切换 prompt。有问题设 `off` | core + `frame_html.py` |

### 3.1 实现要点

- **salvage × skeleton_unfilled**：salvage 门由「selfcheck PASS」放宽为 `_salvageable()`——PASS，或唯一 FAIL 是 `skeleton_unfilled` 且证据不含 `root-empty`（已写 body、未写 timeline 的半成品）。纯骨架（root-empty）不再被 salvage。
- **冲突裁决优先级**写入 worker prompt 与 card：`## Resolved conflicts` > Scene list > Video direction/设计契约 > blueprint > frame.md 叙述。
- **blueprint 正文扫描**只认强 3D 词（`3d`/`rotateX|Y`/`perspective`）+ orbit/spin；`flip/tilt` 只在 frame block 中计入（blueprint 枚举变体噪声大，如 "status pill flips"）。
- **skill-timing**：同一 message.id 多行时 usage 各字段取 max 再累加；截断 = 纯 thinking 且（out ≥ `--trunc-cap`，默认 48000，或 `stop_reason=max_tokens`）的请求 ∪ isMeta「no visible output」提示（归到前一请求，不重复计）；`max_req_s` = 上一非 assistant 事件 → 该请求最后一行。
- **CSV 列变化**：此前表头有 `wall_s` 但行内漏打（列错位），现补齐；末尾追加 `api_requests,truncations,max_req_s,first_write_req`。`rounds` 数值因去重变小（口径修正，非回归）。list/group 表各加 `req/trn/maxReq`、`trunc/maxReq` 列。
- `log/stat/skill-timing.py` 是 11-17 的旧快照（早于 driver 签名识别），无代码调用，**未同步**；以 `stat/skill-timing.py` 为准。

## 4. P2（后续：阶段 p50 10–12m）——**开关落地；富骨架/两阶段/对冲默认开（2026-11-22）**

| ID | 状态 | 内容 | 开关 / 默认 |
|---|---|---|---|
| **P2-1** | ✅ | `buildSkeletons({rich})`：全时长 `class="clip bg" data-skeleton="bg"`（track 0）、每 Scene 占位注释、`const tl = gsap.timeline({ paused: true }); window.__timelines["id"] = tl;` + `tl.addLabel("sceneN", t)`（Scene 溢出时按 duration 等比缩放 label）。结构门全 PASS，仍判 `skeleton_unfilled`；driver 检测 `(rich)` 标记追加复用说明 | `HTMLVIDEO_FRAME_HTML_RICH_SKELETON` 默认 **1**（有问题设 `0`） |
| **P2-2** | ✅ | 仅 attempt 1（skeleton 模式）：Phase A（style+body，cap 24000 / 20 turns，回复 `phase-a OK`）→ rc=0 才跑 Phase B（内联当前文件，写 script，正常 cap）。**只做最终门禁**；A 失败（124/125）直接按普通失败处理（可 salvage：body 已写的 `no-tween` 半成品），重试回到单阶段 | `HTMLVIDEO_FRAME_HTML_TWO_PHASE` 默认 **1**（有问题设 `0`） |
| **P2-3** | ✅ | 仅当「其余帧全部 ok、只剩该帧在跑、主尝试已运行 ≥ 阈值、帧文件自开跑后哈希未变（无 Write 心跳）、未过 deadline、非 salvage」时，在 `.hyperframes/hedge/<fid>-<n>-*/` 副本中起 hedge；先过门禁者胜：hedge 胜 → 取消主尝试（`cancel_event` → 杀进程组 rc=130）→ 等其退出后拷回并复检、标 animated；主胜 → 取消 hedge。两者皆败 → 用主尝试结果走普通重试。每帧最多一次 | `HTMLVIDEO_FRAME_HTML_HEDGE` 默认 **1**；`HTMLVIDEO_FRAME_HTML_HEDGE_AFTER` 默认 **660**s（有问题设 `HEDGE=0`） |
| **P2-4** | ✅ | driver 级 extra_env：`CLAUDE_CODE_MAX_OUTPUT_TOKENS` / `CLAUDE_CODE_EFFORT_LEVEL`（effort 非法值告警忽略）；截断重派 cap = max(64000, 覆盖值)；salvage/降级/Phase A 用各自 cap | `HTMLVIDEO_FRAME_HTML_MAX_OUTPUT`、`HTMLVIDEO_FRAME_HTML_EFFORT` 默认未设（= profile：48000 / high） |
| **P2-5** | ✅ | 重派时若 `deadline − elapsed < window`：走降级 prompt（每元素 ≤1–2 个简单 tween、无 3D/粒子、禁止 Read、Scene 必须齐），cap 32000 / 30 turns / timeout=max(300, 剩余秒)，日志 `retry … as degraded … degraded=1`。salvage 优先于降级；过 deadline 仍按 P0 不再发起 | `HTMLVIDEO_FRAME_HTML_DEGRADE` 默认 **1**（只影响本会失败的尾部帧）；`HTMLVIDEO_FRAME_HTML_DEGRADE_WINDOW` 默认 **600**s |

> P2-5 窗口未采用「1.5× 单次 timeout」：默认 timeout 1500s × 1.5 = 2250s ≈ deadline 2400s，几乎所有重派都会被降级；改为固定窗口 600s（可调）。

### 4.1 cap × effort 矩阵怎么跑（P2-4）

```bash
# 每格至少 3 个样本项目；同一批 STORYBOARD（frame-packets 重新生成）
for cap in 32000 48000 64000; do
  for effort in medium high; do
    HTMLVIDEO_FRAME_HTML_MAX_OUTPUT=$cap HTMLVIDEO_FRAME_HTML_EFFORT=$effort \
      <触发 htmlvideo worker 的 frame-html 阶段>
  done
done
# 观测：worker 日志里的 `frame-html stage summary`（truncations / total_s / critical_path_s）
python3 stat/skill-timing.py --since '2026-11-23' --group --trunc-cap $cap   # trunc、maxReq、1stWavg
```

判读：截断率 = Σtruncations / Σattempts（目标 ≤10%）；阶段 p50 用 `total_s`；单请求思考量看 `maxReq`。

## 5. 环境变量总表（P0 + P1/P2）

| 变量 | 默认 | 作用 |
|---|---|---|
| `HTMLVIDEO_FRAME_HTML_WAVE_MODE` | `0` | `1` = 旧波次 |
| `HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE` | `2400` | 超时后不再发起新尝试（秒） |
| `HTMLVIDEO_FRAME_HTML_TRUNC_KILL` | `1` | 截断早杀 |
| `HTMLVIDEO_FRAME_HTML_SALVAGE` | `1` | salvage 收尾 |
| `HTMLVIDEO_SYNC_SCALE_SCENES` | `1` | sync 等比缩放 Scene |
| `HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT` | `0` | Scene drift 直接报错 |
| `HTMLVIDEO_FRAME_PACKETS_RESOLVE_CONFLICTS` | `1` | **P1-2** 写 `## Resolved conflicts`（`0` 仅 console warn） |
| `HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES` | `snippet` | **P1-5** 默认开；`full` 全文；`off`/`0` 关闭 |
| `HTMLVIDEO_FRAME_HTML_RICH_SKELETON` | `1` | **P2-1** 富骨架（设 `0` 回滚） |
| `HTMLVIDEO_FRAME_HTML_TWO_PHASE` | `1` | **P2-2** 两阶段填充（设 `0` 回滚） |
| `HTMLVIDEO_FRAME_HTML_HEDGE` | `1` | **P2-3** 对冲（设 `0` 回滚） |
| `HTMLVIDEO_FRAME_HTML_HEDGE_AFTER` | `660` | 对冲阈值（秒） |
| `HTMLVIDEO_FRAME_HTML_MAX_OUTPUT` | 未设 | **P2-4** 覆盖 worker cap |
| `HTMLVIDEO_FRAME_HTML_EFFORT` | 未设 | **P2-4** 覆盖 effort（low/medium/high/xhigh/max） |
| `HTMLVIDEO_FRAME_HTML_DEGRADE` | `1` | **P2-5** 临近 deadline 降级重派 |
| `HTMLVIDEO_FRAME_HTML_DEGRADE_WINDOW` | `600` | 降级窗口（秒） |

`skeleton_unfilled` 本身无开关（selfcheck 规则）；回滚方式是去掉骨架模式 `HTMLVIDEO_FRAME_HTML_SKELETON=0`。

## 6. 已知限制（P1/P2）

- 冲突检查是关键词级静态规则：色彩语义只做「背景色固定」一类；`At ~3.4s` 等散落时间点不改；否定识别只看前 4 词，"rows settle with no bounce, cards flip" 中 flip 仍计为正向（正确），但更复杂的否定句可能漏判/误判。
- snippet 模式会让 dispatch 超过 40000B 预算（样本 F04/F06 ≈ 45–47KB，仅 warning）。
- hedge 的「无 Write」心跳只看帧文件哈希是否变化；hedge 期间该帧 token 成本翻倍；被取消一方的 jsonl 会话保留在 Claude 配置目录。
- 两阶段只作用于 attempt 1；Phase A 未回复 `phase-a OK` 不作门禁（只看 rc）。
- 降级帧画面会更素；日志 `degraded=` 与汇总 `degraded=N` 可追溯。
- 全部 P2 只有 mock 单测，**未**真实 `claude` 端到端回放。

## 7. 变更记录

### 2026-11-22 晚 — 默认开关再开一档
- `HTMLVIDEO_FRAME_HTML_RICH_SKELETON` / `TWO_PHASE` / `HEDGE`：默认开；有问题设 `0`。
- `HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES`：默认改为 **`snippet`**（设 `off`/`0` 关闭；`full` 全文）。
- 代码：`frame_html.py`、`frame-packets-core.mjs`（含 `.claude` 副本）、文案/文档/单测同步。

### 2026-11-22 — P2 三项默认打开（初）
- `RICH_SKELETON` / `TWO_PHASE` / `HEDGE`：默认开；fixture 显式 `0` 以免拆坏旧用例。

### 2026-11-22 — P1/P2 初落地

**文件**

- `scripts/frame-selfcheck.mjs`（P1-1）
- `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` + `.claude/` 副本（P1-2、P1-5、P2-1）
- `html_utility/frame_html.py`（P1-4、P1-5 prompt、P2-2/3/4/5、salvage 门）
- `html_utility/claude_cli.py`（`cancel_event` → `CANCELLED_RC=130`，供 hedge）
- `stat/skill-timing.py`（P1-3）
- `skills/aiflow-build-frame-html/SKILL.md`、`sub-agents/frame-worker-card.md` + `.claude/` 副本（文案：Resolved conflicts 优先级、Rule recipes 例外、skeleton_unfilled）
- 测试：`test/test_frame_packets.mjs`（skeleton 现预期仅 `skeleton_unfilled` FAIL；新增 selfcheck/富骨架/冲突/snippet）、`test/test_frame_html_driver.py`（+17）、`test/test_claude_cli.py`（+1 cancel）、新增 `test/test_skill_timing.py`（5）

**验收**

- `python3 -m unittest test.test_frame_html_driver test.test_claude_cli test.test_skill_timing` → 84 OK
- `node test/test_frame_packets.mjs` → 全 OK
- 样本临时副本（同步前 STORYBOARD）：7 帧共检出 blueprint-fields×6、scene-overflow×4、scene-underflow×3、blueprint-duration×2、negative-list-*；7 个已完成帧 selfcheck 仍全 PASS

## 8. 关联

- **完整修改文件清单**：`docs/2026-11-22-frame-html-hardening-file-list.md`  
- [[build-frame-html-deadzone-deepdive]]（48k/32k 截断机制）  
- [[2026-11-20-frame-html-visual-optimization-design]]（旧 P0/P1/P2 命名）  
- [[2026-11-20-frame-html-worker-input-reduction-design]]  
- [[2026-11-18-frame-html-redesign-design]]（确定性 fan-out）  
- 进度记忆：`log/claude/projects/-app/memory/htmlvideo-frame-html-visual-p0p1-progress.md`
