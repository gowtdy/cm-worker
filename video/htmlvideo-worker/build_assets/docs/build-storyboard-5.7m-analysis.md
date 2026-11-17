# build-storyboard 单次调用 5.7 分钟耗时分析报告

> 分析对象:`/aiflow-build-storyboard` 的一次 `claude --print` 调用
> 会话:`7efbbb96-bc26-4112-b2fb-ba2381d152d3`
> 时间:2026-11-11 14:35:01.926Z → 14:40:43.719Z(总耗时 **342s ≈ 5.7 分钟**)
> 视频项目:`/app/videos/2026-11-11/userclm17919893695531772/6af47d10f984844e/`
> 调用入口:`/app/html_utility/storyboard.py` → `run_claude()`(与 `/app/scripts/run_aiflow_build_skills.py --skill storyboard` 同 prompt)
> 分析日期:2026-11-15

**一句话结论:5.7m 的 65% 是单轮扩展思考(T5 的 222.9s,effort=max + adaptive thinking);token 账本显示 94% 输出是思考 token。模型 API 链路(TTFB p50 1.78s)与工具执行(17 次共 <1s)均不构成瓶颈。**

## 数据来源

- 会话转录:`/app/log/claude/projects/-app-videos-2026-11-11-userclm17919893695531772-6af47d10f984844e/7efbbb96-bc26-4112-b2fb-ba2381d152d3.jsonl`(63 条记录,逐事件时间线)
- API 请求体:`/app/log/api-bodies/`(本会话 8 次请求,与转录轮次一一对应)
- worker 日志:`/app/log/htmlvideo_worker.log.2026-11-11.log`(14:35:01 skill start → 14:40:44 storyboard built)
- 跨会话统计:此前 build-frame-html-63m 分析会话(944f39d3)的全链路 token 统计

## 一、时间线分解(8 轮,逐轮)

| 轮次 | 时间 | 耗时 | 内容 | 可见产出 |
|---|---|---|---|---|
| T1 | 14:35:02 | 8.0s | 进程启动(0.1s)+ 首轮 TTFB | `Bash ls` |
| T2 | 14:35:11–14 | ~3s | 读项目输入:BRIEF.md → frame.md → visible-text.txt | — |
| T3 | 14:35:21–23 | ~4s | 读设计类 4 份:story-design / brief-contract / brief-format / story-spine | — |
| T4 | 14:35:26–30 | ~6s | 读格式类 3 份 + 菜单:storyboard-format / script-format / review-loop / blueprints-index | — |
| **T5** | **14:35:30→14:39:12** | **222.9s** | **单次流式响应,扩展思考——读完 blueprints-index 后推演整个分镜结构** | 1 个 thinking 块 + 1 行文本 |
| T6 | 14:39:19→14:39:58 | 39.6s | 又读 meta.json / hyperframes.json + 再思考 | Write STORYBOARD.md(10,225 字符) |
| T7 | 14:39:58→14:40:20 | 22.0s | 独立一轮再思考 | Write SCRIPT.md(3,153 字符) |
| T8 | 14:40:20→14:40:43 | 23.0s | 收尾 gate 总结 | 3,170 字符文本 |

补充事实:

- 17 次工具调用执行本身总计 **~1s**(每次 0.0–0.2s);链路不慢(历史 TTFB p50 1.78s、p99 4.11s);
- 生成侧总时长 ≈ 342 − 8(TTFB)− 1(工具)≈ **~330s**,其中 T5 独占 223s(67%);
- 首响应 8.0s、进程启动 0.1s:不存在优化空间。

## 二、Token 账本

| 指标 | 值 |
|---|---|
| input | 776,124(大头命中 cache) |
| output | **88,662** |
| 交付物(两份 md + 总结) | ≈ 5–6K tokens(**输出占比 ~6%**) |
| 思考 token | ≈ 83K(**94%**) |
| 转录留存的 thinking 块 | 53,506 字符(仅尾部,`display:omitted` 截断) |
| 流式速率 | ~270 tok/s(88.7K ÷ ~330s) |

时间 ≈ 生成 token 数 ÷ 270 tok/s;T5 一轮流了约 6 万思考 token。请求配置 `thinking:{"type":"adaptive"}`,环境 `CLAUDE_EFFORT=max` / `CLAUDE_CODE_EFFORT_LEVEL=max`,单请求 `max_tokens=32000`(思考预算不受独立约束)。

## 三、读入面:模型实际读的 8 份跨 skill 文档

第 T3/T4 轮共读 8 份 skill 参考文档,合计 **87.3 KB ≈ 2.1 万 token**,而其核心输入只有:BRIEF.md 787 B + frame.md 17 KB + visible-text **68 字节**。

| 文档 | 所属 skill | 大小 | 作用 | 自动化模式还需要吗 |
|---|---|---|---|---|
| `blueprints-index.md` | hyperframes-animation | 26.5 KB / 202 行 | 22 个 blueprint 的"菜单+选型器" | **只需菜单表,不需全文** |
| `story-design.md` | aiflow-build-storyboard | 21.0 KB / 255 行 | explainer 结构法/hook 策略/教学真相提炼 | **要规则,不要全文** |
| `brief-contract.md` | hyperframes-core | 12.1 KB / 108 行 | gate 类型、flow×storyboard→交互模式 | **只需 §1 一行规则** |
| `storyboard-format.md` | hyperframes-core | 7.8 KB / 130 行 | STORYBOARD.md 必填字段契约 | **必须,但只需字段清单+示例** |
| `brief-format.md` | hyperframes-core | 7.2 KB / 72 行 | BRIEF.md 字段语义/生命周期 | ❌ brief 已 settled |
| `review-loop.md` | hyperframes-core | 6.9 KB / 41 行 | 评审循环(§1=计划评审 gate) | **只需 autonomous 一句** |
| `story-spine.md` | hyperframes-creative | 3.3 KB / 37 行 | hook 语言、value-before-evidence | 小,可整体内联 |
| `script-format.md` | hyperframes-core | 2.8 KB / 52 行 | SCRIPT.md 格式契约 | 小,可整体内联 |

三重代价:① 3 个读轮 ≈ 25s 墙钟(每轮一次 API 往返 + 轮间思考启动);② 上下文膨胀到 77.6 万 input 量级;③ **思考放大器**——T5 的 222.9s 是"对着 255 行设计方法 + 202 行菜单做 max-effort 推演"的产物,读入面越大,adaptive thinking 越想越多。

另:第 T6 轮读的 `meta.json`(103 B)+ `hyperframes.json`(318 B)共 421 字节,纯属 SKILL.md 行 30 "read all references" 的惯性,不读不会错。

## 四、根因分析(按影响排序)

1. **max-effort 扩展思考做机械性填表**。有固定字段契约的 outline-stage 故事板不需要 8.9 万输出 token;T5 一轮 222.9s 占全时长 65%。这是决定性根因。
2. **8 份跨 skill 大文档串行读取 + 读入面过大**。直接成本 ~25s(3 轮),间接放大 T5 推演时长。
3. **分阶段多次落盘**。STORYBOARD 与 SCRIPT 两轮独立思考 + 收尾总结轮(T6+T7+T8 ≈ 85s),本可合并。
4. **惯性读**。meta.json / hyperframes.json 421 字节也读了一轮。

非根因(排除项):首次响应 8.0s、进程启动 0.1s、工具执行 <1s。

## 五、优化方案(按收益排序)

### ① 关掉/限流扩展思考(收益最大,单独即 5.7m → ~2m;配合低 effort → ~1.5m)

落点:`/app/html_utility/claude_cli.py` 的 `run_claude()` 已预留 `extra_env` 参数,`build_storyboard()` 调用时传入:

```python
rc = run_claude(project_dir, prompt, logger, extra_env={
    "CLAUDE_CODE_MAX_THINKING_TOKENS": "4000",   # 硬上限思考 token,兜底
    "CLAUDE_CODE_EFFORT_LEVEL": "low",
})
```

若 qianfan 端点支持,API 层 `thinking:{"type":"disabled"}` 更彻底(T5 预计 223s → 20–40s)。至少加 thinking cap——当前 max_tokens=32000 但思考预算不受控,cap 是防跑飞保险。

### ② 输入内联 + schema 卡(砍掉 T2–T4,收窄 T5 推演面)

落点:`/app/html_utility/storyboard.py` 的 `_build_storyboard_prompt()`:

- 直接内联 BRIEF.md(787 B)+ frame.md(17 KB)+ visible-text(68 B) 全文;
- prompt 携带一份 **3–5 KB 契约卡**,只抽自动化真正需要的部分:
  - 必填字段清单(from storyboard-format.md,~30 行)+ `status: outline` 要求;
  - SCRIPT.md 契约(from script-format.md,整体搬,2.8 KB);
  - 结构选型 + hook 规则(from story-design.md 255 行压到 ~15 行);
  - blueprint 菜单表(from blueprints-index.md,22 个 `id | 一句适用场景 | VO 形态` 紧凑表——**只搬菜单不搬详情**,原会话本就只读 index 未读任何 `blueprints/<id>.md`);
  - autonomous gate 一句(from brief-contract + review-loop)。

配套:S KILL.md 增补一句"When invoked programmatically and the prompt carries the inlined contract card, skip the Quick Reference reads entirely",交互式人工使用不受影响。

预期:第 T2–T4 三个读轮消失(直接省 ~25s),读入面 87 KB → 3–5 KB,且与 ① 叠加时 T5 收益大于两个单点相加。

### ③ 单响应双写

明确指令"同一轮里用两个 Write 并行写出 STORYBOARD.md 和 SCRIPT.md,然后直接输出 gate 总结",消灭 T7 独立思考轮(~20s)与部分 T8。

### ④ 模板预渲染(可选,治本)

Python 先写好 frontmatter / 结构骨架,模型只填叙事字段——创作面变小,思考自然变短,格式 100% 合规。

### 组合预期

①+②+③:5.7m → **1~1.5m**;仅保守加 thinking cap 也能到 ~2–3m。

## 六、验证方案(必须先做)

storyboard 是流水线最上游,质量直接决定 build-frame-visual(实测 13.6m)和 build-frame-html(实测 63.3m)是否重建:

- 同一批 BRIEF 用 **min effort vs max effort** 各跑 3–5 个任务;
- 对比指标:下游一次性通过率、frame-html 重建帧数、visual 阶段失败往返;
- 若通过率不降 → ① 是纯收益;若偶发质量波动 → 用 cap 4000–8000 的中间档;
- 可先用 6af47d10f984844e 现成项目重跑,与本次 342s 直接对比。

## 附:改动点清单与注意事项

| 文件 | 改动 |
|---|---|
| `/app/html_utility/storyboard.py` | prompt 内联输入 + 携带契约卡;`run_claude` 传 `extra_env` |
| `/app/.claude/skills/aiflow-build-storyboard/references/contract-card.md` | 新增精简契约卡(或作为 Python 常量) |
| `/app/.claude/skills/aiflow-build-storyboard/SKILL.md` | 增补自动化快路径分支 |
| `/app/scripts/run_aiflow_build_skills.py` | `STORYBOARD_PROMPT` 同步同一策略(双入口) |

**双副本注意**:`aiflow-build-storyboard` 同时存在 `.claude/skills/` 与 `hyperframes/skills/` 两处(另各有 `SKILL.md_bak` 27 KB 旧版),目录内 SKILL.md/references 的修改需 `cp -a` 同步 + diff 验证,与 build-frame-visual、verify-frame 报告既有约定一致。

## 实施记录(2026-11-15:优化 ② 已落地)

**1. 新增契约卡 `references/contract-card.md`(13.7 KB)**:从 7 份源文档抽取自动化 outline 阶段全量契约——frontmatter/帧模板/必填字段、type 枚举映射、4 结构选型、9 hook 策略、persuasion/beat 目录、22 blueprint 紧凑菜单、transition 注册表、SCRIPT.md 形制、music 规则、autonomous 输出协议(单响应双 Write + heads-up 表格)、自检清单。canonical hyperframes 先行写,`cp -a` 同步至 `.claude/skills`,diff 逐字节一致。

**2. 新增 `/app/html_utility/storyboard_contract.py`**:共享 prompt 构建器——header(禁重读 references、autonomous 声明)+ 契约卡 + 内联 BRIEF.md / frame.md / visible-text(可选 user_script.txt / VO_MODE,缺失自动跳过)+ footer(单响应双 Write + gate)。卡片缺失时降级到 legacy 读循环并 warning。

**3. 双入口接入**:
- `/app/html_utility/storyboard.py`(worker 生产路径):`_build_storyboard_prompt` 已被 `build_storyboard_prompt` 替换;
- `/app/scripts/run_aiflow_build_skills.py`:新增本地 `build_storyboard_prompt`(import 共享模块,独立部署时 try/except 回退静态 `STORYBOARD_PROMPT`)。

**4. SKILL.md 双副本**:顶部增补 "Programmatic fast path" 条款(卡已内联时跳过 Prerequisites/Quick Reference 读取),canonical 先行写 + `cp -a` + diff 一致(54→58 行)。

**实测效果**:prompt 全长 32,439 字符(~8.1K token = 卡 13.5K + BRIEF 787B + frame 17KB + visible-text 68B);模型读入面从 ~35KB 文件面降到 0 次读取,读循环 T2/T3/T4(实测 ~25s + 每轮思考启动)整体消除。**尚未真跑 A/B**(下次真实队列任务以转录回合数 + 各轮耗时闭环后补记;若 thinking 未关(① 未落地),T5 大思考轮仍会存在,但推演面已收窄一个数量级)。

**2026-11-16 追加(优化 ③ 已落地)**:

- **SKILL.md 双副本**(58→59 行):"Storyboard and Script" 节增补单响应规则——两个 Write 同一回合同时发出、不得跨回合拆分、写完不回读;Gate 行增补 "any frame with a non-empty voiceover ⇒ SCRIPT.md 必须存在" 判定;
- **contract-card.md**:Output protocol 第 2 条同步收紧(write result 已确认文件态、禁止回读);
- **storyboard_contract.py**:footer 改为强制双写措辞 + SCRIPT 存在性 gate 描述;
- **storyboard.py 新增确定性校验**(`_has_spoken_frames` + `_validate_storyboard_outputs` 扩展):任一帧 `voiceover` 非空而 SCRIPT.md 缺失 → `FileNotFoundError`(dual-write gate failed);SCRIPT.md 存在但为空 → `ValueError`。五态测试全过(spoken+script / spoken 缺 script 拦截 / 全静音无 script 放行 / 全静音空 script 拦截 / 全静音正常 script 放行)。

预期回合数:8 轮 → 3 轮(首轮双写 + 工具结果衔接 + heads-up 收尾),T7 独立思考轮(~20s)与 T6/T7 之间的拆写间隙消除。