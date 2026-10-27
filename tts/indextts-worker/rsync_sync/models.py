"""Data models for the rsync sync subsystem.

SyncJob.metadata carries business payloads that the sync module itself
never reads — only CompletionCallback implementations consume those keys.
This is the design decoupling point between transport and business logic.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ProxyTarget:
    """A remote server reachable via SSH.

    Replaces the old RsyncProxy dataclass from socat_rsyc_util.py.
    Frozen to allow use as dict key and prevent accidental mutation.
    """

    user: str
    host: str
    port: int

    @property
    def server(self) -> str:
        """Human-readable server address string."""
        return f"{self.user}@{self.host}:{self.port}"

    @property
    def connect_target(self) -> str:
        """SSH connection string: user@host."""
        return f"{self.user}@{self.host}"


@dataclass
class SyncJob:
    """Metadata describing a file that needs to be synced to proxies.

    The `metadata` dict carries business-specific payloads
    (e.g. is_eapi, short_uri_prefix, output_file) that the sync
    module itself never reads — only CompletionCallback consumers do.
    """

    source_file: str
    target_dir: str
    task_id: str
    proxies: List[ProxyTarget] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransferResult:
    """Raw result of a single transport-level file transfer."""

    ok: bool
    elapsed_sec: float
    stderr: str = ""


@dataclass(frozen=True)
class SingleSyncResult:
    """Result of syncing to ONE proxy."""

    proxy: ProxyTarget
    slot: int
    ok: bool
    elapsed_sec: float
    file_size_bytes: int
    avg_speed_mbps: float = 0.0
    error_msg: str = ""


@dataclass
class SyncMetrics:
    """Aggregated result across all proxies for one SyncJob."""

    results: List[SingleSyncResult] = field(default_factory=list)
    total_elapsed_sec: float = 0.0
    file_size_bytes: int = 0

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def all_succeeded(self) -> bool:
        return self.success_count == self.total_count and self.total_count > 0

    @property
    def failed_proxies(self) -> List[str]:
        return [r.proxy.server for r in self.results if not r.ok]