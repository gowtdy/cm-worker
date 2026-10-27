"""Immutable configuration for the rsync sync subsystem.

Replaces the module-level constants previously scattered across
socat_rsyc_util.py and sync_util.py.
"""

import os
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class SyncConfig:
    """All tunables for the sync subsystem.

    Frozen (immutable) to prevent accidental mutation across threads.
    """

    # SSH ControlMaster directory (socket files)
    control_dir: str = "/app/data/ssh_control"

    # ControlPersist: "yes" = permanent, integer = seconds
    control_persist: Union[str, int] = "yes"

    # SSH connection options
    connect_timeout_sec: int = 5
    connection_attempts: int = 1
    server_alive_interval_sec: int = 5
    server_alive_count_max: int = 2

    # Timeout for initial SSH ControlMaster handshake
    ssh_init_timeout_sec: int = 10

    # Timeout for remote mkdir via SSH
    ssh_mkdir_timeout_sec: int = 15

    # Timeout for rsync transfer
    rsync_timeout_sec: int = 120

    # Flags passed to rsync
    rsync_flags: str = "-rt --whole-file --partial"

    # SSH options applied to all connections
    gssapi_authentication: str = "no"
    compression: str = "no"

    # Number of ControlMaster slots per proxy
    slot_count: int = 2

    # Bounded queue max size for async workers
    queue_maxsize: int = 6

    # How long enqueue blocks before timing out
    enqueue_timeout_sec: int = 60

    # Paths to proxy config files
    socat_proxies_file: str = "/app/data/socat_rsync_proxies"
    local_proxies_file: str = "/app/data/local_rsync_proxies"

    def ensure_control_dir(self) -> None:
        """Create the SSH control directory if it does not exist."""
        os.makedirs(self.control_dir, mode=0o755, exist_ok=True)

    @property
    def ssh_conn_opts_list(self) -> list:
        """SSH connection options used for all SSH invocations."""
        return [
            "-o", f"ConnectTimeout={self.connect_timeout_sec}",
            "-o", f"ConnectionAttempts={self.connection_attempts}",
            "-o", f"ServerAliveInterval={self.server_alive_interval_sec}",
            "-o", f"ServerAliveCountMax={self.server_alive_count_max}",
        ]

    @property
    def ssh_base_opts_list(self) -> list:
        """Common SSH options used for ControlMaster setup and transport."""
        return self.ssh_conn_opts_list + [
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", f"GSSAPIAuthentication={self.gssapi_authentication}",
            "-o", f"Compression={self.compression}",
        ]