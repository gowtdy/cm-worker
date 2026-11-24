# frame-html effort=medium × 64k A/B（2026-11-22）

目的：验证 frame_html worker 从 `high` 降到 `medium`（cap 不变 64000）能否缩短关键路径而不损质量。
**验证完成前不改默认**：`claude_cli._PROFILES["frame_html"]` 仍是 `effort=high`、`max_output=64000`；cap 不降。

## 覆盖开关（已有，无需改代码）

| env | 作用 | 生效位置 |
|---|---|---|
| `HTMLVIDEO_FRAME_HTML_EFFORT` | 普通 worker 尝试的 `CLAUDE_CODE_EFFORT_LEVEL`；白名单 `low/medium/high/xhigh/max`，非法值 WARN 后忽略 | `frame_html._base_worker_env` → `run_claude_result(extra_env=…)`，在 profile 默认之后重新应用 |
| `HTMLVIDEO_FRAME_HTML_MAX_OUTPUT` | 普通 worker 的 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`；`extra_env` 对 cap 始终权威 | 同上 |

salvage / degraded / two-phase A 仍用各自的 cap；截断重派用 `max(64000, 覆盖值)`。
单测：`test/test_frame_html_driver.py`（`MAX_OUTPUT_ENV` / `EFFORT_ENV` 用例）。
确认生效：worker 日志行 `claude skill start … skill=frame_html effort=medium`。

## 跑法

同一项目（同一 STORYBOARD / frame packets）各跑一次 build-frame-html 阶段，记下每次开始时间（UTC）：

```bash
# A：基线 high × 64k（不设 env）
unset HTMLVIDEO_FRAME_HTML_EFFORT HTMLVIDEO_FRAME_HTML_MAX_OUTPUT
# B：medium × 64k
export HTMLVIDEO_FRAME_HTML_EFFORT=medium HTMLVIDEO_FRAME_HTML_MAX_OUTPUT=64000
```

其余开关（rich skeleton / hedge / degrade / two-phase）两组保持一致。

## 对比

```bash
python3 /app/stat/skill-timing.py --since '2026-11-22 10:00' --group --agents --trunc-cap 64000
python3 /app/stat/skill-timing.py --since '2026-11-22 10:00' --agents --trunc-cap 64000   # 逐 worker 看 maxReq / trn / 1stW
```

`--trunc-cap` 默认 48000，64k cap 下需显式传 64000，否则 trn 口径偏宽。`--since` 分别取 A、B 起点（或用 `--csv` 按时间切分）。

| 指标 | 来源 |
|---|---|
| 关键路径 / 阶段墙钟 | driver 日志 `frame-html stage summary … critical_path_s= total_s=`；skill-timing `wall` |
| 单请求最长 `maxReq` | skill-timing |
| 截断 `trn` | skill-timing `trunc`；stage summary `truncations=` |
| selfcheck 失败率 | stage summary `attempts` − `frames`（重派次数）及 `failed=` |

## 成功标准

- 关键路径（`critical_path_s`）下降，`maxReq` 中位数下降；
- `trn ≈ 0`（不高于基线）；
- selfcheck 失败 / 重派率不升，`failed=0`；人工抽看 contact sheet 无明显动效退化。

## 改默认的门槛

至少 **3 个不同项目** 样本均满足上述标准，再把 `_PROFILES["frame_html"]["effort"]` 改为 `medium`（并同步 `test/test_claude_cli.py` 的期望值）。
回滚：`HTMLVIDEO_FRAME_HTML_EFFORT=high`。
