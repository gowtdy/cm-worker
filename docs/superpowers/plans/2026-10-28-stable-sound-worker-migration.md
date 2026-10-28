# stable-sound-worker 迁移 + uscm /api/gensound 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `sound/stable-sound-worker` 迁到新架构（对齐 `tts/indextts-worker`），并在 `uscm` 新增异步 `POST /api/gensound`。

**Architecture:** 参照 `tts/indextts-worker` 的模式：worker 以 `@celery_app.task(name='task_gen_sound')` 注册到 `us_sound_queue`，状态回写走 UserServer HTTP（`update_task_status` + `TaskStatus` + `domain`）；uscm 侧 `post_generate_add` 落库后 `gpu_gen_sound.delay(...)` 异步派发、立即返回 `reqid`。

**Tech Stack:** Python 3 / Celery / FastAPI / UserServer HTTP（`/inapi/*`）/ Docker。

**Spec:** `docs/superpowers/specs/2026-10-28-stable-sound-worker-migration-design.md`

## Global Constraints

- 任务名 `task_gen_sound`，队列 `us_sound_queue`。
- worker 状态回写只用 `utility.userserver_client.update_task_status(logger, task_id, domain, payload, TaskStatus)`。
- 环境变量用 `US_RABBITMQ_*` / `US_COMMON_USERSERVER_HOST/PORT` / `INAPI_AUTH_TOKEN`。
- 输出文件名 `sound_{datetime_str}_{random_number}.mp3`，`datetime_str = time.strftime('%Y%m%d_%H%M%S')`，`random_number = random.randint(1, 1000)`。
- 删除 `sql/mysql_db.py` 与 `config/db_conf.py`；硬切，不共存、不排空旧队列。
- `requirements.txt` 增加 `requests`、`fastapi`。
- 每个 commit 信息末尾追加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## Review Focus

1. **空/空白 text**：应走 `update_task_status(... FAILED)` 并 `return gen_res_dict(1, msg)`，不得抛异常或留 PENDING。→ Task 3 验证时 grep 空文本校验分支。
2. **length/num 越界**（`length<=0||length>=60`、`num<=0||num>=4`）：钳制到默认 15 / 1，不崩溃。→ Task 3 保留原钳制逻辑并 grep 确认。
3. **入队失败**（队列满/worker 未启动）：`enqueue_delivery_job` 返回 False 时须 `update_task_status(... FAILED)`。→ Task 3、Task 1 共同覆盖。
4. **推理抛异常**：except 分支须 `update_task_status(... FAILED)` 并清理 GPU 缓存，不得让任务卡 PENDING。→ Task 3。
5. **producer/worker 参数顺序不一致**：`gpu_gen_sound.delay(text, length, num, userid, reqid, email, domain=domain)` 的位置参数必须与 worker 签名 `(text, length, num, userid, task_id, email, domain)` 完全对应。→ Task 5 的 `.delay` 调用与 Task 3 的签名对照校验。

---

### Task 1: sound worker utility 层对齐 tts

**Files:**
- Create: `sound/stable-sound-worker/build_assets/utility/module_enum.py`（拷贝 tts 同名文件）
- Create: `sound/stable-sound-worker/build_assets/utility/internal_auth.py`（拷贝 tts 同名文件）
- Create: `sound/stable-sound-worker/build_assets/utility/userserver_client.py`（拷贝 tts 同名文件）
- Create: `sound/stable-sound-worker/build_assets/utility/log.py`（拷贝 tts 同名文件）
- Modify: `sound/stable-sound-worker/build_assets/utility/util.py`（重写为干净的 `gen_res_dict`）
- Modify: `sound/stable-sound-worker/build_assets/utility/file_delivery.py`（对齐 tts 版，见下）

**Interfaces:**
- Produces:
  - `utility.module_enum.TaskStatus`（枚举 `PENDING/COMPLETE/FAILED`）、`ModuleType`
  - `utility.userserver_client.update_task_status(logger, task_id, domain, payload_msg_or_uri, task_status: TaskStatus) -> tuple[int, str]`
  - `utility.log.set_reqid(reqid) / clear_reqid() / setup_logger(...) / logset(...)`
  - `utility.util.gen_res_dict(ret: int, msg: str, dt: dict | None = None) -> dict`
  - `utility.file_delivery.SyncJob(source_file, target_dir, task_id, short_uri_prefix, output_file, proxies=[], transport='quic', domain='')`
  - `utility.file_delivery.enqueue_delivery_job(logger, job) -> bool`、`start_file_delivery(logger, transports)`

- [ ] **Step 1: 拷贝 4 个纯工具文件并改写 util.py 的 `gen_res_dict`**

  - `module_enum.py` / `internal_auth.py` / `userserver_client.py` / `log.py` 与 tts 同名文件逐字节一致（`cp`）。
  - `util.py` 重写为（去掉 numpy 依赖与坏实现）：
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

- [ ] **Step 2: 改写 `file_delivery.py`（以 tts 版为蓝本，去掉 VIDEO 路径）**

  相对现状的差异：`SyncJob` 增加 `domain: str = ""`；顶部新增 `from utility.log import clear_reqid, set_reqid`、`from utility.module_enum import TaskStatus`、`from utility.userserver_client import update_task_status`；`complete_sync_job` 用 `update_task_status` 替换 `sql.mysql_db.update_ip_sounds_record`；`_worker_loop` 加 `set_reqid`/`clear_reqid`；保留 `get_file_sync_transport()` 直连逻辑（不引入 tts 的 `resolve_delivery_transport`/`VIDEO_SCRIPT_TTS_EMAIL`）。

- [ ] **Step 3: 验证语法与 `gen_res_dict` 行为**

Run:
```bash
cd sound/stable-sound-worker/build_assets
python3 -m py_compile utility/module_enum.py utility/internal_auth.py utility/userserver_client.py utility/log.py utility/util.py utility/file_delivery.py
python3 -c "from utility.util import gen_res_dict as g; assert g(1,'x')=={'ret':1,'msg':'x'}; d={'a':1}; r=g(0,'ok',d); assert r=={'a':1,'ret':0,'msg':'ok'} and d=={'a':1}"
```
Expected: 无输出、退出码 0（py_compile 通过；断言全部成立）。

- [ ] **Step 4: Commit**

```bash
git add sound/stable-sound-worker/build_assets/utility/
git commit -m "refactor(sound): align utility layer with indextts (userserver/task-status/reqid)"
```

---

### Task 2: sound worker `celery_worker.py` + `start_worker.sh`

**Files:**
- Modify: `sound/stable-sound-worker/build_assets/celery_worker.py`
- Modify: `sound/stable-sound-worker/build_assets/start_worker.sh`

**Interfaces:**
- Consumes: 无（独立）。
- Produces: `celery_worker.celery_app`，`conf.task_routes = {'task_gen_sound': {'queue': 'us_sound_queue'}}`；broker 用 `US_RABBITMQ_*`。

- [ ] **Step 1: 改写 `celery_worker.py`**

  - 环境变量由 `TTS_RABBITMQ_*` → `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
  - `Celery("sound_worker", broker=f"amqp://{US_RABBITMQ_USER}:{quote_plus(US_RABBITMQ_PASS)}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//", backend="rpc://")`。
  - `task_routes = {'task_gen_sound': {'queue': 'us_sound_queue'}}`。
  - 删除文件末尾 `import gpu_sound_tasks`。

- [ ] **Step 2: 改写 `start_worker.sh` 的 `-Q`**

  `celery -A gpu_sound_tasks worker -Q gpu_sound_tasks ...` → `-Q us_sound_queue`（其余不变）。

- [ ] **Step 3: 验证**

Run:
```bash
cd sound/stable-sound-worker/build_assets
python3 -m py_compile celery_worker.py
grep -n "task_gen_sound\|us_sound_queue\|US_RABBITMQ" celery_worker.py
grep -n "\-Q us_sound_queue" start_worker.sh
! grep -n "import gpu_sound_tasks" celery_worker.py
```
Expected: py_compile 通过；grep 分别命中 `US_RABBITMQ_*`、`task_gen_sound`、`us_sound_queue`；`start_worker.sh` 命中 `-Q us_sound_queue`；`celery_worker.py` 中无 `import gpu_sound_tasks`。

- [ ] **Step 4: Commit**

```bash
git add sound/stable-sound-worker/build_assets/celery_worker.py sound/stable-sound-worker/build_assets/start_worker.sh
git commit -m "refactor(sound): task_gen_sound on us_sound_queue with US_RABBITMQ_*"
```

---

### Task 3: 重写 `gpu_sound_tasks.py`

**Files:**
- Modify: `sound/stable-sound-worker/build_assets/gpu_sound_tasks.py`

**Interfaces:**
- Consumes: `from celery_worker import celery_app`（Task 2）；utility 各模块（Task 1）；`from config.comm_config import get_logdir, get_modelconf, get_device, get_remote_soundsave_dir, get_fasttext_modelpath`；`from utility.file_sync_config import get_file_sync_transport`。
- Produces:
  ```python
  @celery_app.task(name='task_gen_sound')
  def gpu_gen_sound(text, length=15, num=1, userid=0, task_id='0', email='', domain=''):
  ```
  产出 `SyncJob(..., transport=get_file_sync_transport(), domain=domain)` 并 `enqueue_delivery_job`。

- [ ] **Step 1: 重写任务函数骨架**

  保留现 `detect_language` / `translate_text` / `load_model` / `initialize_file_delivery` / 底部 `initialize_file_delivery(); load_model()`。将 `gpu_gen_sound` 改为：

  1. 注册 `@celery_app.task(name='task_gen_sound')`，签名见上。
  2. 开头 `set_reqid(task_id)`，`finally: clear_reqid()`。
  3. 空文本校验：`if not (text or '').strip():` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)` + `return gen_res_dict(1, msg)`。
  4. 内部计算 `today = time.strftime('%Y-%m-%d')`、`datetime_str = time.strftime('%Y%m%d_%H%M%S')`、`random_number = random.randint(1,1000)`。
  5. 保留 `length→seconds_total`、`num→batch_size` 原钳制逻辑；保留 `NEED_TRANSLATE` 翻译分支与 `model.generate(...)`/`torchaudio.save(...)`。
  6. 文件名 `f'sound_{datetime_str}_{random_number}.mp3'`。
  7. `SyncJob(..., short_uri_prefix='/sounds/tk/{today}', transport=get_file_sync_transport(), domain=domain)`；`enqueue_delivery_job` 返回 False 时 `update_task_status(... FAILED)`。
  8. except 分支 `update_task_status(... FAILED)` + `torch.cuda.empty_cache()`/`gc.collect()`；末尾返回 `f"Processed: {task_id}"`。

- [ ] **Step 2: 移除旧依赖引用**

  删除 `from sql.mysql_db import update_ip_sounds_record`；`logger` 初始化改用 `get_logdir()`（保留 `setup_logger`）。文件不再 import `sql.mysql_db`。

- [ ] **Step 3: 验证**

Run:
```bash
cd sound/stable-sound-worker/build_assets
python3 -m py_compile gpu_sound_tasks.py
grep -n "name='task_gen_sound'" gpu_sound_tasks.py
grep -n "def gpu_gen_sound(text, length=15, num=1, userid=0, task_id='0', email='', domain='')" gpu_sound_tasks.py
grep -n "update_task_status(logger, task_id, domain" gpu_sound_tasks.py
grep -n "domain=domain" gpu_sound_tasks.py
grep -n "sound_{datetime_str}_{random_number}.mp3" gpu_sound_tasks.py
! grep -n "update_ip_sounds_record\|sql.mysql_db" gpu_sound_tasks.py
```
Expected: py_compile 通过；全部命中相应行；无 `sql.mysql_db`/`update_ip_sounds_record` 引用。

- [ ] **Step 4: Commit**

```bash
git add sound/stable-sound-worker/build_assets/gpu_sound_tasks.py
git commit -m "refactor(sound): migrate gpu_gen_sound to task_gen_sound (async + userserver status)"
```

---

### Task 4: 删除 MySQL 代码 + 补依赖

**Files:**
- Delete: `sound/stable-sound-worker/build_assets/sql/mysql_db.py`
- Delete: `sound/stable-sound-worker/build_assets/config/db_conf.py`
- Modify: `sound/stable-sound-worker/build_assets/requirements.txt`

**Interfaces:**
- Produces: 仓库内不再存在 `sql.mysql_db` / `update_ip_sounds_record`；`requirements.txt` 含 `requests`、`fastapi`。

- [ ] **Step 1: 删除两个文件，追加依赖**

  `rm sql/mysql_db.py config/db_conf.py`；`requirements.txt` 追加两行 `requests`、`fastapi`（不锁版本）。

- [ ] **Step 2: 验证全仓库无残留引用**

Run:
```bash
grep -rn "mysql_db\|update_ip_sounds_record\|add_ip_sounds_record\|get_out_path_by_id" sound/ uscm/ --include='*.py' || echo "NO_REFERENCES"
cat sound/stable-sound-worker/build_assets/requirements.txt
```
Expected: `NO_REFERENCES`（或仅出现于已删除文件之外无命中）；requirements 含 `requests` 与 `fastapi`。

- [ ] **Step 3: Commit**

```bash
git add -A sound/stable-sound-worker/build_assets/sql sound/stable-sound-worker/build_assets/config/db_conf.py sound/stable-sound-worker/build_assets/requirements.txt
git commit -m "refactor(sound): drop MySQL status layer, add requests/fastapi deps"
```

---

### Task 5: uscm 异步 `/api/gensound`

**Files:**
- Modify: `uscm/build_assets/uscm_server.py`

**Interfaces:**
- Consumes: `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound`；`ModuleType.SOUND`；`post_generate_add`；`validate_signature`；`validate_request`；`validate_text_length`；`gen_reqid` / `gen_res_dict`；`MAX_SOUND_TEXT_LENGTH`；`DEFAULT_USER_FIRST_IP`。
- Produces: `@app.post("/api/gensound")` → `gpu_gen_sound.delay(text, length, num, userid, reqid, email, domain=domain)`，返回 `{"ret":0,"msg":"ok","reqid":reqid}`。

- [ ] **Step 1: 扩展 import**

  `from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound`。

- [ ] **Step 2: 新增 `/api/gensound`（对照 `/api/genaudio`）**

  - `await request.json()` 取 `text/length(默认15)/num(默认1)/userid/email/subscript/domain/tstamp/snature`。
  - `first_ip = get_real_ip(request).split(',')[0].strip() or DEFAULT_USER_FIRST_IP`；`reqid = gen_reqid(userid, tm)`；`today`/`datetime` 同 genaudio。
  - 依次 `validate_text_length(text, MAX_SOUND_TEXT_LENGTH, reqid, logger)`、`validate_signature(logger, timestamp, signature, text, datetime, reqid, first_ip, real_ip, userid, email, ModuleType.SOUND)`、`validate_request(logger, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.SOUND, domain, request)`。
  - `generate_content = text`；`task_req = json.dumps({"first_ip":first_ip,"userid":userid,"email":email,"length":length,"num":num}, ensure_ascii=False)`。
  - `post_generate_add(logger, reqid, first_ip, email, userid, domain, generate_content, real_ip, ModuleType.SOUND, task_cmd='/api/gensound', task_req=task_req)`。
  - `gpu_gen_sound.delay(text, length, num, userid, reqid, email, domain=domain)`。
  - 返回 `{"ret": 0, "msg": "ok", "reqid": reqid}`。

- [ ] **Step 3: 验证**

Run:
```bash
cd uscm/build_assets
python3 -m py_compile uscm_server.py
grep -n "gpu_gen_sound" uscm_server.py
grep -n 'app.post("/api/gensound")' uscm_server.py
grep -n "ModuleType.SOUND" uscm_server.py
grep -n "gpu_gen_sound.delay(text, length, num, userid, reqid, email, domain=domain)" uscm_server.py
```
Expected: py_compile 通过；`.delay` 位置参数顺序与 Task 3 的 `gpu_gen_sound(text, length, num, userid, task_id, email, domain)` 完全对应。

- [ ] **Step 4: Commit**

```bash
git add uscm/build_assets/uscm_server.py
git commit -m "feat(uscm): async /api/gensound dispatching to task_gen_sound"
```

---

## Self-Review 摘要（已完成）

- **Spec 覆盖**：设计文档 §3.1（celery_worker / gpu_sound_tasks / utility / 删除 mysql / requirements / start_worker）、§3.2（uscm endpoint）、§5（硬切）分别对应 Task 2/3、Task 1/4、Task 5，均已覆盖。
- **类型一致**：`gpu_gen_sound` 签名在 Task 3 定义、Task 5 消费；`SyncJob.domain` 在 Task 1 定义、Task 3 消费；`update_task_status` 在 Task 1 定义、Task 3/1 消费。
- **Review Focus 五项**：分别落点在 Task 3（空文本、越界钳制、入队失败、异常处理）与 Task 5↔Task 3（参数顺序）。
- **比例**：计划以决策（文件名、签名、验证命令）为主，不转录实现代码。