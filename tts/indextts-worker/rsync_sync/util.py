"""Pure utility functions for the rsync sync subsystem.

These functions have no side effects — no I/O, no subprocess, no mutable state.
Safe to call from any thread without locks.
"""

import os

from .config import SyncConfig
from .models import ProxyTarget


def get_server_key(user: str, host: str, port: int, slot: int = 0) -> str:
    """Generate a unique key for a proxy+slot combination.

    Format: user@host:port#slot
    """
    return f"{user}@{host}:{int(port)}#{int(slot)}"


def parse_server_key(server_key: str):
    """Parse user@host:port#slot into (user, host, port, slot).

    Inverse of get_server_key().
    """
    base, slot_s = server_key.rsplit("#", 1)
    user_host, port_s = base.rsplit(":", 1)
    user, host = user_host.split("@", 1)
    return user, host, int(port_s), int(slot_s)


def get_control_path(
    user: str,
    host: str,
    port: int,
    slot: int = 0,
    config: SyncConfig = None,
) -> str:
    """Generate the filesystem path for a ControlMaster socket.

    Args:
        user: SSH user
        host: server hostname/IP
        port: SSH port
        slot: ControlMaster slot number
        config: SyncConfig instance (uses default if None)

    Returns:
        Absolute path to the control socket file
    """
    if config is None:
        config = SyncConfig()
    safe_name = (
        f"{user}@{host}:{int(port)}"
        .replace("@", "_at_")
        .replace(":", "_")
        .replace("/", "_")
    )
    return os.path.join(config.control_dir, f"control_{safe_name}_{int(slot)}")


def get_dir_key(user: str, host: str, port: int, target_dir: str) -> str:
    """Generate a cache key for tracking created remote directories.

    Format: user@host:port|/path/to/dir
    """
    return f"{user}@{host}:{int(port)}|{target_dir}"


def format_file_size(file_size: int) -> str:
    """Format a byte count into a human-readable string.

    Args:
        file_size: size in bytes

    Returns:
        Formatted string like "1.50 MB", "512 B"
    """
    if file_size < 1024:
        return f"{file_size} B"
    if file_size < 1024 * 1024:
        return f"{file_size / 1024:.2f} KB"
    return f"{file_size / (1024 * 1024):.2f} MB"


def compute_speed_mbps(file_size_bytes: int, elapsed_sec: float) -> float:
    """Compute average transfer speed in MB/s.

    Args:
        file_size_bytes: total bytes transferred
        elapsed_sec: elapsed time in seconds

    Returns:
        Speed in MB/s, or 0.0 if elapsed_sec is 0 or negative
    """
    if elapsed_sec <= 0 or file_size_bytes <= 0:
        return 0.0
    return (file_size_bytes / (1024 * 1024)) / elapsed_sec


def get_proxy_server_key(proxy: ProxyTarget, slot: int = 0) -> str:
    """Shorthand: get_server_key from a ProxyTarget."""
    return get_server_key(proxy.user, proxy.host, proxy.port, slot)


def get_proxy_control_path(
    proxy: ProxyTarget,
    slot: int = 0,
    config: SyncConfig = None,
) -> str:
    """Shorthand: get_control_path from a ProxyTarget."""
    return get_control_path(proxy.user, proxy.host, proxy.port, slot, config)