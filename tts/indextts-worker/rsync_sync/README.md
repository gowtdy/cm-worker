# rsync_sync

独立、可测试的 rsync + SSH ControlMaster 文件同步模块。

## 目录结构

```
rsync_sync/                          # ← 顶级独立包
├── __init__.py                      # SyncOrchestrator + 所有公共API
├── __main__.py                      # CLI入口: python -m rsync_sync ...
├── callbacks.py                     # NoOpCallback + CallbackRecorder
├── config.py                        # SyncConfig
├── exceptions.py                    # SyncError, TransportError, ...
├── interfaces.py                    # SSHTransport, SyncStrategy, CompletionCallback
├── models.py                        # ProxyTarget, SyncJob, SyncMetrics, ...
├── parsers.py                       # parse_proxy_config, get_*_proxy_configs()
├── slot_manager.py                  # SlotManager
├── sync_engine.py                   # SequentialSyncStrategy, AsyncQueueSyncStrategy
├── transport.py                     # NativeSSHTransport, MockSSHTransport
├── util.py                          # 纯工具函数
└── tests/                           # 所有测试（独立的导入）
    ├── conftest.py
    ├── test_config.py
    ├── test_models.py
    ├── test_parsers.py
    ├── test_util.py
    ├── test_transport_mock.py
    ├── test_slot_manager.py
    ├── test_sync_engine_sequential.py
    ├── test_sync_engine_async.py
    └── test_callbacks.py
```

## 独立 CLI 使用

### 同步文件到一个代理

```bash
python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host:2222
```

### 同步到多个代理

```bash
python -m rsync_sync -s /tmp/audio.wav -t /remote/output \
    -p user@host1:22 -p user@host2:22
```

### 从配置文件加载代理

```bash
python -m rsync_sync -s /tmp/audio.wav -t /remote/output -f proxies.txt
```

### 顺序模式（调试更方便）

```bash
python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host:22 --sequential
```

### 预检 — 仅验证配置和连接

```bash
python -m rsync_sync --dry-run -p user@host:22
```

### 使用便捷脚本

```bash
python run_sync.py -s /tmp/audio.wav -t /remote/output -p user@host:22
```

## 作为库使用

```python
from rsync_sync import (
    SyncOrchestrator, SyncConfig, ProxyTarget, SyncJob, SyncMetrics,
    get_socat_proxy_configs, get_local_proxy_configs,
)

config = SyncConfig()
orchestrator = SyncOrchestrator.create_default(config=config, async_mode=True)

proxies = get_socat_proxy_configs() + get_local_proxy_configs()
orchestrator.register_proxies("all", proxies)
orchestrator.initialize()

job = SyncJob(
    source_file="/tmp/audio.wav",
    target_dir="/remote/path/",
    task_id="req-123",
    proxies=proxies,
)
ok = orchestrator.enqueue(job)
```

## 向后兼容

所有旧的导入路径仍然有效（通过位于 `build_assets/utility/rsync_sync/` 的薄层 shim）：

- `from utility.rsync_sync import ...` → 相同对象
- `from utility.rsync_sync.config import SyncConfig` → 相同对象
- `from utility.sync_callbacks import NoOpCallback, CallbackRecorder` → 从新包重新导出

## 测试

所有 10 个测试模块（52 个测试）通过 — 在顶层包和通过向后兼容 shim 都通过。

```bash
# 运行独立包的测试
python3 -m pytest rsync_sync/tests/ -v
```