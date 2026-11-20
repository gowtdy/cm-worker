# frame-html worker 输入减负 + 思考收紧(短设计)

> 状态:已批准(用户「按照建议做吧」,选 A + B 的 prompt 收紧)
> 依据:[[htmlvideo-frame-html-visual-p0p1-progress]]、`2026-11-20-frame-html-visual-optimization-design.md` P2/P4、P0 per-worker 数据

## 意图 / 为什么
frame-html 是当前最大头(**18.6m**)。P0 数据:worker input **76–140K**、回合 7–25;最慢的帧不是回合最多的(03:25 回合/401s vs 01:13 回合/1116s)→ **单回思考量 + 上下文重量才是主因**,回合数是次要。worker 现状:既读 `.dispatch.md` 里内联的 frame.md 摘录 + 紧凑 card,又被逃生口指向整份 `frame.md`(17KB)+ 整份 `_role.md`(33.6KB)——**重复读**。

## 目标
让 `.dispatch.md` 成为 worker 的**唯一输入**(已内联 card + blueprint 全文 + rule recipe + frame.md 摘录 + 本帧 block),删掉「再读完整 frame.md / 完整 _role.md」的诱导;外加 prompt 思考纪律「禁重推导、每回思考 ≤1 段」。

## 非目标(明确不做)
- 不动 cap(64000→48000 留后续 A/B)。
- 不动 max_turns(60→40 是空刀:实测最多 25 回合)。
- 不做 C(design contract 并入 dispatch;需 P1 先在真实项目落 `_design.md`)。

## 改动落点(3 处)
1. `html_utility/frame_html.py::_worker_prompt` —— escape hatches 删掉 `frame.md` / `_role.md` 两条,只留 cut-catalog + 补 RULES_DIR;新增「dispatch 即配方、禁重推导、每回思考 ≤1 段」。
2. `skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md` —— 头注删「full `_role.md` authoritative」;Workflow-Read 删「then `frame.md`」;escape hatches 只留 `RULES_DIR/<id>.md`(recipe 缺失)+ cut-catalog。
3. `skills/hyperframes-core/references/frame-worker-core.md` —— line 21/57 把「frame.md 是你 packet 之外要读的那份」改成「palette/type 走 dispatch 内联摘录;仅摘录缺确切 token 才开全文」。

## 风险与验证
- 风险:12KB 摘录是否够。已核实摘录白名单覆盖 Colors/Typography/Depth/Shapes/Components/Composition Rules/Entities/Numerals/Audit,实质齐全,**低险**。
- 验证 A/B:重跑 `test_parallel_caption_frames.py --log-file`(新 token),对比 per-worker `input`(预期 76–140K 下降)+ `duration`,质量门 frame-selfcheck PASS + assemble 成功 + STORYBOARD `animated:6` 不回归。