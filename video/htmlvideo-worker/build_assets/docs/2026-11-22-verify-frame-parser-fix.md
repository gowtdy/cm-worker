# verify-frame 解析漏检修复（P0 + P1）

> 日期:2026-11-22  
> 触发样本:`6b01e0245007d730` verify 会话 `ab178f45…`（CST 03:21–03:24，阶段 184s 后失败）  
> 依据:Opus 对凌晨 verify 交互的复核

## 问题

`hyperframes check` Layout 行格式为：

```text
✗ t=20.4, 20.7s content_overlap #sel … — message
```

旧 `verify-report.mjs` 只认 `✗ code:`，**8 个 `content_overlap` error 全部漏检**，输出自相矛盾的 `GATE: FAIL` + `0 error finding(s)` / `No action`。模型零 Edit 后被 digest 守卫拒绝，任务失败并空转 ~152s。

## 已落地

### P0
1. **`scripts/lib/verify-check-parse.mjs`**（新建）+ **`scripts/verify-report.mjs`**  
   - `check` 改跑 `--json`，按 `layout/runtime/…findings[].severity` 归桶  
   - 文本兜底同时识别 `t=…s code` 与经典 `code:`  
   - `check exit≠0` 且解析出 0 个 error → 打 `PARSER/INFRA` 并以 **exit 2** 退出（不当成可修 findings）
2. **`html_utility/verify.py`**  
   - `scan_looks_unactionable()`：GATE FAIL + 0 error findings + 无编号 actionable → **不调模型**，直接 raise  
   - 零 Edit 报错文案区分「模型没改 / 上游无可修项」

### P1
3. 扫描打印由 `@path` 改为 **`in path`**，避免 Claude Code 把 `@` 当附件灌整文件  
4. **`claude_cli.py` verify profile** `deny_tools` 增加 **`Skill`**（对抗 SessionStart「必须调 skill」文案冲突；不用 `--bare`，以免弄断 `ANTHROPIC_AUTH_TOKEN` 鉴权）

## 修改文件

```
scripts/lib/verify-check-parse.mjs          # 新建
scripts/verify-report.mjs
html_utility/verify.py
html_utility/claude_cli.py
test/test_verify_check_parse.mjs            # 新建
test/test_verify.py                         # 新建
docs/2026-11-22-verify-frame-parser-fix.md  # 本说明
```

## 验证

```bash
node test/test_verify_check_parse.mjs
python3 -m unittest test.test_verify test.test_claude_cli
```

## 二次加固（同日）

### 1. 文本兜底补齐 CLI 真实格式
`parseCheckText` 现在能识别：
- Layout 时间前缀的三种写法：单点 `t=1.2s`、区间 `t=1-5s (3 samples)`（`formatLayoutIssue` 折叠后的格式）、逗号列表 `t=20.445, 20.737s`
- Contrast 行 `✗ #title 2.1:1 (need 4.5:1, t=1.2s)`：code 用 CLI JSON 里真实的 `contrast_aa_failure`（与 JSON 模式一致），section=Contrast，选择器写入 `selector`，下一行 `Try …; source X.html` 写入 `file`
- Layout 下一行的 `Fix: …` 写入 `fixHint`；行内 ANSI 颜色码先剥掉
- 保留经典 `code:` 和不带时间戳的 bare `code` 两个分支

注意：CLI 打印 Contrast 时不分级别一律用 ✗，所以文本兜底会把 contrast warning 也归进 errors；只有 JSON 模式能区分级别。

### 2. 部分漏检对账
- `jsonDeclaredErrorCount(json)`：非 lint 段的 `errorCount` 求和（缺失时按 findings 中 severity=error 计数）
- `jsonLintErrorCount(json)`：check 信封里 lint 段的 error 数
- `checkParseContradiction(status, buckets, opts?)`：新增 `opts.declaredErrorCount`，只要 declared≠parsed 就算矛盾（不再只在 0 时报警）；`opts.lintErrorCount>0` 时，exit≠0 被 lint 解释，不算「0 解析」矛盾
- `checkParseMismatch(declared, parsed)` → `{declared, parsed}` 或 `null`
- `verify-report.mjs` 在 JSON 模式下打印 `✗ PARSER/INFRA: … declared=N parsed=M` 并 **exit 2**；`verify.py` 已有的 `PARSER/INFRA:` 匹配会拒绝调用模型

### 3. content_overlap 分流（设计叠加 vs 真碰撞）
- `triageContentOverlap(item)` / `triageOverlaps(errors)`，三类：
  - `designed_overlay`：任一侧选择器带装饰类 token（badge/stamp/overlay/watermark/seal/sticker/pill/corner/ribbon/chip/tag/decor/ornament/logo/icon），或 `dataAttributes` 含 allow-overlap/layout-ignore/decor/overlay，或 fixHint 明说 intentional → 建议 `data-layout-allow-overlap`
  - `real_collision`：有 `inside` 且两侧都是正文类 token（headline/title/body/line/copy/caption/text/p…）→ 挪位置或缩小，禁止一律 allow
  - `ambiguous`：其余情况 → 先看 midpoint 判断设计意图
  - CLI 每条 content_overlap 的 fixHint 都会提到 allow-overlap，所以 fixHint 只有出现 "intentional" 才算信号
- `parseCheckJson` 把 `containerSelector` / `text` 拼进 `msg`（`sel inside #cont "text" — message`），并单独保留 `selector/container/text/fixHint/dataAttributes` 字段
- verify-report actionable 输出示例：
  ```text
  1) Known failure #4 — content_overlap triage — designed_overlay×A (file: sel inside cont; …): add data-layout-allow-overlap on the text or an inner container (not the scene root)
     real_collision×B (…): reposition / reduce size; do NOT blanket-allow
     ambiguous×C (…): inspect midpoints for design intent before choosing allow-overlap vs reposition
  ```
- `verify.py` `_FIX_PLAYBOOK` 的 content_overlap 规则改成按分流结果处理，删掉了「一律 allow、do NOT reposition」

### 4. 6b01 验证（以磁盘上的实际产物为准）
- 解析层：`test/test_verify_check_parse.mjs` 的 `test6b01Artifact` 读取最新的 `.hyperframes/artifacts/checks/*.json`（4 份内容一致：runtime `http_error`×1 + layout `content_overlap`×1；事故当时的 8 个已经不在磁盘上），断言 declared===parsed===2，content_overlap=1，分流结果 designed_overlay=1（`#cui-bg > div:nth-of-type(5) inside #cui-badge "NEXT"`，命中 `badge`）
- 完整 `node scripts/verify-report.mjs --videodir <6b01>`（约 25s）：`exit=1 parse=json ✗=2 ⚠=17 ℹ=23`，没有 PARSER/INFRA；actionable 输出 `1) Known failure #4 — content_overlap triage — designed_overlay×1 …`；`GATE: FAIL … 2 error finding(s)`
- 部分漏检路径：用 PATH 垫片伪造 `npx hyperframes check --json`，构造 layout `errorCount=8` 但只有 1 条 finding → 打印 `declared=8 parsed=1`，exit 2

## 修改文件（二次加固）

```
scripts/lib/verify-check-parse.mjs   # 时间前缀/Contrast/Fix 行、对账、triage
scripts/verify-report.mjs            # declared vs parsed → PARSER/INFRA exit 2；overlap triage 输出
html_utility/verify.py               # _FIX_PLAYBOOK content_overlap 规则
test/test_verify_check_parse.mjs     # 区间/逗号/Contrast、对账、triage、6b01 artifact
test/test_verify.py                  # playbook 分流、部分漏检标记
docs/2026-11-22-verify-frame-parser-fix.md
```

## 仍未做

- [x] 真实项目端到端再跑 `6b01e0245007d730`（解析层 + 完整 verify-report 已跑；没有跑带模型的 `verify_frame` 修复轮）
- [x] 「真实排版碰撞 vs 设计重叠」分流（启发式 triage，详见上文第 3 节）
- [ ] verify `max_output` 再降到 4–6k（原 Opus P2 建议）
- [ ] runtime `http_error`（6b01 的 `data:image/svg+xml` 噪点被当成 404）目前在 actionable 里没有对应规则，需单独排查
