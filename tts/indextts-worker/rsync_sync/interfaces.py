"""Abstract interfaces for the rsync sync subsystem.

Defines the three core abstractions that enable dependency injection and testing:

- SSHTransport: all subprocess/SSH/rsync operations (the primary test boundary)
- SyncStrategy: orchestration of syncing to multiple proxies
- CompletionCallback: business logic hooks after sync completes/fails
"""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from .models import ProxyTarget, SyncJob, SyncMetrics, TransferResult


class SSHTransport(ABC):
    """Interface for all SSH and rsync operations.

    This is the primary abstraction boundary. All operations that touch
    the real world (subprocess, filesystem, SSH, rsync) go through this
    interface. Every other layer is testable by injecting a MockSSHTransport.

    Implementations:
        NativeSSHTransport — real subprocess-based SSH/rsync
        MockSSHTransport  — preset-based fake for unit tests
    """

    @abstractmethod
    def initialize_connection(self, proxy: ProxyTarget, slot: int = 0) -> bool:
        """Establish a ControlMaster connection for proxy+slot.

        Args:
            proxy: target server
            slot: ControlMaster slot number

        Returns:
            True if connection was established successfully
        """

    @abstractmethod
    def cleanup_connection(self, proxy: ProxyTarget, slot: int = 0) -> None:
        """Close a single ControlMaster connection.

        Args:
            proxy: target server
            slot: ControlMaster slot number
        """

    @abstractmethod
    def cleanup_all(self) -> None:
        """Close all connections tracked by this transport."""

    @abstractmethod
    def ensure_remote_dir(
        self, proxy: ProxyTarget, target_dir: str, slot: int = 0
    ) -> bool:
        """Create a remote directory via SSH.

        Must be idempotent (safe to call multiple times for the same path)
        and include retry logic for stale control sockets.

        Args:
            proxy: target server
            target_dir: absolute path on the remote server
            slot: ControlMaster slot number

        Returns:
            True if directory was created successfully
        """

    @abstractmethod
    def transfer_file(
        self,
        source_file: str,
        proxy: ProxyTarget,
        target_dir: str,
        slot: int = 0,
    ) -> TransferResult:
        """rsync a single file to a proxy via the given ControlMaster slot.

        Args:
            source_file: local file path to transfer
            proxy: target server
            target_dir: destination directory on the remote server
            slot: ControlMaster slot number

        Returns:
            TransferResult with ok, elapsed_sec, and stderr
        """

    @abstractmethod
    def get_control_path(self, proxy: ProxyTarget, slot: int = 0) -> str:
        """Return the filesystem path of a ControlMaster socket.

        Args:
            proxy: target server
            slot: ControlMaster slot number

        Returns:
            Absolute path to the control socket file
        """


class SyncStrategy(ABC):
    """Abstract strategy for syncing a job to multiple proxies.

    Implementations:
        SequentialSyncStrategy  — iterate proxies one at a time
        AsyncQueueSyncStrategy  — bounded queue with N worker threads
    """

    @abstractmethod
    def sync(self, job: SyncJob) -> SyncMetrics:
        """Execute one full sync (source_file → all proxies in job).

        Args:
            job: the sync job to execute

        Returns:
            Aggregated metrics across all proxy syncs
        """

    @abstractmethod
    def shutdown(self, timeout_sec: float = 30.0) -> None:
        """Gracefully drain and stop.

        For non-async strategies this is a no-op.
        """

    @abstractmethod
    def is_ready(self) -> bool:
        """Whether the strategy is initialized and can accept jobs."""


@runtime_checkable
class CompletionCallback(Protocol):
    """Protocol for post-sync completion actions.

    Business logic (e.g., DB updates) lives in implementations.
    The sync module never imports database code — callbacks are
    injected by the caller (normally gpu_indextts_tasks.py).

    Implementations:
        NoOpCallback      — does nothing (for testing/standalone use)
        DbUpdateCallback  — updates API user / IP use records in MySQL
    """

    def on_sync_complete(self, job: SyncJob, result: SyncMetrics) -> None:
        """Called when a sync job completes successfully.

        Args:
            job: the completed sync job
            result: aggregated metrics across all proxies
        """
        ...

    def on_sync_failed(self, job: SyncJob, error: str) -> None:
        """Called when a sync job fails entirely.

        Args:
            job: the failed sync job
            error: error description
        """
        ...