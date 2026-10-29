# gsvtts-worker 迁移到新 commit-worker 架构 设计

**日期:** 2026-10-29
**分支:** feature/gsvtts
**基准:** 对照 `tts/indextts-worker`（已迁移完成）与 `video/transcribe-worker`（已迁移完成，同为多引擎 worker）

## 背景与目标

把 `tts/gsvtts-worker`（旧架构拷贝，GPT-SoVITS + Fish-Speech 引擎）迁到新 commit-worker 架构，并把 `uscm` 的 `POST /api/genaudio` 中 gsvtts 分支（非 index-tts 分支）从旧 `.delay()` 同步改为新签名。

与 indextts / transcribe 的差异：gsvtts 引擎代码复杂得多（`fish_speech/`、`voice_train/`、`tools/` 且带 `t` 私有模型与 `.eapi` 分支），迁移只动「状态上报 / 队列 / 任务名 / 入参 / 日志 / 派发」六层，引擎推理代码（`fish_tts`、`get_tts_wav`、`voice_train` 等）一律不动。

## commit-worker 架构五要素（本次要落实的行为）

| # | 维度 | 旧（gsvtts） | 新（对齐 indextts） |
|---|------|----|----|
| 1 | 状态上报 | worker 直写 MySQL（`sql.mysql_db.update_ip_use_record` / `update_api_user_record`，按 `.eapi` 分叉） | HTTP `POST /inapi/update_task` 到 UserServer（`utility.userserver_client.update_task_status`，带 `internal_auth` Bearer） |
| 2 | 队列 | `TTS_RABBITMQ_*`、任务名 `gpu_tts_tasks.gpu_gen_tts`、queue `gpu_tts_tasks` | `US_RABBITMQ_*`、任务名 `task_gen_tts`、queue `us_tts_queue`（`tasks_interface.py` 已声明 `task_gen_tts`→`us_tts_queue`） |
| 3 | 入参 | `reqid` + `.eapi` 后缀 + `is_eapi` 分叉 | `task_id` + `domain`，统一 `update_task_status(logger, task_id, domain, ...)` |
| 4 | 日志 | 无 reqid 上下文 | `set_reqid(task_id)` / `clear_reqid()`（ContextVar + `ReqIdFilter`） |
| 5 | 派发 | uscm else 分支 `.delay(modelname, text, modelcat, userid, reqid, output_format, modellang, email)`（8 位置参数，`t` 丢失、`reqid` 串位） | `.delay(modelname, text, modelcat, userid, task_id=reqid, output_format=..., modellang=..., email=..., domain=domain)` |

## 对照基准：indextts-worker 已落实的改动

- `celery_worker.py`: `TTS_RABBITMQ_*` → `US_RABBITMQ_*`，任务路由 `gpu_*_tasks.*` → `task_*`，queue → `us_*_queue`
- `gpu_*_tasks.py`: 删 `sql.mysql_db`、删 `is_eapi` 分叉、加 `domain`、`update_task_status`、`set_reqid/clear_reqid`、`SyncJob.domain`
- `utility/file_delivery.py`: MySQL → `update_task_status`；`SyncJob` 加 `domain`；sync 循环加 `set_reqid/clear_reqid`
- `utility/log.py`: 加 reqid ContextVar 上下文
- 新增 `utility/internal_auth.py`、`utility/userserver_client.py`；删 `sql/mysql_db.py`（连同 `config/db_conf.py`）
- `docker-compose.yml`: `env_file` → `.env_usmqdb`；启动脚本 `-Q` → `us_*_queue`

## 迁移范围

### A. gsvtts-worker（`tts/gsvtts-worker`）

- **拷贝（verbatim，来自 `tts/indextts-worker`）**：`utility/internal_auth.py`、`utility/userserver_client.py`、`utility/log.py`
- **修改**：
  - `utility/file_delivery.py`（贴 indextts diff：MySQL → `update_task_status`、`SyncJob.is_eapi`→`SyncJob.domain`、set/clear reqid；`resolve_delivery_transport` 保持不变，其实现与 indextts 完全一致）
  - `utility/util.py`（保留全部 gsvtts 专属 NLP 函数 `get_phones_and_bert`/`get_spepc`/`split`/`cut5`/`process_text`/…；仅修 `gen_res_dict` bug、删未使用的 `genid` 及其 `__main__`；`verify_signature`/`splits` 保留）
  - `utility/test_file_delivery.py`（`_job()` 助手 `is_eapi=False` → `domain=''`，其余测试不变）
  - `celery_worker.py`（`US_RABBITMQ_*`、任务路由 `task_gen_tts` → `us_tts_queue`；app 名保留 `tts_worker`）
  - `gpu_tts_tasks.py`（任务名重建、去 `t` 参数（私有模型 `t==2` 分支删除，gpt-sovits 模型路径恒为 `common_models/{modelcat}/common_model_{modelname}`）、去 `.eapi` 分叉、加 `domain`、`update_task_status`、`set_reqid/clear_reqid`、`SyncJob.domain`）
  - `build_assets/start_tts_worker.sh`（`-Q us_tts_queue`；`--pool=solo --pool=threads` → `--pool=threads`）
- **删除**：`build_assets/sql/mysql_db.py`、`build_assets/config/db_conf.py`、顶层 `sql/mysql_db.py`（连同两个 `sql/` 目录与顶层 `sql/`）
- **修改 env**：`docker-compose.yml` / `docker-compose-preview.yml` / `docker-compose-test.yml` 的 `env_file` `.env_mqdb` → `.env_usmqdb`
- **保持不动（gsvtts 专属）**：`fish_speech/*`、`voice_train/*`、`tools/*`、`indextts/*`、`indextts_worker.py`（遗留死代码，不清理）、`utility/load_model.py`、`utility/lang_util.py`、`utility/voice_config.py`、`utility/module_enum.py`、`utility/quic_http_transport.py`（`biz_type` 仍为 "tts"）、`utility/samearea_http_transport.py`、`utility/socat_rsyc_util.py`、`utility/sync_util.py`、`utility/file_sync_config.py`、`config/model_conf.py`、`config/__init__.py`、`Dockerfile`、`build-tts-image.sh`、`README.md`

### B. uscm `/api/genaudio` 的 gsvtts 分支（`uscm/build_assets/uscm_server.py`）

- else 分支（现 8 位置参数，`t` 丢失、`reqid` 串位到 `t`）改为显式关键字：
  ```python
  else:
      modellang = map_lang_abbrev(modellang, modellang)
      gpu_gen_tts.delay(
          modelname, text, modelcat, userid,
          task_id=reqid, output_format=output_format, modellang=modellang,
          email=email, domain=domain,
      )
  ```
- `tasks_interface.py` 已声明 `gpu_gen_tts = signature('task_gen_tts')` → `us_tts_queue`，无需改动。
- index-tts 分支已迁移完成，不动；`/api/genaudio` 本身已是异步（立即返回 `reqid`），不改整体结构。

## 已确认决策

1. **`t` 参数删除**：完全对齐 indextts（它无 `t`）。`gpu_gen_tts` 签名去掉 `t=1`；gpt-sovits 分支的 `t==1`/`t==2`/`else invalid` 三段塌缩为「恒用 `common_models/{modelcat}/common_model_{modelname}` 公用模型路径」，用户私有模型分支（`t==2`、读 `user_models/user{userid}/...` 与 `model_file_prefix=f'user{userid}_{modelname}'`）一并删除。uscm 侧不再传 `t`。
2. **`.eapi` 分支彻底删除**：`.eapi` 后缀（企业 API 记录类型）与 `is_eapi` 分叉删除，统一 `update_task_status`；`sql.mysql_db` 连带 `config/db_conf.py` 全部删除。
3. **`test_file_delivery.py` 更新后保留**：`_job()` 助手 `is_eapi=False` → `domain=''`，其余测试逻辑不动。
4. **`fastapi`/`requests` 依赖**：`userserver_client`（lazy `requests`）与 `internal_auth`（顶层 `fastapi`）来自 indextts。gsvtts base 镜像为 `fuse-tts-image:0.2`，两者是否已装需在计划 Task 1 验证；若缺 `fastapi`/`requests` 再单独补装（transcribe 迁移同法处理）。