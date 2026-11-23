# 字体 `@import` 确定性注入（P0 / P1）

> 日期：2026-11-21  
> 状态：已落地（Grok 初版 + Opus 复核修补）  
> 触发案例：`/app/videos/2026-11-21/userclm17919893695531772/6b01b4c0170e63e3/`  
> 相关：verify 两轮 fix 合计约 25–27m，其中字体单项约 6–8m；并列缺陷为 `data-duration="Ns"`（另案已修）

## 1. 问题与目标

### 现象

frame-html 产出的 8 帧 + `captions.html` 在 verify 首次全量 lint 时爆出约 **9 个** `font_family_without_font_face`（Bodoni Moda / Space Grotesk）。verify 模型两轮修字体 + transitions，墙钟约 **27m**；真正批量 Edit 加 `@import` 不到 1 分钟，其余耗在读源码、深思考、找 URL。

### 根因（合同 × CLI 版本漂移）

| 来源 | 说法 | 现实 |
|---|---|---|
| `frame-worker.md` / card（旧） | frame.md 阶梯内字体可 **omit `@import`**，内嵌字体包负责 | ❌ |
| 源码 `fontAliases.ts`（未进已装包） | 已收录 `bodoni moda` / `space grotesk` | ⚠️ 未发布 |
| 已装 CLI **0.7.76** lint | 不在别名表 → **error** | ✅ 现行真相 |
| verify playbook（旧） | 假设「帧里已有 `@import`，抄到 captions」 | ❌ 前提不成立 |

`font_family_without_font_face` 触发条件：CSS 用了非通用字体，且不同时满足「本文件 `@font-face` / Google `@import`·`<link>` / `FONT_ALIAS_KEYS`」。`google_fonts_import` 只是 warning（加 `@import` = 用 warning 换 error）。

selfcheck / assemble 原先不查字体；captions 仅在本地有 `assets/fonts` 时写 `@font-face`，否则不补 Google `@import`。

### 目标

1. **零模型**消掉常规字体 error：确定性脚本注入标记过的 `@import`（或 brand `@font-face`）。  
2. 合同改为 **driver-owned**：worker 只写族名，不写 `@import`。  
3. verify 扫描前再跑一遍（幂等），模型只见脚本处理不了的残留。  
4. playbook / actionable 给出可粘贴完整标记块。

非目标：升级 CLI 发版（P2）；改 duration 逻辑（已另做）。

---

## 2. 架构

一句话：**以 `frame.md` 字体族为准**，减去本文件已声明 / 已导入 / CLI 别名，把剩余以托管块写入 frames 与 captions 的第一个 `<style>` 顶部；可选 prune 非主位死回退（如 Didot）。

```text
[并行]
  frames: workers → selfcheck → run_font_imports(scope=frames) → assemble
  captions: captions.mjs → run_font_imports(scope=captions)

verify_frame → verify-all.mjs
  0. font-imports --scope all     ← 不致命；HTMLVIDEO_FONT_IMPORTS=0 可关
  1. transitions inject (idempotent)
  2. verify-report（lint 已见注入后文件）
  3. gate pass → snapshot + pixels
```

托管标记：

```css
/* aiflow:font-imports v1 */
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda&family=Space+Grotesk&display=swap');
/* aiflow:font-imports end */
```

- 族名在 URL 中按字母序（不区分大小写），保证输出确定。  
- 裸族名：lint 只认族名；渲染编译器按族名自行拉字重。  
- 幂等：先删托管块 → 重算 → 再插入；二次运行 `changed=0`。

---

## 3. 文件清单

### 新建

| 路径 | 职责 |
|---|---|
| `scripts/lib/fonts.mjs` | 解析 frame.md、读 CLI 别名、plan/apply、prune、canonicalImport |
| `scripts/font-imports.mjs` | CLI 入口 |
| `html_utility/fonts.py` | `run_font_imports()`；永不抛；logger 可为 None |
| `test/test_font_imports.mjs` | 核心行为单测 |
| `test/test_fonts_hook.py` | Python 挂点单测 |

### 挂点 / 流水线

| 路径 | 改动 |
|---|---|
| `html_utility/frame_html.py` | assemble **前** `scope=frames` |
| `html_utility/audio.py` | caption 成功后 `scope=captions` |
| `scripts/verify-all.mjs` | **prepare 阶段** 第 0 步(每会话一次) `scope=all` |
| `html_utility/verify.py` | `_FIX_PLAYBOOK` 残留语义；docstring 注明 step 0 |
| `scripts/captions.mjs` | 无本地字体时预写同一标记 `@import` |
| `scripts/frame-selfcheck.mjs` | `font_out_of_ramp` / `font_dead_fallback` 默认 WARN |
| `scripts/verify-report.mjs` | actionable：完整粘贴块 + drop 列表 |

### 合同（`skills/` 与 `.claude/skills/` 两份同步）

| 路径 | 改动 |
|---|---|
| `…/aiflow-build-frame-html/sub-agents/frame-worker.md` | Fonts = driver-owned |
| `…/aiflow-build-frame-html/sub-agents/frame-worker-card.md` | 缩短版；≤8000 字节 |
| `…/aiflow-verify-frame/SKILL.md` | Known failure #3 + 命令块 |

---

## 4. CLI 与环境变量

```bash
node /app/scripts/font-imports.mjs --videodir <dir> \
  [--scope frames|captions|all] \
  [--dry-run] [--no-prune] [--strip] [--json]
```

| 退出码 | 含义 |
|---|---|
| 0 | 成功（无 unresolved） |
| 1 | 有 unresolved（如主位孤儿字体）；不视为基础设施失败 |
| 2 | 用法错误 |

| 变量 | 作用 |
|---|---|
| `HTMLVIDEO_FONT_IMPORTS=0\|false\|off\|no` | 跳过 Python 挂点与 verify-all prepare 阶段 |
| `AIFLOW_FONT_PRUNE=0` / `--no-prune` | 不删死回退 |
| `AIFLOW_HF_CLI_DIST` | 覆盖 CLI dist 路径（读别名用；优先 `FONT_ALIAS_MAP2`） |
| `FRAME_SELFCHECK_STRICT_FONTS=1` | 字体 WARN 升为 FAIL（默认不升） |

回滚产物：`--strip` 删除托管块（已 prune 的回退名不还原）。

---

## 5. 行为细则

### 注入集合

1. 从 `frame.md` 的 `typography:` 块解析 `fontFamily`（引号值、逗号拆、去通用族、保序去重）。  
2. 每文件：用 lint 同口径看 used / declared / google（计算覆盖时排除托管块）。  
3. 仍未覆盖且在 frame.md 中 → 进 `@import`（或 brand `@font-face`）。  
4. CLI 别名可读 → 别名字体不注入；别名不可用 → **保守模式**（注入全部 frame.md 非通用族，并关闭 prune）。

### prune

仅删除**非主位**且「不在 frame.md / 无 `@font-face` / 非别名」的回退；保留 `!important` 与空白。主位孤儿记 unresolved，exit 1，留给模型。

### selfcheck（P1）

- `font_out_of_ramp`：主位不在 frame.md 且非别名 → WARN。  
- `font_dead_fallback`：回退位死字体 → WARN。  
- **不检查**缺 `@import`（driver 负责）。默认 WARN 不改退出码。

---

## 6. 验收记录（落地时）

| 项 | 结果 |
|---|---|
| `node --check` | 通过 |
| `test/test_font_imports.mjs` | 18/18 |
| `test.test_fonts_hook` | 6/6 |
| `testCard()` | 7765 字节 OK |
| 真实项目去 googleapis 后 lint | `font_family_without_font_face` **9 → 0** |
| 二次运行 | `changed=0`，哈希不变 |
| 两份 skills `cmp` | 一致 |

说明：`test_frame_html_driver` 若干失败为既有骨架门禁问题，与字体无关（`HTMLVIDEO_FONT_IMPORTS=0` 后仍失败）。

---

## 7. Opus 相对初版的修补

1. `canonicalImport` 族名字母序，与 SKILL 示例一致。  
2. prune 保留 `!important`。  
3. `loadCliAliases` 优先 `FONT_ALIAS_MAP2`（与 lint 同源）。  
4. verify-report 粘贴完整 v1/end 块；去掉硬编码 Bodoni/Didot；路径 `resolve`。  
5. Known failure #3 / `_FIX_PLAYBOOK` 与完整标记块对齐。

---

## 8. 后续（P2，未做）

1. 重建/升级 HyperFrames CLI，使已装别名含 Bodoni / Space Grotesk；注入集合自动缩小。  
2. CI / doctor：断言 `frame.md` 预设字体 ⊆ 已装 `FONT_ALIAS_KEYS`。  
3. 确认后可评估是否撤掉 Google `@import`、回到「omit import」。

---

## 9. 快速对照命令

```bash
# 注入 / 预览 / 清理
node /app/scripts/font-imports.mjs --videodir /abs/proj
node /app/scripts/font-imports.mjs --videodir /abs/proj --dry-run
node /app/scripts/font-imports.mjs --videodir /abs/proj --strip

# 单测
node /app/test/test_font_imports.mjs
python3 -m unittest test.test_fonts_hook -v

# 合同同步检查
for d in \
  aiflow-build-frame-html/sub-agents/frame-worker.md \
  aiflow-build-frame-html/sub-agents/frame-worker-card.md \
  aiflow-verify-frame/SKILL.md
do cmp /app/skills/$d /app/.claude/skills/$d || echo DRIFT $d; done
```
