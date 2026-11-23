# verify-frame 已知故障单一来源(`scripts/known-failures.mjs`)

> 日期:2026-11-23
> 触发:2026-11-22 22点完整流程(样本 `6b02f91ca413f854`)verify 19m43s / 3 轮模型修复失败
> 依赖:[2026-11-22-verify-frame-parser-fix.md](2026-11-22-verify-frame-parser-fix.md)、[2026-11-22-verify-designed-overlay-autofix.md](2026-11-22-verify-designed-overlay-autofix.md)

## 动机

22点的 verify 失败根因是 `text_occluded` 这条 finding 在三处「已知故障知识源」里**都没有规则**:

1. `skills/aiflow-verify-frame/SKILL.md` 的 "Known failures" 段落(5 条);
2. `html_utility/verify.py::_FIX_PLAYBOOK`(内联 cheat sheet);
3. `scripts/verify-report.mjs` 硬编码的 "Known failure #1-4" + content_overlap triage。

模型走 `_FIX_PLAYBOOK` 兜底「cheapest safe edit」,凭直觉套用了 `content_overlap` 专用的
`data-layout-allow-overlap`(对 occlusion 无效),round2/3 各改一处白烧,撞
`_MAX_FIX_ROUNDS=3` 报错。正确修法(CLI 自己的 fixHint)是 `data-layout-allow-occlusion`,
但 `fixHint` 字段被解析后从没渲染进模型可见的 error 行,于是三处全漏。

更深的问题是**结构性的**:同一套修复知识散在三处、已互相漂移(SKILL.md 说 4 处、playbook 说 6 处、
report 硬编码 4 处),新增 finding 类型时三处都跟不上。本设计把它们收敛成一个**可执行、可持续积累的
单一文件**,并让「脚本能修的」由 `verify-all.mjs` 确定性修掉、「需模型的」命中即把规则装进 prompt。

## 单一来源形态

`/app/scripts/known-failures.mjs`(Node ESM)。选 mjs 而非 md 的原因:机械层全在 JS,且同一份要
同时驱动「可执行的 match/autofix」与「送给模型的 prompt 文字」;用 md 就要另写两套解析器,必然二次漂移。
`prompt` 字段本身用 markdown 写,读起来就是文档;**积累 = 加一条 entry**。

导出:`KNOWN_FAILURES`(有序数组)、`matchKnownFailures(ctx)`、`renderPrompt(entry, ctx, hits)`。

### entry 结构

```js
{
  id: "text-occluded",                       // 稳定 kebab-case slug
  title: "text_occluded",                    // actionable summary 的标题(不重复 prompt 首句)
  match: (ctx) => ctx.checkErrors.filter(e => e.code === "text_occluded"),
  mode: "autofix" | "model" | "ignore" | "info",
  autofix: (ctx, hits) => ({ changed, note }),   // 仅 mode=autofix
  prompt: "…" | (ctx, hits) => "…动态规则…",       // 除 autofix 外的必备;autofix 也为手动路径保留
  reason: "为什么 / 何时见过 / 为何这样修(积累用)",
}
```

### mode 语义

| mode | 含义 | 消费方 | 输出形态 |
|---|---|---|---|
| `autofix` | 确定性脚本修,不进模型 | `verify-all.mjs` 的 autofix pass | 修掉即重扫;残留才回 report 打编号 item |
| `model` | 需模型判断 | `verify-report.mjs` → `verify.py` 的 fix prompt | actionable summary 里编号 `N)` item |
| `ignore` | 已知误报/非问题 | 同 model,但不 blocking | `•`(不编号,不计入 actionable) |
| `info` | tell / 非阻塞警告 | 同 ignore | `•`(不编号) |

编号 `N)` 与非编号 `•` 的区分是**有语义的**:`verify.py::scan_looks_unactionable` 靠
`^ {2}\d+\) ` 判断「是否有可操作项」,只有 model/autofix 产出编号 item,ignore/info 不会让
空 finding 误触发一次模型调用。

### ctx shape

由调用方各建一次(`verify-report.mjs` 用完整 findings;`verify-all.mjs` 的 autofix pass 用最小 ctx):

```js
{
  videodir,            // 绝对项目根
  stub,                // index.html 0 个 composition ref
  digitSelectors,      // frame-selfcheck 报的 digit-led 选择器
  lintErrors,          // [{ code, msg, file }]
  checkErrors,         // [{ code, msg, selector, container, file, fixHint, dataAttributes, ... }]
  transitions,         // { status, error }
  missingFrames,
  digitTell, googleImport,   // Map|object,文件级 warning 计数
}
```

## 目录内容(11 条)

| id | mode | 说明 |
|---|---|---|
| `assemble-index-stub` | autofix | 自动跑 `assemble-index.mjs`(新) |
| `data-duration-unit-suffix` | autofix | 幂等 strip `data-duration="Ns"`→`"N"`(新,11-21 回归的保险) |
| `digit-led-selectors` | model | 选择器转 `[id=…]`/`[class~=…]`(未脚本化,见「未做」) |
| `font-family-no-face` | model | residual:删 dead fallback / 贴 `@import` 块(复用 `fontActionLines`,自 verify-report 迁入) |
| `content-overlap` | model | `triageOverlaps` 三分流 designed/real/ambiguous + caption 保留带禁令 |
| `text-occluded` | **model(新增)** | `data-layout-allow-occlusion`(不是 overlap),或提 z-index —— 直接修掉 22点失败 |
| `text-not-painted` | **model(新增)** | 修 `-webkit-text-fill-color` / `color` 透明填充 |
| `transitions-verify` | info | 自愈(verify-all step1 inject guard);「s」后缀由 duration entry 处理 |
| `caption-text-box-overflow` | ignore | 已知误报(贴合 line-height + 无 overflow:hidden) |
| `id-requires-css-escape` | info | tell,非缺陷 |
| `google-fonts-import` | info | 非阻塞 |

> `data-duration-drift`(帧与 index wrapper 的 duration 差异是设计行为)是**无 finding 的负向规则**,
> 放在文件头注释而非一条永不命中的 entry。

## 接线(四个消费方)

### 1. `verify-report.mjs`

- 顶部删掉 `fontActionLines` 及 `lib/fonts.mjs` 整套 import(迁入 catalog);删 `triageOverlaps` import。
- 新增 `import { matchKnownFailures, renderPrompt } from "./known-failures.mjs"`。
- 「actionable summary」段改为:构建 `ctx` → 循环 `matchKnownFailures(ctx)` → 每个命中 entry
  按 mode 决定编号/非编号、`renderPrompt` 首行接标题后面、其余行缩进。
- `printGroup` 给每条 check error 渲染 `[hint: …]`(读取 `fixHint`;**`content_overlap` 除外**,
  它每条 hint 相同、无信号,交给 catalog 精确 triage)。

### 2. `verify-all.mjs`

- 新增 `import fs` + `import { KNOWN_FAILURES }`。
- 最前加「known-failure autofix pass」:构建最小 ctx,按 catalog 顺序跑 `mode=autofix` 且 match 的
  entry,changed 则日志。顺序保证:assemble → duration-strip →(既有 step0 font-imports → step1
  transitions inject guard → step2 report)。font-imports / transitions 仍是显式 step,未重写,只在
  catalog 里声明归属。

### 3. `verify.py`

- `_FIX_PLAYBOOK` 瘦身为通用纪律 + 指向 catalog + 兜底,删除与 catalog 重复的全部特例规则。
- 其余不变:`_FIX_HARD_CONSTRAINTS`、`_FIX_REPLY`、`_trim_scan_for_fix`(它已把「actionable
  summary→末尾」送进 prompt,匹配规则因此自动装进模型 prompt,零新耦合)。
- `_auto_fix_overlaps`(Python overlap autofix)保持,由 catalog 声明。

### 4. `SKILL.md`(两份拷贝同步)

- 「Known failures — mechanical playbook」段替换为指向 `scripts/known-failures.mjs` + 8 条编排纪律
  (纪律是 process,不属于 per-failure);「Known non-issue」段追加 catalog 指引。

## 新增一个 failure 的流程

1. 在 `scripts/known-failures.mjs` 的 `KNOWN_FAILURES` 加一条 entry(见 entry 结构)—— **唯一落点**。
2. 决定 mode:
   - 能确定性修 → `autofix` + 写 `autofix` 函数(会由 `verify-all.mjs` 自动跑);
   - 需模型判断 → `model` + 写 `prompt`(命中即装进 fix prompt);
   - 误报/非阻塞 → `ignore` / `info`。
3. 在 `test/test_known_failures.mjs` 补一个 match/mode/prompt 断言。
4. **禁止**再在 SKILL.md / verify.py 里硬编码 per-failure 规则(会造成二次漂移)。

## 修改文件

```
scripts/known-failures.mjs            # 新增(单一来源)
scripts/verify-report.mjs             # catalog 接入 + fixHint 渲染;fontActionLines 迁出
scripts/verify-all.mjs                # known-failure autofix pass
html_utility/verify.py                # _FIX_PLAYBOOK 瘦身
skills/aiflow-verify-frame/SKILL.md   # 两份(.claude/skills 与 skills/)同步改
test/test_known_failures.mjs          # 新增
test/test_verify.py                   # _FIX_PLAYBOOK 断言改为「指向 catalog、不再含特例」
```

## 验证

```bash
node test/test_known_failures.mjs          # 7/7:完整性、text_occluded/caption/digit 命中、duration strip 幂等
node test/test_verify_check_parse.mjs      # 不变、仍全过
python3 -m unittest test.test_verify test.test_claude_cli   # 85 OK
```

端到端(只读)对失败样本 `6b02f91ca413f854` 复扫,actionable summary 现在是:

```
  1) text_occluded — If the layering is intentional, add data-layout-allow-occlusion="true"
     on the text element or an ancestor — … (NOT data-layout-allow-overlap, which only
     exempts content_overlap). …
  • google_fonts_import (non-blocking) — Non-blocking warning — ignore.
```

`verify-all.mjs` 的 autofix pass 对干净索引/无「s」后缀的项目正确输出
「no autofix hits … continuing」。

## 已知风险 / 未做

- `digit-led-selectors` 仍是 `model`(跨文件改选择器需要真 HTML 编辑引擎;若把
  `verify_overlap_autofix.py` 的「解析选择器→建树→插属性」下沉到 JS,可升级为 autofix)。
- `text_occluded` 未做 triage 前置:白胶囊压白卡(`.anchor-line.buy` #FFFFFF on `.notecard` #FFFFFF)
  可能是设计意图而非缺陷,当前一律交给模型加 `data-layout-allow-occlusion`。若白底白误报频发,
  应像 `designed_overlay` 一样在 triage 层识别豁免,而不是丢给模型。
- `fixHint` 渲染对 `content_overlap` 刻意跳过(其通用 hint 无 per-finding 信号);对未知 code 是
  CLI 提示的唯一兜底。
- overlap designed_overlay 的确定性修仍在 Python(`verify_overlap_autofix.py` + `VERIFY_FINDINGS_JSON`
  payload),catalog 只声明不重写;未做 JS 下沉。
- 相关开关路径不变:`HTMLVIDEO_VERIFY_AUTO_OVERLAP` / `HTMLVIDEO_VERIFY_FIX_FULL_SCAN` /
  `HTMLVIDEO_VERIFY_FIX_ROUNDS` / `HTMLVIDEO_FONT_IMPORTS`。