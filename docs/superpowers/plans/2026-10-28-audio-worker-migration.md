# audio-worker 迁移 + uscm /api/extractaudio 异步化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `video/audio-worker` 迁到新架构（对齐 `sound/stable-sound-worker` / `tts/indextts-worker`），并把 `uscm` 的 `POST /api/extractaudio` 从同步 form 改为异步 JSON。

**Architecture:** worker 以 `@celery_app.task(name='task_audio_extract')` 注册到 `us_audio_queue`，状态回写走 UserServer HTTP（`update_task_status` + `TaskStatus` + `domain`）；uscm 侧 `post_generate_add` 落库后 `audio_extract.delay(...)` 异步派发、立即返回 `reqid`。

**Tech Stack:** Python 3 / Celery / FastAPI / UserServer HTTP（`/inapi/*`）/ Docker。

**Spec:** `docs/superpowers/specs/2026-10-28-audio-worker-migration-design.md`

## Global Constraints

- 任务名 `task_audio_extract`，队列 `us_audio_queue`。
- worker 状态回写只用 `utility.userserver_client.update_task_status(logger, task_id, domain, payload, TaskStatus)`；不再 import `sql.mysql_db`。
- 环境变量用 `US_RABBITMQ_*` / `US_COMMON_USERSERVER_HOST/PORT` / `INAPI_AUTH_TOKEN`；`docker-compose` 的 `env_file` 用 `/home/aigc/data/env/.env_usmqdb`。
- `audio_tasks.py` 的 `extract_audio` 返回：失败 `gen_res_dict(1, msg)`，成功 `f"ExtractAudioCompleted: {task_id}"`。
- 接口 `/api/extractaudio` 用 `await request.json()` + `userid`，签名对象 `upurl`，异步只返回 `{"ret":0,"msg":"ok","reqid":reqid}`（无 `uri`/`enurl`）。
- 不新增 `requirements.txt`、不改 Dockerfile 安装步骤（base 镜像 `audio-extract-image:0.4` 已含 `requests`/`fastapi`）。
- 每个 commit 信息末尾追加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 干净硬切：不排空旧队列、不保留 `sql/mysql_db.py`/`config/db_conf.py`、不做共存窗口。

## Review Focus

1. **`upfileinfo` 缺 `relative_path`（或非 dict）**：应 `update_task_status(... FAILED)` + `return gen_res_dict(1, msg)`，不得 KeyError 抛异常留 PENDING。→ Task 3 开头校验。
2. **`split_audio` 失败**（转码/缺文件）：走 `update_task_status(... FAILED)`，不得再 `update_ip_tool_record`。→ Task 3。
3. **`enqueue_delivery_job` 返回 False**（队列满/worker 未启动）：须 `update_task_status(... FAILED)`。→ Task 3。
4. **未捕获异常**：整段 body 用 `try/except` 包住，except 分支 `update_task_status(... FAILED)`，不得让任务卡 PENDING。→ Task 3。
5. **producer/worker 参数一致**：`audio_extract.delay(upfileinfo, task_id=reqid, email=email, domain=domain)` 与 worker 签名 `extract_audio(upfileinfo, task_id='0', email='', domain='')` 一一对应。→ Task 5 与 Task 3 对照。

---

### Task 1: audio worker utility 层对齐 sound/tts

**Files:**
- Create: `video/audio-worker/build_assets/utility/internal_auth.py`（拷贝 sound 同名文件）
- Create: `video/audio-worker/build_assets/utility/userserver_client.py`（拷贝 sound 同名文件）
- Modify: `video/audio-worker/build_assets/utility/log.py`（覆盖为 sound 同名文件，带 reqid 上下文）
- Modify: `video/audio-worker/build_assets/utility/file_delivery.py`（覆盖为 sound 同名文件）
- Modify: `video/audio-worker/build_assets/utility/util.py`（重写，见下）
- Modify: `video/audio-worker/build_assets/utility/test_file_delivery.py`（按 audio 值对齐 sound 版）

**Interfaces:**
- Produces:
  - `utility.internal_auth.bearer_auth_header() -> dict`
  - `utility.userserver_client.update_task_status(logger, task_id, domain, payload_msg_or_uri, task_status: TaskStatus) -> tuple[int, str]`
  - `utility.log.set_reqid(reqid) / clear_reqid() / setup_logger(...) / logset(...)`
  - `utility.util.gen_res_dict(ret: int, msg: str, dt=None) -> dict`、`utility.util.format_timing_stats(timing_stats: dict) -> str`
  - `utility.file_delivery.SyncJob(source_file, target_dir, task_id, short_uri_prefix, output_file, proxies=[], transport='quic', domain='')`
  - `utility.file_delivery.enqueue_delivery_job(logger, job) -> bool`、`start_file_delivery(logger, transports)`

- [ ] **Step 1: 拷贝 4 个文件并重写 `util.py`**

  - `cp sound/stable-sound-worker/build_assets/utility/internal_auth.py video/audio-worker/build_assets/utility/internal_auth.py`
  - `cp sound/stable-sound-worker/build_assets/utility/userserver_client.py video/audio-worker/build_assets/utility/userserver_client.py`
  - `cp sound/stable-sound-worker/build_assets/utility/log.py video/audio-worker/build_assets/utility/log.py`
  - `cp sound/stable-sound-worker/build_assets/utility/file_delivery.py video/audio-worker/build_assets/utility/file_delivery.py`
  - `util.py` 重写为（**不能直接 cp sound 版**——audio 仍需 `format_timing_stats`，且要丢掉 `gen_reqid` 的 numpy 依赖）：
    ```python
    # -*- coding: utf-8 -*-
    def gen_res_dict(ret: int, msg: str, dt=None):
        d = {}
        if dt:
            d.update(dt)
        d['ret'] = ret
        d['msg'] = msg
        return d


    def format_timing_stats(timing_stats: dict) -> str:
        """格式化耗时统计为易读的字符串"""
        if not timing_stats:
            return "{}"
        formatted = []
        for key, value in sorted(timing_stats.items()):
            if isinstance(value, (int, float)):
                formatted.append(f"{key}:{value:.3f}s")
            else:
                formatted.append(f"{key}:{value}")
        return "{" + ", ".join(formatted) + "}"
    ```
  - 覆盖 `file_delivery.py` 后，把模块 docstring 首行的 "sound" 改为 "audio-extractor"（其余不动）。

- [ ] **Step 2: 改写 `test_file_delivery.py`（对齐 sound 版 + audio 值）**

  以 `sound/stable-sound-worker/build_assets/utility/test_file_delivery.py` 为底稿，做以下替换后覆盖 audio 版：
  - 删除 `resolve_delivery_transport`（输出 list 里已经没有该函数）相关的 `TestResolveTransport` 类与 import；删除 `from utility.file_sync_config import get_file_sync_transport`。
  - `TestCompleteSyncJob` 改用 `with patch("utility.file_delivery.update_task_status") as mock_update:`，断言改为 `mock_update.assert_called_once_with(logger, "t1", "", "<uri>", TaskStatus.COMPLETE/FAILED)`（参照 sound 版两个用例）。
  - `_job` 默认值：`short_uri_prefix="/sepaudio/2026-09-13/1.2.3.4"`、`output_file="song_audio.mp3"`。
  - `TestQuicHttpTransport`：默认 `biz_type="extract_audio"`；`test_x_filename_sounds_prefix` 重命名为 `test_x_filename_sepaudio_prefix`，断言 `quic_x_filename("/sepaudio/2026-09-13/1.2.3.4", "song_audio.mp3", "extract_audio") == "2026-09-13/1.2.3.4/song_audio.mp3"`；`X-filename == "2026-09-13/1.2.3.4/song_audio.mp3"`、`X-biz-type == "extract_audio"`。

- [ ] **Step 3: 验证语法与 `gen_res_dict`/`SyncJob.domain`**

Run:
```bash
cd video/audio-worker/build_assets
python3 -m py_compile utility/internal_auth.py utility/userserver_client.py utility/log.py utility/file_delivery.py utility/util.py utility/test_file_delivery.py
python3 -c "from utility.util import gen_res_dict as g; assert g(1,'x')=={'ret':1,'msg':'x'}; d={'a':1}; r=g(0,'ok',d); assert r=={'a':1,'ret':0,'msg':'ok'} and d=={'a':1}"
python3 -c "from utility.file_delivery import SyncJob; j=SyncJob('a','b','t','/p','f',transport='quic',domain='d'); assert j.domain=='d'"
```
Expected: 退出码 0，无输出。

- [ ] **Step 4: Commit**

```bash
git add video/audio-worker/build_assets/utility/
git commit -m "refactor(audio): align utility layer with sound (userserver/task-status/reqid)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: audio worker `celery_worker.py` + `start_audio_worker.sh`

**Files:**
- Modify: `video/audio-worker/build_assets/celery_worker.py`
- Modify: `video/audio-worker/build_assets/start_audio_worker.sh`

**Interfaces:**
- Produces: `celery_worker.celery_app`，`conf.task_routes = {'task_audio_extract': {'queue': 'us_audio_queue'}}`；broker 用 `US_RABBITMQ_*`。

- [ ] **Step 1: 改写 `celery_worker.py`**

  - 环境变量由 `TTS_RABBITMQ_*`/`RABBITMQ_*` → `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
  - `Celery("audio_worker", broker=f"amqp://{US_RABBITMQ_USER}:{quote_plus(US_RABBITMQ_PASS)}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//", backend="rpc://")`。
  - `task_routes = {'task_audio_extract': {'queue': 'us_audio_queue'}}`。

- [ ] **Step 2: 改写 `start_audio_worker.sh` 的 `-Q`**

  `-Q audio_tasks` → `-Q us_audio_queue`（`-A audio_tasks` 与 `--concurrency=${CELERY_CONCURRENCY:-3}` 不变）。

- [ ] **Step 3: 验证**

Run:
```bash
cd video/audio-worker/build_assets
python3 -m py_compile celery_worker.py
grep -n "task_audio_extract\|us_audio_queue\|US_RABBITMQ" celery_worker.py
grep -n "\-Q us_audio_queue" start_audio_worker.sh
! grep -n "TTS_RABBITMQ\|audio_tasks.extract_audio" celery_worker.py
```
Expected: py_compile 通过；`celery_worker.py` 命中 `US_RABBITMQ_*`、`task_audio_extract`、`us_audio_queue`；`start_audio_worker.sh` 命中 `-Q us_audio_queue`；无 `TTT_RABBITMQ`/`audio_tasks.extract_audio` 残留。

- [ ] **Step 4: Commit**

```bash
git add video/audio-worker/build_assets/celery_worker.py video/audio-worker/build_assets/start_audio_worker.sh
git commit -m "refactor(audio): task_audio_extract on us_audio_queue with US_RABBITMQ_*" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 重写 `audio_tasks.py`

**Files:**
- Modify: `video/audio-worker/build_assets/audio_tasks.py`

**Interfaces:**
- Consumes: `from celery_worker import celery_app`（Task 2）；`utility.log` 的 `set_reqid/clear_reqid`（Task 1）；`utility.userserver_client.update_task_status`（Task 1）；`utility.util.format_timing_stats/gen_res_dict`（Task 1）；`from config.comm_config import get_remote_sepaudio_dir`；`from utility.file_sync_config import get_file_sync_transport, get_quic_client_host, get_quic_client_port`。
- Produces:
  ```python
  @celery_app.task(name='task_audio_extract')
  def extract_audio(upfileinfo, task_id='0', email='', domain=''):
  ```

- [ ] **Step 1: 改 import 与任务签名**

  - 删除 `from sql.mysql_db import update_ip_tool_record`。
  - `from utility.util import gen_reqid, format_timing_stats` → `from utility.util import gen_res_dict, format_timing_stats`。
  - 新增 `from utility.userserver_client import update_task_status`。
  - `from utility.log import setup_logger` → `from utility.log import setup_logger, set_reqid, clear_reqid`。
  - 装饰器 `@celery_app.task(name='audio_tasks.extract_audio')` → `@celery_app.task(name='task_audio_extract')`。
  - 签名 `def extract_audio(upfileinfo, task_id='0', first_ip='', email='')` → `def extract_audio(upfileinfo, task_id='0', email='', domain='')`。

- [ ] **Step 2: 包 `set_reqid`/`clear_reqid`，去掉 `gen_reqid`**

  - 函数体首行 `set_reqid(task_id)`，末尾 `finally: clear_reqid()`。
  - 删除 `reqid = gen_reqid(task_id, start_time)`；后续 log 与 `split_audio(upfileinfo, reqid)` 中的 `reqid` 一律直接用 `task_id`（`split_audio(upfileinfo, task_id)`）。

- [ ] **Step 3: 开头校验 `upfileinfo`（Review Focus #1）**

  在解包 `upfileinfo` 之前加：
  ```python
  if not isinstance(upfileinfo, dict) or not upfileinfo.get('relative_path'):
      msg = f'invalid upfileinfo, task_id:{task_id}, worker:{WORKER_HOSTNAME}'
      logger.warning(f'msg: {msg}')
      update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
      return gen_res_dict(1, msg)
  ```
  （`WORKER_HOSTNAME` 可在文件顶部 `socket.gethostname()` 定义，与 indextts/sound 保持一致。）

- [ ] **Step 4: 三处状态回写替换 + `domain` + 返回**

  - `split_audio` 失败分支：`update_ip_tool_record(task_id, '', TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`，`return f'extract audio failed, task_id:{task_id}, msg:{msg}'` → `return gen_res_dict(1, msg)`。
  - `enqueue_delivery_job` 返回 False 分支：`update_ip_tool_record(task_id, sync_msg, TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, sync_msg, TaskStatus.FAILED)`，`return f'file sync failed, task_id:{task_id}, msg:{sync_msg}'` → `return gen_res_dict(1, sync_msg)`。
  - `SyncJob(...)` 增加 `domain=domain`。
  - 成功返回 `result = f"ExtractAudioCompleted: {task_id}, ip:{first_ip}, email:{email}"` → `result = f"ExtractAudioCompleted: {task_id}"`（去掉 `first_ip`）。

- [ ] **Step 5: 整段 body 包 try/except（Review Focus #4）**

  把「extract audio + SyncJob 构建 + enqueue」主流程包进 `try/except Exception as e`，except 分支：
  ```python
  msg = f'Error in extract_audio for task_id:{task_id}: {e}'
  logger.error(f'msg: {msg}')
  update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
  return gen_res_dict(1, msg)
  ```
  （保留现有 `split_audio`/`convert_to_mp3`/`format_timing_stats`/`initialize_file_delivery` 逻辑与 `short_uri_prefix=f'/sepaudio/{update_str}/{userip}'` 不变。）

- [ ] **Step 6: 验证**

Run:
```bash
cd video/audio-worker/build_assets
python3 -m py_compile audio_tasks.py
grep -n "name='task_audio_extract'" audio_tasks.py
grep -n "def extract_audio(upfileinfo, task_id='0', email='', domain='')" audio_tasks.py
grep -n "update_task_status(logger, task_id, domain" audio_tasks.py
grep -n "domain=domain" audio_tasks.py
grep -n "set_reqid(task_id)\|clear_reqid()" audio_tasks.py
! grep -n "update_ip_tool_record\|sql.mysql_db\|gen_reqid\|first_ip" audio_tasks.py
```
Expected: py_compile 通过；各 grep 命中相应行；`audio_tasks.py` 无 `update_ip_tool_record`/`sql.mysql_db`/`gen_reqid`/`first_ip` 残留。

- [ ] **Step 7: Commit**

```bash
git add video/audio-worker/build_assets/audio_tasks.py
git commit -m "refactor(audio): migrate extract_audio to task_audio_extract (async + userserver status)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 删除 MySQL 层 + 切换 env_file

**Files:**
- Delete: `video/audio-worker/build_assets/sql/mysql_db.py`
- Delete: `video/audio-worker/build_assets/config/db_conf.py`
- Modify: `video/audio-worker/docker-compose.yml`

**Interfaces:**
- Produces: 仓库内不再存在 `sql.mysql_db`/`update_ip_tool_record`；docker-compose `env_file` 指向 `.env_usmqdb`。

- [ ] **Step 1: 删除两个文件，切 env_file**

  - `rm video/audio-worker/build_assets/sql/mysql_db.py video/audio-worker/build_assets/config/db_conf.py`（若 `sql/` 目录空了可一并删除）。
  - `docker-compose.yml` 中 `- /home/aigc/data/env/.env_mqdb` → `- /home/aigc/data/env/.env_usmqdb`。

- [ ] **Step 2: 验证全仓库无残留引用**

Run:
```bash
grep -rn "mysql_db\|update_ip_tool_record\|add_ip_tool_record\|get_tool_out_path_by_task_id" video/ uscm/ --include='*.py' || echo "NO_REFERENCES"
grep -n "env_usmqdb" video/audio-worker/docker-compose.yml
```
Expected: `NO_REFERENCES`（或仅命中了已删除路径之外无命中）；docker-compose 命中 `env_usmqdb`。

- [ ] **Step 3: Commit**

```bash
git add -A video/audio-worker/build_assets/sql video/audio-worker/build_assets/config/db_conf.py video/audio-worker/docker-compose.yml
git commit -m "refactor(audio): drop MySQL status layer, switch env_file to .env_usmqdb" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: uscm 异步 `/api/extractaudio`

**Files:**
- Modify: `uscm/build_assets/uscm_server.py`

**Interfaces:**
- Consumes: `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract`；`ModuleType.AUDIO_EXTRACTOR`；`post_generate_add`；`validate_audio_url`；`validate_signature`；`validate_request`；`gen_reqid`/`gen_res_dict`；`DEFAULT_USER_FIRST_IP`。
- Produces: `@app.post("/api/extractaudio")` → `audio_extract.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`，返回 `{"ret":0,"msg":"ok","reqid":reqid}`。

- [ ] **Step 1: 扩展 import**

  `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract`。

- [ ] **Step 2: 重写 `/api/extractaudio` body（对照 `/api/genaudio`）**

  - `data = await request.form()` → `json_post_raw = await request.json()`；字段改为 `upurl = json_post_raw.get('upurl','')`、`userid = json_post_raw.get('userid', 0)`、`email`、`subscript = json_post_raw.get('subscript', 0)`、`domain`、`tstamp`、`snature`（不用 `uid`/`data.get`）。
  - `first_ip = get_real_ip(request).split(',')[0].strip() or DEFAULT_USER_FIRST_IP`；`reqid = gen_reqid(userid, start_tm)`；`today`/`datetime` 同 genaudio。
  - 校验顺序：空 `upurl` 检查 → `validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger)` → `(ret, msg, upfileinfo)` → `validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, userid, email, ModuleType.AUDIO_EXTRACTOR)` → `validate_request(logger, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.AUDIO_EXTRACTOR, domain, request)`。
  - `generate_content = upfileinfo['relative_path']`；`task_req = json.dumps({"first_ip": first_ip, "userid": userid, "email": email, "upurl": upurl}, ensure_ascii=False)`。
  - `post_generate_add(logger, reqid, first_ip, email, userid, domain, generate_content, real_ip, ModuleType.AUDIO_EXTRACTOR, task_cmd='/api/extractaudio', task_req=task_req)`。
  - `audio_extract.delay(upfileinfo, task_id=reqid, email=email, domain=domain)`。
  - 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`；**删除** `add_ip_tool_record`、`task_audio_extract.delay(...)`（旧名）、`get_tool_out_path_by_task_id`、`encrypt_url`、`uri`/`enurl`、`logger_audio_extract`（日志统一走 `logger`）。

- [ ] **Step 3: 验证**

Run:
```bash
cd uscm/build_assets
python3 -m py_compile uscm_server.py
grep -n "audio_extract" uscm_server.py
grep -n 'app.post("/api/extractaudio")' uscm_server.py
grep -n "ModuleType.AUDIO_EXTRACTOR" uscm_server.py
grep -n "audio_extract.delay(upfileinfo, task_id=reqid, email=email, domain=domain)" uscm_server.py
grep -n "await request.json()" uscm_server.py
! grep -n "get_tool_out_path_by_task_id\|encrypt_url\|logger_audio_extract\|add_ip_tool_record\|request.form()" uscm_server.py
```
Expected: py_compile 通过；`.delay` 参数与 Task 3 的 `extract_audio(upfileinfo, task_id, email, domain)` 对应；无旧同步轮询/encrypt/form 残留。

- [ ] **Step 4: Commit**

```bash
git add uscm/build_assets/uscm_server.py
git commit -m "feat(uscm): async /api/extractaudio dispatching to task_audio_extract" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 摘要（已完成）

- **Spec 覆盖**：设计 §4.1（celery_worker / start_worker / audio_tasks / utility / 删除 mysql / docker-compose）对应 Task 2/2、Task 3、Task 1/4；§4.2（uscm endpoint）对应 Task 5。已覆盖。
- **类型一致**：`extract_audio` 签名在 Task 3 定义、Task 5 消费；`SyncJob.domain`/`update_task_status`/`set_reqid` 在 Task 1 定义、Task 3 消费；`audio_extract` 在 Task 5 定义并消费。
- **Review Focus 五项**：分别落点 Task 3（upfileinfo 缺键、split_audio 失败、enqueue 失败、未捕获异常）与 Task 5↔Task 3（参数一致）。
- **关键纠偏**：`util.py` 不能盲 cp sound 版——audio 仍需 `format_timing_stats`，且须去掉 `gen_reqid`（numpy）依赖；`test_file_delivery.py` 需做 audio 专用值替换并去除 `resolve_delivery_transport`；`audio_tasks.py` 需把 `gen_reqid(task_id)` 改为直接 `task_id`。
- **比例**：计划以决策（文件名、签名、验证命令、替换点）为主，不转录实现代码。