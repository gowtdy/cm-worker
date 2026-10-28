# stable-sound-worker 迁移设计方案（对齐 indextts-worker 新架构）

- 日期：2026-10-28
- 分支：`feature/sound-worker`
- 状态：待确认

## 1. 目标 / 非目标 / 反目标

### 目标
1. 把 `sound/stable-sound-worker` 从旧架构迁到新架构，对齐已迁移完成的 `tts/indextts-worker`。
2. 在 `uscm` 新增异步 `/api/gensound`，对照已迁移的 `/api/genaudio`。

### 非目标（本次不做）
- 公共库抽取（已定方案 A，留作后续独立任务）。
- 改动 `indextts-worker` 与 uscm 现有 `/api/genaudio` 的行为。
- 改动声学模型推理与翻译逻辑本身（StableAudio 的 `generate`、`detect_language`、`translate_text` 保留）。

### 反目标
- 不再把 MySQL（`sql/mysql_db.py`）当作任务状态回写的常规路径。

## 2. 新旧架构核心差异（本次要改的对照）

| 维度 | 旧（sound） | 新（对齐 indextts） |
|------|-------------|---------------------|
| 任务名 | `gpu_sound_tasks.gpu_gen_sound` | `task_gen_sound` |
| 消费队列 | `gpu_sound_tasks` | `us_sound_queue` |
| 状态回写（worker） | MySQL `update_ip_sounds_record` | HTTP `update_task_status`，带 `domain` + `TaskStatus` |
| 生产者落库 | uscm `add_ip_sounds_record` + `get_out_path_by_id` 轮询（同步） | uscm `post_generate_add`（异步，立即返回） |
| 接口形态 | `POST /sapi/gensound` form，同步返回 `uri`/`enurl` | `POST /api/gensound` JSON，异步返回 `reqid` |
| 环境变量 | `TTS_RABBITMQ_*` | `US_RABBITMQ_*` / `US_COMMON_USERSERVER_*` / `INAPI_AUTH_TOKEN` |
| worker 启动 | `celery -A gpu_sound_tasks ... -Q gpu_sound_tasks` | `celery -A gpu_sound_tasks ... -Q us_sound_queue` |

## 3. 具体改动

### 3.1 sound worker（`sound/stable-sound-worker/build_assets/`）

#### 3.1.1 `celery_worker.py`
改为与 indextts 一致的结构：

- 环境变量统一成 `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
- `Celery("sound_worker", broker=amqp://US_RABBITMQ_*, backend="rpc://")`。
- `task_routes = {'task_gen_sound': {'queue': 'us_sound_queue'}}`。
- 删除文件末尾的 `import gpu_sound_tasks`（新架构由任务模块反向 `from celery_worker import celery_app`）。

#### 3.1.2 `gpu_sound_tasks.py`
- 任务注册：`@celery_app.task(name='task_gen_sound')`。
- 函数签名收窄为：
  ```python
  def gpu_gen_sound(text, length=15, num=1, userid=0, task_id='0', email='', domain=''):
  ```
  去掉 `today/datetime/random_number/first_ip/real_ip/ret/cnt`（`today`/`datetime` 由 worker 内部计算；`first_ip/real_ip/ret/cnt` 仅生产侧留档用，不再进 worker）。
- 用 `set_reqid(task_id)` / `clear_reqid()` 包裹整段。
- 输入校验（text 非空、length/num 的范围钳制沿用现逻辑），失败走 `update_task_status(..., TaskStatus.FAILED)` + `return gen_res_dict(1, msg)`。
- 保留 `detect_language` / `translate_text`（`NEED_TRANSLATE` 分支）与 `model.generate(...)` / `torchaudio.save(...)` 的推理与写盘逻辑。
- `today`/`datetime`/`random_number` 在 worker 内生成：`today = time.strftime('%Y-%m-%d')`、`datetime_str = time.strftime('%Y%m%d_%H%M%S')`、`random_number = random.randint(1, 1000)`；输出文件名保持 `sound_{datetime_str}_{random_number}.mp3`（`datetime` 组件沿用旧 producer 的 `%Y%m%d_%H%M%S` 格式，URL/HTTP-header 安全）。
- 构建 `SyncJob` 时新增 `domain=domain`。
- 推理/入队用 try/except 包裹，异常时 `update_task_status(..., TaskStatus.FAILED)`；末尾保留 `torch.cuda.empty_cache()` + `gc.collect()`。
- 返回 `f"Processed: {task_id}"`（保持 celery 返回值习惯）。

#### 3.1.3 新增/覆盖 `utility/`（以 tts 版为蓝本）
- `module_enum.py`：从 tts 拷贝（提供 `TaskStatus`、`ModuleType`、`ToolType`）。
- `internal_auth.py`：从 tts/uscm 拷贝（提供 `bearer_auth_header`）。
- `userserver_client.py`：从 tts 拷贝 worker 侧版本（`post_update_task` / `update_task_status`）。
- `log.py`：从 tts 拷贝带 reqid 上下文的版本（`set_reqid`/`clear_reqid`/`setup_logger`/`logset`）。
- `file_delivery.py`：以 tts 版为蓝本改造（见下）。
- `util.py`：只保留修正后的 `gen_res_dict`（当前 sound 版有 bug，会原地改传入 dict 并返回），不引入 tts 版的 `verify_signature` 及 `voice_config` 依赖。
- `file_sync_config.py`：保持不变（已含 sound 所需的 quic/rsync 配置函数）。

`file_delivery.py` 相对现状的差异（对齐 tts）：
- `SyncJob` 增加 `domain: str = ""` 字段。
- 顶部新增 `from utility.log import clear_reqid, set_reqid`、`from utility.module_enum import TaskStatus`、`from utility.userserver_client import update_task_status`。
- `complete_sync_job` 改走 `update_task_status(..., TaskStatus.COMPLETE/FAILED)`，移除 `from sql.mysql_db import update_ip_sounds_record`。
- `_worker_loop` 增加 `set_reqid`/`clear_reqid`。
- sound 无 VIDEO 路径，故不引入 tts 的 `resolve_delivery_transport` / `VIDEO_SCRIPT_TTS_EMAIL`（sound 直接 `get_file_sync_transport()`）。

#### 3.1.4 删除/停用
- 删除 `sql/mysql_db.py` 与 `config/db_conf.py`（迁移后无引用；`db_conf.py` 仅被 `mysql_db.py` 使用）。

#### 3.1.5 `requirements.txt`
当前为空（依赖装进 base 镜像 `stable-sound-image:0.1`）。迁移后新增运行时依赖：
- `requests`（`userserver_client` 懒加载导入）
- `fastapi`（`internal_auth` 顶层 `from fastapi import ...`）

需重建 sound 镜像（Dockerfile 已 `pip install -r requirements.txt`）。

#### 3.1.6 `start_worker.sh`
celery 命令 `-Q gpu_sound_tasks` → `-Q us_sound_queue`（`-A gpu_sound_tasks` 不变）。

### 3.2 uscm（`uscm/build_assets/`）

#### 3.2.1 `tasks_interface.py`
无需改动——已定义 `gpu_gen_sound = us_celery_app.signature('task_gen_sound')` 及路由 `task_gen_sound → us_sound_queue`。

#### 3.2.2 `uscm_server.py`
- import 增加 `gpu_gen_sound`（`from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound`）。
- 新增异步 `@app.post("/api/gensound")`，对照 `/api/genaudio`：
  - 读 JSON（`await request.json()`），字段：`text`、`length`（默认 15）、`num`（默认 1）、`userid`、`email`、`subscript`、`domain`、`tstamp`、`snature`。
  - `validate_text_length(text, MAX_SOUND_TEXT_LENGTH, reqid, logger)`。
  - `validate_signature(logger, timestamp, signature, text, datetime, reqid, first_ip, real_ip, userid, email, ModuleType.SOUND)`。
  - `validate_request(logger, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.SOUND, domain, request)`。
  - `generate_content` 用 `text`（无 modelcat/modelname；length/num 如需留档再扩展，见 §4）。
  - `post_generate_add(logger, reqid, first_ip, email, userid, domain, generate_content, real_ip, ModuleType.SOUND, task_cmd='/api/gensound', task_req=...)`。
  - `gpu_gen_sound.delay(text, length, num, userid, reqid, email, domain=domain)`。
  - 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`（异步，不再同步返回 `uri`/`enurl`）。

  结果查询走共享 UserServer（`generateadd` 记录 `task_id=reqid` + `generate_type=sound`，worker 完成后 `/inapi/update_task` 回写 `task_status` 与 `out_path`）。本仓库无常驻的客户端 check 接口；与 TTS 同路径，靠 `reqid` 查询，无需为本仓库新增端点。

## 4. 留给实现者的决定（实现时定，无需回问）
- `generate_content` 精确格式：建议先用 `text`；若要留档 length/num，再用 `f"{length}|{num}|{text}"`。
- `sql/mysql_db.py` / `config/db_conf.py`：按 §3.1.4 删除（如想保留死代码可留，但建议删）。
- sound 的 `requirements.txt` 是否锁定版本：建议不锁（与 base 镜像保持一致即可）。

## 5. 构建与部署
- 重建 sound 镜像（`requirements.txt` 变更）。
- docker-compose 无需改：`env_file: /home/aigc/data/env/.env_usmqdb` 已含 `US_RABBITMQ_*`、`US_COMMON_USERSERVER_*`、`INAPI_AUTH_TOKEN`。
- uscm 重建镜像（`uscm_server.py` 变更）。
- 切换策略：**干净硬切（A）**。旧 producer（`/sapi/gensound`）与旧 worker 同批下线，消费队列 `gpu_sound_tasks` → `us_sound_queue`、状态回写 MySQL → UserServer 一次切完；不保留 `sql/mysql_db.py`、不排空旧队列、不做共存/回滚窗口。切换瞬间旧队列中未消费任务会被丢弃，已确认接受。

## 6. 验证
1. sound worker 启动后从 `us_sound_queue` 消费 `task_gen_sound`，日志出现 reqid 上下文。
2. 调 `/api/gensound` → 立即返回 `reqid`；UserServer 侧能查到 `generateadd` 记录；任务完成后 `/inapi/update_task` 收到 `task_status=complete` 且 `out_path` 为 `/outdt/sounds/tk/<today>/<file>` 形态。
3. 失败路径（空 text、同步入队失败）能通过 `update_task_status(... failed ...)` 回写失败状态。