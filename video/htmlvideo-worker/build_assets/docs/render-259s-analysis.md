# render 阶段 259.3s 耗时分析与 GPU 加速方案(实测验证)

> 分析对象:htmlvideo 流水线 render 阶段(`html_utility/render.py` → `npx hyperframes render --quality high`)
> 生产基线:2026-11-17 任务 6afb004009d47552(8 帧,54.1s@30fps=1624 帧,1080×1920)**259.3s**;对照 2026-11-13 任务 6af6699ac7c3bba6(12 帧,72.2s@30fps=2167 帧,1920×1080)226.5s
> 实验:同一项目 4 组对照渲染(high/draft/--workers 16/--gpu --browser-gpu --workers 16),产物与日志在 `/tmp/hf-x/`
> 分析日期:2026-11-17

## 结论先行

**render 阶段 85% 的时间花在"软件截图逐帧采集"(6 个 worker,SwiftShader 软渲染),而机器上的 Tesla V100 从未被使用。给 render.py 加 3 个 flag(`--gpu --browser-gpu --workers 16`)后,同一项目 36.8s 完成——6.9× 加速,输出质量 PSNR 43.0dB / SSIM 0.997(与软件渲染人眼无差)。**

## 一、基线阶段分解(high, 253.1s 实测)

| 阶段 | 时间窗 | 耗时 | 占比 | 说明 |
|---|---|---|---|---|
| compile + audio_process | 0–4.7s | 4.7s | 2% | 编译 + 旁白音频处理 |
| capture_calibration | 5.2–15.8s | 10.6s | 4% | 浏览器标定采样;**显式 --workers 时整体跳过** |
| worker_resolution + strategy | 15.8–20.8s | 5.0s | 2% | auto 解析 = **6 workers** |
| **capture_disk(1624 帧)** | 20.8–236.1s | **215.3s** | **85%** | screenshot 模式,每帧 ~790ms/worker |
| encode(x264 slow CRF15) | 236.1–250.3s | 14.2s | 6% | 1624 帧编码其实很快 |
| assemble + validate | 250.3–253.0s | 2.7s | 1% | 混流 + 校验 |

- capture 速率:6 workers 合计 ~7.5fps 采集 30fps 视频 = 4× 实时;前 30s 14.2fps、后段降到 ~6.5fps(转场页帧更重)。
- **auto=6 的根因**(源码 `parallelCoordinator.ts`):不是 CPU(64 核)也不是内存(62G)——是 **V8 堆预算**:默认堆 ~4GB,公式 (heap−1024MB 保留)/640MB·worker ≈ 5–6;auto 上限地板 6(`max(6, min(16, cpu/8))=8`),再被 shader-transitions 成本系数压到 6。机器 90% 的采集能力闲置。
- 每 worker ~790ms/帧 = 确定性渲染 settle 等待 + SwiftShader 软光栅,截图本身并非 CPU 瓶颈。

## 二、四组对照实验(同一项目,同一条命令族)

| 组 | 参数 | wall | capture | encode | 产物 | 结论 |
|---|---|---|---|---|---|---|
| ① 基线重放 | `--quality high`(auto 6w) | 253.1s | 215.3s | 14.2s | 11.26MB | ≈ 生产 259.3s ✓ |
| ② draft | `--quality draft`(auto 6w) | 244.1s | 212.6s | 8.1s | 6.9MB | **quality 只值 ~9s**;差异在文件体积 |
| ③ 加 worker | `--quality high --workers 16` | 159.4s | 136.6s | 13.7s | 11.25MB | capture 1.58×(次线性,SwiftShader 争抢);**另省 14s 标定** |
| ④ **GPU+worker** | `--quality high --workers 16 --gpu --browser-gpu` | **36.8s** | **14.5s**(beginFrame) | 13.5s(NVENC) | 11.03MB | **capture 14.8×、总耗时 6.9×** |

④ 的关键机理:`--browser-gpu` 把 Chrome 光栅从 SwiftShader 切到 V100 后,`forceScreenshot`(shader-transitions 强制软截图)被解除,采集模式切到 **beginFrame**(合成器帧抄录),1624 帧仅 14.5s ≈ 112 fps(3.7× 实时);`--gpu` 同时把编码切到 NVENC。

**画质验证**(① vs ④ 全片对比):PSNR 均值 43.0dB(Y 41.3)/ SSIM 0.997(Y 0.9967)——远超"视觉无差"阈值(>0.98),文件体积 11.26 vs 11.03MB 一致。

## 三、优化方案(按收益)

### P0. render.py 启用 GPU + 显式 workers(259.3s → ~37s,实测 6.9×)

```python
cmd = ["npx", "hyperframes", "render", str(project_dir), "--quality", "high"]
if gpu_available():                       # nvidia-smi -L 成功即真
    cmd += ["--gpu", "--browser-gpu", "--workers", "16"]
else:
    cmd += ["--workers", "16"]            # 软渲染兜底:~160s(1.6×)
cmd += ["--output", output_path]
```

- 12 帧 landscape 项目按 ④ 的速率外推 ~45s(约 5×)。
- 建议同时 `env NODE_OPTIONS=--max-old-space-size=8192`:显式 16 workers 不触发 advisory(条款只报 auto),但堆预算公式(1GB 保留 + 640MB×16 ≈ 11.3GB)超过默认 4GB,加环境变量消除风险。实测 ③④ 均无 OOM。
- 显式 workers 还附带省掉 10.6s+ 的采集标定。
- 风险与验收:
  1. **GPU 单卡共享**——worker 是单进程单任务,V100 只有一块;若未来并发渲染需串行/队列化 GPU 任务,或 `--max-concurrent-renders` 协同。
  2. beginFrame 采集断言确定性——本实验产物过 CLI 内置校验,建议上线前对随机 2-3 个任务跑 `verify-pixels.py` 复核(截图快照应不受影响)。
  3. 多主机部署时 `gpu_available()` 探针保证无 GPU 机回落软渲染。

### P1. 观测性补齐(近于零成本)

`render.py` 成功路径现在 `discard` CLI stdout——把最后 ~1KB 输出(log 含 `rendered in Xm Y.Zs`、workers/GPU 标注、trace 阶段耗时)打进日志,render 阶段即可纳入 skill-timing 式统计;失败路径已有 stderr 记录。

### P2. 已知交付 bug(与耗时无关,但同日志发现,须另查)

02:48:57 新任务(6afb004009d47552)完成渲染后,`file_delivery` 入队同步的是**上一个任务**的旧产物(`…/6af47d10f984844e/6af6699ac7c3bba6_20261113013627_056.mp4`,12.44MB)——本次新渲染的 11.24MB 产物未交付,`/outdt` 收到的文件名也是旧的。疑似 `htmlvideo_task.py` → file_delivery 传参串号(单 worker + 上一任务同目录残留),影响交付正确性,建议排查。

## 附:实验资产

- 产物/日志:`/tmp/hf-x/{high,draft,w16,w16gpu}.{mp4,out}`、`timestamps*.log`、`psnr.log`
- 要点出处:`/app/hyperframes/packages/engine/src/services/parallelCoordinator.ts`(sizing 常量)、`chunkEncoder.ts`(quality→preset:`draft` ultrafast/CRF80、`standard` medium/CRF18、`high` slow/CRF15)、`producer/…/captureCost.ts`(auto 决议)、`cli/src/commands/render/*`(flags)

## 实施记录(2026-11-17:P0/P1/P2 已落地,待 worker 重启)

1. **render.py(P0+P1)**:`gpu_available()` 探针(`nvidia-smi -L`)+ `build_render_cmd()`;GPU 可用追加 `--gpu --browser-gpu --workers 16`,否则纯 `--workers 16`;subprocess env 注入 `NODE_OPTIONS=--max-old-space-size=8192`;成功路径记 CLI 尾部 6 行摘要进日志;失败路径 stdout/stderr 截尾 4KB(原为全长)。
2. **htmlvideo_task.py(P2)**:删除 4 处调试硬编码——`today = "2026-11-16"`、`task_id = "6afb004009d47552"`(覆盖队列参数)、`#project_dir` 注释残留、`output_file/output_path` 指向旧任务产物(正确版本被注释);还原 `output_file = video_name` / `output_path = f'{project_dir}/{output_file}'`;存在性检查由空串判断升级为 `os.path.isfile(output_path)`。
3. **验证**:两文件 `py_compile` 通过;e2e 以 aigc 身份在真实项目目录调 `render_video`:GPU 探针 True、**36.9s** 完成、抽样 3 帧亮度 0.913/内容占 6.3%(非黑帧)、测试产物已清理。**celery worker(PID 35,02:26 启动)须重启后新代码才生效**。