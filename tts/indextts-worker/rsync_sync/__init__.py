"""rsync_sync — Independent, testable rsync+SSH ControlMaster file sync module.

A standalone Python package for syncing files to multiple remote servers
via rsync over SSH ControlMaster connections. Designed to work both as
an embedded library and as a standalone CLI tool.

Provides:
    SyncOrchestrator — high-level orchestration (create, init, enqueue, shutdown)
    SyncConfig        — immutable configuration
    ProxyTarget       — immutable remote server descriptor
    SyncJob           — file sync job with generic metadata
    SyncMetrics       — aggregated sync results
    SingleSyncResult  — single proxy result
    TransferResult    — raw transport result

    get_socat_proxy_configs()  — load socat proxy config
    get_local_proxy_configs()  — load local proxy config

    SSHTransport      — abstract transport interface (for testing)
    SyncStrategy      — abstract sync strategy (for testing)

Library usage:
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

Standalone CLI usage:
    python -m rsync_sync --source-file /tmp/audio.wav --target-dir /remote/path \\
        --proxy user@host1:22 --proxy user@host2:22
"""

import atexit
from typing import Dict, List, Optional

from .callbacks import CallbackRecorder, NoOpCallback
from .config import SyncConfig
from .interfaces import CompletionCallback, SSHTransport, SyncStrategy
from .models import ProxyTarget, SingleSyncResult, SyncJob, SyncMetrics, TransferResult
from .parsers import get_local_proxy_configs, get_socat_proxy_configs
from .slot_manager import SlotManager
from .sync_engine import AsyncQueueSyncStrategy, SequentialSyncStrategy
from .transport import MockSSHTransport, NativeSSHTransport


class SyncOrchestrator:
    """Top-level entry point for the sync subsystem.

    Owns the transport, strategy, and full lifecycle.
    Designed to be created once at application startup.

    Usage:
        # Simple: factory method
        orchestrator = SyncOrchestrator.create_default(
            callback=my_callback, async_mode=True,
        )
        orchestrator.register_proxies("all", all_proxies)
        orchestrator.initialize()

        # Explicit: full control
        config = SyncConfig(slot_count=4)
        transport = NativeSSHTransport(config, logger)
        strategy = AsyncQueueSyncStrategy(
            transport, SlotManager(transport, config.slot_count),
            config, callback=my_callback, logger=logger,
        )
        orchestrator = SyncOrchestrator(config, strategy, transport, my_callback)
    """

    def __init__(
        self,
        config: SyncConfig,
        strategy: SyncStrategy,
        transport: SSHTransport,
        callback: Optional[CompletionCallback] = None,
        logger=None,
    ):
        import logging
        self._logger = logger or logging.getLogger("root")
        self._config = config
        self._transport = transport
        self._strategy = strategy
        self._callback = callback
        self._proxies: Dict[str, List[ProxyTarget]] = {}
        self._slot_manager = SlotManager(transport, config.slot_count, logger=self._logger)
        self._atexit_registered = False

    # ---- Public API ----

    def register_proxies(self, name: str, proxies: List[ProxyTarget]) -> None:
        """Register a named proxy group (e.g. 'socat', 'local', 'all')."""
        self._proxies[name] = list(proxies)
        self._logger.info(
            f"register_proxies name={name} count={len(proxies)} "
            f"proxies={[p.server for p in proxies]}"
        )

    def register_all_proxies(self) -> int:
        """Load and register proxies from all config sources.

        Returns total proxy count.
        """
        all_proxies: List[ProxyTarget] = []
        for loader_name, loader in [
            ("socat", get_socat_proxy_configs),
            ("local", get_local_proxy_configs),
        ]:
            try:
                all_proxies.extend(loader())
            except Exception as e:
                self._logger.warning(f"register_proxies_fail source={loader_name} error={e}")

        if all_proxies:
            self.register_proxies("all", all_proxies)
        return len(all_proxies)

    def initialize(
        self, proxy_groups: Optional[List[str]] = None
    ) -> Dict[str, bool]:
        """Initialize SSH ControlMaster connections.

        Args:
            proxy_groups: which groups to init (None = all registered)

        Returns:
            Dict mapping server_key to success status
        """
        if proxy_groups is None:
            proxy_groups = list(self._proxies.keys())

        all_proxies: List[ProxyTarget] = []
        for group_name in proxy_groups:
            if group_name in self._proxies:
                all_proxies.extend(self._proxies[group_name])
            else:
                self._logger.warning(f"init_unknown_group group={group_name}")

        if not all_proxies:
            self._logger.warning("init_no_proxies register proxies first")
            return {}

        # Deduplicate proxies
        seen = set()
        deduped = []
        for p in all_proxies:
            if p.server not in seen:
                seen.add(p.server)
                deduped.append(p)

        self._logger.info(f"init_start proxies={len(deduped)}")
        results = self._slot_manager.initialize_all(deduped)

        if isinstance(self._strategy, AsyncQueueSyncStrategy):
            self._strategy.start()

        success = sum(1 for ok in results.values() if ok)
        total = len(results)
        self._logger.info(f"init_summary success={success} total={total}")

        if success < total:
            failed = [k for k, ok in results.items() if not ok]
            self._logger.warning(f"init_partial_fail failed={failed}")
            self._logger.warning(
                "Rsync will still work but may be slower on first connection"
            )

        return results

    def enqueue(self, job: SyncJob) -> bool:
        """Enqueue a sync job.

        For async strategies: blocks up to config.enqueue_timeout_sec.
        For sequential: executes synchronously.

        Returns:
            True if enqueued/executed, False on timeout/error
        """
        if not self._strategy.is_ready():
            self._logger.error(f"enqueue_fail task_id={job.task_id} reason=strategy_not_ready")
            return False

        if isinstance(self._strategy, AsyncQueueSyncStrategy):
            return self._strategy.sync(job)
        else:
            self._strategy.sync(job)
            return True

    def shutdown(self, cleanup_transport: bool = True) -> None:
        """Gracefully stop workers and close SSH connections.

        Args:
            cleanup_transport: if False, skip transport.cleanup_all() so
                ControlMaster connections persist for reuse by later runs.
                Use with ControlPersist=<seconds> for auto-cleanup.
        """
        try:
            self._strategy.shutdown(timeout_sec=30)
        except Exception as e:
            self._logger.warning(f"shutdown_strategy_error error={e}")
        if cleanup_transport:
            try:
                self._transport.cleanup_all()
            except Exception as e:
                self._logger.warning(f"shutdown_transport_error error={e}")
        else:
            self._logger.info(
                "shutdown_keep_control skipping transport cleanup, "
                "ControlMaster sockets preserved"
            )

    # ---- Factory ----

    @classmethod
    def create_default(
        cls,
        config: Optional[SyncConfig] = None,
        callback: Optional[CompletionCallback] = None,
        async_mode: bool = True,
        logger=None,
    ) -> "SyncOrchestrator":
        """Factory for the common case: native transport, auto-load proxies.

        Args:
            config: SyncConfig (uses defaults if None)
            callback: completion callback
            async_mode: use async queue workers (recommended for production)
            logger: logger instance

        Returns:
            Initialized SyncOrchestrator (call .register_proxies + .initialize next)
        """
        import logging
        logger = logger or logging.getLogger("root")
        if config is None:
            config = SyncConfig()

        transport = NativeSSHTransport(config, logger=logger)
        slot_manager = SlotManager(transport, config.slot_count, logger=logger)

        if async_mode:
            strategy = AsyncQueueSyncStrategy(
                transport, slot_manager, config,
                callback=callback, logger=logger,
            )
        else:
            strategy = SequentialSyncStrategy(
                transport, slot_manager,
                callback=callback, logger=logger,
            )

        orch = cls(config, strategy, transport, callback=callback, logger=logger)

        # Register atexit for graceful shutdown
        if not orch._atexit_registered:
            atexit.register(orch._atexit_shutdown)
            orch._atexit_registered = True

        return orch

    def _atexit_shutdown(self):
        """atexit handler: stop workers and close SSH connections."""
        try:
            self.shutdown()
        except Exception:
            pass


__all__ = [
    # Core classes
    "SyncOrchestrator",
    "SyncConfig",
    # Models
    "ProxyTarget",
    "SyncJob",
    "SyncMetrics",
    "SingleSyncResult",
    "TransferResult",
    # Callbacks
    "NoOpCallback",
    "CallbackRecorder",
    # Config parsers
    "get_socat_proxy_configs",
    "get_local_proxy_configs",
    # Transport
    "NativeSSHTransport",
    "MockSSHTransport",
    # Interfaces (for testing)
    "SSHTransport",
    "SyncStrategy",
    "CompletionCallback",
    # Strategies (for testing)
    "SequentialSyncStrategy",
    "AsyncQueueSyncStrategy",
    # Slot management (for testing)
    "SlotManager",
]