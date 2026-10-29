# cover-worker 迁移 + uscm /api/cover、/api/vocal、/api/instrument 异步化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `cover/cover-worker` 迁到新架构（对齐 `tts/indextts-worker` / `sound/stable-sound-worker` / `video/audio-worker`），并把 `uscm` 的 `POST /api/cover`、`/api/vocal`、`/api/instrument` 从同步 form 改为异步 JSON（`uid` 字段、返回 `reqid`）。

**Architecture:** worker 以 `@celery_app.task(name='task_cover.cover'/'task_cover.vocal'/'task_cover.instrument')` 注册到 `us_cover_queue`，状态回写走 UserServer HTTP（`update_task_status` + `TaskStatus` + `domain`）；uscm 侧 `post_generate_add` 落库后 `gpu_cover/gpu_vocal/gpu_instrument.delay(...)` 异步派发、立即返回 `reqid`。cover 的 quic 文件投递保留 `biz_type`，且需同时保留 `.env_mqdb`（QUIC 变量）与 `.env_usmqdb`（US_RABBITMQ/UserServer/INAPI 变量）。

**Tech Stack:** Python 3 / Celery / FastAPI / UserServer HTTP（`/inapi/*`）/ Docker。

**Spec:** `docs/superpowers/specs/2026-10-28-cover-worker-migration-design.md`

## Global Constraints

- 任务名 `task_cover.cover` / `task_cover.vocal` / `task_cover.instrument`，统一消费队列 `us_cover_queue`。
- worker 状态回写只用 `utility.userserver_client.update_task_status(logger, task_id, domain, payload, TaskStatus)`；不再 import `sql.mysql_db`。
- 环境变量用 `US_RABBITMQ_*` / `US_COMMON_USERSERVER_HOST/PORT` / `INAPI_AUTH_TOKEN`；docker-compose 的 `env_file` 同时保留 `/home/aigc/data/env/.env_mqdb`（QUIC_CLIENT_HOST/PORT）与 `/home/aigc/data/env/.env_usmqdb`（US_* / INAPI_*）。
- base 镜像 `coverimage:0.4` 已内置 `requests`、`fastapi`，**不新增 `requirements.txt`、不改 Dockerfile**。
- cover worker 的 `file_delivery.SyncJob` 必须保留 `biz_type`（`cover`/`extract_vocal`/`extract_instrument`）字段，并新增 `domain`。
- uscm 三端点用 `await request.json()` + **`uid`** 字段；`/api/cover` 签名对象 `converturl`，`/api/vocal`、`/api/instrument` 签名对象 `upurl`；异步只返回 `{"ret":0,"msg":"ok","reqid":reqid}`（无 `uri`/`enurl`）。
- cover 的模型文件存在性校验从 producer 移入 worker（用 worker 的 `model_dir='/app/models/service_models'`）。
- 每个 commit 信息末尾追加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 干净硬切：不排空旧队列、不保留 `sql/mysql_db.py`/`config/db_conf.py`、不做共存窗口。

## Review Focus

1. **`upfileinfo` 缺 `relative_path`（或非 dict）**：worker 三个任务应在解包前 `update_task_status(... FAILED)` + `return`，不得 KeyError 抛异常留 PENDING。→ Task 3。
2. **cover 模型文件缺失**（`model_index_path`/`model_file_path` 不存在）：worker 内 `update_task_status(... FAILED)`，不再由 producer 的 `os.path.exists` 拦截。→ Task 3（worker）与 Task 5（producer 删除校验）对照。
3. **`enqueue_delivery_job` 返回 False**（队列满/worker 未启动）：须 `update_task_status(... FAILED)`。→ Task 3。
4. **未捕获异常**：每个任务整段 body 用 `try/finally(set_reqid/clear_reqid)` 包裹，异常分支（现有 try/except 或兜底）`update_task_status(... FAILED)`，不得卡 PENDING。→ Task 3。
5. **producer/worker 参数一致**：`gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, email=email, domain=domain, lang=lang)`、`gpu_vocal/gpu_instrument.delay(upfileinfo, task_id=reqid, email=email, domain=domain)` 与 worker 签名一一对应。→ Task 5 与 Task 3 对照。

---

### Task 1: cover worker utility 层对齐 tts/sound

**Files:**
- Create: `cover/cover-worker/build_assets/utility/internal_auth.py`（拷贝 `tts/indextts-worker/build_assets/utility/internal_auth.py`）
- Create: `cover/cover-worker/build_assets/utility/userserver_client.py`（拷贝 `tts/indextts-worker/build_assets/utility/userserver_client.py`）
- Modify: `cover/cover-worker/build_assets/utility/log.py`（覆盖为 tts 同名文件，带 reqid 上下文）
- Modify: `cover/cover-worker/build_assets/utility/util.py`（重写为仅 `gen_res_dict`）
- Modify: `cover/cover-worker/build_assets/utility/file_delivery.py`（原地改造，保留 `biz_type`，见下）
- Modify: `cover/cover-worker/build_assets/utility/test_file_delivery.py`（修 `TestCompleteSyncJob`）

**Interfaces:**
- Produces:
  - `utility.internal_auth.bearer_auth_header() -> dict`
  - `utility.userserver_client.update_task_status(logger, task_id, domain, payload_msg_or_uri, task_status: TaskStatus) -> tuple[int, str]`
  - `utility.log.set_reqid(reqid) / clear_reqid() / setup_logger(...) / logset(...)`
  - `utility.util.gen_res_dict(ret: int, msg: str, dt=None) -> dict`
  - `utility.file_delivery.SyncJob(source_file, target_dir, task_id, short_uri_prefix, output_file, proxies=[], transport='quic', biz_type='cover', domain='')`
  - `utility.file_delivery.enqueue_delivery_job(logger, job) -> bool`、`start_file_delivery(logger, transports)`

- [ ] **Step 1: 拷贝 3 个文件，重写 `util.py`**

  - `cp tts/indextts-worker/build_assets/utility/internal_auth.py cover/cover-worker/build_assets/utility/internal_auth.py`
  - `cp tts/indextts-worker/build_assets/utility/userserver_client.py cover/cover-worker/build_assets/utility/userserver_client.py`
  - `cp tts/indextts-worker/build_assets/utility/log.py cover/cover-worker/build_assets/utility/log.py`
  - `util.py` 重写为（**不能直接 cp tts/sound 版**——它们 import `voice_config`/含 `verify_signature`，cover 都不需要）：
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

- [ ] **Step 2: 原地改造 `file_delivery.py`（保留 `biz_type`）**

  以当前 `cover/cover-worker/build_assets/utility/file_delivery.py` 为底稿，做以下改动（**不要用 `cp` 覆盖 tts 版**，cover 必须要保留 `biz_type`）：
  - 模块 docstring 首行 "Completion updates MySQL tool records" → "Completion updates task records via UserServer /inapi/update_task"。
  - 顶部 import 新增三行：
    ```python
    from utility.log import clear_reqid, set_reqid
    from utility.module_enum import TaskStatus
    from utility.userserver_client import update_task_status
    ```
  - `SyncJob` dataclass 在 `biz_type: str = "cover"` 之后新增 `domain: str = ""`。
  - `complete_sync_job`：删除 `from sql.mysql_db import update_ip_tool_record` 与函数内 `from utility.module_enum import TaskStatus`；把
    - `update_ip_tool_record(job.task_id, uri, TaskStatus.COMPLETE)` → `update_task_status(logger, job.task_id, job.domain, uri, TaskStatus.COMPLETE)`
    - `update_ip_tool_record(job.task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, job.task_id, job.domain, msg, TaskStatus.FAILED)`
  - `_worker_loop`：在拿到非 sentinel 的 job 之后（`queue_depth = ...` 之前）加 `set_reqid(job.task_id)`；`finally:` 分支改为
    `if job is not _SYNC_SENTINEL: clear_reqid()` 后接 `_sync_queue.task_done()`。
  - `resolve_delivery_transport` 与 `biz_type` 相关逻辑保持不变。

- [ ] **Step 3: 修 `test_file_delivery.py` 的 `TestCompleteSyncJob`**

  把 `TestCompleteSyncJob._patch_mysql_update` 及三个用例改为 patch `utility.file_delivery.update_task_status`：

  ```python
  class TestCompleteSyncJob(unittest.TestCase):
      def test_success_writes_outdt_complete(self):
          from utility.module_enum import TaskStatus
          logger = logging.getLogger("test_complete")
          job = _job()
          with patch("utility.file_delivery.update_task_status") as mock_update:
              complete_sync_job(logger, job, True)
          mock_update.assert_called_once_with(
              logger, "t1", "", "/outdt/final/2026-09-13/song_cover.mp3", TaskStatus.COMPLETE)

      def test_vocal_success_uri(self):
          from utility.module_enum import TaskStatus
          logger = logging.getLogger("test_complete")
          job = _job(short_uri_prefix="/sepvocal/2026-09-13", output_file="v.mp3", biz_type="extract_vocal")
          with patch("utility.file_delivery.update_task_status") as mock_update:
              complete_sync_job(logger, job, True)
          mock_update.assert_called_once_with(
              logger, "t1", "", "/outdt/sepvocal/2026-09-13/v.mp3", TaskStatus.COMPLETE)

      def test_failure_writes_failed_not_covered(self):
          from utility.module_enum import TaskStatus
          logger = logging.getLogger("test_complete")
          job = _job()
          with patch("utility.file_delivery.update_task_status") as mock_update:
              complete_sync_job(logger, job, False, err_msg="quic timeout")
          mock_update.assert_called_once_with(
              logger, "t1", "", "quic timeout", TaskStatus.FAILED)
          self.assertNotIn("/covered", mock_update.call_args[0][3])
  ```

  （删除 `_patch_mysql_update` 与 `import sys`/`from types import ModuleType` 辅助；`_job` 保留 `biz_type="cover"` 默认值，可加 `domain=""`。）

- [ ] **Step 4: 验证**

  ```bash
  cd cover/cover-worker/build_assets
  python3 -m py_compile utility/internal_auth.py utility/userserver_client.py utility/log.py utility/file_delivery.py utility/util.py utility/test_file_delivery.py
  python3 -c "from utility.util import gen_res_dict as g; assert g(1,'x')=={'ret':1,'msg':'x'}; d={'a':1}; r=g(0,'ok',d); assert r=={'a':1,'ret':0,'msg':'ok'} and d=={'a':1}"
  python3 -c "from utility.file_delivery import SyncJob; j=SyncJob('a','b','t','/p','f',transport='quic',biz_type='cover',domain='d'); assert j.domain=='d' and j.biz_type=='cover'"
  python3 -m unittest utility.test_file_delivery -v
  ```
  Expected: 退出码 0；unittest 全绿；gen_res_dict/SyncJob 断言通过。

- [ ] **Step 5: Commit**

  ```bash
  git add cover/cover-worker/build_assets/utility/
  git commit -m "refactor(cover): align utility layer with tts/sound (userserver/task-status/reqid)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 2: cover worker `celery_worker.py` + `start_cover_worker.sh`

**Files:**
- Modify: `cover/cover-worker/build_assets/celery_worker.py`
- Modify: `cover/cover-worker/build_assets/start_cover_worker.sh`

**Interfaces:**
- Produces: `celery_worker.celery_app`，`conf.task_routes = {'task_cover.cover': {'queue': 'us_cover_queue'}, 'task_cover.vocal': {'queue': 'us_cover_queue'}, 'task_cover.instrument': {'queue': 'us_cover_queue'}}`；broker 用 `US_RABBITMQ_*`。

- [ ] **Step 1: 改写 `celery_worker.py`**

  - 环境变量由 `TTS_RABBITMQ_*` → `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
  - `Celery("cover_worker", broker=f"amqp://{US_RABBITMQ_USER}:{quote_plus(US_RABBITMQ_PASS)}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//", backend="rpc://")`。
  - `task_routes` 改为上面三个 `task_cover.*` → `us_cover_queue`。

- [ ] **Step 2: 改写 `start_cover_worker.sh` 的 `-Q`**

  `-Q gpu_cover_tasks` → `-Q us_cover_queue`（`-A gpu_cover_tasks` 与其余参数不变）。

- [ ] **Step 3: 验证**

  ```bash
  cd cover/cover-worker/build_assets
  python3 -m py_compile celery_worker.py
  grep -n "task_cover\|us_cover_queue\|US_RABBITMQ" celery_worker.py
  grep -n "\-Q us_cover_queue" start_cover_worker.sh
  ! grep -n "TTS_RABBITMQ\|gpu_vocal_tasks\|gpu_instrum_tasks" celery_worker.py
  ```
  Expected: py_compile 通过；`celery_worker.py` 命中 `US_RABBITMQ_*`、`task_cover.*`、`us_cover_queue`；`start_cover_worker.sh` 命中 `-Q us_cover_queue`；无 `TTT_RABBITMQ`/旧队列名残留。

- [ ] **Step 4: Commit**

  ```bash
  git add cover/cover-worker/build_assets/celery_worker.py cover/cover-worker/build_assets/start_cover_worker.sh
  git commit -m "refactor(cover): task_cover.* on us_cover_queue with US_RABBITMQ_*" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 3: 重写 `gpu_cover_tasks.py`

**Files:**
- Modify: `cover/cover-worker/build_assets/gpu_cover_tasks.py`

**Interfaces:**
- Consumes: `from celery_worker import celery_app`（Task 2）；`utility.log` 的 `set_reqid/clear_reqid`、`setup_logger`（Task 1）；`utility.userserver_client.update_task_status`（Task 1）；`utility.util.gen_res_dict`（Task 1）；`utility.file_delivery.SyncJob`（Task 1）。
- Produces:
  ```python
  @celery_app.task(name='task_cover.cover')
  def gpu_cover(modelcat, modelname, modelgender, upfileinfo, upgender, task_id='0', email='', domain='', lang='english')

  @celery_app.task(name='task_cover.vocal')
  def gpu_vocal(upfileinfo, task_id='0', email='', domain='')

  @celery_app.task(name='task_cover.instrument')
  def gpu_instrument(upfileinfo, task_id='0', email='', domain='')
  ```

- [ ] **Step 1: 改 import 与任务签名**

  - 删除 `from sql.mysql_db import update_ip_tool_record`。
  - 删除 `from utility.encrypt_util import encrypt_url, decrypt_url`。
  - `from utility.util import gen_reqid, gen_res_dict` → `from utility.util import gen_res_dict`。
  - `from utility.log import setup_logger` → `from utility.log import setup_logger, set_reqid, clear_reqid`。
  - 新增 `from utility.userserver_client import update_task_status`。
  - 三个装饰器：`name='gpu_cover_tasks.gpu_cover'` → `name='task_cover.cover'`；`name='gpu_cover_tasks.gpu_vocal'` → `name='task_cover.vocal'`；`name='gpu_cover_tasks.gpu_instrument'` → `name='task_cover.instrument'`。
  - 三个函数签名按上面 Interfaces 改（去掉 `first_ip`，新增 `domain`）。

- [ ] **Step 2: 包 `set_reqid`/`clear_reqid`，去掉 `gen_reqid`**

  - 每个任务函数首行 `set_reqid(task_id)`，并把整段 body 包进 `try: ... finally: clear_reqid()`。
  - 删除 `reqid = gen_reqid(task_id, start_time)`，改为 `reqid = task_id`（保留 body 内对 `reqid` 的日志引用不变）。
  - 删除 body 内所有日志/返回字符串里的 `first_ip:{first_ip}` 与 `ip:{first_ip}` token（`first_ip` 已不在签名）；成功返回改为 `f"CoverProcessed: {task_id}"` / `f"Vocal Processed: {task_id}"` / `f"Instrument Processed: {task_id}"`。

- [ ] **Step 3: 开头校验 `upfileinfo`（Review Focus #1）**

  三个任务在解包 `upfileinfo` 之前加：
  ```python
  if not isinstance(upfileinfo, dict) or not upfileinfo.get('relative_path'):
      msg = f'invalid upfileinfo, task_id:{task_id}'
      logger.warning(f'msg: {msg}')
      update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
      return f'cover process failed, task_id:{task_id}, msg:{msg}'   # vocal/instrument 相应改为 vocal/instrument 前缀
  ```

- [ ] **Step 4: `gpu_cover` 移入模型文件存在性校验（Review Focus #2）**

  在 `gpu_cover` 计算完 `model_path` / `model_index_path` / `model_file_path` 之后、`extract_vocals` 之前加：
  ```python
  if not (os.path.exists(model_index_path) and os.path.exists(model_file_path)):
      msg = f'model {modelcat}-{modelname} is not exists, task_id:{task_id}'
      logger.warning(f'{model_index_path} or {model_file_path} is not exists, {msg}')
      update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
      return f'cover process failed, task_id:{task_id}, msg:{msg}'
  ```

- [ ] **Step 5: 状态回写替换 + `domain`**

  三个任务里所有 `update_ip_tool_record(...)` 替换为 `update_task_status(logger, task_id, domain, <msg>, TaskStatus.FAILED)`（用该分支已定义的 `msg`/`validation_msg`/`sync_msg`；COVER 的 CUDA 失败分支先 `msg = 'CUDA health check failed'` 再传入）：
  - `gpu_cover`：CUDA 失败、`extract_vocals` 失败、`extractinfo` 校验失败（用 `validation_msg`）、`process_voice_cover` 失败、混音失败、异常分支、`enqueue_delivery_job` 失败（用 `sync_msg`）。
  - `gpu_vocal`：`extract_vocals` 失败、`convert_wav_to_mp3` 失败（try 内 `ret!=0` 与 except 两处，用 `msg`）、`enqueue_delivery_job` 失败（用 `sync_msg`）。
  - `gpu_instrument`：同 `gpu_vocal`。
  - 删除 `process_voice_cover` 内部（except 分支）的 `update_ip_tool_record(reqid, '', TaskStatus.FAILED)`——状态回写由调用方 `gpu_cover` 的 `ret != 0` 分支负责。
  - 三个任务的 `SyncJob(...)` 均新增 `domain=domain`（保留 `biz_type` 与 `short_uri_prefix` 不变）。

- [ ] **Step 6: 验证**

  ```bash
  cd cover/cover-worker/build_assets
  python3 -m py_compile gpu_cover_tasks.py
  grep -n "name='task_cover.cover'\|name='task_cover.vocal'\|name='task_cover.instrument'" gpu_cover_tasks.py
  grep -n "def gpu_cover(modelcat, modelname, modelgender, upfileinfo, upgender, task_id='0', email='', domain='', lang='english')" gpu_cover_tasks.py
  grep -n "def gpu_vocal(upfileinfo, task_id='0', email='', domain='')" gpu_cover_tasks.py
  grep -n "def gpu_instrument(upfileinfo, task_id='0', email='', domain='')" gpu_cover_tasks.py
  grep -n "update_task_status(logger, task_id, domain" gpu_cover_tasks.py
  grep -n "domain=domain" gpu_cover_tasks.py
  grep -n "set_reqid(task_id)\|clear_reqid()" gpu_cover_tasks.py
  ! grep -n "update_ip_tool_record\|sql.mysql_db\|gen_reqid\|encrypt_url\|first_ip" gpu_cover_tasks.py
  ```
  Expected: py_compile 通过；各 grep 命中；无 `update_ip_tool_record`/`sql.mysql_db`/`gen_reqid`/`encrypt_url`/`first_ip` 残留。

- [ ] **Step 7: Commit**

  ```bash
  git add cover/cover-worker/build_assets/gpu_cover_tasks.py
  git commit -m "refactor(cover): migrate gpu_cover/vocal/instrument to task_cover.* (async + userserver status)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 4: 删除 MySQL 层 + 切换 env_file（保留 QUIC）

**Files:**
- Delete: `cover/cover-worker/build_assets/sql/mysql_db.py`
- Delete: `cover/cover-worker/build_assets/config/db_conf.py`
- Modify: `cover/cover-worker/docker-compose.yml`
- Modify: `cover/cover-worker/docker-compose-preview.yml`

**Interfaces:**
- Produces: 仓库内不再存在 `sql.mysql_db`/`update_ip_tool_record`；compose 的 `env_file` 同时指向 `.env_mqdb` + `.env_usmqdb`。

- [ ] **Step 1: 删除两个文件，切 env_file**

  - `rm cover/cover-worker/build_assets/sql/mysql_db.py cover/cover-worker/build_assets/config/db_conf.py`（若 `sql/` 目录空了一并删除）。
  - `docker-compose.yml` 与 `docker-compose-preview.yml` 中原来的：
    ```yaml
    env_file:
      - /home/aigc/data/env/.env_mqdb
    ```
    改为：
    ```yaml
    env_file:
      - /home/aigc/data/env/.env_mqdb       # QUIC_CLIENT_HOST / QUIC_CLIENT_PORT
      - /home/aigc/data/env/.env_usmqdb     # US_RABBITMQ_* / US_COMMON_USERSERVER_* / INAPI_AUTH_TOKEN
    ```

- [ ] **Step 2: 验证全仓库无残留引用 + env_file 正确**

  ```bash
  grep -rn "mysql_db\|update_ip_tool_record\|add_ip_tool_record\|get_tool_out_path_by_task_id\|encrypt_url\|decrypt_url" cover/ uscm/ --include='*.py' || echo "NO_REFERENCES"
  grep -n "env_usmqdb\|env_mqdb" cover/cover-worker/docker-compose.yml cover/cover-worker/docker-compose-preview.yml
  ```
  Expected: 第一句输出 `NO_REFERENCES`；compose 两个文件都同时命中 `env_mqdb` 与 `env_usmqdb`。

- [ ] **Step 3: Commit**

  ```bash
  git add -A cover/cover-worker/build_assets/sql cover/cover-worker/build_assets/config/db_conf.py cover/cover-worker/docker-compose.yml cover/cover-worker/docker-compose-preview.yml
  git commit -m "refactor(cover): drop MySQL status layer, keep QUIC env + .env_usmqdb" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 5: uscm 异步 `/api/cover`、`/api/vocal`、`/api/instrument`

**Files:**
- Modify: `uscm/build_assets/uscm_server.py`

**Interfaces:**
- Consumes: `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract, gpu_cover, gpu_vocal, gpu_instrument`；`ModuleType.COVER/VOCAL_ISOLATOR/VOCAL_REMOVER`；`post_generate_add`；`validate_audio_url`；`validate_signature`；`validate_request`；`validate_gender`；`gen_reqid`/`gen_res_dict`；`DEFAULT_USER_FIRST_IP`。
- Produces:
  - `@app.post("/api/cover")` → `gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, email=email, domain=domain, lang=lang)`。
  - `@app.post("/api/vocal")` → `gpu_vocal.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
  - `@app.post("/api/instrument")` → `gpu_instrument.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
  - 三者均返回 `{"ret":0,"msg":"ok","reqid":reqid}`。

- [ ] **Step 1: 扩展 import，删除 `cover_model_dir`**

  - `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract` → 追加 `, gpu_cover, gpu_vocal, gpu_instrument`。
  - 删除模块级 `cover_model_dir = '/app/models/cover_service_models'`（模型校验已移入 worker）。

- [ ] **Step 2: 重写 `/api/cover` body（对照 `/api/genaudio` + `/api/extractaudio`）**

  - `data = await request.form()` → `json_post_raw = await request.json()`。字段（**保留 `uid`**）：
    `uid = json_post_raw.get("uid", 0)`、`email = json_post_raw.get("email", '')`、`domain = json_post_raw.get("domain", '')`、
    `subscript = json_post_raw.get("subscript", 0)`、`modelname = json_post_raw.get("modelname", '')`、
    `modelcat = json_post_raw.get("modelcat", '')`、`modelgender = json_post_raw.get("modelgender", '').lower()`、
    `lang = json_post_raw.get("lang", 'english')`、`converturl = json_post_raw.get("converturl", '')`、
    `upgender = json_post_raw.get("upgender", '').lower()`、`timestamp = json_post_raw.get('tstamp', 0)`、`signature = json_post_raw.get('snature', '')`。
  - `first_ip = get_real_ip(request).split(',')[0].strip() or DEFAULT_USER_FIRST_IP`；`reqid = gen_reqid(uid, tm)`；`today`/`datetime` 同 genaudio。
  - 校验顺序：
    1. `validate_gender(upgender)` 与 `validate_gender(modelgender)` 任一为 False → `gen_res_dict(1, ...)`。
    2. `modelname`/`modelcat`/`lang`/`converturl` 任一 strip 空 → `gen_res_dict(1, ...)`。
    3. `ret, msg, upfileinfo = validate_audio_url(converturl, reqid, first_ip, email, today, datetime, logger)`；`ret != 0` → `gen_res_dict(ret, msg)`。
    4. `ret, msg = validate_signature(logger, timestamp, signature, converturl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.COVER)`；`ret != 0` → `gen_res_dict(ret, msg)`。
    5. `ret, msg = validate_request(logger, real_ip, referer, reqid, email, subscript, today, uid, ModuleType.COVER, domain, request)`；`ret != 0` → `gen_res_dict(ret, msg)`。
  - `generate_content = f'{modelcat}|{modelname}|{converturl}'`；`task_req = json.dumps({"uid": uid, "email": email, "modelcat": modelcat, "modelname": modelname, "modelgender": modelgender, "upgender": upgender, "lang": lang, "converturl": converturl}, ensure_ascii=False)`。
  - `post_generate_add(logger, reqid, first_ip, email, uid, domain, generate_content, real_ip, ModuleType.COVER, task_cmd='/api/cover', task_req=task_req)`。
  - `try: gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, email=email, domain=domain, lang=lang) except Exception as e: return gen_res_dict(1, f'dispatch failed: {e}')`。
  - 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`；**删除** `model_path`/`os.path.exists` 校验、`add_ip_tool_record`、`get_tool_out_path_by_task_id`、`encrypt_url`、`uri`/`enurl`、`tabtype`、`logger_cover`（日志统一走 `logger`）。

- [ ] **Step 3: 重写 `/api/vocal` 与 `/api/instrument` body**

  - 两者结构一致，差异仅在字段/模块类型/派发：
    - `/api/vocal`：`upurl = json_post_raw.get("upurl", '')`；空 `upurl` → `gen_res_dict(1, ...)`；`validate_audio_url(upurl, ...)`；`validate_signature(..., upurl, ..., uid, email, ModuleType.VOCAL_ISOLATOR)`；`validate_request(..., uid, ModuleType.VOCAL_ISOLATOR, domain, ...)`；`generate_content = upfileinfo['relative_path']`；`task_req = json.dumps({"uid": uid, "email": email, "upurl": upurl}, ensure_ascii=False)`；`post_generate_add(..., uid, domain, ..., ModuleType.VOCAL_ISOLATOR, task_cmd='/api/vocal', ...)`；`gpu_vocal.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
    - `/api/instrument`：同上，但 `ModuleType.VOCAL_REMOVER`、`task_cmd='/api/instrument'`、`gpu_instrument.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
  - 删除 `add_ip_tool_record`、`get_tool_out_path_by_task_id`、`encrypt_url`、`uri`/`enurl`、`logger_cover`/`logger_echobeat_cover`（统一走 `logger`）。

- [ ] **Step 4: 验证**

  ```bash
  cd uscm/build_assets
  python3 -m py_compile uscm_server.py
  grep -n "gpu_cover\|gpu_vocal\|gpu_instrument" uscm_server.py
  grep -n 'app.post("/api/cover")\|app.post("/api/vocal")\|app.post("/api/instrument")' uscm_server.py
  grep -n "gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, email=email, domain=domain, lang=lang)" uscm_server.py
  grep -n "gpu_vocal.delay(upfileinfo, task_id=reqid, email=email, domain=domain)" uscm_server.py
  grep -n "gpu_instrument.delay(upfileinfo, task_id=reqid, email=email, domain=domain)" uscm_server.py
  grep -n "await request.json()" uscm_server.py
  ! grep -n "get_tool_out_path_by_task_id\|encrypt_url\|add_ip_tool_record\|logger_cover\|logger_echobeat_cover\|request.form()\|cover_model_dir" uscm_server.py
  ```
  Expected: py_compile 通过；`.delay` 参数与 Task 3 签名对应；无旧同步轮询/encrypt/form/`cover_model_dir` 残留。

- [ ] **Step 5: Commit**

  ```bash
  git add uscm/build_assets/uscm_server.py
  git commit -m "feat(uscm): async /api/cover /api/vocal /api/instrument dispatching to task_cover.*" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

## Self-Review 摘要（已完成）

- **Spec 覆盖**：设计 §3.1（celery_worker / start_worker / gpu_cover_tasks / utility / 删除 mysql / docker-compose / 运行时依赖）对应 Task 2/2、Task 3、Task 1/4；§3.2（uscm 三端点）对应 Task 5。已覆盖。
- **类型一致**：`gpu_cover/gpu_vocal/gpu_instrument` 签名在 Task 3 定义、Task 5 消费；`SyncJob.domain`/`SyncJob.biz_type`/`update_task_status`/`set_reqid` 在 Task 1 定义、Task 3 消费。
- **Review Focus 五项**：分别落点 Task 3（upfileinfo 缺键、模型缺失、enqueue 失败、未捕获异常）与 Task 5↔Task 3（参数一致）。
- **关键纠偏**：`util.py` 不能盲 cp tts/sound 版（cover 只需 `gen_res_dict`）；`file_delivery.py` 不能 `cp` 覆盖 tts/sound 版（cover 必须保留 `biz_type`）；compose 不能像 audio 那样单纯切单个 `.env_usmqdb`——必须同时保留 `.env_mqdb`（QUIC 变量）；`gpu_cover` 需把 producer 的模型存在性校验移入 worker；uscm 三端点保留 `uid` 字段、删除 `first_ip`/`tabtype` 与同步轮询/encrypt。
- **比例**：计划以决策（文件名、签名、字段名、验证命令、替换点）为主，不转录实现代码。