# build-frame-visual P0+P1+P2 hardening（2026-11-22）

> 续篇（同日下午）：强制每帧 `- rules:` + gate ✗ + worker 验收 → `docs/2026-11-22-thinking-reduction-and-visual-rules.md` §2.B / §7

来源：任务 `6b01e0245007d730` visual 阶段 433.8s 的分析。主要耗时/失败点：

| 问题 | 损耗 |
|---|---|
| F6 把 Scene 行写成 `- Scene 1`，`_SCENES_RE=^Scene` 判失败 | 1 次无谓重派 |
| `pool.map` 整轮屏障：失败帧要等最慢帧结束才重试 | ~120s 白等 |
| Scene 时间窗重叠（如 F3 `0.0–1.8 / 1.6–6.8 / 6.6–9.0`）→ gate 失败 → LLM repair | ~40s |
| F1 thinking 过长（在思考里写完整稿 + 逐条负面清单自检） | 分钟级 |
| design 看不到帧内容，转场/共用舞台无法统一 | 质量 |
| `/aiflow-build-frame-visual` slash + 内联指令 → SKILL 双灌 | 每会话输入 token |

## 改动

### P0
- **P0-1 Scene 解析放宽 + 规范化**：`_SCENES_RE` 接受 `- ` / `* ` / `**` / 缩进前缀；`_normalize_scene_lines` 把 Scene 行改写为裸 `Scene N (…): …`（`**Scene 1 (…):**` 会去掉闭合 `**`），并去掉 `- focal:` 等字段行的缩进。`_diagnose_worker_reply` 返回失败原因（empty / 缺 Scene / 缺 focal|roles / 缺或不匹配 `done OK N`）。
- **P0-2 时间窗确定性纠偏**：`_normalize_scene_windows(text, duration)`，在 parse 之后、落盘之前执行：
  - 首段 start 一律拉到 0.0；
  - 和上一段重叠时，把 start 钳到上一段 end（会导致窗口倒置时不改，留给 gate/repair）；
  - 帧 block 有 `- duration:` 时：覆盖率 < 90% 则把末段 end 延长到 duration；末段超出 duration > 0.2s 则收回到 duration（与 gate 阈值一致）；
  - gate 正则解析不了的窗口写法（如 `(0.0 s – 2.0 s)`）规范成 `(0.0–2.0s)`；
  - 纠偏内容会打日志 `visual F<n> scene windows fixed: …`。gate + LLM repair 仍作为兜底保留。
- **P0-3 立即重派**：`_run_frame_workers` 改用 `submit` + `wait(FIRST_COMPLETED)`，某帧失败后**立刻**以 `_dispatch_note(rc, reply, fn)`（失败原因 + 回复末尾 200 字）重派，不等其它帧；每帧额外重试次数仍为 `MAX_RETRY_ROUNDS`（=1）。修复了旧代码重派时传入 `text=""`、导致 note 里永远没有原文的问题。

### P1
- **P1-1 thinking 收紧**（`claude_cli.py`）：`visual` profile 的 thinking 默认 64000 → **24000**，effort 保持 high，max_output 保持 32000。design 会话（含 `design-bp-fix`）通过新增的 `run_claude_result(..., thinking_default=)` 按调用要 **32000**。worker prompt 增加一句：不要在思考里写完整稿/逐条负面清单自检，由 harness 校验格式。
  > 注意：文件头注释已说明，adaptive 后端会剥掉 `budget_tokens`，thinking 这个参数可能不生效；真正生效的是 `max_output`。这一项是低风险的对齐改动，是否有收益需要 A/B 验证。
- **P1-2 design 喂帧摘要 + 定转场**：`_build_design_prompt(..., summaries=)` 内联每帧的 `F<n> — title · beat · duration · transition_in · vo 首句（≤120 字）`，以及共用舞台提示（启发式：相邻帧 scene 中出现 `same/continues/remains…`，或 scene 关键词 Jaccard ≥ 0.3，或 ≥ 0.2 且 duration 相差 ≤ 10%）。creative body 新增 **Locked transitions**（逐帧原样抄 `transition_in`）和 **Shared stage** 两段要求。design / worker / assignment-rewrite 三个 prompt 都去掉了开头的 slash，改为一句内联的 “aiflow-build-frame-visual contract … do not invoke any skill”。
- **P1-3 禁用 Skill**：`visual` profile 的 `deny_tools` 增加 `Skill`（与 verify 相同；不使用 `--bare`，避免弄断 token 鉴权）。

### P2
- **P2-1 prompt 顺序利于缓存**：worker prompt 改为“固定指令（与帧无关）→ `[DESIGN CONTRACT]` → `[SELECTED BLUEPRINT]` → `[CONTEXT — Frame N]` → `[YOUR FRAME]`”。design contract 之前的部分在所有帧之间逐字节相同（有单测断言）。
- **P2-2 blueprint 截断**：`_truncate_blueprint`，默认上限 5000 字符（env `HTMLVIDEO_FRAME_VISUAL_BLUEPRINT_MAX_CHARS`）。按以下顺序保留：标题 → 能完整放下的优先段（intent / motion vocabulary / duration …）→ 把超长的优先段（通常是 shot structure）按行截断，并补齐未闭合的 ``` → 剩余预算再填其它段。末尾附 `(truncated: blueprint X → Y chars; dropped: …)`。例如 kinetic-type-beats 28.7k → ~5.0k。
- **P2-3 每帧片段落盘**：成功的帧写入 `<project>/.hyperframes/visual/F{n}.md`（已规范化 + 纠偏）；merge 优先读磁盘片段。Phase B 开始前会清掉上一次运行留下的 `F*.md`（它们对应的是旧的 design contract）。`_design.md` 不变。
- **P2-4 timing 可观测**：每个 worker 打一行 `visual worker F<n> attempt=… wall=…s rc=… out=… chars ok=…`，design 打一行 `visual design wall=…s rc=… out=… chars`。`claude_cli` 在 usage 里含 thinking/reasoning 字段时（包括 `output_tokens_details.*` 这种嵌套一层的），额外打一行 `claude skill thinking label=… skill=… <k>=<v>`；没有这些字段时不打。

### 顺带修复：legacy 回滚路径
`HTMLVIDEO_FRAME_VISUAL_DRIVER=0` 的单会话流程需要模型自己 Edit STORYBOARD.md，但 `visual` profile 早已禁用 Write/Edit，所以这条回滚路径之前实际跑不通。新增 `visual_legacy` profile（high / 64000 / max_output 32000 / 不额外禁用工具），legacy 改用它，并保留 slash 写法。

## env 回滚 / 调参

| env | 作用 |
|---|---|
| `HTMLVIDEO_FRAME_VISUAL_DRIVER=0` | 整体回到 legacy 单会话（`visual_legacy` profile） |
| `HTMLVIDEO_CLAUDE_VISUAL_THINKING_TOKENS=64000` | 回滚 thinking；优先级高于 worker 默认值和 design 的按调用默认值 |
| `HTMLVIDEO_CLAUDE_VISUAL_EFFORT` / `_MAX_OUTPUT_TOKENS` | 沿用已有的通用机制（用户需求里写的 `_THINKING` / `_MAX_OUTPUT` 即这两个已有名字） |
| `HTMLVIDEO_FRAME_VISUAL_DESIGN_THINKING_TOKENS` | design 会话的 thinking 默认值（32000） |
| `HTMLVIDEO_FRAME_VISUAL_BLUEPRINT_MAX_CHARS` | blueprint 内联上限（5000）；设一个很大的值即关闭截断 |
| `HTMLVIDEO_FRAME_VISUAL_MAX_CONCURRENCY` / `_REPAIR_ROUNDS` | 不变 |

## 文件
- `html_utility/visual.py`：Scene 解析/规范化/诊断、时间窗纠偏、帧摘要 + 共用舞台、prompt 去 slash 并调整顺序、blueprint 截断、`_run_frame_workers`（立即重派 + 落盘）、merge 优先读片段、timing 日志、legacy 改用 `visual_legacy`
- `html_utility/claude_cli.py`：`visual` 改为 thinking 24000 并 deny `Skill`、新增 `visual_legacy` profile、`thinking_default` 参数、thinking usage 日志
- `test/test_visual_driver.py`：新增 Scene 前缀、诊断原因、dispatch note、时间窗纠偏（含 6b01 F3）、prompt 无 slash/前缀可缓存、帧摘要/共用舞台、blueprint 截断，以及 driver 级 mock 测试（失败帧在其它帧未结束时即重派、片段落盘、清理陈旧片段、叙事字段逐字节保留、legacy profile）
- `test/test_claude_cli.py`、`test/test_storyboard_contract.py`：profile 镜像同步更新，新增 thinking_default / env 优先级 / thinking 日志的断言

## 预期收益（以 6b01 为参照，433.8s）
- 重派不再被整轮屏障卡住：约 −120s
- 时间窗由 Python 修正，省掉一轮 gate→LLM repair：约 −40s
- `- Scene` 前缀不再判失败：省 1 次 worker 重派（与上一项叠加时，该帧不再进入重试）
- prompt 去 slash、blueprint 截断（长 blueprint 输入减少约 80%）、共享前缀利于缓存：降低输入 token 和首 token 延迟
- thinking 24000 + 提示不要在思考里写完整稿：取决于后端是否生效，需要 A/B
- 合计预计 433.8s → 约 250–280s；最慢的单帧仍是下限
