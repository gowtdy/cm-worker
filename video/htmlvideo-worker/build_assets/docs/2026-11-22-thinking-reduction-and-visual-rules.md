# frame-html 思考减负 + frame-visual rule 引用强制（2026-11-22 下午）

> 日期:2026-11-22  
> 触发样本:`6b02a2d6e070393b`（UTC 08:07–08:31，端到端 ~23.7m；frame-html span ~13.9m）  
> 依据:`stat/skill-timing.py` + Opus 对 frame-html 转录的深挖 + Rule index 空索引根因追查  
> 状态:**代码已落地**；effort=medium 默认未改（待 A/B）；历史样本 STORYBOARD / dispatch **未回写**（需重跑 visual → packets → html）

## 0. 一句话

frame-html 编排合理，瓶颈是单请求超长 thinking；靠 **packet 预裁决 + worker 合同收紧 + visual 强制 `- rules:`** 压输入与思考，而不是再拧 driver 开关。

## 1. 样本事实（下午分析）

| 项 | 值 |
|---|---|
| 项目 | `/app/videos/2026-11-22/userclm17919893695531772/6b02a2d6e070393b` |
| frame-html | 8 帧并行；wall span ~13.7–13.9m；0 截断；首轮均 done OK |
| 交互模式 | Read 骨架 → **单请求思考 4–13m** → 一次 Write/首 Edit；wall≈model |
| Rule index | 8/8 `- (no motion rules cited)`（当时磁盘 dispatch） |
| STORYBOARD | 8/8 无 `- rules:`；Scene 仅有口语动效（`types on` / `hard-cut` 等），无精确 rule stem |

### 空 Rule index 因果

1. **frame-visual（主因）**：合同要求 cite rule id，但 worker 输出模板无 `- rules:`，验收/gate 不检查 → 只写口语动效。  
2. **frame-packets（旧，次因）**：`citedRules()` 只认 `- rules:` 或块内精确 stem，无 blueprint mapping / 短语兜底。  
3. **frame-html worker**：输入无 recipe snippet → 从零推 GSAP（非本阶段写错）。

## 2. 落地项对照

### A. frame-html / packets（思考减负）

| ID | 内容 | 默认 | 落点 |
|---|---|---|---|
| H-P0-1 | effort=medium × 64k **A/B 文档**（不改默认 high） | 默认仍 high×64k | `docs/2026-11-22-frame-html-effort-medium-ab.md`；env 已有 |
| H-P0-2 | `narrowBlueprint`：按 role/Variant 裁剪大 blueprint | **ON** `HTMLVIDEO_FRAME_PACKETS_NARROW_BLUEPRINT` | `frame-packets-core.mjs` |
| H-P0-3 | `inferCitedRules` / `frameRules`：mapping + 短语表补 Rule index | **ON** `HTMLVIDEO_FRAME_PACKETS_INFER_RULES` | 同上 |
| H-P0-4 | conflicts：`idle-loop-vs-continuous-motion` | 随 `RESOLVE_CONFLICTS` | 同上 |
| H-P1-1 | recipes 标题 **行首匹配**（避免 card 反引号误判） | 常开 | `frame_html._recipes_mode` |
| H-P1-2 | `frame_html` deny `Skill` | 常开 | `claude_cli.py` |
| H-P1-3 | &lt;~15KB **一次 Write**；终态只回 `done OK`；card 短 GSAP 惯用 | 合同 | worker.md / card / SKILL / `_worker_prompt` |

样本 dry-run（packets，不覆盖项目）：01 blueprint **29.2KB → 8.8KB**；8 帧 Rule index 均非空（推断）。

### B. frame-visual（rule 引用强制）

| ID | 内容 | 落点 |
|---|---|---|
| V-1 | worker 输出必填 `- rules:`；候选 id 来自完整 blueprint mapping；全量合法 id 列表 | `visual._build_worker_prompt` |
| V-2 | 无/空/全未知 rules → `_diagnose_worker_reply` 失败并重派；通过后规范化 | `visual.py` |
| V-3 | 已有 Scene 的帧缺 `- rules:` 或未知 id → gate **✗** | `storyboard-gate.mjs` |
| V-4 | SKILL / visual-design / motion-language 写成硬要求 | aiflow-build-frame-visual 文案（含 `.claude` 同步） |

## 3. 环境开关速查

```bash
# packets
HTMLVIDEO_FRAME_PACKETS_NARROW_BLUEPRINT=0   # 关 blueprint 裁剪
HTMLVIDEO_FRAME_PACKETS_INFER_RULES=0        # 关 rule 推断兜底

# frame-html effort A/B（默认勿改；见 effort-medium-ab 文档）
HTMLVIDEO_FRAME_HTML_EFFORT=medium
HTMLVIDEO_FRAME_HTML_MAX_OUTPUT=64000
```

## 4. 验证

```bash
python3 -m unittest test.test_frame_html_driver test.test_claude_cli test.test_visual_driver -q
node --test test/test_frame_packets.mjs
# 历史样本无 - rules: 时应 GATE FAIL（需把 status 临时改回 outline，或复制后再测）
```

下午落地时：`test_frame_html_driver`+`test_claude_cli` 88 OK；`test_visual_driver` 58 OK；packets node 测通过。

## 5. 未做 / 风险

- 未改 `frame_html` 默认 effort；medium 需按 A/B 文档至少 3 个项目后再切。  
- 未回写样本 `STORYBOARD.md` / `.dispatch.md`；下一完整流水线才会带上 `- rules:` 与新 packets。  
- rule 推断可能偏多/偏漏；visual 合规后应以显式 `- rules:` 为主，推断为兜底。  
- Scene 行内反引号 id 仍主要靠 prompt，gate 硬检的是 `- rules:` 字段。  
- worker card ≈7993B，贴 8000 预算上限。

## 6. 相关文档

- `docs/2026-11-22-frame-html-effort-medium-ab.md` — effort A/B 跑法  
- `docs/2026-11-22-frame-html-critical-path-hardening.md` — 凌晨关键路径硬化（前序）  
- `docs/2026-11-22-frame-html-hardening-file-list.md` — 凌晨 P0–P2 文件清单  
- `docs/2026-11-22-frame-visual-p0p1p2-hardening.md` — visual 早间硬化（前序；本轮 V-1..V-4 为其续）

---

## 7. 修改文件列表（本轮）

共 **25** 个路径（含 `.claude/skills` 同步副本、新建文档，以及两份前序文档的续篇链接）。

### Driver / CLI / Gate

```
html_utility/claude_cli.py                 # frame_html deny Skill
html_utility/frame_html.py                 # recipes 行首匹配；一次 Write / done OK only 措辞
html_utility/visual.py                     # - rules: 必填、候选 id、验收与规范化
scripts/storyboard-gate.mjs                # 缺/未知 - rules: → ✗
```

### Packet builder

```
skills/hyperframes-core/scripts/lib/frame-packets-core.mjs
.claude/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs   # 与 skills/ 同步
```

### frame-html 合同

```
skills/aiflow-build-frame-html/SKILL.md
skills/aiflow-build-frame-html/sub-agents/frame-worker.md
skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
.claude/skills/aiflow-build-frame-html/SKILL.md
.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker.md
.claude/skills/aiflow-build-frame-html/sub-agents/frame-worker-card.md
```

### frame-visual 合同

```
skills/aiflow-build-frame-visual/SKILL.md
skills/aiflow-build-frame-visual/references/visual-design.md
skills/aiflow-build-frame-visual/references/motion-language.md
.claude/skills/aiflow-build-frame-visual/SKILL.md
.claude/skills/aiflow-build-frame-visual/references/visual-design.md
.claude/skills/aiflow-build-frame-visual/references/motion-language.md
```

### 测试

```
test/test_claude_cli.py
test/test_frame_html_driver.py
test/test_frame_packets.mjs
test/test_visual_driver.py
```

### 文档

```
docs/2026-11-22-frame-html-effort-medium-ab.md          # 新建（A/B）
docs/2026-11-22-thinking-reduction-and-visual-rules.md  # 本文件（新建）
docs/2026-11-22-frame-html-critical-path-hardening.md   # 续篇链接
docs/2026-11-22-frame-visual-p0p1p2-hardening.md        # 续篇链接
```
