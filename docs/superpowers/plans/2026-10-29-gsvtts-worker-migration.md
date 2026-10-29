# gsvtts-worker 迁移到新 commit-worker 架构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `tts/gsvtts-worker` 迁到新架构（对齐 `tts/indextts-worker`），并把 `uscm` 的 `POST /api/genaudio` 中 gsvtts 分支（非 index-tts）改为新签名派发。

**Architecture:** worker 以 `@celery_app.task(name='task_gen_tts')` 注册到 `us_tts_queue`，状态回写走 UserServer HTTP（`update_task_status` + `TaskStatus` + `domain`）；uscm 侧 `post_generate_add` 落库后 `gpu_gen_tts.delay(...)` 异步派发、立即返回 `reqid`。gsvtts 引擎代码（`fish_speech/`、`voice_train/`、`tools/`）全部不动。

**Tech Stack:** Python 3 / Celery / UserServer HTTP（`/inapi/*`）/ Docker。

**Spec:** `docs/superpowers/specs/2026-10-29-gsvtts-worker-migration-design.md`

## Global Constraints

- 任务名 `task_gen_tts`，队列 `us_tts_queue`（与 `uscm/build_assets/tasks_interface.py` 已声明的 `gpu_gen_tts = us_celery_app.signature('task_gen_tts')` 对应）。
- worker 状态回写只用 `utility.userserver_client.update_task_status(logger, task_id, domain, payload, TaskStatus)`；不再 import `sql.mysql_db`。
- 环境变量用 `US_RABBITMQ_*` / `US_COMMON_USERSERVER_HOST/PORT` / `INAPI_AUTH_TOKEN`；`docker-compose*` 的 `env_file` 用 `/home/aigc/data/env/.env_usmqdb`。
- 删 `t` 参数：gpt-sovits 模型路径恒为 `models/service_model/common_models/{modelcat}/common_model_{modelname}`，用户私有模型分支（`t==2`）删除；uscm 不传 `t`。
- 删 `.eapi` 分叉：不再有 `reqid.endswith('.eapi')` / `is_eapi` / `update_api_user_record`。
- 保留 gsvtts 引擎专属代码：`fish_speech/`、`voice_train/`、`tools/`、`indextts/`、`utility/load_model.py`、`utility/lang_util.py`、`utility/voice_config.py`、`utility/module_enum.py`、`utility/quic_http_transport.py`、`utility/samearea_http_transport.py`、`utility/socat_rsyc_util.py`、`utility/file_sync_config.py`、`config/model_conf.py`、`config/__init__.py`、`Dockerfile`、`build-tts-image.sh`、`README.md`、`indextts_worker.py`。
- 干净硬切：不保留 `sql/mysql_db.py`/`config/db_conf.py`，不做共存窗口。
- 每个 commit 信息末尾追加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## Review Focus

1. **`enqueue_delivery_job` 返回 False**（队列满/worker 未启动）：须 `update_task_status(... FAILED)`，不得留 PENDING。→ Task 3。
2. **未捕获异常**：整段 body 用 `try/except` 包住，`finally: clear_reqid()`，except 分支 `update_task_status(... FAILED)`。→ Task 3。
3. **producer/worker 参数一致**：uscm `.delay(modelname, text, modelcat, userid, task_id=reqid, output_format=..., modellang=..., email=..., domain=domain)` 与 worker 签名 `gpu_gen_tts(modelname, text, modelcat='', userid=0, task_id='0', output_format=DEFAULT_FORMAT, modellang='', email='', domain='')` 一一对应。→ Task 5 与 Task 3 对照。
4. **`t` 彻底删除**：签名无 `t`，gpt-sovits 分支无 `t==2`/`else` 私有模型代码，uscm 不传 `t`。→ Task 3 / Task 5。
5. **`.eapi` 彻底删除**：无 `.eapi`/`isEapi`/`is_eapi`/`update_api_user_record` 残留。→ Task 3 / Task 4。

---

### Task 1: gsvtts worker utility 层对齐 indextts

**Files:**
- Create: `tts/gsvtts-worker/build_assets/utility/internal_auth.py`（拷贝 indextts 同名文件）
- Create: `tts/gsvtts-worker/build_assets/utility/userserver_client.py`（拷贝 indextts 同名文件）
- Modify: `tts/gsvtts-worker/build_assets/utility/log.py`（覆盖为 indextts 同名文件，带 reqid 上下文）
- Modify: `tts/gsvtts-worker/build_assets/utility/file_delivery.py`（贴 indextts diff）
- Modify: `tts/gsvtts-worker/build_assets/utility/util.py`（保留 NLP 函数，修 `gen_res_dict`，删 `genid`）
- Modify: `tts/gsvtts-worker/build_assets/utility/test_file_delivery.py`（`is_eapi` → `domain`）

**Interfaces:**
- Produces:
  - `utility.internal_auth.bearer_auth_header() -> dict`
  - `utility.userserver_client.update_task_status(logger, task_id, domain, payload_msg_or_uri, task_status: TaskStatus) -> tuple[int, str]`
  - `utility.log.set_reqid(reqid) / clear_reqid() / setup_logger(...) / logset(...)`
  - `utility.util.gen_res_dict(ret: int, msg: str, dt=None) -> dict`
  - `utility.file_delivery.SyncJob(source_file, target_dir, task_id, short_uri_prefix, output_file, proxies=[], transport='quic', domain='')`
  - `utility.file_delivery.enqueue_delivery_job(logger, job) -> bool`、`start_file_delivery(logger, transports)`、`resolve_delivery_transport(email="") -> str`

- [ ] **Step 1: 拷贝 3 个文件**

  - `cp tts/indextts-worker/build_assets/utility/internal_auth.py tts/gsvtts-worker/build_assets/utility/internal_auth.py`
  - `cp tts/indextts-worker/build_assets/utility/userserver_client.py tts/gsvtts-worker/build_assets/utility/userserver_client.py`
  - `cp tts/indextts-worker/build_assets/utility/log.py tts/gsvtts-worker/build_assets/utility/log.py`

- [ ] **Step 2: 改写 `file_delivery.py`（贴 indextts diff）**

  在 `tts/gsvtts-worker/build_assets/utility/file_delivery.py` 上做以下精确修改：
  - 顶部 docstring 第 5 行：`Completion updates MySQL task records; ...` → `Completion updates task records via UserServer /inapi/update_task; ...`。
  - 顶层 import 增加：`from utility.log import clear_reqid, set_reqid`（第 16 行后）、`from utility.userserver_client import update_task_status`（第 18 行后）。
  - `SyncJob` dataclass：`is_eapi: bool` → `domain: str = ""`（在 `transport: str = "quic"` 之后）。
  - `complete_sync_job` 内删除两行惰性 import（`from sql.mysql_db import update_api_user_record, update_ip_use_record`）；把 `if job.is_eapi: update_api_user_record(...) else: update_ip_use_record(...)` 两处（COMPLETE 与 FAILED）都替换为 `update_task_status(logger, job.task_id, job.domain, uri_or_msg, TaskStatus.COMPLETE/FAILED)`。
  - `_worker_loop` sync 循环：sentinel 检查返回之后加 `set_reqid(job.task_id)`；循环 `finally` 里、`_sync_queue.task_done()` 之前加 `if job is not _SYNC_SENTINEL: clear_reqid()`。
  - `resolve_delivery_transport` 保持不变（其实现与 indextts 完全一致）。

- [ ] **Step 3: 改写 `util.py`**

  - 删除 `genid` 函数（用 `np.random` 的那个，第 23-27 行）。
  - `gen_res_dict` 重写为干净版（修复默认参数被改写、返回 `d` 而非 `dt` 的 bug）：
    ```python
    def gen_res_dict(ret: int, msg: str, dt=None):
        d = {}
        if dt:
            d.update(dt)
        d['ret'] = ret
        d['msg'] = msg
        return d
    ```
  - 删除 `if __name__ == "__main__":` 块（末尾 4 行，引用 `genid`）。
  - 其余全保留（`verify_signature`、`splits`、`get_first`、`is_whole_sentence`、`get_pause_duration`、`get_all_pause_durations`、`split`、`cut5`、`cut_custom`、`process_text`、`clean_text_inf`、`get_bert_feature`、`get_bert_inf`、`get_phones_and_bert`、`merge_short_text_in_array`、`split_long_sentence`、`resample`、`get_spepc`）。

- [ ] **Step 4: 改动 `test_file_delivery.py`**

  - `_job()` 助手的 `is_eapi=False` → `domain=""`。其余测试逻辑不动。

- [ ] **Step 5: 验证**

  Run（在 `tts/gsvtts-worker/build_assets` 目录下，`PYTHONPATH=.`）：
  ```bash
  python3 -m py_compile utility/internal_auth.py utility/userserver_client.py utility/log.py utility/file_delivery.py utility/util.py
  PYTHONPATH=. python3 -c "from utility.file_delivery import SyncJob; j=SyncJob('a','b','t','/p','f',transport='quic',domain='d'); assert j.domain=='d' and not hasattr(j,'is_eapi')"
  PYTHONPATH=. python3 -m unittest utility.test_file_delivery -v 2>&1 | tail -3
  grep -n "def gen_res_dict(ret: int, msg: str, dt=None)" utility/util.py
  ! grep -rn "sql.mysql_db\|update_ip_use_record\|update_api_user_record\|is_eapi" utility/file_delivery.py
  ! grep -n "def genid\|genid(" utility/util.py
  ```
  Expected: py_compile 通过；`SyncJob` 有 `domain`、无 `is_eapi`；test 全绿（27 tests OK）；`gen_res_dict` 命中新签名；`file_delivery.py` 无 MySQL/`is_eapi` 残留；`util.py` 无 `genid`。
  > 若 `from utility.file_delivery import SyncJob` 因 `utility.userserver_client` 缺依赖报 ImportError：`userserver_client` 顶层只 import `logging/os/module_enum`，只有 `internal_auth` 顶层 import `fastapi`（lazy 在 `post_update_task` 内部触发）。本地无 `fastapi` 不影响 `SyncJob` 导入；若在生产 `fuse-tts-image:0.2` 缺 `fastapi`，则 Task 1 结尾记录并在 `requirements.txt` 补 `fastapi`（不 `import fastapi` 会绕过，但必须保证生产镜像可用）。

- [ ] **Step 6: Commit**

  ```bash
  git add tts/gsvtts-worker/build_assets/utility/
  git commit -m "refactor(gsvtts): align utility layer with indextts (userserver/task-status/reqid)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 2: gsvtts worker `celery_worker.py` + `start_tts_worker.sh`

**Files:**
- Modify: `tts/gsvtts-worker/build_assets/celery_worker.py`
- Modify: `tts/gsvtts-worker/build_assets/start_tts_worker.sh`

**Interfaces:**
- Produces: `celery_worker.celery_app`，`conf.task_routes = {'task_gen_tts': {'queue': 'us_tts_queue'}}`；broker 用 `US_RABBITMQ_*`。

- [ ] **Step 1: 改写 `celery_worker.py`**

  - 环境变量由 `TTS_RABBITMQ_*` → `US_RABBITMQ_HOST/PORT/USER/PASS`（默认 host `149.28.202.108`）。
  - `Celery("tts_worker", broker=f"amqp://{US_RABBITMQ_USER}:{quote_plus(US_RABBITMQ_PASS)}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//", backend="rpc://")`（app 名 `tts_worker` 保留）。
  - `task_routes = {'task_gen_tts': {'queue': 'us_tts_queue'}}`。

- [ ] **Step 2: 改写 `start_tts_worker.sh` 的 `-Q` 与 pool**

  - `-Q gpu_tts_tasks` → `-Q us_tts_queue`。
  - `--pool=solo --pool=threads` → `--pool=threads`（`-A gpu_tts_tasks` 与 `--concurrency=${CELERY_CONCURRENCY:-1}` 不变）。

- [ ] **Step 3: 验证**

  ```bash
  python3 -m py_compile celery_worker.py
  grep -n "task_gen_tts\|us_tts_queue\|US_RABBITMQ" celery_worker.py
  grep -n "\-Q us_tts_queue" start_tts_worker.sh
  ! grep -n "TTS_RABBITMQ\|gpu_tts_tasks.gpu_gen_tts\|gpu_tts_tasks\|--pool=solo" celery_worker.py start_tts_worker.sh
  ```
  Expected: py_compile 通过；`celery_worker.py` 命中 `US_RABBITMQ_*`、`task_gen_tts`、`us_tts_queue`；`start_tts_worker.sh` 命中 `-Q us_tts_queue`，且不再有 `gpu_tts_tasks` 队列/`--pool=solo`。

- [ ] **Step 4: Commit**

  ```bash
  git add tts/gsvtts-worker/build_assets/celery_worker.py tts/gsvtts-worker/build_assets/start_tts_worker.sh
  git commit -m "refactor(gsvtts): task_gen_tts on us_tts_queue with US_RABBITMQ_*" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 3: 重写 `gpu_tts_tasks.py`

**Files:**
- Modify: `tts/gsvtts-worker/build_assets/gpu_tts_tasks.py`

**Interfaces:**
- Consumes: `from celery_worker import celery_app`（Task 2）；`utility.log.set_reqid/clear_reqid/setup_logger`（Task 1）；`utility.userserver_client.update_task_status`（Task 1）；`utility.util.gen_res_dict`（Task 1）；`utility.file_delivery.SyncJob`（含 `domain`，Task 1）；gsvtts 引擎函数 `fish_tts_with_retry`、`get_tts_wav`、`load_sovits_weights`、`load_gpt_weights`、`init_sv` 及 `utility.load_model`/`tools.*`/`voice_train.*`（不变）。
- Produces:
  ```python
  @celery_app.task(name='task_gen_tts')
  def gpu_gen_tts(modelname, text, modelcat='', userid=0, task_id='0',
                  output_format=DEFAULT_FORMAT, modellang='', email='', domain=''):
  ```

- [ ] **Step 1: 改 import**

  - 删除 `from sql.mysql_db import ...`（第 62-64 行，整块）。
  - `from utility.util import (genid, gen_res_dict, ...)` → 去掉 `genid`。
  - `from utility.log import logset, setup_logger` → `from utility.log import logset, setup_logger, set_reqid, clear_reqid`。
  - 新增 `from utility.userserver_client import update_task_status`。

- [ ] **Step 2: 任务签名 + `.eapi`/`t` 删除**

  - 装饰器 `@celery_app.task(name='gpu_tts_tasks.gpu_gen_tts')` → `@celery_app.task(name='task_gen_tts')`。
  - 签名 `def gpu_gen_tts(modelname, text, modelcat='', userid=0, t=1, reqid='0', output_format=DEFAULT_FORMAT, modellang='', email='')` → `def gpu_gen_tts(modelname, text, modelcat='', userid=0, task_id='0', output_format=DEFAULT_FORMAT, modellang='', email='', domain='')`。
  - 删除函数头 4 行（`isEapi = str(reqid).endswith('.eapi')` / `task_id = reqid` / `if isEapi:` / `task_id = str(reqid).removesuffix('.eapi')`）。

- [ ] **Step 3: `set_reqid`/`clear_reqid` 包裹**

  - 函数体首行 `set_reqid(task_id)`。
  - 整段函数体包进 `try:`，末尾 `finally: clear_reqid()`（对齐 indextts：`set_reqid(task_id)` → `try:` 包住原 body 全部 → 原返回点自然走 `finally: clear_reqid()`）。

- [ ] **Step 4: 全函数 `reqid` → `task_id`**

  - 原 body 内所有 `reqid` 标识符与日志 `reqid:{reqid}` 改名为 `task_id` / `task_id:{task_id}`（参数已从 `reqid` 改为 `task_id`，无需再 `task_id = reqid` 赋值）。

- [ ] **Step 5: 删除 `t` 三段分支**

  - gpt-sovits 分支内（`else:` 块）：
    ```python
    model_file_prefix = modelname
    if t == 1:
        model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
    elif t == 2:
        model_path = f'models/service_model/user_models/user{userid}/model_user{userid}_{modelname}'
        model_file_prefix = f'user{userid}_{modelname}'
    else:
        ret = 1
        msg = f't:{t} is invalid, task_id:{task_id}!'
        logger.warning(f'msg: {msg}')
        update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
        return gen_res_dict(ret, msg)
    ```
    塌缩为：
    ```python
    model_file_prefix = modelname
    # 公用模型
    model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
    ```

- [ ] **Step 6: `enqueue_tts_sync` 去 `is_eapi` + 状态回写全替换**

  - `enqueue_tts_sync(output_path, output_file, short_uri_prefix, remote_target_dir, task_id, is_eapi, reqid, email='')` → `enqueue_tts_sync(output_path, output_file, short_uri_prefix, remote_target_dir, task_id, domain, email='')`。
  - 其内部：`SyncJob(...)` 去 `is_eapi=...`、加 `domain=domain`；失败分支 `update_api_user_record/update_ip_use_record(task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`；日志 `reqid:{reqid}` → `task_id:{task_id}`。
  - 两处调用点 `enqueue_tts_sync(output_path, output_file, short_uri_prefix, remote_target_dir, task_id, isEapi, reqid, email)` → `enqueue_tts_sync(output_path, output_file, short_uri_prefix, remote_target_dir, task_id, domain, email)`。
  - 密布 `gpu_gen_tts` body 内的所有 `update_ip_use_record(task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`；末尾 except 分支 `update_api_user_record/update_ip_use_record(task_id, msg, TaskStatus.FAILED)` → `update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)`（不再按 `isEapi` 分叉）。

- [ ] **Step 7: 成功返回**

  - 保持 `result = f"Processed: {task_id}"` 与 `return result` 不变（与 indextts 一致）。

- [ ] **Step 8: 验证**

  ```bash
  python3 -m py_compile gpu_tts_tasks.py
  grep -n "name='task_gen_tts'" gpu_tts_tasks.py
  grep -n "def gpu_gen_tts(modelname, text, modelcat='', userid=0, task_id='0', output_format=DEFAULT_FORMAT, modellang='', email='', domain='')" gpu_tts_tasks.py
  grep -n "update_task_status(logger, task_id, domain" gpu_tts_tasks.py
  grep -n "set_reqid(task_id)\|clear_reqid()" gpu_tts_tasks.py
  grep -n "domain=domain" gpu_tts_tasks.py
  ! grep -n "update_ip_use_record\|update_api_user_record\|sql.mysql_db\|genid\|isEapi\|is_eapi\|\.eapi\|, t=1\|elif t == 2\|t:{t}\|def gpu_gen_tts(modelname, text, modelcat='', userid=0, t=1" gpu_tts_tasks.py
  ```
  Expected: py_compile 通过；各 grep 命中；无任何 `sql.mysql_db`/`update_ip_use_record`/`update_api_user_record`/`genid`/`.eapi`/`isEapi`/`t==` 残留。

- [ ] **Step 9: Commit**

  ```bash
  git add tts/gsvtts-worker/build_assets/gpu_tts_tasks.py
  git commit -m "refactor(gsvtts): migrate gpu_gen_tts to task_gen_tts (async + userserver status)" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 4: 删除 MySQL 层 + 切换 env_file

**Files:**
- Delete: `tts/gsvtts-worker/build_assets/sql/mysql_db.py`（及空 `build_assets/sql/` 目录）
- Delete: `tts/gsvtts-worker/build_assets/config/db_conf.py`
- Delete: `tts/gsvtts-worker/sql/mysql_db.py`（顶层 `sql/` 目录）
- Modify: `tts/gsvtts-worker/docker-compose.yml`
- Modify: `tts/gsvtts-worker/docker-compose-preview.yml`
- Modify: `tts/gsvtts-worker/docker-compose-test.yml`

**Interfaces:**
- Produces: 仓库内不再存在 `sql.mysql_db`/`update_ip_use_record`/`update_api_user_record`；三个 docker-compose `env_file` 指向 `.env_usmqdb`。

- [ ] **Step 1: 删除文件，切 env_file**

  - `rm tts/gsvtts-worker/build_assets/sql/mysql_db.py tts/gsvtts-worker/build_assets/config/db_conf.py tts/gsvtts-worker/sql/mysql_db.py`；空目录 `tts/gsvtts-worker/build_assets/sql/`、`tts/gsvtts-worker/sql/` 一并删除。
  - `docker-compose.yml` / `docker-compose-preview.yml` / `docker-compose-test.yml` 中 `- /home/aigc/data/env/.env_mqdb` → `- /home/aigc/data/env/.env_usmqdb`（共 3 处，每个文件 1 处）。

- [ ] **Step 2: 验证全仓库无残留引用**

  ```bash
  grep -rn "mysql_db\|update_ip_use_record\|update_api_user_record\|add_ip_use_record\|add_api_user_record\|get_ip_use\|get_api_user\|get_email_use\|config.db_conf" tts/gsvtts-worker --include='*.py' || echo "NO_REFERENCES"
  grep -rn "env_usmqdb" tts/gsvtts-worker/docker-compose*.yml
  ! grep -rn "env_mqdb\|\.env_mqdb" tts/gsvtts-worker/docker-compose*.yml
  ```
  Expected: `NO_REFERENCES`；三个 compose 均命中 `env_usmqdb`；无 `.env_mqdb` 残留。

- [ ] **Step 3: Commit**

  ```bash
  git add -A tts/gsvtts-worker/build_assets/sql tts/gsvtts-worker/sql tts/gsvtts-worker/build_assets/config/db_conf.py tts/gsvtts-worker/docker-compose.yml tts/gsvtts-worker/docker-compose-preview.yml tts/gsvtts-worker/docker-compose-test.yml
  git commit -m "refactor(gsvtts): drop MySQL status layer, switch env_file to .env_usmqdb" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

### Task 5: uscm `/api/genaudio` gsvtts 分支新签名

**Files:**
- Modify: `uscm/build_assets/uscm_server.py`（else 分支，第 181-183 行）

**Interfaces:**
- Consumes: `from tasks_interface import gpu_gen_tts`（已声明 `task_gen_tts` → `us_tts_queue`，不新增）；`map_lang_abbrev`；`domain`（已解析）。
- Produces: `gpu_gen_tts.delay(modelname, text, modelcat, userid, task_id=reqid, output_format=output_format, modellang=modellang, email=email, domain=domain)`。

- [ ] **Step 1: 重写 else 分支**

  把：
  ```python
  else:
      modellang = map_lang_abbrev(modellang, modellang)
      gpu_gen_tts.delay(modelname, text, modelcat, userid, reqid, output_format, modellang, email)
  ```
  改为：
  ```python
  else:
      modellang = map_lang_abbrev(modellang, modellang)
      gpu_gen_tts.delay(
          modelname, text, modelcat, userid,
          task_id=reqid, output_format=output_format, modellang=modellang,
          email=email, domain=domain,
      )
  ```

- [ ] **Step 2: 验证**

  ```bash
  python3 -m py_compile uscm_server.py
  grep -n "gpu_gen_tts.delay(" -A5 uscm_server.py
  grep -n "task_id=reqid" uscm_server.py
  ! grep -n "gpu_gen_tts.delay(modelname, text, modelcat, userid, reqid, output_format, modellang, email)" uscm_server.py
  ```
  Expected: py_compile 通过；`.delay` 用关键字 `task_id=reqid`/`output_format=`/`modellang=`/`email=`/`domain=`，参数与 Task 3 的 worker 签名一一对应；旧的 8 位置参数调用已不在。

- [ ] **Step 3: Commit**

  ```bash
  git add uscm/build_assets/uscm_server.py
  git commit -m "feat(uscm): dispatch /api/genaudio gsvtts branch to task_gen_tts with domain" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
  ```

---

## Self-Review 摘要

- **Spec 覆盖**：设计 §A（utility / celery_worker / gpu_tts_tasks / 删 MySQL / docker-compose）对应 Task 1/2/3/4；§B（uscm else 分支）对应 Task 5；决策点 1（删 t）→ Task 3 Step 5 + Task 5；决策点 2（删 .eapi）→ Task 3 Step 2/6 + Task 4；决策点 3（test 文件保留）→ Task 1 Step 4；决策点 4（fastapi/requests）→ Task 1 Step 5 备注。已覆盖。
- **类型一致**：`gpu_gen_tts` 签名在 Task 3 定义、Task 5 消费；`SyncJob.domain`/`update_task_status`/`set_reqid` 在 Task 1 定义、Task 3 消费；任务名/队列在 Task 2 定义、Task 3/5 对齐。
- **Review Focus 五项**：enqueue 失败 → Task 3 Step 6；未捕获异常 → Task 3 Step 3；参数一致 → Task 5↔Task 3；删 t → Task 3 Step 5；删 .eapi → Task 3 Step 2/6。
- **关键纠偏**：`util.py` 不能盲 cp indextts 版（gsvtts 有 heavy NLP 依赖 & torch import，无法本地 `from utility.util import`），只改 `gen_res_dict`/删 `genid` 并保留全部 NLP 函数；`gpu_tts_tasks.py` 保留 gsvtts 内的 `@cuda_error_handler` 装饰器与 GPU 工具函数（不引入 `utility/gpu_util.py`）；`test_file_delivery.py` 可本地 `unittest` 跑全绿。
- **比例**：计划以决策（文件名、签名、验证命令、替换点）为主，不转录实现代码。