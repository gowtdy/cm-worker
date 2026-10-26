# Caluma 声音模型动态加载器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个通用动态文件加载器，在进程启动时及文件 mtime 变化后自动加载/重载 YAML，解析成 dict 供 O(1) 查询。

**Architecture:** 一个 stdlib-only 的通用 `FileReloader`（单例 + 单后台线程 + 注册表，方案 A「每进程独立加载」），配合 caluma 专用解析器把 `models.*` 展平成 `{catid:name: entry}`。每进程 import 时注册一次。

**Tech Stack:** Python 3.11+、PyYAML（已存在）、标准库 `os/threading/logging/time`；测试用标准库 `unittest`（仓库无 pytest，不新增依赖）。

**Spec:** `uscm/docs/caluma-model-reloader-design.md`

## Global Constraints

- 不引入任何新第三方依赖：加载器只用 stdlib，解析器只用已存在的 PyYAML + stdlib。
- 每进程独立加载，不做跨进程 IPC/共享内存（放弃方案 B/C）。
- 文件缺失/半写入/解析失败时必须保留旧值并打日志，绝不抛异常导致服务崩溃。
- key 分隔符 `:`，value 为该模型原始 11 字段 dict。

## Review Focus

下列输入/条件在 spec 里隐含但可能被忽略，逐条用测试钉在所属任务里：

1. 启动时文件不存在 → `register` 不抛异常，`get()` 返回 `None`；文件稍后出现会被自动加载。
2. 重载时文件内容损坏/半写入（parse 抛异常）→ 保留旧值，不崩。
3. 查询不存在的 `catid`/`name` → 返回 `None`，不抛 `KeyError`。
4. 同一 `catid:name` 重复出现 → 结果确定（后者覆盖）。
5. 可选字段为空字符串（如 `modelicon: ''`）→ 不崩溃、原样保留。

---

### Task 1: 通用 FileReloader —— register 立即加载 + get

**Files:**
- Create: `uscm/build_assets/utility/file_reloader.py`
- Create: `uscm/tests/test_file_reloader.py`

**Interfaces:**
- Produces: `FileReloader(interval: float = 2.0)`；`.register(name: str, path: str, parse_fn: Callable[[str], Any]) -> Any`（立即同步 load 一次并返回初值，懒启动 daemon 线程）；`.get(name: str) -> Any`（未注册返回 `None`）。

- [ ] **Step 1: 写失败测试**

`uscm/tests/test_file_reloader.py`：

```python
import tempfile
import unittest
from pathlib import Path

from utility.file_reloader import FileReloader


class RegisterTest(unittest.TestCase):
    def test_register_loads_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("hello")
            r = FileReloader()
            value = r.register("m", str(p), parse_fn=lambda t: {"len": len(t)})
            self.assertEqual(value, {"len": 5})
            self.assertEqual(r.get("m"), {"len": 5})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/aigc/service/cm-worker/uscm && PYTHONPATH=build_assets python3 -m unittest discover -s tests -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'utility.file_reloader'`）

- [ ] **Step 3: 实现 `FileReloader` 的 `__init__`、`register`、`get`**

在 `uscm/build_assets/utility/file_reloader.py`。`_FileEntry` 存 `name/path/parse_fn/last_mtime_ns/value`（`value` 初值 `None`，`last_mtime_ns` 初值 `None`）；`register` 内调用一个 `_load(entry)` 做「stat → read_text → parse_fn → 赋值 value 与 last_mtime_ns」，任一异常 catch + `logging.getLogger(__name__).exception(...)` + 保持 value 不变，返回当前 `entry.value`；首次 `register` 时以 `threading.Thread(target=self._poll_loop, daemon=True)` 起线程（`_poll_loop` 本任务先留 `pass` 占位）。`get` 返回 `entry.value`（无此 name 返回 `None`）。

- [ ] **Step 4: 运行确认通过**

Run: 同上 `unittest discover`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/aigc/service/cm-worker && git add uscm/build_assets/utility/file_reloader.py uscm/tests/test_file_reloader.py
git commit -m "feat(file_reloader): add register with immediate load

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: FileReloader —— mtime 轮询自动重载

**Files:**
- Modify: `uscm/build_assets/utility/file_reloader.py`
- Modify: `uscm/tests/test_file_reloader.py`

**Interfaces:**
- Consumes: Task 1 的 `FileReloader`。
- Produces: 后台线程 `_poll_loop` 行为（无新公共 API）。

- [ ] **Step 1: 写失败测试**

在 `RegisterTest` 后追加（`import time, os`）：

```python
class ReloadTest(unittest.TestCase):
    def test_reload_on_mtime_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("aaa")
            r = FileReloader(interval=0.05)
            r.register("m", str(p), parse_fn=lambda t: {"v": t})
            self.assertEqual(r.get("m"), {"v": "aaa"})
            before = p.stat().st_mtime_ns
            p.write_text("bbb")
            os.utime(p, ns=(before + 1, before + 1))   # 强制 mtime 变化
            deadline = time.time() + 2
            while r.get("m") != {"v": "bbb"} and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(r.get("m"), {"v": "bbb"})
```

- [ ] **Step 2: 运行确认失败**

Run: `unittest discover`（同上）
Expected: FAIL（`_poll_loop` 为空，重载不触发）

- [ ] **Step 3: 实现 `_poll_loop`**

循环体：对 `self._registry.values()` 每个 entry，`os.stat(entry.path).st_mtime_ns`；若 `!= entry.last_mtime_ns` 则复用 `_load(entry)`（读+解析+替换 value 与 last_mtime_ns），异常 catch + 记日志；最后 `time.sleep(self.interval)`。`stat`/`_load` 的异常都只记日志不退出线程。

- [ ] **Step 4: 运行确认通过**

Run: `unittest discover`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/aigc/service/cm-worker && git add uscm/build_assets/utility/file_reloader.py uscm/tests/test_file_reloader.py
git commit -m "feat(file_reloader): poll mtime and reload on change

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: FileReloader —— 失败兜底（文件缺失、parse 异常保留旧值）

**Files:**
- Modify: `uscm/build_assets/utility/file_reloader.py`
- Modify: `uscm/tests/test_file_reloader.py`

**Interfaces:**
- Consumes: Task 2 的 `_load` / `_poll_loop`。

- [ ] **Step 1: 写失败测试**

追加（覆盖 Review Focus #1 #2）：

```python
class FailureTest(unittest.TestCase):
    def test_parse_error_keeps_old_value(self):
        def parse(t):
            if t == "BAD":
                raise ValueError("parse error")
            return {"v": t}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text("aaa")
            r = FileReloader(interval=0.05)
            r.register("m", str(p), parse_fn=parse)
            before = p.stat().st_mtime_ns
            p.write_text("BAD")
            os.utime(p, ns=(before + 1, before + 1))
            time.sleep(0.2)                       # 给线程一轮时间
            self.assertEqual(r.get("m"), {"v": "aaa"})  # 旧值仍在

    def test_missing_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            r = FileReloader()
            r.register("missing", str(Path(d) / "nope.yaml"),
                       parse_fn=lambda t: {"v": t})
            self.assertIsNone(r.get("missing"))
```

- [ ] **Step 2: 运行确认失败**

Run: `unittest discover`
Expected: 至少 `test_missing_file_does_not_crash` FAIL（`_load` 未捕获 `FileNotFoundError`）

- [ ] **Step 3: 实现兜底（若 Task 1/3 已覆盖则补齐）**

确保 `_load` 与 `_poll_loop` 对 `os.stat` 与 `read_text` 的 `OSError`（含 `FileNotFoundError`）以及 `parse_fn` 抛出的任意异常都 `try/except`，`logging.getLogger(__name__).exception(...)` 后保持 `entry.value`、`entry.last_mtime_ns` 不变（文件缺失时 `last_mtime_ns` 保持，文件稍后出现即可被自动加载）。

- [ ] **Step 4: 运行确认通过**

Run: `unittest discover`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/aigc/service/cm-worker && git add uscm/build_assets/utility/file_reloader.py uscm/tests/test_file_reloader.py
git commit -m "feat(file_reloader): keep old value on stat/parse failure

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: caluma 解析器 `_parse_caluma`

**Files:**
- Create: `uscm/build_assets/utility/caluma_models.py`
- Create: `uscm/tests/test_caluma_models.py`

**Interfaces:**
- Produces: `_parse_caluma(text: str) -> dict`：`yaml.safe_load` → `models.*` 展平为 `{f"{item['catid']}:{item['name']}": item}`，`item` 为原始 entry dict。空输入/无 `models` 键返回 `{}`。

- [ ] **Step 1: 写失败测试**

`uscm/tests/test_caluma_models.py`：

```python
import unittest
from utility.caluma_models import _parse_caluma


SAMPLE = """models:
  english:
    - id: 0
      name: voice-lady-female
      display: Female-Jessie(Voice Lady)
      catid: english
      catname: English(US)
      author: OceanWhisper
      wavplay: /wav/a.mp3
      modelicon: jessie.webp
      modellang: en
      model_type: index-tts
      model_typeversion: v2.5
  china:
    - id: 1
      name: zh-female
      display: 中文女声
      catid: china
      catname: Chinese
      author: someone
      wavplay: /wav/b.mp3
      modelicon: ''
      modellang: zh
      model_type: gsv
      model_typeversion: v1
"""


class ParseCalumaTest(unittest.TestCase):
    def test_flatten_to_catid_name_key(self):
        d = _parse_caluma(SAMPLE)
        self.assertEqual(len(d), 2)
        e = d["english:voice-lady-female"]
        self.assertEqual(e["catid"], "english")
        self.assertEqual(e["name"], "voice-lady-female")
        self.assertEqual(e["display"], "Female-Jessie(Voice Lady)")
        self.assertEqual(e["model_type"], "index-tts")

    def test_empty_optional_field_preserved(self):        # Review Focus #5
        d = _parse_caluma(SAMPLE)
        self.assertEqual(d["china:zh-female"]["modelicon"], "")

    def test_duplicate_key_last_wins(self):               # Review Focus #4
        dup = """models:
  english:
    - {id: 0, name: n, catid: english, display: first}
    - {id: 1, name: n, catid: english, display: second}
"""
        d = _parse_caluma(dup)
        self.assertEqual(len(d), 1)
        self.assertEqual(d["english:n"]["display"], "second")

    def test_empty_text(self):
        self.assertEqual(_parse_caluma(""), {})
```

- [ ] **Step 2: 运行确认失败**

Run: `unittest discover`
Expected: FAIL（`ModuleNotFoundError: No module named 'utility.caluma_models'`）

- [ ] **Step 3: 实现 `_parse_caluma(text: str) -> dict`**

`data = yaml.safe_load(text) or {}`；`result = {}`；遍历 `(data.get("models") or {}).values()` 的每个列表、每个 `item`，`result[f"{item['catid']}:{item['name']}"] = item`；返回 `result`。

- [ ] **Step 4: 运行确认通过**

Run: `unittest discover`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/aigc/service/cm-worker && git add uscm/build_assets/utility/caluma_models.py uscm/tests/test_caluma_models.py
git commit -m "feat(caluma_models): flatten caluma yaml into catid:name dict

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: caluma 注册 + `get_common_model` 查询

**Files:**
- Modify: `uscm/build_assets/utility/caluma_models.py`
- Modify: `uscm/tests/test_caluma_models.py`

**Interfaces:**
- Consumes: Task 1 的 `FileReloader`、Task 4 的 `_parse_caluma`。
- Produces:
  - `MODELS_NAME = "caluma"`、`MODELS_PATH = "/app/models/aivoice_models/caluma_common_models.yaml"`（常量）
  - `register_caluma(reloader: FileReloader | None = None) -> None`（默认用模块级 `file_reloader` 单例；注册时 `get(__name__)` 打一条 `len` 日志）
  - `get_common_model(catid: str, name: str, reloader: FileReloader | None = None) -> dict | None`（`(data or {}).get(f"{catid}:{name}")`）

- [ ] **Step 1: 写失败测试**

追加到 `test_caluma_models.py`：

```python
import tempfile
from pathlib import Path
from utility.file_reloader import FileReloader
from utility import caluma_models


class GetCommonModelTest(unittest.TestCase):
    def test_lookup_and_miss(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.yaml"
            p.write_text(SAMPLE)
            r = FileReloader(interval=60)
            r.register(caluma_models.MODELS_NAME, str(p), caluma_models._parse_caluma)
            m = caluma_models.get_common_model("english", "voice-lady-female", reloader=r)
            self.assertEqual(m["display"], "Female-Jessie(Voice Lady)")
            self.assertIsNone(caluma_models.get_common_model("english", "nope", reloader=r))  # Review Focus #3
```

- [ ] **Step 2: 运行确认失败**

Run: `unittest discover`
Expected: FAIL（`caluma_models` 无 `get_common_model`）

- [ ] **Step 3: 实现常量、`register_caluma`、`get_common_model`**

在 `caluma_models.py` 顶部 `from utility.file_reloader import FileReloader, default_reloader`（或等价的模块级单例访问）；实现上述三个符号。

- [ ] **Step 4: 运行确认通过**

Run: `unittest discover`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/aigc/service/cm-worker && git add uscm/build_assets/utility/caluma_models.py uscm/tests/test_caluma_models.py
git commit -m "feat(caluma_models): register loader and get_common_model lookup

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 接线到 `uscm_server.py` 并验证

**Files:**
- Modify: `uscm/build_assets/uscm_server.py`（import 区 + 日志设置块之后）

**Interfaces:**
- Consumes: Task 5 的 `register_caluma`。

- [ ] **Step 1: 加 import 与模块级注册**

在 `uscm_server.py` 的 `from utility...` 导入区加 `from utility.caluma_models import register_caluma`；在模块 logger 块（`logger_audio_extract = logging.getLogger('audio_extract')` 之后）加一行 `register_caluma()`。

- [ ] **Step 2: 重建镜像并启动**

Run: `cd /home/aigc/service/cm-worker/uscm && bash build-uscm-image.sh && bash stop_uscm.sh && bash start_uscm.sh`
Expected: build 无错、容器 `Up`

- [ ] **Step 3: 验证日志与存活**

Run: `docker logs uscm-server 2>&1 | tail -20`，以及 `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/openapi.json`
Expected: 出现 `Application startup complete`、`caluma` 加载日志（`len` 为 221），无 traceback；`/openapi.json` 返回 `200`。

- [ ] **Step 4: Commit**

```bash
cd /home/aigc/service/cm-worker && git add uscm/build_assets/uscm_server.py
git commit -m "feat(uscm_server): register caluma model loader at startup

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 记录

- Spec 覆盖：设计文档中「通用加载器 / 解析器 / get_common_model / 启动接线」四块分别落在 Task 1-3、Task 4、Task 5、Task 6；「失败兜底」「原子替换」「每进程独立」由 Task 3 与 Global Constraints 覆盖。
- 类型一致：`FileReloader.register/get`、`_parse_caluma`、`register_caluma`、`get_common_model` 的签名在 Interfaces 与各 Task 中一致。
- Review Focus 5 条均已钉入对应任务的测试（#1/#2→Task3，#4/#5→Task4，#3→Task5）。
- 测试运行：`cd uscm && PYTHONPATH=build_assets python3 -m unittest discover -s tests -v`（stdlib unittest，双 Python 3.11/3.12 均可用）。