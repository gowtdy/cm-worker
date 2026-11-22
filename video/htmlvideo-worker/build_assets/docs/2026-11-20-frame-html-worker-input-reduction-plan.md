# frame-html worker 输入减负 + 思考收紧 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 frame-html worker 的 `.dispatch.md` 成为唯一输入,删掉「再读完整 frame.md / _role.md」的诱导,并加「禁重推导、每回思考 ≤1 段」的思考纪律,压低 18.6m 里每 worker 的 76–140K input。

**Architecture:** 纯 prompt/文案收紧,不动任何代码逻辑、不动 cap、不动 max_turns、不并入 design contract。三处文案源(`frame_html.py` 的 `_worker_prompt`、dispatch 内嵌的 `frame-worker-card.md`、全量 role 的 `frame-worker-core.md`)统一把「design truth / role 全文」从「必读/逃生口」降级为「已内联到 dispatch,除非缺确切 token 才开」。

**Tech Stack:** Python 3(unittest)+ Node(`frame-packets.mjs` 重建 packet)+ `test_parallel_caption_frames.py` 做 A/B。

**Spec:** `/app/docs/2026-11-20-frame-html-worker-input-reduction-design.md`

## Global Constraints

- **无 git 仓库**(`/app` 非 git)——本计划**不含任何 commit 步骤**,验证靠测试 + A/B 数据对比。
- 环境:frame-html 经 `claude` 子进程消费 token,需 `ANTHROPIC_AUTH_TOKEN` 内联注入(当前新 token `bce-v3/ALTAKSP-FqUf…`);A/B 跑真实项目 `6afea3068088f914`(in-place 重写帧,是该测试设计用途)。
- 不改 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`(cap 保持 64000)、不改 `max_turns`(保持 60)、不改 `HTMLVIDEO_FRAME_*` 默认开关。
- 基线锚点(P0,11-20 r2 落盘):total=1116.9s,6 帧 round 0,per-worker input 76–140K、回合 7–25、duration 401–1116s。A/B 对比对象就是这份。

## Review Focus

- **设计保真漂移(最高风险)**:删掉「读完整 frame.md」后,worker 只用 12KB 摘录;frame-selfcheck/assemble 是结构性 gate,**看不到用错色/错字体**。→ 摘录白名单经核实覆盖 Colors/Typography/Depth/Shapes/Components/Composition/Entities/Numerals/Audit,低险,但仍需 A/B 后肉眼/verify 抽验一帧。测试落在 Task 3。
- **禁用过头**:若误删了 worker 真正需要的 recipe 引用(RULES_DIR),某个引用 motion 会缺 recipe → 帧坏。→ 设计保留 `RULES_DIR/<id>.md` 逃生口;Task 2 静态核对 dispatch 仍列出 Rule index。测试落在 Task 2。
- **测试断言英文串漂移**:现有 `test_worker_prompt_locks_scope_to_single_frame` 断言 `"Scope is exactly one file: …"` 与 `"Never read, open, or write any other"` —— 编辑不得碰这两句。测试落在 Task 1。

---

### Task 1: 收紧 `_worker_prompt`,加回归测试

**Files:**
- Modify: `html_utility/frame_html.py`(函数 `_worker_prompt` 的 escape-hatch 段)
- Test: `test/test_frame_html_driver.py`(新增 `test_worker_prompt_dispatch_self_sufficient`)

**Interfaces:**
- Consumes: 无(独立)。
- Produces:`_worker_prompt(frame_id, skeleton_mode) -> str` 语义不变(prompt 文本);新增测试断言其不再把 `frame.md`/`_role.md` 列为逃生口。

- [ ] **Step 1: 写失败测试**

在 `DriverTest` 加:

```python
def test_worker_prompt_dispatch_self_sufficient(self):
    p = fh._worker_prompt("01-hook", skeleton_mode=True)
    self.assertNotIn(".hyperframes/frame-packets/_role.md", p)
    self.assertIn("RULES_DIR", p)
    self.assertIn("never open frame.md or _role.md", p)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /app && python3 -m pytest test/test_frame_html_driver.py::DriverTest::test_worker_prompt_dispatch_self_sufficient -q`
Expected: FAIL(现有 prompt 的 escape hatch 仍含 `_role.md`,无 `never open frame.md or _role.md`)。

- [ ] **Step 3: 改 `_worker_prompt` 的 escape-hatch 段**

把
```
Escape hatches — open ONLY when a rule in the dispatch is genuinely unclear,
and read at most the one you need:
  frame.md                                       (design truth: palette/type/tokens)
  .hyperframes/frame-packets/_role.md            (full contract, authoritative)
  {CUT_CATALOG_PATH}   (only for within-scene cut seams)
Never edit STORYBOARD.md. Turn discipline: ...
```
替换为:「dispatch 已内联 frame.md 摘录 + 卡 + blueprint + recipe,自洽;禁重推导、每回思考 ≤1 段」+ 逃生口只留 `RULES_DIR/<id>.md`(recipe 缺失)与 cut-catalog,并写明「never open frame.md or _role.md」。**不得触碰** `Scope is exactly one file: …` 与 `Never read, open, or write any other` 两句。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /app && python3 -m pytest test/test_frame_html_driver.py -q`
Expected: 全 PASS(新测试绿 + 原有 `test_worker_prompt_locks_scope_to_single_frame` 仍绿)。

- [ ] **Step 5: 完整测试文件复查**

Run: `cd /app && python3 -m pytest test/test_frame_html_driver.py -q`
Expected: 无回归。

---

### Task 2: 收紧 dispatch card + 全量 role 两处文案

**Files:**
- Modify: `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`
- Modify: `skills/hyperframes-core/references/frame-worker-core.md`

**Interfaces:**
- Consumes: Task 1 的「dispatch 自洽」口径(用词一致:`RULES_DIR/<id>.md` 为唯一 recipe 逃生口、`never open frame.md / _role.md`)。
- Produces: 重建后 `.dispatch.md` 内嵌 card、`_role.md` 全文不再诱导读整份 frame.md / 整份 _role.md。

- [ ] **Step 1: 改 `frame-worker-card.md` 三处**

  - 头注(第 3–4 行):删「The full `_role.md` … is authoritative」,改为「本 card + frame block + 内联 blueprint/recipe/frame.md 摘录 = 完整契约,勿开 `frame.md`/`_role.md`;`RULES_DIR/<id>.md` 仅 recipe 缺失时开」。
  - Workspace-Read(第 40 行):「then `frame.md`」→「the inlined frame.md design-token excerpt」,并加「never `frame.md` / `_role.md` / skill files」。
  - 末段 escape hatches(第 45 行):删「full `_role.md` — authoritative」「`frame.md` (project root) = design truth」,只留 `RULES_DIR/<id>.md` + cut catalog。

- [ ] **Step 2: 改 `frame-worker-core.md` 两处**

  - 第 21 行:`frame.md` bullet 把「This is the one file you read outside your packet」改为「dispatch 已内联 design-token 摘录;仅摘录缺确切 token 才开全文」。
  - 第 57 行(Workflow Read):「then `frame.md` (the look)」→「the inlined frame.md design-token excerpt」。(第 58 行 Design 提 `frame.md` 的 components 系指摘录里的 components,措辞可不动。)

- [ ] **Step 3: 重建 packet + 静态核对**

Run:
```bash
cd /app && node scripts/frame-packets.mjs --project /app/tmp-hf-visual-fixture --storyboard /app/tmp-hf-visual-fixture/STORYBOARD.md 2>&1
```
Expected: 无报错;随后 `grep -nE "frame\.md|_role\.md" .../.hyperframes/frame-packets/XX.dispatch.md` 确认「诱导读全文」只剩「已内联」表述,且 Rule index(`RULES_DIR`)仍在、`_role.md` 不再以逃生口出现。

- [ ] **Step 4: 复查 reconstruction 产物**

Run: `grep -nE "one file you read outside|full ._role.md. — authoritative|= design truth" <fixture>/.hyperframes/frame-packets/*.md`
Expected: 无命中(旧诱导语已清)。

---

### Task 3: A/B 复验(新 token)对比基线

**Files:**
- 无代码改动;仅运行测试 + 读日志。

**Interfaces:**
- Consumes: Task 1/2 的收紧文案(已落盘)。
- Produces: 一份对比结论(per-worker `input`/`duration` vs P0 基线),写入 memory。

- [ ] **Step 1: 重建真实项目 packet**(确保真实项目用上新的 card/role)

Run: `cd /app && node scripts/frame-packets.mjs --project /app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914 --storyboard /app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914/STORYBOARD.md 2>&1`

- [ ] **Step 2: 跑 A/B(后台,~18m,新 token 内联)**

Run(background):
```bash
cd /app && ANTHROPIC_AUTH_TOKEN="bce-v3/ALTAKSP-FqUfjwDb9vZdQkWx3Pxfg/726b59cee089188eb528c3e42e233c8287ee5fc2" \
  python3 test/test_parallel_caption_frames.py --log-file /app/log/frame-html-baseline-16m-r3.log 2>&1
```

- [ ] **Step 3: 提取 per-worker 数据对比基线**

从 `--log-file` 抽 `claude skill done … num_turns/duration_ms/input/output/cache_read`,逐帧对比 P0 r2 的 76–140K input / 401–1116s。
Expected 判读:(a) input 中位数下降即「输入减负」生效;(b) duration 下降即「思考纪律」见效;(c) 均不降则记录「软收紧作用有限,需转 cap 48000 A/B」。

- [ ] **Step 4: 质量门 + 视觉抽验**

Run: 确认日志 `STORYBOARD statuses: {'outline': 0, 'animated': 6}` + `frames: ret=0`;再对慢帧跑一次 `node /app/scripts/frame-selfcheck.mjs compositions/frames/<慢帧>.html` 全 PASS;并对 contact sheet / 一帧 HTML 做一次肉眼抽验(用色/字体是否仍合 frame.md)。

- [ ] **Step 5: 结论写进 memory**

更新 `htmlvideo-frame-html-visual-p0p1-progress.md`:记录 A/B 结果(每 worker input 变化、duration 变化、是否达成)——成功则标记「P4 输入减负已落地」,失败则标记「软收紧无效果,下一步 cap 64000→48000 A/B」。