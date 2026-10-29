# cover-worker 迁移 + uscm /api/cover、/api/vocal、/api/instrument 异步化 设计方案（对齐 indextts-worker / sound / audio）

- 日期：2026-10-28
- 分支：`feature/cover-worker`
- 状态：已确认（范围 + 两项决策已由维护者拍板）

## 0. 背景与结论先行

`tts/indextts-worker`、`sound/stable-sound-worker`、`video/audio-worker` 已迁到新架构；本次把
`cover/cover-worker`（旧架构拷贝而来）对齐到同一套模式，并把 `uscm` 里仍为旧框架的
`POST /api/cover`、`/api/vocal`、`/api/instrument` 对照已迁移的 `/api/genaudio` 改成异步。

先例文档（同一套模板）：
- `docs/superpowers/specs/2026-10-28-stable-sound-worker-migration-design.md`
- `docs/superpowers/specs/2026-10-28-audio-worker-migration-design.md`

`/api/extractaudio` 已在提交 `f287faa` 完成异步化（代码里已用 `audio_extract.delay` + `post_generate_add`
返回 `reqid`），本次不需再改；本设计里它只作为 cover/vocal/instrument 最接近的先例引用。

现状关键事实（已核实）：
- `uscm_server.py` 里的 `/api/cover`、`/api/vocal`、`/api/instrument` 是旧 form 同步写法，且引用了
  `logger_cover`/`logger_echobeat_cover`/`add_ip_tool_record`/`get_tool_out_path_by_task_id`/`encrypt_url`/
  `ToolType`/`gpu_cover`/`gpu_vocal`/`gpu_instrument` 等本文件未定义的名字，并仍按旧签名调用
  `validate_signature(... subscript, referer, ...)`（新签名已无这两参）。这些端点在当前文件里处于
  「半粘贴」的不可运行状态，必须完整迁移。
- `tasks_interface.py` 已就绪：`gpu_cover/gpu_vocal/gpu_instrument` 三个 signature 及
  `task_cover.* → us_cover_queue` 路由已定义，**无需改动**。

## 1. 目标 / 非目标 / 反目标

### 目标
1. 把 `cover/cover-worker` 从旧架构迁到新架构，对齐 `tts/indextts-worker`。
2. 把 `uscm` 的 `/api/cover`、`/api/vocal`、`/api/instrument` 从同步 form 改成异步 JSON，对照 `/api/genaudio`。

### 非目标（本次不做）
- 公共库抽取（沿用既定方案，留作后续独立任务）。
- 改动 indextts/sound/audio-worker 及 uscm 现有 `/api/genaudio`、`/api/gensound`、`/api/extractaudio` 的行为。
- 改动音频分离/翻唱推理逻辑本身（`extract_vocals`、`extract_instruments`、`process_voice_cover`、
  `convert_to_wav`/`convert_wav_to_mp3` 的 ffmpeg 回退策略保留）。

### 反目标
- 不再把 MySQL（`sql/mysql_db.py`）当作任务状态回写的常规路径。
- 不再让 `/api/cover`、`/api/vocal`、`/api/instrument` 同步阻塞（轮询）等待结果。

## 2. 新旧架构核心差异

| 维度 | 旧（cover） | 新（对齐 indextts / audio） |
|------|-------------|-----------------------------|
| 任务名 | `gpu_cover_tasks.gpu_cover` / `.gpu_vocal` / `.gpu_instrument` | `task_cover.cover` / `task_cover.vocal` / `task_cover.instrument` |
| 消费队列 | `gpu_cover_tasks` / `gpu_vocal_tasks` / `gpu_instrum_tasks` | `us_cover_queue`（统一） |
| worker 状态回写 | MySQL `update_ip_tool_record` | HTTP `update_task_status`，带 `domain` + `TaskStatus` |
| 生产者落库 | uscm `add_ip_tool_record` + `get_tool_out_path_by_task_id` 轮询（同步） | uscm `post_generate_add`（异步，立即返回） |
| 接口形态 | `POST /api/cover|vocal|instrument` form，同步返回 `uri`/`enurl` | JSON，异步返回 `reqid` |
| 环境变量 | `TTS_RABBITMQ_*` / env_file `.env_mqdb` | `US_RABBITMQ_*` / `US_COMMON_USERSERVER_*` / `INAPI_AUTH_TOKEN` / `.env_usmqdb` |
| worker 启动 | `celery -A gpu_cover_tasks ... -Q gpu_cover_tasks` | `celery -A gpu_cover_tasks ... -Q us_cover_queue` |

## 3. 具体改动

### 3.1 cover-worker（`cover/cover-worker/build_assets/`）

#### 3.1.1 `celery_worker.py`
- 环境变量统一为 `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
- `Celery("cover_worker", broker=amqp://...US_RABBITMQ_*..., backend="rpc://")`。
- `task_routes`：
  ```python
  celery_app.conf.task_routes = {
      'task_cover.cover': {'queue': 'us_cover_queue'},
      'task_cover.vocal': {'queue': 'us_cover_queue'},
      'task_cover.instrument': {'queue': 'us_cover_queue'},
  }
  ```

#### 3.1.2 `gpu_cover_tasks.py`
- 任务注册改为 `@celery_app.task(name='task_cover.cover')` / `name='task_cover.vocal'` / `name='task_cover.instrument'`。
- 函数签名：
  - `gpu_cover(modelcat, modelname, modelgender, upfileinfo, upgender, task_id='0', email='', domain='', lang='english')`（去掉 `first_ip`，新增 `domain`）。
  - `gpu_vocal(upfileinfo, task_id='0', email='', domain='')`（去 `first_ip`，增 `domain`）。
  - `gpu_instrument(upfileinfo, task_id='0', email='', domain='')`（去 `first_ip`，增 `domain`）。
- 用 `set_reqid(task_id)` / `finally: clear_reqid()` 包裹整段；删除 `reqid = gen_reqid(task_id, start_time)`（直接用 `task_id` 作 reqid）。
- 所有失败路径 `update_ip_tool_record(task_id, '', TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)` + `return`。
- `gpu_cover` 新增模型文件存在性校验（从旧 producer 迁入 worker，见 §3.2.2；用 worker 的 `model_dir='/app/models/service_models'`）：
  ```python
  model_path = f'{model_dir}/{lang}/{modelcat}/{modelname}'
  model_index_path = f'{model_path}/{modelname}_v2.index'
  model_file_path = f'{model_path}/{modelname}_weights.pth'
  if not (os.path.exists(model_index_path) and os.path.exists(model_file_path)):
      # update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED) + return
  ```
- `SyncJob(...)` 三个任务均新增 `domain=domain`（保留 `biz_type="cover"/"extract_vocal"/"extract_instrument"`）。
- 删除 `from sql.mysql_db import update_ip_tool_record`；删除未使用的 `from utility.encrypt_util import encrypt_url, decrypt_url`、`from utility.util import gen_reqid`（保留 `gen_res_dict`）。
- 新增 import：`from utility.userserver_client import update_task_status`、`from utility.log import set_reqid, clear_reqid`。

#### 3.1.3 新增/覆盖 `utility/`（以 tts/sound 已迁移版为蓝本）
- 新增 `userserver_client.py`：拷贝自 sound/tts worker 侧（`post_update_task` / `update_task_status`）。
- 新增 `internal_auth.py`：拷贝自 sound/tts（提供 `bearer_auth_header`）。
- 覆盖 `log.py`：拷贝带 reqid 上下文的版本（`set_reqid`/`clear_reqid`/`setup_logger`/`logset`）。
- 覆盖 `util.py`：只保留修正后的 `gen_res_dict`（当前 cover 版有「原地改传入 dict」的 bug）；删除 `gen_reqid`/`genid`/`verify_cover_signature` 及相应 `numpy`/`hmac`/`hashlib`/`get_hmac_secret_key` 引用（迁移后无引用）。
- `module_enum.py`：保持不变（已与 tts 版一致，含 `TaskStatus`/`ModuleType`/`ToolType`）。
- `file_delivery.py`：改造（见下，注意保留 cover 特有的 `biz_type`）。
- `file_sync_config.py`、`quic_http_transport.py`、`socat_rsyc_util.py`、`model_util.py`、`vocal_extract_util.py`、`encrypt_util.py`：保持不变。
  （`sync_util.py` 为旧版，但非本次迁移所需；保持不动，与 audio/sound 先例一致。）

`file_delivery.py` 相对现状的差异（对齐 tts，但保留 cover 的 `biz_type`）：
- `SyncJob` 增加 `domain: str = ""` 字段（保留现有 `biz_type: str = "cover"`）。
- 顶部新增 `from utility.log import clear_reqid, set_reqid`、`from utility.module_enum import TaskStatus`、
  `from utility.userserver_client import update_task_status`。
- `complete_sync_job` 改走 `update_task_status(logger, job.task_id, job.domain, ..., TaskStatus.COMPLETE/FAILED)`，移除 `from sql.mysql_db import update_ip_tool_record`。
- `_worker_loop` 增加 `set_reqid(job.task_id)` / `clear_reqid()`。

#### 3.1.4 删除/停用
- 删除 `sql/mysql_db.py` 与 `config/db_conf.py`（迁移后无引用）。
- `config/comm_config.py` 保留（`get_remote_cover_dir`/`get_remote_vocal_dir`/`get_remote_instrument_dir`/`get_hostname`/`get_device`/`get_userupload_dir` 仍在用）。
- `config/rvc_config.py`、`infer/` 推理代码：保持不变。

#### 3.1.5 运行时依赖
- 新增 `utility/userserver_client.py`（懒加载 `requests`）、`utility/internal_auth.py`（顶层 `from fastapi import ...`），
  需 `requests` + `fastapi`。
- 已确认：base 镜像 `coverimage:0.4` 已内置 `requests`、`fastapi`，因此**不新增 `requirements.txt`、不改 Dockerfile**。

#### 3.1.6 `start_cover_worker.sh`
- 命令 `-Q gpu_cover_tasks` → `-Q us_cover_queue`（`-A gpu_cover_tasks` 不变）。

#### 3.1.7 `docker-compose.yml` / `docker-compose-preview.yml`
- `env_file: /home/aigc/data/env/.env_mqdb` → 改为同时保留两个（cover 的 quic 文件投递依赖 `QUIC_CLIENT_HOST/PORT`，
  这两项在 `.env_mqdb` 里、`.env_usmqdb` 里没有；已核实）：
  ```yaml
  env_file:
    - /home/aigc/data/env/.env_mqdb       # QUIC_CLIENT_HOST / QUIC_CLIENT_PORT
    - /home/aigc/data/env/.env_usmqdb     # US_RABBITMQ_* / US_COMMON_USERSERVER_* / INAPI_AUTH_TOKEN
  ```
  （docker compose 同名 key 后者覆盖前者；两文件 key 基本不重叠，残留的 MYSQL_* / TTS_RABBITMQ_* 项无害。）

### 3.2 uscm（`uscm/build_assets/`）

#### 3.2.1 `tasks_interface.py`
- 无需改动（已定义 `gpu_cover/gpu_vocal/gpu_instrument` signature 及 `task_cover.* → us_cover_queue` 路由）。

#### 3.2.2 `uscm_server.py` 的 `/api/cover`、`/api/vocal`、`/api/instrument`
- import 增加：`from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract, gpu_cover, gpu_vocal, gpu_instrument`。
- 三端点 body 从 `await request.form()` 改为 `await request.json()`。
- 字段命名：**保留 `uid`**（JSON 字段名仍为 `uid`，默认 `0`），其值作为 userid 参数传入 `gen_reqid`/`validate_signature`/
  `validate_request`/`post_generate_add`；`converturl`（cover）/`upurl`（vocal、instrument）沿用；新增 `domain`、`tstamp`、`snature`。
- 校验顺序对齐 `/api/extractaudio`（签名对象为 `converturl`/`upurl`）：
  1. 空值校验：cover `modelname`/`modelcat`/`lang`/`converturl` 非空 + `validate_gender(upgender)`/`validate_gender(modelgender)`；
     vocal/instrument `upurl` 非空。
  2. `validate_audio_url(converturl|upurl, reqid, first_ip, email, today, datetime, logger)` → `(ret, msg, upfileinfo)`。
  3. `validate_signature(logger, timestamp, signature, converturl|upurl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.COVER|VOCAL_ISOLATOR|VOCAL_REMOVER)`（**去掉旧版 `subscript`/`referer` 传参**——新版签名已无这两参）。
  4. `validate_request(logger, real_ip, referer, reqid, email, subscript, today, uid, ModuleType.*, domain, request)`。
- 删除 producer 侧模型文件存在性校验（`cover_model_dir` / `os.path.exists`）——该校验移入 worker（§3.1.2）。
- `generate_content`：cover `f'{modelcat}|{modelname}|{converturl}'`；vocal/instrument `upfileinfo['relative_path']`。
- `post_generate_add(logger, reqid, first_ip, email, uid, domain, generate_content, real_ip, ModuleType.*, task_cmd='/api/cover|vocal|instrument', task_req=...)`。
- dispatch：
  - `gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, email=email, domain=domain, lang=lang)`。
  - `gpu_vocal.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
  - `gpu_instrument.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
- 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`；删除同步轮询 `get_tool_out_path_by_task_id`、`encrypt_url`、
  返回字段 `uri`/`enurl`、`add_ip_tool_record`。
- 日志统一走全局 `logger`；删除 `logger_cover` / `logger_echobeat_cover` 引用。

  结果查询走共享 UserServer（`generateadd` 记录 `task_id=reqid` + `generate_type=cover|vocal_isolator|vocal_remover`，
  worker 完成后 `/inapi/update_task` 回写 `task_status` 与 `out_path`）。本仓库无常驻客户端 check 接口，靠 `reqid` 查询，不新增端点。

## 4. 留给实现者的决定（实现时定，无需回问）
- `generate_content` 精确格式（上述为建议值）。
- `task_req` 存什么（建议 cover 存 `{uid, email, modelcat, modelname, modelgender, upgender, lang, converturl}`；vocal/instrument 存 `{uid, email, upurl}`）。
- `sync_util.py` 是否同步 tts 版：本次不动（非必需）。

## 5. 测试
- `utility/test_file_delivery.py`：现有版本 patch `sql.mysql_db.update_ip_tool_record`，需改为 patch
  `utility.file_delivery.update_task_status`；`_job()` 的 SyncJob 构造按需补 `domain`。
  参考 `sound/stable-sound-worker/build_assets/utility/test_file_delivery.py` 的迁移版。

## 6. 构建与部署
- 重建 cover-worker 镜像（`build_assets` 变更；base 镜像不变、无需新装依赖）。
- 重建 uscm 镜像（`uscm_server.py` 变更）。
- 切换策略：**干净硬切**。旧 producer（同步 form + `add_ip_tool_record`/`get_tool_out_path_by_task_id` 轮询）
  与旧 worker（队列 `gpu_*_tasks`、MySQL 回写）同批下线；队列 → `us_cover_queue`、状态回写 MySQL → UserServer 一次切完；
  不保留 `sql/mysql_db.py`、不排空旧队列、不做共存/回滚窗口（切换瞬间旧队列未消费任务被丢弃，接受）。

## 7. 验证
1. cover-worker 启动后从 `us_cover_queue` 消费 `task_cover.cover/vocal/instrument`，日志出现 reqid 上下文。
2. 调 `/api/cover`（JSON + userid + converturl 等）→ 立即返回 `{"ret":0,"msg":"ok","reqid":...}`；UserServer 查到 `generateadd` 记录；
   任务完成后 `/inapi/update_task` 收到 `task_status=complete` 且 `out_path` 为 `/outdt/final/<update_str>/...`、
   `/outdt/sepvocal/<update_str>/...`、`/outdt/sepinstrum/<update_str>/...` 形态。
3. 失败路径（空参、非法 converturl/upurl、cover 模型文件缺失、sync 入队失败、分离/转码失败）能通过
   `update_task_status(... failed ...)` 回写失败状态。