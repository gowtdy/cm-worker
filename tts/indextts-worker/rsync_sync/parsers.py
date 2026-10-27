"""Config-file parsing logic for rsync proxy lists.

Migrated from socat_rsyc_util.py and sync_util.py.
Functions are pure (no mutable global state).
"""

import logging
import os
import re
from typing import List

from .config import SyncConfig
from .exceptions import ProxyConfigError
from .models import ProxyTarget

_RSYNC_PROXY_LINE_RE = re.compile(
    r"^\s*(?P<user>[^@\s#]+)@(?P<host>[^:\s#]+):(?P<port>\d+)\s*$"
)


def parse_proxy_config(text: str) -> List[ProxyTarget]:
    """Parse proxy config file content.

    Supported format (one per line):
      user@host:port

    Notes:
      - Blank lines are ignored.
      - Lines starting with '#' are ignored.
      - Leading/trailing whitespace is ignored.
      - Port must be in range 1-65535.

    Args:
        text: raw config file content

    Returns:
        List of valid ProxyTarget entries
    """
    proxies: List[ProxyTarget] = []
    if not text:
        return proxies

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        m = _RSYNC_PROXY_LINE_RE.match(line)
        if not m:
            logging.warning(f"Invalid rsync_proxy line: {line!r}")
            continue

        user = m.group("user").strip()
        host = m.group("host").strip()
        port_s = m.group("port").strip()
        try:
            port = int(port_s)
        except ValueError:
            logging.warning(f"Invalid rsync_proxy port: {port_s!r}")
            continue

        if port < 1 or port > 65535:
            logging.warning(f"Out-of-range rsync_proxy port: {port}")
            continue

        proxies.append(ProxyTarget(user=user, host=host, port=port))

    return proxies


def load_proxy_config_file(
    config_file: str,
    fail_if_empty: bool = True,
) -> List[ProxyTarget]:
    """Read and parse a rsync proxy config file from disk.

    Args:
        config_file: absolute path to the config file
        fail_if_empty: if True, raise ProxyConfigError when no valid proxies found

    Returns:
        List of valid ProxyTarget entries

    Raises:
        ProxyConfigError: if fail_if_empty is True and no valid proxies are found
    """
    proxies: List[ProxyTarget] = []
    try:
        if os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8", errors="replace") as f:
                proxies = parse_proxy_config(f.read())
    except Exception as e:
        logging.warning(f"Error reading proxy config {config_file}: {e}")

    if fail_if_empty and (not proxies or len(proxies) == 0):
        raise ProxyConfigError(f"No valid proxy entries found in {config_file}")

    return proxies


def get_socat_proxy_configs(
    config_file: str = None,
    config: SyncConfig = None,
) -> List[ProxyTarget]:
    """Load socat (cross-region tunnel) proxy configs.

    Args:
        config_file: override path (defaults to SyncConfig.socat_proxies_file)
        config: SyncConfig instance (uses default if None)

    Returns:
        List of ProxyTarget entries

    Raises:
        ProxyConfigError: if no valid proxies found
    """
    if config is None:
        config = SyncConfig()
    if config_file is None:
        config_file = config.socat_proxies_file
    return load_proxy_config_file(config_file, fail_if_empty=True)


def get_local_proxy_configs(
    config_file: str = None,
    config: SyncConfig = None,
) -> List[ProxyTarget]:
    """Load local (LAN) proxy configs for video-script TTS sync.

    Args:
        config_file: override path (defaults to SyncConfig.local_proxies_file)
        config: SyncConfig instance (uses default if None)

    Returns:
        List of ProxyTarget entries

    Raises:
        ProxyConfigError: if no valid proxies found
    """
    if config is None:
        config = SyncConfig()
    if config_file is None:
        config_file = config.local_proxies_file
    return load_proxy_config_file(config_file, fail_if_empty=True)


# Legacy aliases for backward compatibility
parse_rsync_proxy_config = parse_proxy_config