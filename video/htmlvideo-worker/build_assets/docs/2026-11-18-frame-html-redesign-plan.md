# aiflow-build-frame-html 重设计 实施计划（确定性 fan-out）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 build-frame-html 阶段从「单 claude -p orchestrator + 子代理 fan-out」（58.3m / 5 截断失败）改造成「Python 确定性 fan-out N 个独立 worker」（目标 ≤15m、0 截断、≤15 USD/run），worker 侧配合合并 dispatch 文档（输入减负）与确定性骨架预生成（filler 契约）。

**Architecture:** 控制回路全部下沉到 `html_utility/frame_html.py`（零 LLM 状态机：preflight → 并行 fan-out → collect → 重派 ≤2 轮 → 标 animated → assemble → 失败时一次性 repair 会话）；每帧一个独立 `claude -p` 进程（`skill="frame_html"`，per-worker env 注入 cap 64000 / timeout）；`frame-packets-core.mjs` 新增两张确定性产物（`<frame_id>.dispatch.md` 合并输入 + `compositions/frames/<frame_id>.html` 骨架）。

**Tech Stack:** Python 3（unittest + mock、concurrent.futures）、Node ESM（frame-packets/selfcheck，node:assert 测试）、Claude Code CLI（`claude --print`）、GSAP 3.14.2（骨架内联）。

**Spec:** `build_assets/docs/2026-11-18-frame-html-redesign-design.md`（计划从 spec 展开，执行者两份都读）。

## Global Constraints

- cap 数值只能落在 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`（`MAX_THINKING_TOKENS` budget_tokens 被 adaptive 后端剥除，写了也无效但仍须保留以回避 CLI 语义问题）。
- 串行阶段 profile：`max_output=32000`；`frame_html` profile：`max_output=64000`、`max_turns=60`、`timeout=720`、`deny_agent=True`；新增 `repair` profile：`effort=high, thinking=40000, max_output=16000, max_turns=40, timeout=600, deny_agent=True`。
- worker 完成信号：`rc==0 ∧ result 文本含 done OK <frame_id>`；skeleton 模式下 artifact 存在性不作为完成信号（骨架预写）；skelton off（回退）模式额外要求 artifact 存在。
- 重派 ≤2 轮，整批并行重派，每轮附失败原因；最终仍败 → raise（不代写帧）。
- dispatch 文档总预算 ≤40KB（超限 warning 不 fail）；role card ≤8KB（实测 7820B）；frame.md 摘取白名单小节（见 Task 5）；skeleton 必须过 `frame-selfcheck.mjs`。
- 所有新产物必须确定性与幂等：同输入两次构建逐字节一致。
- 质量门不降：frame-selfcheck 一次通过率、assemble 成功、`_validate_frame_html_outputs` 终态、verify 阶段终态不回归。
- 回滚开关：`HTMLVIDEO_FRAME_HTML_DRIVER=0`（回旧 orchestrator 路径）、`HTMLVIDEO_FRAME_HTML_SKELETON=0`（回 artifact 判定 + 无骨架提示）、`HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER=1`（不产 dispatch 文档）、`HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS=128000`（cap 回退档）、`HTMLVIDEO_CLAUDE_PROFILE=off`（全量回滚）。
- commit message 以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾。

---

### Task 1: 分级 cap profile（M1，止血 58.3m 事故）

**Files:**
- Create: `build_assets/test/__init__.py`（空文件，使 `test/` 成为包。没有它 `python3 -m unittest test.test_claude_cli` 直接 ModuleNotFoundError——本计划所有 Python 测试命令都以 `test.<模块名>` 形式运行）
- Modify: `build_assets/html_utility/claude_cli.py:23-65`（`_PROFILES` + 头注释）
- Modify: `build_assets/test/test_claude_cli.py:20-28`（`_PROFILES` 镜像表 + 新增断言）

**Interfaces:**
- Produces: `_PROFILES` 各 skill 的 `max_output` 键（intent/storyboard/visual/verify="32000"、frame_html="64000"、repair="16000"）；env 通道 `HTMLVIDEO_CLAUDE_<SKILL>_MAX_OUTPUT_TOKENS` / `_TIMEOUT` / `_MAX_TURNS` 不变。Task 6 驱动的 `extra_env` 依赖这些通道逐值注入。

- [ ] **Step 1: 更新 test_claude_cli.py 的镜像表为「目标值」（先写失败测试）**

`build_assets/test/test_claude_cli.py` 头部 `_PROFILES` 镜像改为目标六元祖（尾位即 deny_agent）：

```python
# Mirrors html_utility.claude_cli._PROFILES (post-clamp thinking where needed).
_PROFILES = {
    "intent": ("high", "32000", "100", 1800, True, "32000"),
    "storyboard": ("high", "64000", "100", 1800, True, "32000"),
    "visual": ("high", "64000", "100", 1800, True, "32000"),
    "frame_html": ("high", "64000", "60", 720, True, "64000"),
    "repair": ("high", "40000", "40", 600, True, "16000"),
    "verify": ("medium", "32000", "100", 1800, True, "32000"),
}
```

在既有断言函数后追加新测试（沿用该文件既有的 `_result_json` / mock-popen 风格，不放占位）：

```python
def test_profile_max_output_env(self):
    from html_utility.claude_cli import _resolve_profile
    logger = logging.getLogger("t")
    # frame_html default cap reaches the child env
    prof = _resolve_profile("frame_html", logger)
    self.assertEqual(prof["max_output"], "64000")
    # per-stage override beats the default
    with mock.patch.dict(os.environ, {"HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS": "128000"}):
        prof = _resolve_profile("frame_html", logger)
        self.assertEqual(prof["max_output"], "128000")
    # serial stages pinned at 32000
    for skill in ("intent", "storyboard", "visual", "verify"):
        self.assertEqual(_resolve_profile(skill, logger)["max_output"], "32000")
```

- [ ] **Step 2: 运行验证失败**

Run: `cd build_assets && python3 -m unittest test.test_claude_cli -v`
Expected: 新测试 FAIL（`_resolve_profile` 尚未注入 `max_output` 或值不符）。

- [ ] **Step 3: 修改 `_PROFILES`**

`build_assets/html_utility/claude_cli.py`：把 `_PROFILES` 全表替换为（同时更新文件头 14-22 行注释，说明 32000/64000/repair 分级的依据与 M0 结论填入点）：

```python
_PROFILES: dict[str, dict[str, object]] = {
    "intent": {
        "effort": "high", "thinking": "32000",
        "max_output": "32000",  # 2026-11-18 A/B 实证的地板档（intent 16.7s 历史最优）
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
    "storyboard": {
        "effort": "high", "thinking": "64000",
        "max_output": "32000",  # 同上（storyboard 3.0m 近地板）
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
    "visual": {
        "effort": "high", "thinking": "64000",
        "max_output": "32000",  # 同上（visual 7.1m 历史最优）
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
    "frame_html": {
        "effort": "high", "thinking": "64000",
        # Per-worker session cap. 64000: enough output budget that a worker
        # reading ~10k tokens of dispatch + skeleton-Editing never truncates,
        # far enough below 128k that marathon thinking loses its silent runway.
        # 128000 is the documented one-line rollback; serial stages stay 32000.
        "max_output": "64000",
        "max_turns": 60,       # per-frame fill: read + ~5 edits + selfcheck fixes
        "timeout": 720,        # per-worker wall kill-switch (skeleton keeps a valid file on disk)
        "deny_agent": True,    # B architecture: no stage dispatches subagents anymore
    },
    "repair": {
        "effort": "high", "thinking": "40000",
        "max_output": "16000", "max_turns": 40, "timeout": 600, "deny_agent": True,
    },
    "verify": {
        "effort": "medium", "thinking": "32000",
        "max_output": "32000", "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
}
```

- [ ] **Step 4: 运行全量单测**

Run: `cd build_assets && python3 -m unittest test.test_claude_cli -v`
Expected: 全绿（含 Step 1 新断言 + 既有 profile 测试）。

- [ ] **Step 5: Commit**

```bash
cd build_assets && git add html_utility/claude_cli.py test/__init__.py test/test_claude_cli.py
git commit -m "feat(claude_cli): per-stage max_output caps (serial 32000, frame_html 64000, repair profile)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: M0/M1 验证 runbook（一次真实 run + 结论回写）

**Files:** 无代码改动（如无 run 条件，产出 runbook 记录于 spec 实施记录并标「待部署后执行」）。

**Interfaces:** 依赖 Task 1 的 profile；输出 = spec §3.1「M0 验证项」的结论 + `claude_cli.py` 头注释的注入链路事实。

- [ ] **Step 1: 注入链路验证（M0）**

对一个真实视频项目跑一轮 frame-html（此时仍是 A 架构 orchestrator 路径，`HTMLVIDEO_CLAUDE_FRAME_HTML_DRIVER` 未实现所以走旧路径），env：

```bash
HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS=64000
```

跑完后核对 worker 子会话 SDK result（部署日志路径按实际 `/app/log/...`）：

```bash
grep -r '"maxOutputTokens":64000' /app/log/claude/projects/*/subagents/ 2>/dev/null | head -5
# 结论三者取一：a) 64000 到达 worker（继承成立）→ 继续
#             b) worker 仍是旧值（32000/128000）→ env 不在子代理继承链，改在
#                frame_html profile 的 max_output 上直接调，并回写本结论到 spec §3.1
#             c) api-bodies 显示 max_tokens 与 SDK 不一致 → 抓包模式复现并记录
```

- [ ] **Step 2: 止血 A/B 测量**

同一次 run 记录：`skill-timing.py --group`（frame-html 墙钟 + 各 worker 回合）、截断迹象（worker result 无 `done OK` 且 `outputTokens ≈ 64000`）、阶段成本（cost 字段）。

- [ ] **Step 3: 结论回写 + Commit**

把 M0 结论与本次墙钟/成本写进 `build_assets/html_utility/claude_cli.py` 头注释（M0 结论填入点）与 spec 文档「实施记录」节。

```bash
cd build_assets && git add html_utility/claude_cli.py docs/2026-11-18-frame-html-redesign-design.md
git commit -m "docs(frame-html): record M0 env-injection chain + M1 A/B numbers

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `run_claude_result` —— 驱动需要拿到 worker 终态文本

**Files:**
- Modify: `build_assets/html_utility/claude_cli.py:295-301`（`_log_parsed_result` 返回 result 文本）、`384-473`（`_run_profile` 返回 `(rc, result_text)`；新增导出 `run_claude_result`；`run_claude` 薄封装）
- Modify: `build_assets/test/test_claude_cli.py`（新增 result 断言）

**Interfaces:**
- Consumes: `_log_parsed_result`（现有）。
- Produces: `run_claude_result(project_dir: str, prompt: str, logger, extra_env: dict[str,str]|None = None, skill: str|None = None) -> tuple[int, str]` —— `(归一化 rc, 解析出的 result 字符串)`（JSON 解析失败或 legacy 路径时 result 为 `""`）。`run_claude(...) -> int` 保持既有签名与全部调用方不变（Task 6 使用 `run_claude_result` 判定 `done OK`）。

- [ ] **Step 1: 先写失败测试**

`build_assets/test/test_claude_cli.py` 追加（沿用文件内既有 mock `subprocess.Popen` 工具函数风格；`_result_json` 已在文件中）：

```python
def test_run_claude_result_returns_parsed_result(self):
    from html_utility.claude_cli import run_claude_result
    logger = logging.getLogger("t")
    with mock.patch("subprocess.Popen") as popen_mock:
        popen_mock.return_value.communicate.return_value = (
            _result_json(result="done OK 01-hook"), "")
        popen_mock.return_value.returncode = 0
        rc, text = run_claude_result(CWD, PROMPT, logger, skill="frame_html")
        self.assertEqual(rc, 0)
        self.assertIn("done OK 01-hook", text)
```

- [ ] **Step 2: Run → FAIL（`run_claude_result` 未定义）**

Run: `cd build_assets && python3 -m unittest test.test_claude_cli -v`

- [ ] **Step 3: 实现**

`claude_cli.py`：

```python
def _log_parsed_result(logger, skill, rc, stdout) -> tuple[int, str]:
    # ... 现有体不变，结尾：
    #   return rc                      →
    # return rc, result_text            # result_text: str（解析失败为 ""）
```

`_run_profile` 末尾：`return _log_parsed_result(logger, skill, proc.returncode or 0, stdout or "")`（已经是返回该函数，类型自然变 `tuple[int, str]`；超时分支改为 `return 124, ""`）。

```python
def run_claude_result(project_dir, prompt, logger, extra_env=None, skill=None):
    """run_claude + the parsed terminal 'result' text.

    Frame workers gate on their final reply (`done OK <frame_id>`), which the
    CLI surfaces in the JSON stdout's `result` field — callers that need that
    text (driver collect/retry logic) use this; everyone else keeps run_claude.
    """
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    if skill is None or _profile_off():
        return _run_legacy(project_dir, prompt, logger, extra_env), ""
    if skill not in _PROFILES:
        raise ValueError(f"unknown claude skill profile: {skill}")
    return _run_profile(project_dir, prompt, logger, extra_env, skill)


def run_claude(project_dir, prompt, logger, extra_env=None, skill=None):
    rc, _result = run_claude_result(project_dir, prompt, logger, extra_env, skill)
    return rc
```

- [ ] **Step 4: Run → PASS（全量）**

Run: `cd build_assets && python3 -m unittest test.test_claude_cli -v`
Expected: 全绿（既有 `run_claude` 断言不破 —— 它只依赖 rc）。

- [ ] **Step 5: Commit**

```bash
cd build_assets && git add html_utility/claude_cli.py test/test_claude_cli.py
git commit -m "feat(claude_cli): run_claude_result exposes the parsed terminal result text

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: worker 可执行条款卡（role card）

**Files:**
- Create: `build_assets/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md`
- Create: `build_assets/test/test_frame_packets.mjs`（node:assert 测试脚本，本任务先加 card 断言，后续任务扩）

**Interfaces:**
- Produces: `frame-worker-card.md` —— 打包器在 Task 5 `buildDispatchDocs` 中**逐字**内联进 `<id>.dispatch.md` 的 `## Role` 节。硬约束：≤8KB（实测 7820B）；必须包含全部 9 个 selfcheck FAIL 码；首行声明「`_role.md` 全量契约是权威，冲突时以 `_role.md` 为准」。

- [ ] **Step 1: 写测试（card 覆盖自检）**

`build_assets/test/test_frame_packets.mjs`：

```js
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CARD = join(ROOT, "skills", "aiflow-build-frame-html",
  "sub-agents", "frame-worker-card.md");

export const SELFCHECK_CODES = [
  "missing_template_wrapper", "missing_composition_id",
  "subcomposition_root_styled_by_class", "clip_missing_data_attrs",
  "timeline_track_too_dense", "timeline_not_paused",
  "timeline_not_registered", "missing_root_duration",
  "id_requires_css_escape",
];

export function testCard() {
  const card = readFileSync(CARD, "utf8");
  assert.ok(card.trim().length > 0, "card must exist");
  assert.ok(Buffer.byteLength(card) <= 6500,
    `card ${Buffer.byteLength(card)} bytes > 6500 budget`);
  for (const code of SELFCHECK_CODES) {
    assert.ok(card.includes(code), `card must mention FAIL-gate code ${code}`);
  }
  assert.ok(card.includes("_role.md"), "card must point at the full contract");
  console.log("card OK", Buffer.byteLength(card), "bytes");
}

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testCard();
}
```

- [ ] **Step 2: Run → FAIL（文件不存在）**

Run: `cd build_assets && node test/test_frame_packets.mjs`

- [ ] **Step 3: 写 card（按下面的骨架填满，内容逐字取自指定源文件，不新造规则）**

`build_assets/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md` 结构（各节内容 = 从源文件**对应小节原文收缩抄录**，删示例与说理、留条款；每个 selfcheck 码必须出现一次）：

```markdown
# Frame worker — executable rule card (aiflow-build-frame-html)

> Compact dispatch contract. The full `_role.md` (`.hyperframes/frame-packets/_role.md`)
> is authoritative wherever this card is unclear — open it then, not before.

## Hard structural gates (frame-selfcheck FAIL codes; fix all before replying)

<!-- 逐字收缩自 hyperframes-core/references/frame-worker-core.md § Self-check，
     9 个 FAIL 码一条不漏：missing_template_wrapper / missing_composition_id /
     subcomposition_root_styled_by_class / clip_missing_data_attrs /
     timeline_track_too_dense / timeline_not_paused / timeline_not_registered /
     missing_root_duration / id_requires_css_escape -->

## Ruled conventions (do not re-derive)

<!-- 逐字收缩自 sub-agents/frame-worker.md § Ruled conventions 五条：
     fonts / GSAP 3.14.2 / template+root 形状 / cqw 单位 / turn discipline，
     外加 root 必带 data-duration（Frame constraints） -->

## Frame visual law

<!-- 逐字收缩自 frame-worker-core.md § Frame constraints 全五条 +
     aiflow delta Frame constraints 的轨道密度/glow-nest 两条（Wrong/Right 示例可删） -->
```

- [ ] **Step 4: Run → PASS**

Run: `cd build_assets && node test/test_frame_packets.mjs`（6000–6500 字节达标、9 码全在）

- [ ] **Step 5: Commit**

```bash
cd build_assets && git add skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md test/test_frame_packets.mjs
git commit -m "feat(frame-html): worker rule card + selfcheck-code coverage test

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: dispatch 文档生成（输入减负）

**Files:**
- Modify: `build_assets/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs:104-150`（新增 `frameMdExcerpt` / `buildDispatchDocs` / `buildSkeletons` 导出；`runCli` 加 `buildExtras` 钩子）
- Modify: `build_assets/scripts/frame-packets.mjs:1-27`（wrapper：CONFIG 补 `cardPath`；main 挂钩子读 env 开关）
- Modify: `build_assets/test/test_frame_packets.mjs`（fixture 项目 + 尺寸/确定性断言）

**Interfaces:**
- Consumes: `splitFrames/field/selectedFile/citedRules/knownRuleIds`（同文件已有）。
- Produces:
  - `frameMdExcerpt(frameMd: string, maxFallbackBytes = 12000) -> string` —— 白名单小节抽提，全缺时回退 head 截断。
  - `buildDispatchDocs({ projectDir, frames, outDir, animationDir, cardPath, frameMdPath, maxDispatchBytes = 40000 }) -> [{frameId, path, bytes}]`。
  - `buildSkeletons({ projectDir, frames, canvas }) -> [{frameId, path}]`（Task 8 用；本任务先落函数体，测试在 Task 8）。
  - `runCli({ buildFramePackets, buildRolePayload, buildExtras })` —— `buildExtras` 为可选钩子，`(ctx) => void`，ctx = `{ projectDir, storyboardPath, outDir, frames, packets }`。
- 环境开关（wrapper 读）：`HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER` 非 `"1"`/`"0"` ⇒ 产 dispatch 文档；`HTMLVIDEO_FRAME_HTML_SKELETON` 非 `"0"` 且值为真 ⇒ 产骨架（默认都开，Task 8 起才依赖骨架）。**驱动（Task 6/9）读同一组 env**。

- [ ] **Step 1: 写测试（fixture 项目 + 摘取白名单 + 尺寸 + 确定性）**

`build_assets/test/test_frame_packets.mjs` 追加：

```js
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import * as core from "../skills/hyperframes-core/scripts/lib/frame-packets-core.mjs";

const FIX_STORYBOARD = `---
format: landscape
---
## Frame 1 — Hook

- status: outline
- src: compositions/frames/01-hook.html
- duration: 4s
- blueprint: split-panel
- rules: punch-in

Scene 1 (0.0–4.0s): title pill in.

## Frame 2 — Answer

- status: outline
- src: compositions/frames/02-answer.html
- duration: 5s
- blueprint: compose

Scene 1 (0.0–5.0s): stat grid reveal.
`;
const FIX_FRAME_MD = `# Placeholder\n\n## Colors\n\n- ink: #111\n\n## Typography\n\n- display: 9cqw\n\n## Frame Treatments\n\nbig catalog section\n\n## Numerals & Claims (hard rule)\n\nNo invented stats.\n`;

function fixtureProject() {
  const dir = mkdtempSync(join(tmpdir(), "fh-"));
  const anim = join(dir, "rules"); mkdirSync(anim, { recursive: true });
  writeFileSync(join(anim, "punch-in.md"), "recipe body");
  writeFileSync(join(dir, "STORYBOARD.md"), FIX_STORYBOARD);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  const frameMd = join(dir, "frame.md"); writeFileSync(frameMd, FIX_FRAME_MD);
  return { dir, card, frameMd, anim };
}

export function testDispatchDocs() {
  const { dir, card, frameMd, anim } = fixtureProject();
  const storyboard = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(storyboard).map((f) =>
    ({ ...f, frameId: core.frameId(f) }));
  const outDir = join(dir, ".hyperframes", "frame-packets");
  const docs = core.buildDispatchDocs({
    projectDir: dir, frames, outDir, animationDir: anim,
    cardPath: card, frameMdPath: frameMd,
  });
  assert.strictEqual(docs.length, 2);
  for (const d of docs) {
    const text = readFileSync(d.path, "utf8");
    assert.ok(d.bytes <= 40000, `dispatch ${d.frameId} over budget`);
    assert.ok(text.includes("## Role"), "has role section");
    assert.ok(text.includes("## Assigned storyboard block"), "has block");
    assert.ok(text.includes("## Rule index"), "has rule index");
    assert.ok(text.includes("recipe body") === false,
      "rule recipes are indexed, not inlined");
    assert.ok(text.includes("## frame.md design tokens"), "has excerpt");
    assert.ok(text.includes("#111") && text.includes("9cqw"), "excerpt has tokens");
    assert.ok(text.includes("No invented stats"), "hard-rule section included");
    assert.ok(!text.includes("big catalog section"), "treatment catalog excluded");
  }
  // determinism
  const again = core.buildDispatchDocs({
    projectDir: dir, frames: core.splitFrames(readFileSync(join(dir, "STORYBOARD.md"), "utf8")).map((f) => ({ ...f, frameId: core.frameId(f) })),
    outDir: join(dir, "second"), animationDir: anim,
    cardPath: card, frameMdPath: frameMd,
  });
  for (let i = 0; i < docs.length; i++)
    assert.strictEqual(readFileSync(again[i].path, "utf8"),
      readFileSync(docs[i].path, "utf8"));
  console.log("dispatch docs OK", docs.map((d) => `${d.frameId}:${d.bytes}`).join(" "));
}

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testCard(); testDispatchDocs();
}
```

- [ ] **Step 2: Run → FAIL（`buildDispatchDocs` 未定义）**

Run: `cd build_assets && node test/test_frame_packets.mjs`

- [ ] **Step 3: 实现 core 新导出**

`frame-packets-core.mjs` 追加（放在 `buildRolePayload` 之后）：

```js
const FRAME_MD_EXCERPT_HEADINGS = [
  "## Colors", "## Typography", "## Depth & Surface", "## Shapes",
  "## Components", "## Composition Rules",
  "## Approved Real Entities", "## Approved Entities",
  "## Numerals & Claims (hard rule)", "## Pre-Render Self-Audit",
];

export function frameMdExcerpt(frameMd, maxFallbackBytes = 12000) {
  if (!frameMd) return "";
  const headingRe = (h) => new RegExp(`^${escapeRegExp(h)}\\s*$`, "m");
  const bounds = FRAME_MD_EXCERPT_HEADINGS.map((h) => {
    const m = headingRe(h).exec(frameMd);
    return m ? { h, start: m.index } : null;
  }).filter(Boolean);
  if (bounds.length === 0) {
    console.warn("frame-packets: no whitelisted frame.md sections found; falling back to head excerpt");
    return `> (head-excerpt fallback: whitelisted sections not found)\n\n${frameMd.slice(0, maxFallbackBytes)}`;
  }
  const nextHeading = /^##\s/m;
  let out = "";
  for (const { h, start } of bounds) {
    const rest = frameMd.slice(start + h.length);
    const endM = rest.search(nextHeading);
    out += `\n${h}\n\n${(endM === -1 ? rest : rest.slice(0, endM)).trim()}\n`;
  }
  return out.trim();
}

export function buildDispatchDocs({ projectDir, frames, outDir, animationDir, cardPath, frameMdPath, maxDispatchBytes = 40000 }) {
  const card = readFileSync(cardPath, "utf8").trim();
  const frameMd = frameMdPath && existsSync(frameMdPath) ? readFileSync(frameMdPath, "utf8") : "";
  const excerpt = frameMdExcerpt(frameMd);
  const ruleIds = knownRuleIds(animationDir);
  mkdirSync(outDir, { recursive: true });
  return frames.map((frame) => {
    const blueprint = field(frame.block, "blueprint");
    const blueprintSection = blueprint && blueprint.toLowerCase() !== "compose"
      ? selectedFile(join(animationDir, "blueprints", `${blueprint}.md`), `Selected blueprint: ${blueprint}`)
      : "";
    const rules = citedRules(frame.block, ruleIds);
    const ruleIndex = rules.length
      ? rules.map((r) => `- \`${r}\` → ${join(animationDir, "rules", `${r}.md`)}`).join("\n")
      : "- (no motion rules cited)";
    const doc = `# Dispatch: ${frame.frameId}

## Project inputs

- Project: ${resolve(projectDir)}
- RULES_DIR: ${join(animationDir, "rules")}

## Role (executable rule card)

${card}

## Assigned storyboard block

${frame.block}
${blueprintSection}
## Rule index (open a recipe file only while authoring its phase)

${ruleIndex}

## frame.md design tokens (excerpt)

${excerpt}
`;
    const bytes = Buffer.byteLength(doc);
    if (bytes > maxDispatchBytes)
      console.warn(`frame-packets: dispatch ${frame.frameId} is ${bytes} bytes (budget ${maxDispatchBytes})`);
    const path = join(outDir, `${frame.frameId}.dispatch.md`);
    writeFileSync(path, doc);
    return { frameId: frame.frameId, path, bytes };
  });
}
```

`runCli` 签名与 body：

```js
export function runCli({ buildFramePackets: build, buildRolePayload: buildRole, buildExtras }) {
  const argv = process.argv.slice(2);
  const projectDir = resolve(flag(argv, "project", "."));
  const outDir = resolve(flag(argv, "out-dir", join(projectDir, ".hyperframes", "frame-packets")));
  try {
    const packets = build({ projectDir, storyboardPath: resolve(flag(argv, "storyboard", join(projectDir, "STORYBOARD.md"))), outDir });
    const role = buildRole({ outDir });
    if (buildExtras) {
      const storyboardPath = resolve(flag(argv, "storyboard", join(projectDir, "STORYBOARD.md")));
      const frames = splitFrames(readFileSync(storyboardPath, "utf8")).map((f) => ({ ...f, frameId: frameId(f) }));
      buildExtras({ projectDir, storyboardPath, outDir, frames, packets });
    }
    console.log(`✓ frame packets: ${packets.length} bounded packet(s)`);
    for (const packet of packets) console.log(`  ${packet.frameId}: ${packet.bytes} bytes → ${packet.path}`);
    console.log(`  worker role: ${role.bytes} bytes → ${role.path}`);
  } catch (error) { console.error(`✗ frame packets: ${error.message}`); process.exit(1); }
}
```

- [ ] **Step 4: wrapper 接线**

`build_assets/scripts/frame-packets.mjs` 全文替换 main 尾部：

```js
import { resolve } from "node:path";
import { readFileSync } from "node:fs";
import * as core from "/app/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs";
import { parseFormat } from "./lib/dimensions.mjs";

const SKILL_DIR = "/app/skills/aiflow-build-frame-html";
const CONFIG = {
  animationDir: resolve(SKILL_DIR, "../hyperframes-animation"),
  corePath: resolve(SKILL_DIR, "../hyperframes-core/references/frame-worker-core.md"),
  deltaPath: resolve(SKILL_DIR, "sub-agents/frame-worker.md"),
  cardPath: resolve(SKILL_DIR, "sub-agents/frame-worker-card.md"),
};

function switchOn(name, defaultOn = true) {
  const raw = (process.env[name] ?? "").trim().toLowerCase();
  if (!raw) return defaultOn;
  return !(raw === "0" || raw === "false" || raw === "off");
}
function dispatchVerOn() {
  const raw = (process.env["HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER"] ?? "").trim();
  return raw !== "0" && raw !== "1"; // 0/1 = pinned legacy (rollback)
}

export function buildRolePayload({ outDir }) { return core.buildRolePayload({ ...CONFIG, outDir }); }
export function buildFramePackets(options) { return core.buildFramePackets({ ...CONFIG, ...options }); }

function buildExtras({ projectDir, storyboardPath, outDir, frames }) {
  if (dispatchVerOn()) {
    const docs = core.buildDispatchDocs({
      projectDir, frames, outDir, animationDir: CONFIG.animationDir,
      cardPath: CONFIG.cardPath, frameMdPath: resolve(projectDir, "frame.md"),
    });
    console.log(`✓ dispatch docs: ${docs.length} merged document(s)`);
  }
  if (switchOn("HTMLVIDEO_FRAME_HTML_SKELETON")) {
    const storyboard = readFileSync(storyboardPath, "utf8");
    const m = /^\s*-?\s*format:\s*(.+)$/m.exec(storyboard);
    const skeletons = core.buildSkeletons({
      projectDir, frames, canvas: m ? parseFormat(m[1]) : undefined,
    });
    console.log(`✓ skeletons: ${skeletons.length} pre-built frame file(s)`);
  }
}

if (core.isMainModule(import.meta.url)) core.runCli({ buildFramePackets, buildRolePayload, buildExtras });
```

- [ ] **Step 5: Run → PASS**

Run: `cd build_assets && node test/test_frame_packets.mjs`
Expected: `card OK …` + `dispatch docs OK 01-hook:… 02-answer:…`

- [ ] **Step 6: Commit**

```bash
cd build_assets && git add skills/hyperframes-core/scripts/lib/frame-packets-core.mjs scripts/frame-packets.mjs test/test_frame_packets.mjs
git commit -m "feat(frame-packets): merged per-frame dispatch docs with whitelisted frame.md excerpt

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: `frame_html.py` 确定性驱动（M2 主体）

**Files:**
- Modify: `build_assets/html_utility/frame_html.py`（保留旧路径；新增驱动 + 开关路由）
- Modify: `build_assets/scripts/run_aiflow_build_skills.py:370-410`（`run_aiflow_build_frame_html` 走驱动，镜像 storyboard fast-path 的导入降级模式）
- Create: `build_assets/test/test_frame_html_driver.py`

**Interfaces:**
- Consumes: Task 3 `run_claude_result`；Task 5 产物（`.dispatch.md`，开关开启时）。
- Produces:
  - `SKELETON_MARK = "skeleton-v1"`
  - `_frame_ids(project_dir) -> list[str]`（`.hyperframes/frame-packets/*.md` 去 `_role.md` 与 `*.dispatch.md`）
  - `_worker_prompt(frame_id: str, skeleton_mode: bool) -> str`
  - `_dispatch_note(rc: int, text: str) -> str`（重派原因）
  - `_mark_animated_storyboard(project_dir: str, frame_id: str) -> bool`
  - `_build_frame_html_driver(project_dir, logger) -> None`
  - 开关语义：`HTMLVIDEO_FRAME_HTML_DRIVER` 为 `"0"` → 旧 orchestrator 路径；否则驱动。`HTMLVIDEO_FRAME_HTML_SKELETON` 为 `"0"` → 骨架模式 off（artifact 判定 + 「写文件」提示）；否则骨架模式 on（`done OK` 唯一信号 + 「骨架已在盘」提示）。preflight 检出文件与开关不符 → raise（提示重跑 frame-packets 或关开关）。

- [ ] **Step 1: 写测试（drive paths 全覆盖）**

`build_assets/test/test_frame_html_driver.py`：

```python
# -*- coding: utf-8 -*-
"""Deterministic fan-out driver: collect, retry, marking, repair."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from html_utility import frame_html as fh

STORYBOARD = """## Frame 1 — Hook
- status: outline
- src: compositions/frames/01-hook.html
- duration: 4s

Scene 1 (0.0–4.0s): pill.
## Frame 2 — Answer
- status: outline
- src: compositions/frames/02-answer.html
- duration: 5s

Scene 1 (0.0–5.0s): grid.
## Video direction
"""

def make_project(skeleton=False, with_dispatch=True):
    d = tempfile.mkdtemp(prefix="fhdriver-")
    os.makedirs(os.path.join(d, ".hyperframes", "frame-packets"), exist_ok=True)
    os.makedirs(os.path.join(d, "compositions", "frames"), exist_ok=True)
    with open(os.path.join(d, "STORYBOARD.md"), "w") as f: f.write(STORYBOARD)
    with open(os.path.join(d, "frame.md"), "w") as f: f.write("x")
    with open(os.path.join(d, "hyperframes.json"), "w") as f: f.write("{}")
    for fid in ("01-hook", "02-answer"):
        open(os.path.join(d, ".hyperframes", "frame-packets", fid + ".md"), "w").close()
        open(os.path.join(d, ".hyperframes", "frame-packets", "_role.md"), "w").close()
        if with_dispatch:
            open(os.path.join(d, ".hyperframes", "frame-packets", fid + ".dispatch.md"), "w").close()
        if skeleton:
            with open(os.path.join(d, "compositions", "frames", fid + ".html"), "w") as f:
                f.write("<!-- skeleton-v1 --><template></template>")
    return d

class DriverTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {
            "HTMLVIDEO_FRAME_HTML_DRIVER": "1",
            "HTMLVIDEO_FRAME_HTML_SKELETON": "1",
            "HTMLVIDEO_CLAUDE_PROFILE": "1",
        }, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_dispatch(self, results, assemble_results=((0, ""),)):
        """results: dict frame_id -> list of (rc, text) per attempt.
        assemble_results: build_index_html return values in call order."""
        calls = {k: list(v) for k, v in results.items()}
        def fake(project_dir, prompt, logger, extra_env=None, skill=None):
            fid = next(rid for rid in calls if rid in prompt)
            return calls[fid].pop(0)
        p1 = mock.patch.object(fh, "run_claude_result", side_effect=fake)
        p2 = mock.patch.object(fh, "build_index_html", side_effect=list(assemble_results))
        p1.start(); self.addCleanup(p1.stop)
        p2.start(); self.addCleanup(p2.stop)

    def test_all_ok_marks_and_gates(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({k: [(0, f"done OK {k}")] for k in ("01-hook", "02-answer")})
        fh.build_frame_html(d, None)
        text = open(os.path.join(d, "STORYBOARD.md")).read()
        self.assertIn("- status: animated", text)

    def test_retry_then_converge(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({
            "01-hook": [(0, "done OK 01-hook")],
            "02-answer": [(0, "…"), (0, "done OK 02-answer")],
        })
        fh.build_frame_html(d, None)

    def test_two_rounds_then_raise(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({"01-hook": [(0, "done OK 01-hook")],
                              "02-answer": [(0, ""), (0, ""), (0, "")]})
        with self.assertRaises(RuntimeError):
            fh.build_frame_html(d, None)

    def test_timeout_signal_embedded_in_retry_note(self):
        d = make_project(skeleton=True)
        seen = []
        def fake(project_dir, prompt, logger, extra_env=None, skill=None):
            fid = "01-hook" if "01-hook" in prompt else "02-answer"
            seen.append((fid, prompt))
            if fid == "01-hook" and len([s for s in seen if s[0] == fid]) == 1:
                return 124, ""
            return 0, f"done OK {fid}"
        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")):
            fh.build_frame_html(d, None)
        retry_prompt = [p for fid, p in seen if fid == "01-hook"][-1]
        self.assertIn("wall-clock timeout", retry_prompt)

    def test_assemble_failure_runs_repair_once(self):
        d = make_project(skeleton=True)
        self._patch_dispatch(
            {k: [(0, f"done OK {k}")] for k in ("01-hook", "02-answer")},
            assemble_results=((1, "bad index"), (0, "")),
        )
        with mock.patch.object(fh, "run_claude", return_value=0) as repair:
            fh.build_frame_html(d, None)
        self.assertEqual(repair.call_count, 1)
        self.assertEqual(repair.call_args.kwargs.get("skill"), "repair")

    def test_skeleton_mismatch_raises_preflight(self):
        d = make_project(skeleton=False)
        with self.assertRaises(RuntimeError):
            fh.build_frame_html(d, None)
```

（`build_frame_html(d, None)` 传 logger=None 安全：驱动内所有 `logger.info` 均走 `_log` 辅助 —— 若无 logger 则跳过。）

- [ ] **Step 2: Run → FAIL（driver 未实现）**

Run: `cd build_assets && python3 -m unittest test.test_frame_html_driver -v`

- [ ] **Step 3: 实现驱动**

`build_assets/html_utility/frame_html.py` 顶部 import 区追加 `from concurrent.futures import ThreadPoolExecutor`、`from html_utility.claude_cli import run_claude, run_claude_result, _env_positive_int`（`re`/`logging` 已有）。文件尾部追加：

```python
SKELETON_MARK = "skeleton-v1"
DRIVER_ENV = "HTMLVIDEO_FRAME_HTML_DRIVER"
SKELETON_ENV = "HTMLVIDEO_FRAME_HTML_SKELETON"
DISPATCH_SUFFIX = ".dispatch.md"
MAX_RETRY_ROUNDS = 2
DEFAULT_MAX_WORKERS = 8
DONE_OK_RE = re.compile(r"\bdone\s+OK\b")
CUT_CATALOG_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "skills",
    "aiflow-build-frame-html", "references", "cut-catalog.md"))


def _env_on(name, default=True):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


def _frame_ids(project_dir):
    pkt = os.path.join(project_dir, FRAME_PACKETS_DIR)
    return sorted(
        name[:-3] for name in os.listdir(pkt)
        if name.endswith(".md") and name != FRAME_PACKETS_ROLE
        and not name.endswith(DISPATCH_SUFFIX)
    )


def _worker_prompt(frame_id, skeleton_mode):
    frame_file = f"compositions/frames/{frame_id}.html"
    fill_line = (
        f"A pre-built skeleton already exists at {frame_file} (a `{SKELETON_MARK}` "
        "comment marks it). Your job is to FILL it, not design from scratch: edit in "
        "chunks — style block → body → script block, each payload under ~20 KB. You may "
        "add/remove any node or rewrite the file wholly (last write wins)."
        if skeleton_mode else
        f"You must create {frame_file}: build it in chunks — style block → body → "
        "script block, each payload under ~20 KB."
    )
    return f"""You are one per-frame HTML worker for a HyperFrames video.

Your single required input is the merged dispatch document for your frame:
  .hyperframes/frame-packets/{frame_id}.dispatch.md
Read it fully first, then build. {fill_line}

Then run the structural gate and fix every FAIL:
  node /app/scripts/frame-selfcheck.mjs {frame_file}

Your terminal reply after the gate passes is exactly: done OK {frame_id}
On an unrecoverable failure instead reply: fail {frame_id}: <reason>

Escape hatches — open ONLY when a rule in the dispatch is unclear:
  .hyperframes/frame-packets/_role.md            (full contract, authoritative)
  frame.md                                       (full design truth)
  {CUT_CATALOG_PATH}   (only for within-scene cut seams)
Never edit STORYBOARD.md. Turn discipline: every turn ends with a tool call or
visible text; your first edit must land within your first 1–2 turns; never burn
a single turn on marathon deliberation.
"""


def _dispatch_note(rc, text):
    if rc == 124:
        return "prior attempt hit its wall-clock timeout"
    if rc != 0:
        return f"prior attempt exited non-zero (rc={rc})"
    tail = (text or "").strip()[-300:]
    return ("prior attempt finished without passing its gate (possibly truncated); "
            "last words: " + (tail or "∅"))


def _mark_animated_storyboard(project_dir, frame_id):
    path = os.path.join(project_dir, STORYBOARD_FILENAME)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    block_re = re.compile(
        r"^## (?:Frame|Beat|Scene)\b[^\n]*$(.*?)(?=^## (?:Frame|Beat|Scene)\b|^## Video direction|\Z)",
        re.M | re.S)
    needle = re.compile(rf"^- src:\s*.*{re.escape(frame_id)}\.html", re.M)
    for m in block_re.finditer(text):
        block = m.group(1)
        if not needle.search(block):
            continue
        new_block, n = re.subn(r"^- status:\s*\w+", "- status: animated",
                               block, count=1, flags=re.M)
        if n:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text[:m.start(1)] + new_block + text[m.end(1):])
            return True
    return False


def _preflight_dispatch(project_dir, frame_ids, skeleton_mode):
    pkt = os.path.join(project_dir, FRAME_PACKETS_DIR)
    for fid in frame_ids:
        if not os.path.isfile(os.path.join(pkt, f"{fid}{DISPATCH_SUFFIX}")):
            raise RuntimeError(
                f"missing dispatch doc for {fid} — re-run frame-packets with "
                "HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER >= 2")
    if skeleton_mode:
        for fid in frame_ids:
            p = os.path.join(project_dir, "compositions", "frames", f"{fid}.html")
            if not os.path.isfile(p):
                raise RuntimeError(
                    f"skeleton missing for {fid} while HTMLVIDEO_FRAME_HTML_SKELETON "
                    "is on — re-run frame-packets or set HTMLVIDEO_FRAME_HTML_SKELETON=0")
            with open(p, encoding="utf-8") as f:
                if SKELETON_MARK not in f.read():
                    raise RuntimeError(
                        f"frame file for {fid} is not a pre-built skeleton while "
                        "HTMLVIDEO_FRAME_HTML_SKELETON is on")


def _repair_prompt(assemble_msg):
    return (f"You are repairing a HyperFrames assembled project. The assemble-index "
            f"step failed with:\n{(assemble_msg or '')[-2000:]}\n"
            f"Make the CHEAPEST safe structural edit to compositions/frames/*.html that "
            f"fixes the reported problem (do not touch STORYBOARD.md). Stop after the "
            f"edit — the pipeline re-runs assemble itself.")


def _build_frame_html_driver(project_dir, logger):
    log = logger.info if logger else lambda *a, **k: None
    frame_ids = _frame_ids(project_dir)
    if not frame_ids:
        raise RuntimeError(f"no frame packets found under {project_dir}/{FRAME_PACKETS_DIR}")
    skeleton_mode = _env_on(SKELETON_ENV)
    _preflight_dispatch(project_dir, frame_ids, skeleton_mode)
    max_workers = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY", DEFAULT_MAX_WORKERS,
        logger or logging.getLogger("frame-html"))
    remaining = list(frame_ids)
    notes = {}
    log("frame-html driver start frames=%s skeleton=%s", len(remaining), skeleton_mode)
    for round_no in range(1 + MAX_RETRY_ROUNDS):
        def run_one(fid):
            prompt = _worker_prompt(fid, skeleton_mode)
            if notes.get(fid):
                prompt += (
                    f"\n[RE-DISPATCH] {notes[fid]}. The frame file may already hold a "
                    "skeleton or your own partial content — fill it directly; first "
                    "edit within 1–2 turns; no single-turn marathon.")
            rc, text = run_claude_result(
                project_dir, prompt, logger,
                extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
                skill="frame_html")
            return fid, rc, text

        failures = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fid, rc, text in pool.map(run_one, remaining):
                ok = rc == 0 and DONE_OK_RE.search(text or "") and fid in (text or "")
                if (not skeleton_mode) and ok and not os.path.isfile(
                        os.path.join(project_dir, "compositions", "frames", f"{fid}.html")):
                    ok = False
                if ok:
                    _mark_animated_storyboard(project_dir, fid)
                    remaining.remove(fid)
                    log("worker ok %s (round %s)", fid, round_no)
                else:
                    failures[fid] = (rc, text)
                    log("worker fail %s rc=%s (round %s)", fid, rc, round_no)
        if not remaining:
            break
        notes = {fid: _dispatch_note(*t) for fid, t in failures.items()}
        log("retry round %s pending=%s", round_no + 1, remaining)
    if remaining:
        raise RuntimeError(
            f"frames still broken after {1 + MAX_RETRY_ROUNDS} rounds: {sorted(remaining)}")

    ret, msg = build_index_html(logger, project_dir)
    if ret != 0:
        repair_rc = run_claude(project_dir, _repair_prompt(msg), logger, skill="repair")
        log("assemble repair session rc=%s", repair_rc)
        ret, msg = build_index_html(logger, project_dir)
        if ret != 0:
            raise RuntimeError(f"assemble failed even after repair: {msg}")
```

`build_frame_html()` 函数体改为路由（保留既有 preflight 与 `_validate_frame_html_outputs` 不动）：

```python
    if _env_on(DRIVER_ENV):
        _build_frame_html_driver(project_dir, logger)
    else:
        prompt = _build_frame_html_prompt()
        _ensure_frame_worker_agent(project_dir)
        rc = run_claude(project_dir, prompt, logger,
                        extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
                        skill="frame_html")
        if rc != 0:
            raise RuntimeError(f"claude build-frame-html failed rc:{rc}")
    return _validate_frame_html_outputs(project_dir)
```

- [ ] **Step 4: `run_aiflow_build_skills.py` 接驱动**

`run_aiflow_build_frame_html` 中 `run_claude(... FRAME_HTML_PROMPT ...)` 之前插入（镜像 storyboard fast-path 模式）：

```python
    if os.environ.get("HTMLVIDEO_FRAME_HTML_DRIVER", "1") != "0":
        try:
            sys.path.insert(0, "/app")
            from html_utility.frame_html import build_frame_html as _b
            _b(str(project_dir), _script_logger())
            return 0
        except Exception as exc:  # pragma: no cover - deployment fallback
            print(f"warning: frame-html driver unavailable ({exc}); "
                  "falling back to orchestrator path", file=sys.stderr)
```

- [ ] **Step 5: Run → PASS**

Run: `cd build_assets && python3 -m unittest test.test_frame_html_driver test.test_claude_cli -v`
Expected: 驱动 6 例 + claude_cli 全绿。

- [ ] **Step 6: Commit**

```bash
cd build_assets && git add html_utility/frame_html.py scripts/run_aiflow_build_skills.py test/test_frame_html_driver.py
git commit -m "feat(frame-html): deterministic fan-out driver (collect/retry/mark/repair)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: skill 文档重写为可执行契约（与 M2 同批）

**Files:**
- Rewrite: `build_assets/skills/aiflow-build-frame-html/SKILL.md`
- Modify: `build_assets/skills/aiflow-build-frame-html/sub-agents/frame-worker.md`（按下方 Edit 清单）
- Rewrite: `build_assets/skills/aiflow-build-frame-html/references/subagent-dispatch.md`
- Modify: `build_assets/skills/aiflow-build-frame-html/references/frame-html-worker.md`

**Interfaces:** 无代码接口；文档是 Task 6 驱动的可执行规格（filler 契约、done OK 语义）。

- [ ] **Step 1: SKILL.md 全文替换**

新 `SKILL.md`（直接整文件写出）：

```markdown
---
name: aiflow-build-frame-html
description: "Contract for the deterministic frame fan-out driver: per-frame claude workers fill pre-built compositions/frames/*.html skeletons from a merged .dispatch.md. Use when STORYBOARD.md has time-coded shot sequences and ## Video direction. Not visual design, narrative, audio, transitions inject, or render."
---

# AIFlow build frame HTML

This skill is the **executable contract** for the build-frame-html stage. The
Python driver (`html_utility/frame_html.py`) implements it deterministically;
the per-frame `claude` workers implement the filler contract below. No
orchestrator LLM exists in this stage. Design and motion rules live in
`sub-agents/frame-worker-card.md`, the full `_role.md`, `references/cut-catalog.md`,
and `../hyperframes-animation/`.

## Prerequisites

- `hyperframes.json`, `frame.md`, `STORYBOARD.md` (shot sequences + `## Video direction`) exist.
- `frame-packets` ran with dispatch ver 2: for every frame —
  `.hyperframes/frame-packets/<frame_id>.dispatch.md` (merged input: rule card +
  storyboard block + blueprint + rule index + frame.md excerpt) and
  `.hyperframes/frame-packets/<frame_id>.md` (legacy packet, rollback only).
- M3 (skeleton on): `compositions/frames/<frame_id>.html` pre-exists, marked
  `<!-- skeleton-v1 -->`, passing `frame-selfcheck.mjs`.
- Missing either → the driver raises; **do not build packets inline here**.

## Driver spec (frame_html.py — the executable)

1. Enumerate frames from `.hyperframes/frame-packets/*.md` (skip `_role.md`,
   `*.dispatch.md`).
2. Fan out one `claude -p` worker per frame, concurrency
   `HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY` (default 8); per-worker env:
   cap/turns/timeout from the `frame_html` claude profile (64000/60/720).
3. A frame is **done** iff rc==0 ∧ result contains `done OK <frame_id>`
   (skeleton off additionally requires the file on disk). Artifact existence
   alone is NOT a completion signal while skeletons pre-exist.
4. Collect every failure, re-dispatch the whole failing batch in parallel with
   the failure reason appended, at most 2 rounds. Then raise — never hand-fix.
5. Mark landed frames `animated` in STORYBOARD (regex, deterministic).
6. Assemble via `assemble-index.mjs`; on failure run ONE `repair`-profile
   session (cheapest safe structural edit to frame files only), re-assemble
   once, else raise with stderr.

## Worker contract (sub-agents/frame-worker.md + frame-worker-card.md)

Read `.dispatch.md` fully (the only required input) → fill the
`compositions/frames/<frame_id>.html` file —— skeleton already on disk:
chunked edits style → body → script, each ≤ ~20 KB, first edit in 1–2 turns →
`node /app/scripts/frame-selfcheck.mjs` → fix all FAILs → reply
`done OK <frame_id>` (or `fail <frame_id>: <reason>`). Escape hatches:
`_role.md` (full contract), `frame.md`, cut catalog, `RULES_DIR/*.md` — open
only when a dispatch rule is unclear.

## Gates

Every frame `done OK` ∧ `status: animated` ∧ `compositions/frames/*.html`
non-empty → `assemble-index.mjs` → (verify stage owns the rest).
```

- [ ] **Step 2: frame-worker.md 定向改写（6 处 Edit，old→new 逐字）**

```text
E1  标题下引言段（3–36 行「The workflow-specific half…once.」）整段替换为：
    "> Filler contract for the aiflow frame worker. The packet builder prepends
     the shared contract (`../hyperframes-core/references/frame-worker-core.md`)
     to form the full `_role.md`; the dispatch `.dispatch.md` carries the compact
     rule card. This delta covers what is aiflow-specific: invented visuals, the
     `done OK` reply, root `data-duration`, same-track density, the frame file that
     already exists on disk."

E2  § INPUT 首段「Your packet carries…never open STORYBOARD.md 」之后插入：
    "  **Your dispatch document is the single required input**
     (`.hyperframes/frame-packets/<frame_id>.dispatch.md`): it inlines the rule
     card, your storyboard block, the blueprint, a rule INDEX (recipes are opened
     on demand from `RULES_DIR` while you author each phase), and the frame.md
     design-token excerpt. The full `_role.md` and `frame.md` are escape hatches —
     open them only when a dispatch rule is unclear."

E3  § OUTPUT（Override）「The artifact itself follows…(Sketches do not occur…)」前插入：
    "  **The frame file already exists.** In this workflow the packet builder
     pre-writes a valid skeleton (`<!-- skeleton-v1 -->`, passes frame-selfcheck)
     to `compositions/frames/<frame_id>.html` before you start — fill it with
     chunked Edits (style → body → script); you may add/remove nodes or rewrite
     the file wholly (last write wins). A file on disk is therefore your working
     document, never proof of completion — your `done OK` reply is."

E4  § Ruled conventions「Skeleton first.」条目整条替换为：
    "- **Skeleton fill (Override).** The skeleton already exists on disk — your
     FIRST edit must land within your first 1–2 turns, flesh it out with chunked
     Edits after that. Never burn a turn planning a whole frame before the first
     edit; the file is your heartbeat, keep it valid at every step."

E5  § Workflow 步骤 3「Author」末尾追加：
    "  (the root already carries `data-duration` — the skeleton stamped it; keep it)."

E6  § Self-check 首段末尾追加：
    "  The skeleton guaranteed `missing_template_wrapper` / root attrs /
     paused-registered timeline before you started — do not regress them."
```

- [ ] **Step 3: subagent-dispatch.md 全文替换（驱动语义版）**

```markdown
# Frame dispatch — deterministic driver contract

The aiflow-build-frame-html stage has **no orchestrator agent**. Dispatch,
wait, and re-dispatch are implemented by `html_utility/frame_html.py` using
`claude_cli.run_claude_result` per frame. This file pins the semantics.

## The contract

- **DISPATCH(role_docs, frame_id)** — one `claude -p` process per frame. The
  prompt carries the merged dispatch path (`.dispatch.md`), the skeleton fact,
  the selfcheck command, the terminal reply protocol; the full role lives on
  disk as an escape hatch.
- **Parallel fan-out** — workers are independent; concurrency =
  `HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY` (default 8), then queued waves.
- **WAIT** — completion = rc 0 ∧ `done OK <frame_id>` in the session result
  (skeleton-off mode additionally requires the file). Wall-clock cap per worker
  = the `frame_html` profile timeout (720 s default).
- **Re-dispatch** — same prompt + failure reason, batch-parallel, ≤2 rounds.

## Rollback

`HTMLVIDEO_FRAME_HTML_DRIVER=0` routes back to the legacy orchestrator skill
run; `HTMLVIDEO_FRAME_HTML_SKELETON=0` disables skeleton mode.
```

- [ ] **Step 4: frame-html-worker.md 追加历史注释**

文件尾追加一行：`> Historic: defined the subagent type for the legacy orchestrator path (`HTMLVIDEO_FRAME_HTML_DRIVER=0`). The deterministic driver sets effort via the `frame_html` claude profile instead.`

- [ ] **Step 5: 文档一致性自检 + Commit**

Run: `grep -n "orchestrator" build_assets/skills/aiflow-build-frame-html/SKILL.md` —— 除「No orchestrator agent exists」句外不得残留编排指令。

```bash
cd build_assets && git add skills/aiflow-build-frame-html/
git commit -m "docs(frame-html): rewrite skill as executable driver contract + filler role

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: 骨架生成 + 打包接线收口（M3）

**Files:**
- Modify: `build_assets/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`（补 Task 5 预留的 `buildSkeletons` 函数体）
- Modify: `build_assets/test/test_frame_packets.mjs`（骨架断言：selfcheck 通过 + 确定性）

**Interfaces:**
- Consumes: Task 5 的 `buildExtras`（已调用 `core.buildSkeletons`）。
- Produces: `buildSkeletons({ projectDir, frames, canvas }) -> [{frameId, path}]`；canvas 无值时骨架省略 data-width/height（assemble-index 的 guard ① 会自动补）。产出的每个文件必过 `frame-selfcheck.mjs`。

- [ ] **Step 1: 写测试（产物过 selfcheck）**

`test_frame_packets.mjs` 追加：

```js
import { spawnSync } from "node:child_process";

export function testSkeletons() {
  const { dir, card, frameMd, anim } = fixtureProject();
  const storyboard = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(storyboard).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const out = core.buildSkeletons({ projectDir: dir, frames, canvas: { width: 1920, height: 1080 } });
  assert.strictEqual(out.length, 2);
  const selfcheck = join(ROOT, "scripts", "frame-selfcheck.mjs");
  for (const { frameId, path } of out) {
    const html = readFileSync(path, "utf8");
    assert.ok(html.includes("skeleton-v1"), "marker present");
    assert.ok(html.includes(`data-composition-id="${frameId}"`), "comp id");
    assert.ok(html.includes("data-width=\"1920\""), "canvas dims");
    assert.ok(html.includes("data-duration=\"4s\"") || html.includes("data-duration=\"5s\""), "duration stamped");
    const run = spawnSync("node", [selfcheck, path], { encoding: "utf8" });
    assert.strictEqual(run.status, 0, `selfcheck FAIL:\n${run.stdout}${run.stderr}`);
  }
  console.log("skeletons OK: all pass frame-selfcheck");
}

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testCard(); testDispatchDocs(); testSkeletons();
}
```

- [ ] **Step 2: Run → FAIL（`buildSkeletons` 空实现）**

Run: `cd build_assets && node test/test_frame_packets.mjs`

- [ ] **Step 3: 实现 `buildSkeletons`**

`frame-packets-core.mjs`（Task 5 步骤 3 里预留的位置）落函数体：

```js
export function buildSkeletons({ projectDir, frames, canvas }) {
  const framesDir = resolve(projectDir, "compositions", "frames");
  mkdirSync(framesDir, { recursive: true });
  const dims = canvas && canvas.width && canvas.height
    ? ` data-width="${canvas.width}" data-height="${canvas.height}"` : "";
  return frames.map((frame) => {
    const dur = field(frame.block, "duration") ?? field(frame.block, "durationSeconds");
    if (!dur) throw new Error(`${frame.frameId}: skeleton needs a - duration field on the storyboard block`);
    const skeleton = `<!-- skeleton-v1: fill style/body/script per .hyperframes/frame-packets/${frame.frameId}.dispatch.md — last write wins -->
<template>
<div id="root" data-composition-id="${frame.frameId}"${dims} data-duration="${dur}"></div>
<style>/* fill: frame styles */</style>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {};
window.__timelines["${frame.frameId}"] = gsap.timeline({ paused: true });
</script>
</template>
`;
    const path = join(framesDir, `${frame.frameId}.html`);
    writeFileSync(path, skeleton);
    return { frameId: frame.frameId, path };
  });
}
```

- [ ] **Step 4: Run → PASS**

Run: `cd build_assets && node test/test_frame_packets.mjs`
Expected: `card OK / dispatch docs OK / skeletons OK（两帧全 PASS）`

- [ ] **Step 5: Commit**

```bash
cd build_assets && git add skills/hyperframes-core/scripts/lib/frame-packets-core.mjs test/test_frame_packets.mjs
git commit -m "feat(frame-packets): deterministic pre-built skeletons (selfcheck-clean)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: 驱动收口 A/B + 全套回归（M3 验证）

**Files:** 无新增代码（必要时按 Step 2 结论小修驱动 preflight 措辞）。

**Interfaces:** 依赖 Task 6+8；产出 = spec「实施记录」的 M2/M3 实测数据。

- [ ] **Step 1: 全量单测回归**

Run: `cd build_assets && python3 -m unittest test.test_claude_cli test.test_frame_html_driver -v && node test/test_frame_packets.mjs`
Expected: 全绿。

- [ ] **Step 2: 真实 run（skeleton on + driver on）**

标准 1 个视频全流水线跑 build-frame-html（env：`HTMLVIDEO_FRAME_HTML_DRIVER` 缺省/1、`HTMLVIDEO_FRAME_HTML_SKELETON` 缺省/1、`HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER` 缺省）。核对：每 worker 日志终态 `done OK`、帧全部 `animated`、`assemble-index` 一次通过、verify 终态与像素对比不回归。skill-timing `--group` 记录墙钟（目标 ≤15m）与成本（目标 ≤15 USD）。

- [ ] **Step 3: 失败注入（可选但推荐）**

把某帧骨架删掉验证 preflight raise；把某 worker 的 dispatch 文档改坏验证重派两轮语义（用单测已覆盖，此处是端到端确认一次即可）。

- [ ] **Step 4: 数据回写 + Commit**

把 M2/M3 实测写入 spec「实施记录」：

```bash
cd build_assets && git add docs/2026-11-18-frame-html-redesign-design.md
git commit -m "docs(frame-html): record M2/M3 A/B results against the <=15m/0-truncation targets

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: 收尾 —— profiles 文档同步与残留清理（M4）

**Files:**
- Modify: `build_assets/docs/claude-cli-skill-profiles.md`（§4 表格 + §9 已知限制）
- Modify: `build_assets/html_utility/frame_html.py`（`_build_frame_html_prompt` 与 `_ensure_frame_worker_agent` 标 `# legacy path (HTMLVIDEO_FRAME_HTML_DRIVER=0)`；`FRAME_WORKER_AGENT_MARKDOWN` 保留）
- Modify: `build_assets/skills/aiflow-build-frame-html/references/frame-worker.md` 无需再动

**Interfaces:** 无新增。

- [ ] **Step 1: claude-cli-skill-profiles.md §4 表格替换**

§4 表整体替换为：

```markdown
| skill | effort | thinking | max_output | max_turns | timeout(s) | deny Agent/Task | 理由 |
|---|---|---|---|---|---|---|---|
| intent | high | 32000 | 32000 | 100 | 1800 | 是 | 2026-11-18 cap A/B 实证地板（16.7s 历史最优），显式固化 |
| storyboard | high | 64000 | 32000 | 100 | 1800 | 是 | 同上（3.0m 近地板） |
| visual | high | 64000 | 32000 | 100 | 1800 | 是 | 同上（7.1m 历史最优） |
| frame_html | high | 64000 | 64000 | 60 | 720 | 是 | 每帧一个进程：64000 防截断档；720s 杀马拉松断电闸；B 架构无需 Agent |
| repair | high | 40000 | 16000 | 40 | 600 | 是 | 仅 assemble 失败的一次性修复会话 |
| verify | medium | 32000 | 32000 | 100 | 1800 | 是 | 固化 |
```

§9 追加一条：`7. worker 会话不再经子代理（B 架构）；CLAUDE_CODE_SUBAGENT_EFFORT 仅 legacy 回退路径有意义。`

- [ ] **Step 2: 注释收口**

`frame_html.py` 的 `_build_frame_html_prompt`、`_ensure_frame_worker_agent`、`FRAME_WORKER_AGENT_*` docstring 首行统一加 `# legacy path — only used when HTMLVIDEO_FRAME_HTML_DRIVER=0.`

- [ ] **Step 3: 最终回归 + Commit**

Run: `cd build_assets && python3 -m unittest test.test_claude_cli test.test_frame_html_driver -v && node test/test_frame_packets.mjs`

```bash
cd build_assets && git add docs/claude-cli-skill-profiles.md html_utility/frame_html.py
git commit -m "docs(claude-cli): sync profiles doc with stage-graded caps; tag legacy frame-html path

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 计划自检记录

- **Spec 覆盖**：§3.1→Task 1/2；§3.2（dispatch）→Task 4/5；§3.2（骨架）→Task 8；§3.3→Task 6；§3.4→Task 7；§4 契约→Task 6/7；§6 M0–M4→Task 2/1/5-7/8-9/10；§7 测试→各任务内嵌；§10 回滚→Global Constraints + Task 7 Step 3。
- **Type/命名一致性**：`run_claude_result` / `_worker_prompt` / `buildDispatchDocs` / `buildSkeletons` / `SKELETON_MARK` / 三个 env 开关在 Task 3→10 间同名同型。
- **执行顺序依赖**：Task 6 依赖 Task 3 与 Task 5；Task 8 依赖 Task 5（`buildExtras` 已调用其导出）；Task 9/10 依赖前全部。Task 1/2 可独立先行（止血）。