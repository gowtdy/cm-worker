# Caluma 声音模型动态加载器 — 设计文档

日期：2026-10-26
状态：待确认

## 意图（为什么做）

后面要新做一个 TTS 接口，请求带 `catid + name`，需要 O(1) 查出声音模型再投递任务。模型清单放在挂载卷上的
`/app/models/aivoice_models/caluma_common_models.yaml`（宿主机 `/home/aigc/models/aivoice_models/cloned_models/common_models/caluma_common_models.yaml`）。

运维会直接改这个 YAML 文件（不重新 build 镜像），所以加载器要：启动时加载一次，文件内容变化时自动重载，把解析结果放到 dict 里供查询。

## 目标 / 非目标 / 反目标

**目标**
- 启动时同步加载一次，数据立即可查。
- 文件 mtime 变化后（约 2 秒内）自动重载。
- 通用：一个组件管多份文件，后面加文件只需再注册一条。
- 查询 O(1)、无锁、无 IPC 开销。

**非目标**
- 不做跨进程共享内存 / IPC（放弃方案 B/C）。
- 不做 YAML schema 严格校验（字段异常由 parse_fn 容错 + 记日志）。
- 不做多进程强一致（各进程各自刷新，允许秒级漂移）。

**反目标**
- 文件缺失 / 半写入 / 解析失败时，服务不能崩、不能起不来——保留旧值或空 dict + 打日志。
- 读热路径不能引入锁竞争或序列化开销。

## 方案选择

| 方案 | 结论 |
|---|---|
| A. 每进程独立加载（一个 FileReloader 管多文件） | **选定**。简单、读快、内存小（每份 dict 几百 KB）。 |
| B. 单 loader 进程广播给所有 worker | 未选。省的是最便宜的重复 parse，内存一份不省，却引入 IPC/广播/单点故障。 |
| C. shared_memory + 紧凑二进制 | 未选。最省内存但工程量大、字符串变长难处理、对多 schema 难通用。 |

## 设计

### 1. 通用加载器 `utility/file_reloader.py`

模块级单例 `FileReloader`，一个后台 daemon 线程，维护注册表：

```
register(name, path, parse_fn)   # 立即同步 load 一次，并懒启动线程
get(name) -> value                # 无锁读当前值
```

每个注册项：

```python
class _FileEntry:
    name: str
    path: str
    parse_fn: Callable[[str], Any]   # 文本 -> 解析结果
    last_mtime_ns: int
    value: Any                       # 当前结果，原子替换
```

单线程循环（伪代码）：

```python
while not stopped:
    for entry in registry:
        mtime = os.stat(entry.path).st_mtime_ns   # 只 stat，便宜
        if mtime != entry.last_mtime_ns:
            new = entry.parse_fn(read_text(entry.path))  # 只有变了的文件才读+解析
            entry.value = new                      # 一条赋值，读方永远看到完整对象
            entry.last_mtime_ns = mtime
    sleep(interval)   # 默认 2s
```

关键决策：
- **轮询 mtime，间隔 2s**（可配）。每轮只 `stat`，真正读+解析只发生在变了的文件。
- **原子替换**：解析成新对象后一条赋值替换 `value`；读方锁无关（CPython 下引用赋值原子）。
- **失败兜底**：stat / read / parse 任一异常 → 记日志、保留旧 `value`、下轮重试、线程不退出、服务不崩。
- **启动即用**：`register` 内同步 load，避免冷启动 2s 空窗。
- 重复 `register` 同名：后者覆盖（记 warning）。

### 2. Caluma 解析器 + 查询封装 `utility/caluma_models.py`

- `_parse_caluma(text) -> dict`：`yaml.safe_load` → 遍历 `models.*` 下每个模型条目，产出
  `{f"{item['catid']}:{item['name']}": item}`，`item` 保留原始 11 个字段
  （`id/name/display/catid/catname/author/wavplay/modelicon/modellang/model_type/model_typeversion`）。
- `get_common_model(catid, name) -> dict|None`：`reloader.get(file_name)` + 拼 key 查询。

### 3. 启动接线（`uscm_server.py`）

模块 import 时（每个 worker 进程各执行一次）：

```python
FILE_RELOADER.register(
    "caluma", "/app/models/aivoice_models/caluma_common_models.yaml", _parse_caluma)
```

这替换之前删除的 entapi `@app.on_event("startup")` 死代码。每进程独立加载（方案 A）。

## 给实现者的留白（我的默认，可改）

- key 分隔符 `:`（即 `english:voice-lady-female`）；要换成 `+` 或 `/` 是一行改。
- 解析器放独立模块 `utility/caluma_models.py`，便于单测。
- 间隔 2s、日志用现有 logging，级别 debug/error。
- 服务关闭时线程退出：daemon 线程、进程退出即终止，不做显式停止。