# transcribe-worker 迁移 + uscm /api/transcribe 异步化 设计

**日期:** 2026-10-29
**分支:** feature/transcribe-worker

## 背景与目标

把 `video/transcribe-worker` 迁到新 commit-worker 架构（对照 `tts/indextts-worker` 已完成迁移），并把 `uscm` 的 `POST /api/transcribe` 从旧框架（同步轮询 + MySQL 工具记录）迁到异步 JSON（对照同一文件的 `/api/genaudio` / `/api/extractaudio`）。

## commit-worker 架构五要素（本次迁移要落实的行为）

| # | 维度 | 旧 | 新 |
|---|------|----|----|
| 1 | 状态上报 | worker 直写 MySQL（`sql.mysql_db.update_ip_tool_record`） | HTTP `POST /inapi/update_task` 到 UserServer（`utility.userserver_client.update_task_status`，带 `internal_auth` Bearer） |
| 2 | 队列 | `TTS_RABBITMQ_*`、任务名 `gpu_transcribe_tasks.transcribe_whisper`、queue `gpu_transcribe_whisper_tasks` | `US_RABBITMQ_*`、任务名 `task_transcribe_whisper`、queue `us_transcribe_whisper_queue` |
| 3 | 入参 | `reqid` + `email` 分叉 | `task_id` + `domain`，统一 `update_task_status(logger, task_id, domain, ...)` |
| 4 | 日志上下文 | 无 | `set_reqid(task_id)` / `clear_reqid()`（ContextVar + `ReqIdFilter`） |
| 5 | 派发 | uscm 同步等结果（`get_tool_out_path_by_task_id`）| 异步 `.delay()` 返回 `reqid`，结果由 check 接口查 |

## 对照基准：indextts-worker 迁移实际改动

- `celery_worker.py`:`TTS_RABBITMQ_*` → `US_RABBITMQ_*`，任务路由改为 `task_*` → `us_*_queue`
- `gpu_indextts_tasks.py`:删 `sql.mysql_db`、删 `is_eapi` 分叉、加 `domain`、`update_task_status`、`set_reqid/clear_reqid`、`SyncJob.domain`
- `utility/file_delivery.py`:MySQL → `update_task_status`；`SyncJob` 加 `domain`；sync 循环加 `set_reqid/clear_reqid`
- `utility/log.py`:加 reqid ContextVar 上下文
- 新增 `utility/internal_auth.py`、`utility/userserver_client.py`；删 `sql/mysql_db.py`（连同 `config/db_conf.py`）
- `docker-compose.yml`:`env_file` → `.env_usmqdb`；启动脚本 `-Q` → `us_*_queue`

## 迁移范围

### A. transcribe-worker（`video/transcribe-worker`）

- 拷贝（verbatim，来自 `tts/indextts-worker`）：`utility/internal_auth.py`、`utility/userserver_client.py`、`utility/log.py`
- 修改（贴 indextts 的 diff 但保留 transcribe 专属逻辑）：
  - `utility/file_delivery.py`（MySQL → update_task_status、SyncJob.domain、set/clear reqid；保留 transcribe 的 `resolve_delivery_transport`）
  - `utility/util.py`（重写为干净的 `gen_res_dict`，去 numpy/gen_reqid）
  - `celery_worker.py`（US_RABBITMQ_*、任务路由）
  - `start_transcribe_worker.sh`（`-Q us_transcribe_whisper_queue`）
  - `gpu_transcribe_tasks.py`（任务名重建、`domain`、`update_task_status`、`set_reqid/clear_reqid`、`SyncJob.domain`、去 `first_ip`）
- 删除：`sql/mysql_db.py`、`config/db_conf.py`
- 修改：`docker-compose-whisper.yml`（`env_file` → `.env_usmqdb`）
- 保持不动（transcribe 专属）：`transcriber/*`、`config/comm_config.py`、`config/model_conf.py`、`utility/quic_http_transport.py`（保留 `"transcribe": "/transcribe"` biz_type）、`utility/samearea_http_transport.py`、`utility/socat_rsyc_util.py`、`utility/module_enum.py`、`utility/file_sync_config.py`

### B. uscm `/api/transcribe`

- `reqid = gen_reqid(userid, tm)`（删 `genid`/`userid_hash`）
- 解析 `domain`；`validate_request` 用主 `logger` + 传 `domain`/`request`
- 保留 format 白名单与 `email == video_tts_transcribe` → `validate_internal_tts_url` 分支（否则 `validate_audio_url`）
- `validate_signature` 去 `subscript`/`referer`
- `add_ip_tool_record` → `post_generate_add(..., ModuleType.TRANSCRIBE, task_cmd='/api/transcribe', ...)`
- `resolve_transcribe_task(language).delay(...)` → `resolve_transcribe_engine(language)` 选引擎（v1 恒 whisper）→ `transcribe_whisper.delay(upfileinfo, language, output_format, task_id=reqid, userid=userid, email=email, domain=domain)`
- 删除同步等待（`get_tool_out_path_by_task_id` / `encrypt_url` / `uri`/`enurl`），异步返回 `{"ret":0,"msg":"ok","reqid":reqid}`

## 已确认决策

1. **异步化**：符合预期（`/api/transcribe` 立即返回 `reqid`）。
2. **保留** `email == video_tts_transcribe` 内部转写分支（`validate_internal_tts_url` + worker 的 localnet http 派发）。
3. **env_file**：切到 `/home/aigc/data/env/.env_usmqdb`（含 `US_RABBITMQ_*` `US_COMMON_USERSERVER_HOST/PORT` `INAPI_AUTH_TOKEN`）。