# audio-worker 迁移 + uscm /api/extractaudio 异步化 设计方案（对齐 indextts-worker / sound）

- 日期：2026-10-28
- 分支：`feature/audio-worker`
- 状态：已确认（设计 3 项决策已由维护者拍板）

## 0. 背景与结论先行

`tts/indextts-worker` 与 `sound/stable-sound-worker` 已迁到新架构；本次把 `video/audio-worker`
（旧架构拷贝而来）对齐到同一套模式，并把 `uscm` 里仍为旧框架的 `POST /api/extractaudio`
对照已迁移的 `/api/genaudio` 改成异步。

先例文档（同一套模板）：
`docs/superpowers/specs/2026-10-28-stable-sound-worker-migration-design.md`。

## 1. 目标 / 非目标 / 反目标

### 目标
1. 把 `video/audio-worker` 从旧架构迁到新架构，对齐 `tts/indextts-worker` / `sound/stable-sound-worker`。
2. 把 `uscm` 的 `/api/extractaudio` 从同步 form 改成异步 JSON，对照 `/api/genaudio`。

### 非目标（本次不做）
- 公共库抽取（沿用 sound 已定方案，留作后续独立任务）。
- 改动 indextts-worker / sound-worker 及 uscm 现有 `/api/genaudio`、`/api/gensound` 的行为。
- 改动音频提取/转码逻辑本身（`split_audio`、`convert_to_mp3` 的 ffmpeg 回退策略保留）。

### 反目标
- 不再把 MySQL（`sql/mysql_db.py`）当作任务状态回写的常规路径。
- 不再让 `/api/extractaudio` 同步阻塞等待结果。

## 2. 新旧架构核心差异

| 维度 | 旧（audio） | 新（对齐 indextts / sound） |
|------|-------------|-----------------------------|
| worker 任务名 | `audio_tasks.extract_audio` | `task_audio_extract` |
| 消费队列 | `audio_tasks` | `us_audio_queue` |
| worker 状态回写 | MySQL `update_ip_tool_record` | HTTP `update_task_status`，带 `domain` + `TaskStatus` |
| 生产者落库 | uscm `add_ip_tool_record` + `get_tool_out_path_by_task_id` 同步轮询 | uscm `post_generate_add`（异步，立即返回） |
| 接口形态 | `POST /api/extractaudio` form，同步返回 `uri`/`enurl` | `POST /api/extractaudio` JSON，异步返回 `reqid` |
| 环境变量 | `TTS_RABBITMQ_*` / env_file `.env_mqdb` | `US_RABBITMQ_*` / `US_COMMON_USERSERVER_*` / `INAPI_AUTH_TOKEN` / `.env_usmqdb` |

## 3. 已确认的三项决策（维护者拍板）

1. **接口形态**：对齐 genaudio，`/api/extractaudio` 改为 JSON + `userid`；签名对象为 `upurl`；异步只返回 `reqid`，去掉 `uri`/`enurl`。
2. **运行依赖**：base 镜像 `audio-extract-image:0.4` 已含 `requests`、`fastapi`；不新增 `requirements.txt`，不改 Dockerfile 安装步骤。
3. **环境变量**：`docker-compose.yml` 的 `env_file` 切到 `/home/aigc/data/env/.env_usmqdb`。

## 4. 具体改动

### 4.1 audio worker（`video/audio-worker/build_assets/`）

#### 4.1.1 `celery_worker.py`
- 环境变量统一为 `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
- `Celery("audio_worker", broker=f"amqp://...US_RABBITMQ_*...", backend="rpc://")`。
- `task_routes = {'task_audio_extract': {'queue': 'us_audio_queue'}}`。

#### 4.1.2 `start_audio_worker.sh`
- `-Q audio_tasks` → `-Q us_audio_queue`（`-A audio_tasks` 不变）。

#### 4.1.3 `audio_tasks.py`
- 任务注册：`@celery_app.task(name='task_audio_extract')`。
- 函数签名收窄为：
  ```python
  def extract_audio(upfileinfo, task_id='0', email='', domain=''):
  ```
  去掉 `first_ip`（仅生产侧留档/日志用，不再进 worker），新增 `domain`（回写状态用）。
- 用 `set_reqid(task_id)` / `finally: clear_reqid()` 包裹整段。
- `update_ip_tool_record(task_id, ..., TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`。
- `SyncJob(..., transport=get_file_sync_transport(), domain=domain)`。
- 输入/入队/异常失败均走 `update_task_status(... FAILED)` + `return`。
- 保留 `split_audio` / `convert_to_mp3`（`utility/audio_util.py`）/ `get_remote_sepaudio_dir`、
  `short_uri_prefix = f'/sepaudio/{update_str}/{userip}'`、`enqueue_delivery_job` 等主要逻辑。
- 返回 `f"ExtractAudioCompleted: {task_id}"`（保持 celery 返回值习惯）。

#### 4.1.4 `utility/` 对齐（以 sound 已迁移版为蓝本）
- 新增 `internal_auth.py`：从 sound/tts 拷贝（提供 `bearer_auth_header`）。
- 新增 `userserver_client.py`：从 sound/tts 拷贝 worker 侧版本（`post_update_task` / `update_task_status`）。
- 覆盖 `log.py`：从 sound/tts 拷贝带 reqid 上下文的版本（`set_reqid`/`clear_reqid`/`setup_logger`/`logset`）。
- 覆盖 `util.py`：只保留修正后的 `gen_res_dict`（现版本有"原地改传入 dict"的 bug）。
- 覆盖 `file_delivery.py`：用 sound 版改造（见下）。
- `module_enum.py`：保持不变（内容已是正确的新版，含 `TaskStatus`/`ModuleType`/`ToolType`）。
- `file_sync_config.py`、`audio_util.py`、`quic_http_transport.py`、`socat_rsyc_util.py`、`sync_util.py`：保持不变。

`file_delivery.py` 相对现状的差异（对齐 sound）：
- `SyncJob` 增加 `domain: str = ""` 字段。
- 顶部 `from utility.log import clear_reqid, set_reqid`、`from utility.module_enum import TaskStatus`、
  `from utility.userserver_client import update_task_status`。
- `complete_sync_job` 改走 `update_task_status(logger, job.task_id, job.domain, ...)`，移除 `from sql.mysql_db import update_ip_tool_record`。
- `_worker_loop` 增加 `set_reqid`/`clear_reqid`。
- audio 无 VIDEO 路径；`extract_audio` 直接 `get_file_sync_transport()`，不引入 tts 的 `resolve_delivery_transport`/`VIDEO_SCRIPT_TTS` 分支（可删掉现有未使用的 `resolve_delivery_transport` shim）。

#### 4.1.5 删除/停用
- 删除 `sql/mysql_db.py` 与 `config/db_conf.py`（迁移后无引用）。
- `config/comm_config.py` 保留（`get_remote_sepaudio_dir` 仍在用）。
- `test_file_delivery.py`：现有版本引用 `mysql_db.update_ip_tool_record`，需同步改为引用 `utility.file_delivery.update_task_status`（对齐 sound 的同名测试），否则删除。

#### 4.1.6 `docker-compose.yml`
- `env_file: /home/aigc/data/env/.env_mqdb` → `/home/aigc/data/env/.env_usmqdb`。
- 其余不变；不新增 `requirements.txt`、不改 Dockerfile。

### 4.2 uscm（`uscm/build_assets/`）

#### 4.2.1 `tasks_interface.py`
- 无需改动——已定义 `audio_extract = us_celery_app.signature('task_audio_extract')` 及路由
  `task_audio_extract → us_audio_queue`。

#### 4.2.2 `uscm_server.py` 的 `/api/extractaudio`
- import 增加 `audio_extract`：
  `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract`。
- body 从 `await request.form()` 改为 `await request.json()`，字段：`upurl`、`userid`、`email`、`subscript`、`domain`、`tstamp`、`snature`。
- `reqid = gen_reqid(userid, tm)`；`first_ip`/`today`/`datetime` 同 genaudio。
- 校验顺序（对齐 genaudio，签名对象为 `upurl`）：
  - 空 `upurl`：`len(upurl.strip())==0` → 返回 `gen_res_dict(1, ...)`。
  - `validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger)` → `(ret, msg, upfileinfo)`。
  - `validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, userid, email, ModuleType.AUDIO_EXTRACTOR)`。
  - `validate_request(logger, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.AUDIO_EXTRACTOR, domain, request)`。
- `post_generate_add(logger, reqid, first_ip, email, userid, domain, generate_content, real_ip, ModuleType.AUDIO_EXTRACTOR, task_cmd='/api/extractaudio', task_req=task_req)`。
- `audio_extract.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
- 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`；删除同步轮询 `get_tool_out_path_by_task_id`、`encrypt_url`、返回字段 `uri`/`enurl`。
- 日志统一走全局 `logger`，删除 `logger_audio_extract`。

  结果查询走共享 UserServer（`generateadd` 记录 `task_id=reqid` + `generate_type=audio_extractor`，worker 完成后 `/inapi/update_task` 回写 `task_status` 与 `out_path`）。本仓库无常驻客户端 check 接口，与 TTS/sound 同路径靠 `reqid` 查询，不新增端点。

## 5. 留给实现者的决定（实现时定，无需回问）
- `generate_content` 取值：用 `upfileinfo['relative_path']`（含 `update_str`/文件名，比裸 `upurl` 更有留档价值）。
- `task_req` 存 `{"first_ip": first_ip, "userid": userid, "email": email, "upurl": upurl}`（`json.dumps(..., ensure_ascii=False)`）。
- 是否保留 `file_delivery.py` 中未使用的 `resolve_delivery_transport` shim：建议删除（对齐 sound）。
- `test_file_delivery.py`：优先对齐 sound 版重写；若维护成本高则删除。

## 6. 构建与部署
- audio-worker 重建镜像（`build_assets` 变更；base 镜像不变，无需新装依赖）。
- uscm 重建镜像（`uscm_server.py` 变更）。
- 切换策略：**干净硬切**。旧 producer（同步 form）与旧 worker（队列 `audio_tasks`、MySQL 状态回写）同批下线，
  队列 `audio_tasks → us_audio_queue`、状态回写 MySQL → UserServer 一次切完；不保留 `sql/mysql_db.py`、不排空旧队列、不做共存/回滚窗口。
  切换瞬间旧队列未消费任务会被丢弃，已确认接受。

## 7. 验证
1. audio-worker 启动后从 `us_audio_queue` 消费 `task_audio_extract`，日志出现 reqid 上下文。
2. 调 `POST /api/extractaudio`（JSON + userid + upurl）→ 立即返回 `{"ret":0,"msg":"ok","reqid":...}`；
   UserServer 侧查到 `generateadd` 记录；任务完成后 `/inapi/update_task` 收到 `task_status=complete` 且
   `out_path` 为 `/outdt/sepaudio/<update_str>/<userip>/<file>` 形态。
3. 失败路径（空 upurl、非法 upurl、sync 入队失败、转码失败）通过 `update_task_status(... failed ...)` 回写失败状态。