# frame-visual 契约解析容错与 frame-visual / frame-html 耗时成本优化

> 触发样本：2026-11-25 UTC 12:08–12:13，`task_id=6b06cfc0eeb0cbb3`（失败）；基线对照：2026-11-24 `task_id=6b05ba2b5d081581`（完整 7 帧）  
> 项目目录：`/app/videos/2026-11-25/userclm17919893695531772/6b06cfc0eeb0cbb3`  
> 相关日志：`/app/log/htmlvideo_worker.log`、`/app/log/claude/projects/-app-videos-2026-11-25-userclm17919893695531772-6b06cfc0eeb0cbb3/b784dccd-634f-40be-9cd6-0ca0905f1076.jsonl`、`/app/log/api-bodies/d6afaa0b-484b-40d9-8393-37ff1359d79a.request.json`、`/app/log/api-bodies/7a2a55dd-3268-47b5-939e-2e8632f18da7.response.json`

---

## 1. 事故背景与根因回顾

2026-11-25 运行 `6b06cfc0eeb0cbb3` 时，`init`、`storyboard` 与并行 `tts` 均成功完成，但在 `frame-visual` Phase A（`design`）结束时异常终止，整条任务白耗 289.2s：

```text
visual design wall=165.1s rc=0 out=3226 chars
task_id:6b06cfc0eeb0cbb3 Failed to build frame visual: design contract missing ## Blueprint assignment
```

结合 API 原始请求/响应体、未脱敏会话 `thinking` 以及 11-24 完整任务 `6b05ba2b5d081581` 的逐轮 JSONL 统计，定位出 **1 个直接故障根因 + 5 个耗时/成本核心瓶颈**：

| 序号 | 问题 | 现象与实测证据 |
|---|---|---|
| 1 | **Prompt 负面约束外溢 + 严格子串校验导致 Fatal** | `visual.py` 要求 creative body `plain prose with **bold** labels, NOT ## headings`，未明示 `## Blueprint assignment` 为强制解析锚点。原始 `thinking` 显示模型刻意将其改写为 `**Blueprint assignment**` 以保持一致，后续 7 行 `F1 = ...` 全部合法且零幻觉；但 `_validate_design_contract()` 死板检查 `"## Blueprint assignment" not in text` 且零重试，直接中断流水线。 |
| 2 | **`visual` Phase A (`design`) 缺信息、盲猜长思考** | `6b06` 的 `design` 耗时 165.1s（`thinking=8441`, 可见输出仅 `819 tokens`）；`6b05` 耗时 58.5s（占整个 `visual` 91.2s 的 64%）。原因：catalog 只给 22 个 blueprint id 没给一句话释义，且 `_frame_summary_block()` 漏拼了 `scene` 与 outline `blueprint`，模型在思考中逐个猜测 blueprint 含义与画面意图。 |
| 3 | **`frame-html` 跨 worker 前缀缓存失效** | `6b05` 实测 `visual` Phase B 跨 7 帧 cache 命中率达 **79.6%**，而 `frame-html` T1 命中率仅 **8.5%**（2,304/27,000，仅命中系统提示词）。原因：`_worker_prompt()` 开头即出现 `{frame_id}`，且 `frame-packets-core.mjs` 将每帧独有的 `# Dispatch` / storyboard / blueprint / recipes 放在前半段，却将 7 帧完全相同的 `frame.md excerpt` 与 `Design contract` 放在末尾。 |
| 4 | **`frame-html` 每帧多出 T1(`Read`) + T3(`done OK`) 两次高成本往返** | `6b05` 7 帧共 24 turns，总计消耗 **471,537 uncached input tokens**。所有帧 T1 仅读 1KB skeleton（各付 ~25K uncached input）；T2 写盘后 T3 仅输出 11 token `done OK`，若 T2 长思考导致 KV Cache 过期（如 `03-first-gate-the-business`），单次 T3 即白烧 **66,843 uncached input tokens**。 |
| 5 | **Thinking 占比过高 + 布局下沉/降档开关尚未默认开启** | `6b05` `frame-html` 7 帧输出 171,102 tokens，其中 **141,824 tokens（82.9%）为 thinking**（关键路径 251.8s，单帧 thinking 35.9K）。已落地的 `HTMLVIDEO_FRAME_VISUAL_LEDGER` 与 `frame_html` `effort=medium` 尚未开启默认。 |
| 6 | **`CLAUDE.md` 矛盾注入、未使用工具 schema 与 `.dispatch.md` 重复冗余** | `hyperframes init` 在项目目录落盘 5.9KB 交互式 `CLAUDE.md`（“Always invoke skill” / “always run npm run check”），随 `# Memory` 拼入 7.9KB `<system-reminder>`（占 11-25 `design` 输入的 **39%**）；`visual design` 仍携带 1.7KB 未用 `Read` schema；`.dispatch.md` 同时内联 `frame.md` tokens、`Pre-Render Self-Audit`、重复的 `**Palette & type**` 以及它帧 `## Blueprint assignment`。 |

---

## 2. 本次修改详情（P0-1 ~ P1-3）

### P0-1：`visual` 契约容错解析 + Prompt 去歧义 + 补全 `scene` 摘要
- **`html_utility/visual.py`**
  - 新增 `_normalize_design_contract()`：匹配 `#{1,6}` / `**Blueprint assignment**` / 裸 `Blueprint assignment:` 等变体并统一规范为标准 `## Blueprint assignment`；若标题缺失但存在 `F<n> = ...` 行，自动在首行前插入 `## Blueprint assignment`。
  - `_validate_design_contract()` 与 `_split_design()` 接入规范化；Phase A 若规范化后仍无分配段，不再直接硬崩，自动触发一次定向 `design-bp-fix` 修复并 splice。
  - `_build_design_prompt()` 显式区分两段格式：creative body 用 **bold** 标签，随后必须独占一行输出字面量 parser-anchor `## Blueprint assignment`。
  - `_frame_summary()` 与 `_frame_summary_block()` 补齐每帧 `scene`（截断至 ≤160 chars）与 outline `blueprint`（`outline_bp`）。
- **`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`**（及 `.claude` 镜像）
  - `blueprintAssignment()` 同步放宽标题正则，兼容 bold/heading 变体。

### P0-2：`frame-html` 共享前缀前置（跨帧 Prompt Prefix Cache）
- **`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`**（及 `.claude` 镜像）
  - `buildDispatchDocs()` 重排段落顺序：**所有帧逐字节一致的公共段前置**（`## Project inputs` → `## Role (executable rule card)` → `## frame.md design tokens (excerpt)` → `## Design contract (video-wide)`），之后再以 `# Dispatch: <frameId>` 开始帧专属内容（`Assigned storyboard block` → `Selected blueprint` → `Rule index` → `Rule recipes` → `Resolved conflicts`）。
- **`html_utility/frame_html.py`**
  - `_worker_prompt()` 将所有含 `{frame_id}` / `{frame_file}` 的说明统一后移至 `## Dispatch` 之后的 `## Target frame & output protocol`，确保多并发 worker 从 prompt 字节 0 直至 `## Design contract` 末尾完全逐字节一致。

### P0-3：`frame-html` 1-Turn Text-Out 模式
- **`html_utility/frame_html.py`**
  - 新增 `HTMLVIDEO_FRAME_HTML_TEXT_OUT`（默认 ON，`=0` 回退至 Read+Write 工具模式；`two_phase=1` 或 `salvage` 模式自动沿用原工具流）。
  - Text-Out 模式下：将预构建 skeleton 直接内联至 prompt 末尾（`## Pre-built skeleton`）；当 recipes 已内联（`snippets`/`full`）或处于 `degraded`/`hedge` 模式时，以 `tools=""` + `max_turns=2` 零工具单轮执行（recipes `off` 时保留 `tools="Read"`）。
  - 新增 `_extract_template_html()` / `_extract_and_write_frame_html()`：从模型最终文本回复中提取 `<template>…</template>` 写盘并跑 `frame-selfcheck.mjs`，彻底消除 T1 `Read(skeleton)` 与 T3 `done OK`；缺 `<template>` 时 `_failure_note()` 给出精确诊断并触发重派。

### P0-4：清理项目 `CLAUDE.md` 矛盾注入 + `--tools` 白名单
- **`html_utility/init_htmlvideo.py`**
  - `init_htmlframe()` 在 `hyperframes init` 成功后自动删除项目目录下的交互式 `CLAUDE.md`（由 `HTMLVIDEO_STRIP_PROJECT_CLAUDE_MD` 控制，默认 ON），消除每通调用 ~1.5K tokens 的矛盾 `<system-reminder>`。
- **`html_utility/frame_html.py`**
  - `_prepare_hedge_dir()` 停止向 hedge 临时目录复制 `CLAUDE.md`。
- **`html_utility/claude_cli.py`**
  - `_PROFILES` / `_profile_cmd()` / `_run_profile()` / `run_claude_result()` 新增 `--tools` 白名单机制及调用级 `tools=` 覆盖：
    - `intent` / `visual_design`：`--tools ""`（零工具）
    - `storyboard`：`--tools Write`
    - `visual`：`--tools Read`
    - `frame_html` / `verify`：`--tools Read,Write,Edit`（`frame_html` text-out 运行时覆盖为 `""`）
    - `visual_legacy` / `repair`：不设 `--tools`（保留默认工具集）

### P1-1：启用布局下沉（`LEDGER`）与 `frame_html` `effort=medium`
- **`html_utility/visual.py`、`scripts/storyboard-gate.mjs`**
  - `HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认值由 OFF 切换为 **ON**（保持 `=0` 一键回滚）。
- **`html_utility/claude_cli.py`**
  - `_PROFILES["frame_html"]["effort"]` 默认值由 `"high"` 切换为 **`"medium"`**，`max_output` 保持 `64000`（`HTMLVIDEO_FRAME_HTML_EFFORT=high` 可一键回滚）。

### P1-2：`visual` Phase A (`design`) 提速与截断保护
- **`html_utility/visual.py`**
  - 新增 `_blueprint_intents()`，从 `skills/hyperframes-animation/blueprints/*.md` 提取各 blueprint 的 `**Intent**` 首句拼入 `_catalog_prompt_block()`，消除模型盲猜 22 个 blueprint 含义的思考开销。
  - Phase A（`design` 与 `design-bp-fix`）切换至专用 `visual_design` profile，`DEFAULT_DESIGN_THINKING_TOKENS` 由 `32000` 调整为 `12000`。
  - Phase A 与 Phase B 默认开启 `trunc_kill=True`（由 `HTMLVIDEO_FRAME_VISUAL_TRUNC_KILL` 控制，默认 ON）。
- **`html_utility/claude_cli.py`**
  - 新增 `visual_design` profile：`effort="medium"`, `thinking="12000"`, `max_output="12000"`, `max_turns=4`, `timeout=1800`, `tools=""`, `deny_agent=True`, `deny_tools=[..., "Read", "Skill"]`。

### P1-3：`.dispatch.md` 冗余去重
- **`skills/hyperframes-core/scripts/lib/frame-packets-core.mjs`**（及 `.claude` 镜像）
  - 从 `FRAME_MD_EXCERPT_HEADINGS` 移除面向 verify 阶段的 `## Pre-Render Self-Audit`。
  - 新增 `trimDesignContractForDispatch()`：在向 `.dispatch.md` 内联 `_design.md` 时，剔除它帧的 `## Blueprint assignment`，并在 `frame.md` 前置 token 已存在时剔除重复的 `**Palette & type …**` 段落。

---

## 3. 运维开关与回滚

| 环境变量 | 默认值 | 说明 / 回滚方式 |
|---|---|---|
| `HTMLVIDEO_FRAME_HTML_TEXT_OUT` | `1`（开启） | 设为 `0` 回退至 Read+Write 工具模式（`done OK` 协议） |
| `HTMLVIDEO_FRAME_VISUAL_LEDGER` | `1`（开启） | 设为 `0` 关闭 `visual` 布局/动效 ledger 契约与 `storyboard-gate` 校验 |
| `HTMLVIDEO_FRAME_HTML_EFFORT` | `medium`（profile 默认） | 设为 `high` 回退 `frame_html` worker effort |
| `HTMLVIDEO_CLAUDE_VISUAL_DESIGN_EFFORT` | `medium` | 设为 `high` 回退 `visual` Phase A effort |
| `HTMLVIDEO_CLAUDE_VISUAL_DESIGN_MAX_OUTPUT_TOKENS` | `12000` | 设为 `32000` 回退 `visual` Phase A 输出上限 |
| `HTMLVIDEO_FRAME_VISUAL_DESIGN_THINKING_TOKENS` | `12000` | 覆盖 `visual` Phase A thinking 默认值 |
| `HTMLVIDEO_FRAME_VISUAL_TRUNC_KILL` | `1`（开启） | 设为 `0` 关闭 `visual` 阶段截断 watcher |
| `HTMLVIDEO_STRIP_PROJECT_CLAUDE_MD` | `1`（开启） | 设为 `0` 保留 `hyperframes init` 生成的项目 `CLAUDE.md` |

---

## 4. 验证记录

1. **Python 单元测试**：`python3 -m unittest discover -s /app/test -p 'test_*.py'` — **266/266 通过**（较改动前 256 项新增 10 项覆盖 P0-1 ~ P1-3）。
2. **Node 单元测试**：
   - `node /app/test/test_frame_packets.mjs` — **全通过**（含跨帧前缀逐字节一致、`trimDesignContractForDispatch`、`Blueprint assignment` 容错解析测试）。
   - `node /app/test/test_known_failures.mjs` — **12/12 通过**。
   - `node /app/test/test_verify_check_parse.mjs` — **全通过**。
3. **镜像同步校验**：`diff -u /app/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs /app/.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` — **0 差异**。
4. **失败样本实测回放**：加载 `6b06cfc0eeb0cbb3` 原始失败响应 `7a2a55dd-3268-47b5-939e-2e8632f18da7.response.json`，经 `visual._validate_design_contract()` 规范化后成功产出标准 `## Blueprint assignment` 且 7 帧全部命中 catalog（`misses=[]`）。

---

## 5. 修改文件列表

| 文件 | 类别 | 主要改动 |
|---|---|---|
| `html_utility/visual.py` | 核心实现 | 新增 `_normalize_design_contract()` 与 Phase A 缺失分配定向修复；修正 `_build_design_prompt()` 标题锚点指令；`_frame_summary_block()` 补齐 `scene` 与 `outline_bp`；新增 `_blueprint_intents()` 一句话索引；Phase A 切 `visual_design` profile（12k）并开启 `trunc_kill`；`HTMLVIDEO_FRAME_VISUAL_LEDGER` 默认 ON |
| `html_utility/claude_cli.py` | 核心实现 | 新增 `--tools` 白名单与 `tools=` 调用级覆盖；新增 `visual_design` profile（`medium`/`12000`/`tools=""`）；`frame_html` 默认 `effort` 改为 `medium`；各无头 profile 接入 `--tools` 白名单 |
| `html_utility/init_htmlvideo.py` | 核心实现 | `init_htmlframe()` 在 `hyperframes init` 后自动清理交互式 `CLAUDE.md`（支持 `HTMLVIDEO_STRIP_PROJECT_CLAUDE_MD=0` 回滚） |
| `html_utility/frame_html.py` | 核心实现 | `_worker_prompt()` 重排为跨帧共享前缀前置 + 尾部目标块；新增 P0-3 1-Turn Text-Out 模式（内联 skeleton、`--tools ""`、`<template>` 提取写盘、缺块诊断重派、`HTMLVIDEO_FRAME_HTML_TEXT_OUT=0` 回滚）；`_prepare_hedge_dir()` 不再复制 `CLAUDE.md` |
| `scripts/storyboard-gate.mjs` | 门禁脚本 | `HTMLVIDEO_FRAME_VISUAL_LEDGER` 校验默认值切换为 ON（支持 `=0` 回滚） |
| `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` | 打包脚本 | `buildDispatchDocs()` 重排为跨帧共享前缀前置；新增 `trimDesignContractForDispatch()` 去重 `Blueprint assignment` 与重复 `Palette & type`；移除 `Pre-Render Self-Audit`；`blueprintAssignment()` 容错解析标题变体 |
| `.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` | 镜像同步 | 与 `skills/hyperframes-core/scripts/lib/frame-packets-core.mjs` 保持逐字节一致 |
| `test/test_visual_driver.py` | 单元测试 | 新增 `**Blueprint assignment**` / `###` / 裸行规范化、缺失分配修复、blueprint intent 索引、`scene` 摘要、`visual_design` profile、`LEDGER` 默认开启与 `=0` 回滚用例 |
| `test/test_claude_cli.py` | 单元测试 | 同步 `visual_design`、`frame_html` `effort=medium` 与 `--tools` 白名单断言；新增 `tools=""` 覆盖与 `init_htmlframe` 清理 `CLAUDE.md` 用例 |
| `test/test_frame_html_driver.py` | 单元测试 | 新增 `TextOutAndPrefixCacheTest`（跨帧前缀逐字节一致、`<template>` 提取、零工具 1-turn 写盘、缺块重派、hedge 不复制 `CLAUDE.md`）；原工具模式用例显式置 `TEXT_OUT=0` |
| `test/test_frame_packets.mjs` | 单元测试 | 新增跨帧共享前缀逐字节一致、`trimDesignContractForDispatch` 去重及 `**Blueprint assignment**` 容错解析断言 |
| `docs/2026-11-25-frame-visual-and-html-optimization.md` | 说明文档 | 本次故障分析、P0-1 ~ P1-3 修改说明、运维开关、验证记录与文件清单 |
