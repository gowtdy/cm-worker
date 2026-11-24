# verify-frame：designed_overlay 确定性自动修 + fix prompt 收紧

## 动机

样本 `6b02a2d6e070393b`：扫描已把 5 条 `content_overlap` 全部 triage 为
`designed_overlay`，actionable summary 明写「add data-layout-allow-overlap」，
模型却花了约 2.5 分钟 Read + 思考，最后只加了 3 个属性
（`price-num` / `cap-sub` / `f8-ticker`）。这一步的修法是固定的，不需要模型。

## 行为

1. `verify-report.mjs` 在 GATE FAIL 时，在 `GATE:` 行之后输出一行机读结果：
   `VERIFY_FINDINGS_JSON {"version":1,"designed_overlay":[{file,code,selector,container,reason,autofix_ok,mark_side}],"real_collision":[…],"ambiguous":[…],"other_check_errors":N,"lint_errors":N,"digit_led_selectors":N,"transitions_ok":bool}`
   （由 `verify-check-parse.mjs::findingsPayload` 生成，复用 `triageOverlaps`）。
   `mark_side` 为 `"selector"` / `"container"`（`autofix_ok=true` 时）或 `null`。
2. `verify_frame` 在每轮调模型**之前**解析这一行，对 `designed_overlay` 中
   `autofix_ok=true` 的行，只在 `mark_side` 那一侧用确定性 HTML 编辑插入
   `data-layout-allow-overlap="true"`；只要写了文件就
   立刻重扫，PASS 则直接返回，**全程零模型**。
3. 重扫仍 FAIL：把新扫描结果（剩 real_collision / ambiguous / 非 safe 行 / 其它 finding）
   交给模型；如果剩余部分没有可操作项，仍走原来的 unactionable → raise 路径。
4. 首扫 PASS 的路径不变（不解析、不改文件、不调模型）。
5. 最后一轮模型修完后的重扫现在也会检查：PASS 直接返回（之前这次重扫结果被忽略、
   一律 raise）；仍 FAIL 时再跑一次自动修 + 重扫，仍不过才 raise。

### triage 规则（`triageContentOverlap`）

前提：CLI 只把两个**实心文字块**配对报 content_overlap（可见、有自身文本、文字颜色
alpha ≥ 0.35；已在 `[data-layout-allow-overlap]` 下的不参与），所以两侧都是真正渲染出来的
文字——「装饰」只是类名上的猜测。

按顺序（第一条命中即返回）：

1. dataAttributes 带 allow-overlap/decor/overlay → `designed_overlay`，`autofix=true`，
   `mark_side="selector"`（finding 的 dataAttributes 不区分侧，默认打 selector 侧，
   即 CLI 报 `text` 的那个元素）。
2. **E′ caption 保留带**：任一侧是 `#caption-word-<n>-<m>`（运行时字幕词）→
   `real_collision`，`autofix=false`，reason `frame text inside caption keep-out band`。
   压过下面的 decor 规则（`f01-cap-pill` × caption 不会因 `pill` 被豁免）。
   fix playbook 明确：此类**禁止** `data-layout-allow-overlap`，把帧内文字上移到
   `--cap-band-top` 之上，或删掉帧内自带的字幕条。
3. 两侧都是文字块 token → `real_collision`。
4. **A′ 双侧装饰**：两侧都带 `DECOR_TOKENS` → `designed_overlay`，但 `autofix=false`
   （reason `decorative tokens on both sides (…)`）——不知道该打哪一侧，交给模型。
5. **B′ 单侧装饰**：`DECOR_TOKENS`（badge / stamp / overlay / watermark / seal / sticker /
   pill / corner / ribbon / chip / tag / decor / decoration / ornament / logo / icon /
   ticker / dot）只决定 kind=`designed_overlay`；是否自动修看更窄的
   `AUTOFIX_DECOR_TOKENS`：`watermark, stamp, seal, sticker, ribbon, ornament, decor,
   decoration, ticker`。
   - 命中白名单 → `autofix=true`，reason `decorative token "<t>"`，`mark_side` = 装饰词所在侧。
   - 只命中弱词（tag / dot / pill / chip / badge / icon / logo / corner / overlay）→
     `autofix=false`，reason `weak decorative token "<t>"`（刻意不以 `decorative token`
     开头）。**没有 `*-tag` 例外**：`price-tag`、`span.tag` 都走模型。
   - `badge` 暂不进白名单：`#cui-badge` 这类常是带文字的 CTA 胶囊，豁免会把真实遮挡藏掉。
6. 其余 → `ambiguous`。

**fixHint 不再参与判定。** 真实 CLI（`layout-audit.browser.js`）对每一条
content_overlap 给出的 fixHint 完全相同：
“Give each block its own zone, or mark intentional layering with data-layout-allow-overlap.”
——**里面就有 `intentional`**。旧的「fixHint 含 intentional → designed_overlay」分支
会把所有没被 text-vs-text 拦住的 overlap（`#a`/`#b`、`stat-value`/`stat-label`、
`cap-sub`/`#caption-word-*` …）判成 designed 并被自动豁免，现已删除；这些现在是 `ambiguous`。

### 6b02 类样本的预期

| finding | triage | 处理 |
|---|---|---|
| `div.f8-ticker > span:nth-of-type(n)` × `span.f8-row-text` | designed, `ticker`, mark_side=selector | 自动打在 `div.f8-ticker` |
| `span.f8-dot` × `span.f8-row-text` | designed, weak `dot` | 不自动修；dot 在 ticker 里，ticker 打标后重扫即消失 |
| `div.price-num` × `div.price-tag`（兄弟节点） | designed, weak `tag` | 模型 |
| `div.cap-sub` × `#caption-word-*` | real_collision（caption 保留带） | 模型，上移/删字幕条，禁止 allow-overlap |

### 只自动修 safe 行（纵深防御）

`findingsPayload` 给每行加 `autofix_ok` 和 `mark_side`（取自 triage）。词表**只在 JS
triage**；Python 不复制任何 token 表，也不再看 `reason`。

Python 侧 `row_is_safe`：必须 `autofix_ok is True` **且** `mark_side ∈ {selector, container}`，
否则跳过——缺 `autofix_ok` 或缺 `mark_side` 的旧 payload 行一律跳过（不再按 reason 兼容）。
跳过的行打 INFO 日志并留给模型。
`real_collision` / `ambiguous` 永远不自动修。payload `version != 1` → WARN 并整体跳过。

### 选目标节点的算法

- 选择器语法与 CLI `selectorFor` 一致：`#id`、`.class`、`tag.c1.c2`、
  `[attr="v"]`、`tag[attr="v"]`，以及 `X > tag:nth-of-type(n)` 子选择器链；
  支持 CSS 转义（`#\30 7-x`）。其它语法（后代空格、伪类、选择器列表）不处理，留给模型。
- 打标点只能是链尾复合选择器（带 class/id/attr），或它的**直接父级**——仅当链尾是
  唯一一段无钩子的子段（如 `span:nth-of-type(5)`）时才允许升到父级，且父级必须真的有
  匹配该子段的子节点：`div.f5-stamp` 打在自身；`div.f8-ticker > span:nth-of-type(5)`
  打在 `div.f8-ticker` 上（日志标 `ancestor div.f8-ticker`）。CLI 判定豁免用
  `element.closest("[data-layout-allow-overlap]")`；harness 判断「是否已被覆盖」时
  在该 frame 源文件里从节点**向上找到 composition 根（`data-composition-id`）为止**，
  这就是 CLI `closest()` 在这个文件内能看到的全部祖先，因此打在父级上同样能豁免。
  更深的链（`div.wrap > div:nth-of-type(2) > span:nth-of-type(1)`）→ unresolved，不打标、打日志。
- 同一行选择器匹配到的「未被已有标记或同批窄行目标覆盖」的节点 > 3 个时，视为过宽，
  WARN 并留给模型。
- **只打 `mark_side` 那一侧**；该侧选择器为空或源码里解析不到 → unresolved，
  **不回退**到另一侧（旧实现会回退，可能把 `cap-sub` 这类正文打标）。
- 拒绝打标：`html/head/body/template`、带 `data-composition-id` 的场景根、
  `.clip` 轨道层、没有 class/id/attr 钩子的纯结构选择器、
  `compositions/` 以外的文件。
- 已经被打过标的祖先覆盖的节点直接跳过（幂等，不会重复堆属性）。
- 实现只用标准库 `html.parser`：带源码偏移建一棵轻量树，按偏移把属性插进
  原始 start tag，其余字节不变；`<script>`/`<style>` 里的选择器字符串不会被误认成节点。
- 读写都用 `encoding="utf-8", newline=""`（保留 CRLF）；写入走同目录 tmp 文件 +
  `os.replace`，并复制原文件权限。
- `_auto_fix_overlaps` 外包 `try/except Exception`：自动修崩溃只 `logger.exception`，
  返回原 `(rc, output)`，verify 照常进入模型轮。

## fix prompt 收紧（仍需模型时）

- 默认只附「`✗` 错误行（连同其下更深缩进的明细续行，直到空行或缩进回退，
  例如 transitions verify 的失败明细）+ 从 `── actionable summary ──` 到扫描末尾」，
  去掉 font-imports / transitions inject ✓ 瀑布、warnings/infos、midpoints；
  机读 JSON 行在任何模式下都会剥掉。
- 收尾要求改为只回 `fixed <n>`（无事可做时回 `fixed 0`），删除 “list what you changed”。
- playbook 写死 `data-layout-allow-overlap="true"`。
- hard constraints：每个目标文件（绝对路径）最多 Read 一次再 Edit；禁止
  Grep / Glob / 列目录 / 打开 findings 没点名的文件。prompt 里带上项目绝对路径。

## 开关

| env | 默认 | 作用 |
|---|---|---|
| `HTMLVIDEO_VERIFY_AUTO_OVERLAP` | 开 | `0/false/off/no` 关闭自动修，回到旧行为 |
| `HTMLVIDEO_VERIFY_FIX_FULL_SCAN` | 关 | `1/true/on/yes` 时 fix prompt 附完整扫描输出（回滚用） |

verify 的 `max_output` 默认值本次不改（仍为 16000）；8000 档可以用
`HTMLVIDEO_CLAUDE_VERIFY_MAX_OUTPUT_TOKENS` 做 A/B。

## 文件

- `html_utility/verify_overlap_autofix.py`（新增）：解析 payload、选择器、轻量树、插入属性
- `html_utility/verify.py`：`_auto_fix_overlaps`、`_trim_scan_for_fix`、
  prompt 文案；最终报错里的轮数改为实际生效的 `max_fix`
- `scripts/lib/verify-check-parse.mjs`：`findingsPayload`、`FINDINGS_JSON_PREFIX`
- `scripts/verify-report.mjs`：GATE FAIL 时输出 `VERIFY_FINDINGS_JSON` 行
- `test/test_verify.py`、`test/test_verify_check_parse.mjs`

## 已知风险

- 白名单 token 仍是启发式：类名里带 `stamp` / `ticker` / `seal` 等但实为正文的元素
  （如 `.stamp-copy`；`.timestamp` 分词后是整词 `timestamp`，不命中）仍会被自动豁免。
  两侧都是实心文字，豁免意味着这一处重叠不再被 gate 拦。
- 弱装饰词（tag / dot / pill / chip / badge …）现在全部走模型：`price-num`×`price-tag`
  这类会比之前多一次模型轮，换来不误豁免真实价格/标签文字。
- caption 相关（`#caption-word-*` 任一侧）一律 `real_collision` 走模型，且 playbook
  禁止 allow-overlap；模型若仍硬加属性，只能靠 prompt 约束。
- class 选择器会给同名节点全部打标（≤3 个未覆盖节点；已被祖先覆盖的跳过）。
- 旧 payload（无 `autofix_ok` / `mark_side`）的行全部跳过，走模型路径；
  旧扫描输出无 `VERIFY_FINDINGS_JSON` 行时自动修整体跳过。
- 误修时可用 `HTMLVIDEO_VERIFY_AUTO_OVERLAP=0` 关闭。
