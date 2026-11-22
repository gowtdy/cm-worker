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

## 仍未做

- 真实项目端到端再跑 `6b01e0245007d730` 的 verify（修好后应给出 #4 content_overlap actionable，而不是空转）  
- verify `max_output` 再降到 4–6k（原 Opus P2 建议）  
- 对「真实排版碰撞 vs 设计重叠」的人工/下游分流（避免机械 `data-layout-allow-overlap`）
