"""ControlMaster slot allocation per proxy.

Each proxy gets `slot_count` independent ControlMaster connections (slots).
This module assigns slots to sync jobs and manages slot lifecycle.

Previously this was implicit via range(SYNC_SLOT_COUNT) in
initialize_ssh_connections_for_proxies() and the worker loop.
"""

import threading
from typing import Dict, List

from .interfaces import SSHTransport
from .models import ProxyTarget


class SlotManager:
    """Manages ControlMaster slot allocation per proxy.

    Responsible for:
      - Initializing all slots for all proxies
      - Providing a list of slot numbers to use
      - Coordinated cleanup of all slots

    Does NOT own the transport — the strategy/caller does.
    """

    def __init__(
        self,
        transport: SSHTransport,
        slot_count: int = 2,
        logger=None,
    ):
        """
        Args:
            transport: SSHTransport to use for init/cleanup
            slot_count: number of ControlMaster slots per proxy
            logger: optional logger
        """
        self._transport = transport
        self._slot_count = slot_count
        import logging
        self._logger = logger or logging.getLogger("root")
        self._lock = threading.Lock()

    @property
    def slot_count(self) -> int:
        return self._slot_count

    def slot_range(self) -> range:
        """Return range(0, slot_count) for iterating over slots."""
        return range(self._slot_count)

    def initialize_proxy(self, proxy: ProxyTarget) -> Dict[str, bool]:
        """Initialize all slots for a single proxy.

        Returns:
            Dict mapping server_key -> success status
        """
        results: Dict[str, bool] = {}
        for slot in self.slot_range():
            ok = self._transport.initialize_connection(proxy, slot=slot)
            key = f"{proxy.server}#{slot}"
            results[key] = ok
            if ok:
                self._logger.info(f"slot_init_ok proxy={proxy.server} slot={slot}")
            else:
                self._logger.warning(f"slot_init_fail proxy={proxy.server} slot={slot}")
        return results

    def initialize_all(self, proxies: List[ProxyTarget]) -> Dict[str, bool]:
        """Initialize all slots for all proxies.

        Returns:
            Dict mapping server_key -> success status
        """
        results: Dict[str, bool] = {}
        for proxy in proxies:
            proxy_results = self.initialize_proxy(proxy)
            results.update(proxy_results)

        success_count = sum(1 for ok in results.values() if ok)
        total_count = len(results)
        self._logger.info(
            f"slots_init_summary success={success_count} total={total_count} "
            f"slot_count={self._slot_count}"
        )
        return results

    def cleanup_proxy(self, proxy: ProxyTarget) -> None:
        """Cleanup all slots for a single proxy."""
        for slot in self.slot_range():
            self._transport.cleanup_connection(proxy, slot=slot)

    def cleanup_all(self, proxies: List[ProxyTarget]) -> None:
        """Cleanup all slots for a list of proxies."""
        for proxy in proxies:
            self.cleanup_proxy(proxy)

    def get_slot_for_worker(self, worker_index: int) -> int:
        """Map a worker index to a ControlMaster slot.

        Simple round-robin: worker_index % slot_count.

        Can be overridden for custom allocation strategies.
        """
        return worker_index % self._slot_count