# transcribe-worker 迁移 + uscm /api/transcribe 异步化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `video/transcribe-worker` 迁到新架构（对齐 `tts/indextts-worker`），并把 `uscm` 的 `POST /api/transcribe` 从同步 form/轮询改为异步 JSON。

**Architecture:** worker 以 `@celery_app.task(name='task_transcribe_whisper')` 注册到 `us_transcribe_whisper_queue`，状态回写走 UserServer HTTP（`update_task_status` + `TaskStatus` + `domain`）；uscm 侧 `post_generate_add` 落库后 `transcribe_whisper.delay(...)` 异步派发、立即返回 `reqid`。

**Tech Stack:** Python 3 / Celery / FastAPI / UserServer HTTP（`/inapi/*`）/ Docker。

**Spec:** `docs/superpowers/specs/2026-10-29-transcribe-worker-migration-design.md`

## Global Constraints

- 任务名 `task_transcribe_whisper`，队列 `us_transcribe_whisper_queue`（与 `uscm/build_assets/tasks_interface.py` 已声明的 signature 一致）。
- worker 状态回写只用 `utility.userserver_client.update_task_status(logger, task_id, domain, payload, TaskStatus)`；不再 import `sql.mysql_db`。
- 环境变量用 `US_RABBITMQ_*` / `US_COMMON_USERSERVER_HOST/PORT` / `INAPI_AUTH_TOKEN`；`docker-compose` 的 `env_file` 用 `/home/aigc/data/env/.env_usmqdb`。
- worker 任务函数 `transcribe_whisper` 返回：失败 `gen_res_dict(1, msg)`，成功 `f"TranscribeCompleted: {task_id}"`。
- 接口 `/api/transcribe` 用 `await request.json()` + `userid`，签名对象 `upurl`，异步只返回 `{"ret":0,"msg":"ok","reqid":reqid}`（无 `uri`/`enurl`）。
- 保留 `email == video_tts_transcribe` 内部转写分支（uscm 侧 `validate_internal_tts_url`；worker 侧 localnet http 派发）。
- 不新增 `requirements.txt`、不改 Dockerfile 安装步骤；worker 依赖沿用 indextts 的 base 镜像（含 `requests`/`fastapi`），若 whisper base 镜像缺失则在本计划 Task 1 的验证中检出并单独处理。
- 保留 transcribe 专属的 `utility/quic_http_transport.py`（`biz_type="transcribe"` → `/transcribe`），不从 indextts 覆盖。
- 每个 commit 信息末尾追加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 干净硬切：不保留 `sql/mysql_db.py`/`config/db_conf.py`、不做共存窗口。

## Review Focus

1. **`upfileinfo` 缺 `relative_path`（或非 dict）**：应 `update_task_status(... FAILED)` + `return gen_res_dict(1, msg)`，不得 KeyError 抛异常留 PENDING。→ Task 3 开头校验。
2. **空转写结果（无语音）**：`update_task_status(... FAILED)`，不得再 `update_ip_tool_record`。→ Task 3。
3. **`enqueue_delivery_job` 返回 False**（队列满/worker 未启动）：须 `update_task_status(... FAILED)`。→ Task 3。
4. **未捕获异常**：整段 body 用 `try/except` 包住，`finally: clear_reqid()`，except 分支 `update_task_status(... FAILED)`。→ Task 3。
5. **producer/worker 参数一致**：`transcribe_whisper.delay(upfileinfo, language, output_format, task_id=reqid, userid=userid, email=email, domain=domain)` 与 worker 签名 `transcribe_whisper(upfileinfo, language=None, output_format='json', task_id='0', userid=0, email='', domain='')` 一一对应。→ Task 5 与 Task 3 对照。

---

### Task 1: transcribe worker utility 层对齐 indextts

**Files:**
- Create: `video/transcribe-worker/build_assets/utility/internal_auth.py`（拷贝 indextts 同名文件）
- Create: `video/transcribe-worker/build_assets/utility/userserver_client.py`（拷贝 indextts 同名文件）
- Modify: `video/transcribe-worker/build_assets/utility/log.py`（覆盖为 indextts 同名文件，带 reqid 上下文）
- Modify: `video/transcribe-worker/build_assets/utility/file_delivery.py`（贴 indextts 的迁移 diff，但保留 transcribe 专属 `resolve_delivery_transport`）
- Modify: `video/transcribe-worker/build_assets/utility/util.py`（重写，见下）

**Interfaces:**
- Produces:
  - `utility.internal_auth.bearer_auth_header() -> dict`
  - `utility.userserver_client.update_task_status(logger, task_id, domain, payload_msg_or_uri, task_status: TaskStatus) -> tuple[int, str]`
  - `utility.log.set_reqid(reqid) / clear_reqid() / setup_logger(...) / logset(...)`
  - `utility.util.gen_res_dict(ret: int, msg: str, dt=None) -> dict`
  - `utility.file_delivery.SyncJob(source_file, target_dir, task_id, short_uri_prefix, output_file, proxies=[], transport='quic', domain='')`
  - `utility.file_delivery.enqueue_delivery_job(logger, job) -> bool`、`start_file_delivery(logger, transports)`、`resolve_delivery_transport(email="") -> str`

- [ ] **Step 1: 拷贝 3 个文件并重写 `util.py`**

  - `cp tts/indextts-worker/build_assets/utility/internal_auth.py video/transcribe-worker/build_assets/utility/internal_auth.py`
  - `cp tts/indextts-worker/build_assets/utility/userserver_client.py video/transcribe-worker/build_assets/utility/userserver_client.py`
  - `cp tts/indextts-worker/build_assets/utility/log.py video/transcribe-worker/build_assets/utility/log.py`
  - `util.py` 重写为（去 numpy/`gen_reqid`/`validate_gender`/`format_timing_stats` 及损坏的 `__main__`，只留干净 `gen_res_dict`）：
    ```python
    # -*- coding: utf-8 -*-

    def gen_res_dict(ret: int, msg: str, dt=None):
        d = {}
        if dt:
            d.update(dt)
        d['ret'] = ret
        d['msg'] = msg
        return d
    ```

- [ ] **Step 2: 改写 `file_delivery.py`（贴 indextts diff，保留 transcribe 专属逻辑）**

  在 `video/transcribe-worker/build_assets/utility/file_delivery.py` 上做以下精确修改：
  - 顶部 docstring：首行 `audio-extractor output files` → `transcribe output files`；第三行注释 `Completion updates MySQL tool records` → `Completion updates task records via UserServer /inapi/update_task`。
  - 顶层 import 增加三行：`from utility.log import clear_reqid, set_reqid`、`from utility.module_enum import TaskStatus`、`from utility.userserver_client import update_task_status`。
  - `SyncJob` dataclass 的 `transport: str = "quic"` 之后增加字段 `domain: str = ""`。
  - `_on_sync_complete` 内删除两行惰性 import（`from sql.mysql_db import update_ip_tool_record`、`from utility.module_enum import TaskStatus`）；把 `update_ip_tool_record(job.task_id, uri, TaskStatus.COMPLETE)` → `update_task_status(logger, job.task_id, job.domain, uri, TaskStatus.COMPLETE)`，`update_ip_tool_record(job.task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, job.task_id, job.domain, msg, TaskStatus.FAILED)`。
  - sync 循环里：sentinel 检查返回之后加 `set_reqid(job.task_id)`；循环 `finally` 里、`_sync_queue.task_done()` 之前加 `if job is not _SYNC_SENTINEL: clear_reqid()`。
  - **不新增** `VIDEO_SCRIPT_TTS_EMAIL`，`resolve_delivery_transport(email: str = "")`（`del email; return get_file_sync_transport()`）保持原样。

- [ ] **Step 3: 验证语法与 `gen_res_dict`/`SyncJob.domain`**

Run:
```bash
cd video/transcribe-worker/build_assets
python3 -m py_compile utility/internal_auth.py utility/userserver_client.py utility/log.py utility/file_delivery.py utility/util.py
python3 -c "from utility.util import gen_res_dict as g; assert g(1,'x')=={'ret':1,'msg':'x'}; d={'a':1}; r=g(0,'ok',d); assert r=={'a':1,'ret':0,'msg':'ok'} and d=={'a':1}"
python3 -c "from utility.file_delivery import SyncJob; j=SyncJob('a','b','t','/p','f',transport='quic',domain='d'); assert j.domain=='d'"
python3 -c "from utility.userserver_client import update_task_status; from utility.log import set_reqid, clear_reqid"
! grep -rn "sql.mysql_db\|update_ip_tool_record" utility/file_delivery.py
```
Expected: 退出码 0，无输出；`file_delivery.py` 无 MySQL 残留。若 `from utility.userserver_client` 或 `from utility.internal_auth` 因缺 `requests`/`fastapi` 报 ImportError，记录并转入「补充依赖」处理（本计划暂定 base 镜像已含，如缺则加 pip 安装）。

- [ ] **Step 4: Commit**

```bash
git add video/transcribe-worker/build_assets/utility/
git commit -m "refactor(transcribe): align utility layer with indextts (userserver/task-status/reqid)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: transcribe worker `celery_worker.py` + `start_transcribe_worker.sh`

**Files:**
- Modify: `video/transcribe-worker/build_assets/celery_worker.py`
- Modify: `video/transcribe-worker/build_assets/start_transcribe_worker.sh`

**Interfaces:**
- Produces: `celery_worker.celery_app`，`conf.task_routes = {'task_transcribe_whisper': {'queue': 'us_transcribe_whisper_queue'}}`；broker 用 `US_RABBITMQ_*`。

- [ ] **Step 1: 改写 `celery_worker.py`**

  - 环境变量由 `TTS_RABBITMQ_*`/`RABBITMQ_*` → `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
  - `Celery("transcribe_worker", broker=f"amqp://{US_RABBITMQ_USER}:{quote_plus(US_RABBITMQ_PASS)}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//", backend="rpc://")`（app 名 `transcribe_worker` 保留）。
  - `task_routes = {'task_transcribe_whisper': {'queue': 'us_transcribe_whisper_queue'}}`。

- [ ] **Step 2: 改写 `start_transcribe_worker.sh` 的 `-Q`**

  `-Q gpu_transcribe_whisper_tasks` → `-Q us_transcribe_whisper_queue`（`-A gpu_transcribe_tasks` 与 `--concurrency=${CELERY_CONCURRENCY:-1}` 不变；顺带把重复的 `--pool=solo --pool=threads` 收敛为 `--pool=threads`）。

- [ ] **Step 3: 验证**

Run:
```bash
cd video/transcribe-worker/build_assets
python3 -m py_compile celery_worker.py
grep -n "task_transcribe_whisper\|us_transcribe_whisper_queue\|US_RABBITMQ" celery_worker.py
grep -n "\-Q us_transcribe_whisper_queue" start_transcribe_worker.sh
! grep -n "TTS_RABBITMQ\|gpu_transcribe_whisper_tasks\|gpu_transcribe_tasks.transcribe_whisper" celery_worker.py start_transcribe_worker.sh
```
Expected: py_compile 通过；`celery_worker.py` 命中 `US_RABBITMQ_*`、`task_transcribe_whisper`、`us_transcribe_whisper_queue`；`start_transcribe_worker.sh` 命中 `-Q us_transcribe_whisper_queue`；无旧队列/任务名残留。

- [ ] **Step 4: Commit**

```bash
git add video/transcribe-worker/build_assets/celery_worker.py video/transcribe-worker/build_assets/start_transcribe_worker.sh
git commit -m "refactor(transcribe): task_transcribe_whisper on us_transcribe_whisper_queue with US_RABBITMQ_*" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 重写 `gpu_transcribe_tasks.py`

**Files:**
- Modify: `video/transcribe-worker/build_assets/gpu_transcribe_tasks.py`

**Interfaces:**
- Consumes: `from celery_worker import celery_app`（Task 2）；`utility.log.set_reqid/clear_reqid/setup_logger`（Task 1）；`utility.userserver_client.update_task_status`（Task 1）；`utility.util.gen_res_dict`（Task 1）；`utility.file_delivery.SyncJob`（含 `domain`，Task 1）；`from transcriber import get_engine`、`from transcriber.formatter import format_result`（不变）。
- Produces:
  ```python
  @celery_app.task(name='task_transcribe_whisper')
  def transcribe_whisper(upfileinfo, language=None, output_format='json', task_id='0',
                         userid=0, email='', domain=''):
  ```

- [ ] **Step 1: 改 import 与任务签名**

  - 删除 `from sql.mysql_db import update_ip_tool_record`。
  - 删除 `from utility.util import gen_reqid`。
  - `from utility.log import setup_logger` → `from utility.log import setup_logger, set_reqid, clear_reqid`。
  - 新增 `from utility.userserver_client import update_task_status`。
  - 装饰器 `@celery_app.task(name='gpu_transcribe_tasks.transcribe_whisper')` → `@celery_app.task(name='task_transcribe_whisper')`。
  - 签名 `def transcribe_whisper(upfileinfo, language=None, output_format='json', task_id='0', userid=0, first_ip='', email='')` → `def transcribe_whisper(upfileinfo, language=None, output_format='json', task_id='0', userid=0, email='', domain='')`（去掉 `first_ip`）。

- [ ] **Step 2: 函数体 `set_reqid`/`clear_reqid`，去掉 `gen_reqid`**

  - 函数体首行 `set_reqid(task_id)`；末尾新增 `finally: clear_reqid()`。
  - 删除 `reqid = gen_reqid(task_id, start_time)`；`start_time`/`time_str` 挪进 `try` 内首行；后续日志中的 `reqid` → `task_id`。

- [ ] **Step 3: 开头校验 `upfileinfo`（Review Focus #1）**

  在解包 `upfileinfo` 之前加：
  ```python
  if not isinstance(upfileinfo, dict) or not upfileinfo.get('relative_path'):
      msg = f'invalid upfileinfo, task_id:{task_id}'
      logger.warning(f'msg: {msg}')
      update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
      return gen_res_dict(1, msg)
  ```

- [ ] **Step 4: 三处状态回写替换 + `domain` + 返回**

  - 空转写分支：`update_ip_tool_record(task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`，`return f'transcribe failed, task_id:{task_id}, msg:{msg}'` → `return gen_res_dict(1, msg)`。
  - `enqueue_delivery_job` 返回 False 分支：`update_ip_tool_record(task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`，`return f'transcribe failed, task_id:{task_id}, msg:{msg}'` → `return gen_res_dict(1, msg)`。
  - `SyncJob(...)` 增加 `domain=domain`。
  - 成功返回保持 `return f'TranscribeCompleted: {task_id}'`。
  - `except` 分支：`update_ip_tool_record(task_id, f'transcribe error: {e}', TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, f'transcribe error: {e}', TaskStatus.FAILED)`，`return f'transcribe failed, task_id:{task_id}'` → `return gen_res_dict(1, f'transcribe error: {e}')`。

- [ ] **Step 5: 验证**

Run:
```bash
cd video/transcribe-worker/build_assets
python3 -m py_compile gpu_transcribe_tasks.py
grep -n "name='task_transcribe_whisper'" gpu_transcribe_tasks.py
grep -n "def transcribe_whisper(upfileinfo, language=None, output_format='json', task_id='0', userid=0, email='', domain='')" gpu_transcribe_tasks.py
grep -n "update_task_status(logger, task_id, domain" gpu_transcribe_tasks.py
grep -n "domain=domain" gpu_transcribe_tasks.py
grep -n "set_reqid(task_id)\|clear_reqid()" gpu_transcribe_tasks.py
! grep -n "update_ip_tool_record\|sql.mysql_db\|gen_reqid\|first_ip" gpu_transcribe_tasks.py
```
Expected: py_compile 通过；各 grep 命中相应行；无 `update_ip_tool_record`/`sql.mysql_db`/`gen_reqid`/`first_ip` 残留。

- [ ] **Step 6: Commit**

```bash
git add video/transcribe-worker/build_assets/gpu_transcribe_tasks.py
git commit -m "refactor(transcribe): migrate transcribe_whisper to task_transcribe_whisper (async + userserver status)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 删除 MySQL 层 + 切换 env_file

**Files:**
- Delete: `video/transcribe-worker/build_assets/sql/mysql_db.py`
- Delete: `video/transcribe-worker/build_assets/config/db_conf.py`
- Modify: `video/transcribe-worker/docker-compose-whisper.yml`

**Interfaces:**
- Produces: 仓库内不再存在 `sql.mysql_db`/`update_ip_tool_record`；docker-compose `env_file` 指向 `.env_usmqdb`。

- [ ] **Step 1: 删除两个文件，切 env_file**

  - `rm video/transcribe-worker/build_assets/sql/mysql_db.py video/transcribe-worker/build_assets/config/db_conf.py`（`sql/` 目录空了可一并删除）。
  - `docker-compose-whisper.yml` 中 `- /home/aigc/data/env/.env_mqdb` → `- /home/aigc/data/env/.env_usmqdb`。

- [ ] **Step 2: 验证全仓库无残留引用**

Run:
```bash
grep -rn "mysql_db\|update_ip_tool_record\|add_ip_tool_record\|get_tool_out_path_by_task_id" video/transcribe-worker uscm/build_assets/uscm_server.py --include='*.py' || echo "NO_REFERENCES"
grep -n "env_usmqdb" video/transcribe-worker/docker-compose-whisper.yml
! grep -rn "from sql.mysql_db\|import db_conf\|config.db_conf" video/transcribe-worker --include='*.py'
```
Expected: `NO_REFERENCES`；docker-compose 命中 `env_usmqdb`；无 `sql.mysql_db`/`config.db_conf` import 残留。

- [ ] **Step 3: Commit**

```bash
git add -A video/transcribe-worker/build_assets/sql video/transcribe-worker/build_assets/config/db_conf.py video/transcribe-worker/docker-compose-whisper.yml
git commit -m "refactor(transcribe): drop MySQL status layer, switch env_file to .env_usmqdb" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: uscm 异步 `/api/transcribe`

**Files:**
- Modify: `uscm/build_assets/uscm_server.py`

**Interfaces:**
- Consumes: `from tasks_interface import ..., transcribe_whisper`；`from config.transcribe_routing import resolve_transcribe_engine`；`ModuleType.TRANSCRIBE`；`post_generate_add`；`validate_audio_url`、`validate_internal_tts_url`；`validate_signature`；`validate_request`；`gen_reqid`/`gen_res_dict`；`DEFAULT_USER_FIRST_IP`；模块级 `logger`（不再有 `logger_tts`）。
- Produces: `@app.post("/api/transcribe")` → `transcribe_whisper.delay(upfileinfo, language, output_format, task_id=reqid, userid=userid, email=email, domain=domain)`，返回 `{"ret":0,"msg":"ok","reqid":reqid}`。

- [ ] **Step 1: 扩展 import**

  - `from utility.request_util import get_real_ip, validate_request, validate_signature, validate_text_length, validate_gender, validate_audio_url` → 追加 `, validate_internal_tts_url`。
  - `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract, gpu_cover, gpu_vocal, gpu_instrument` → 追加 `, transcribe_whisper`。
  - 新增 `from config.transcribe_routing import resolve_transcribe_engine`。

- [ ] **Step 2: 重写 `/api/transcribe` body（对照 `/api/extractaudio`）**

  - `reqid = gen_reqid(userid, tm)` 替换 `genid(userid_hash, tm)`（删除 `userid_hash` 计算）。
  - 解析 `domain = json_post_raw.get("domain", '')`。
  - `first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP`（对齐 genaudio，不再 `""`）。
  - 校验顺序：`output_format` 白名单检查（保留 `ALLOWED_TRANSCRIBE_FORMATS`）→ `email == video_tts_transcribe` 走 `validate_internal_tts_url(upurl, reqid, first_ip, email, today, datetime, logger)`、否则 `validate_audio_url(...)` → `validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, userid, email, ModuleType.TRANSCRIBE)` → `validate_request(logger, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.TRANSCRIBE, domain, request)`。
  - 所有日志用 `logger`（删 `logger_tts`）。
  - `generate_content = upfileinfo['relative_path']`；`task_req = json.dumps({"userid": userid, "email": email, "language": language, "format": output_format, "upurl": upurl}, ensure_ascii=False)`。
  - `post_generate_add(logger, reqid, first_ip, email, userid, domain, generate_content, real_ip, ModuleType.TRANSCRIBE, task_cmd='/api/transcribe', task_req=task_req)`。
  - `engine = resolve_transcribe_engine(language)`；v1 恒 `whisper`，`transcribe_whisper.delay(upfileinfo, language, output_format, task_id=reqid, userid=userid, email=email, domain=domain)`。
  - 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`；**删除** `add_ip_tool_record`、`resolve_transcribe_task(language).delay(...)`、`get_tool_out_path_by_task_id`、`encrypt_url`、`uri`/`enurl`、`timeout`/`interval`、`ToolType`。

- [ ] **Step 3: 验证**

Run:
```bash
cd uscm/build_assets
python3 -m py_compile uscm_server.py
grep -n 'app.post("/api/transcribe")' uscm_server.py
grep -n "def transcribe_endpoint(request: Request)" uscm_server.py
grep -n "ModuleType.TRANSCRIBE" uscm_server.py
grep -n "transcribe_whisper.delay(upfileinfo, language, output_format, task_id=reqid, userid=userid, email=email, domain=domain)" uscm_server.py
grep -n "validate_internal_tts_url\|video_tts_transcribe" uscm_server.py
grep -n "await request.json()" uscm_server.py
! grep -n "get_tool_out_path_by_task_id\|encrypt_url\|logger_tts\|add_ip_tool_record\|resolve_transcribe_task\|ToolType\|genid" uscm_server.py
```
Expected: py_compile 通过；`.delay` 参数与 Task 3 的 `transcribe_whisper(...)` 对应；`video_tts_transcribe` 分支保留；无旧符号残留。

- [ ] **Step 4: Commit**

```bash
git add uscm/build_assets/uscm_server.py
git commit -m "feat(uscm): async /api/transcribe dispatching to task_transcribe_whisper" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 摘要（已完成）

- **Spec 覆盖**：设计 §A（utility / celery_worker / gpu_transcribe_tasks / 删 MySQL / docker-compose）对应 Task 1/2/3/4；§B（uscm endpoint）对应 Task 5。已覆盖。
- **类型一致**：`transcribe_whisper` 签名在 Task 3 定义、Task 5 消费；`SyncJob.domain`/`update_task_status`/`set_reqid` 在 Task 1 定义、Task 3 消费；任务名/队列在 Task 2 定义、Task 3/5 对齐。
- **Review Focus 五项**：分别落点 Task 3（upfileinfo 缺键、空转写、enqueue 失败、未捕获异常）与 Task 5↔Task 3（参数一致）。
- **关键纠偏**：`file_delivery.py`/`quic_http_transport.py` 不能盲 cp indextts 版——前者保留 transcribe 的 `resolve_delivery_transport`、后者保留 `"transcribe": "/transcribe"` biz_type；`util.py` 去 numpy `gen_reqid`；`gpu_transcribe_tasks.py` 去 `first_ip`、加 `finally: clear_reqid()`。
- **比例**：计划以决策（文件名、签名、验证命令、替换点）为主，不转录实现代码。