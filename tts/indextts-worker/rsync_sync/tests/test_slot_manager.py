"""Tests for rsync_sync.slot_manager."""

from rsync_sync.slot_manager import SlotManager


class TestSlotManager:
    """Test the SlotManager slot allocation."""

    def test_slot_count(self, transport):
        sm = SlotManager(transport, slot_count=3)
        assert sm.slot_count == 3

    def test_slot_range(self, transport):
        sm = SlotManager(transport, slot_count=2)
        assert list(sm.slot_range()) == [0, 1]

    def test_get_slot_for_worker(self, transport):
        sm = SlotManager(transport, slot_count=2)
        assert sm.get_slot_for_worker(0) == 0
        assert sm.get_slot_for_worker(1) == 1
        assert sm.get_slot_for_worker(2) == 0  # wraps around
        assert sm.get_slot_for_worker(3) == 1

    def test_initialize_proxy(self, transport, proxy):
        sm = SlotManager(transport, slot_count=2)
        for slot in range(2):
            transport.preset_initialize(proxy, slot=slot, return_value=True)
        results = sm.initialize_proxy(proxy)
        assert len(results) == 2
        assert all(results.values())

    def test_initialize_all(self, transport, proxies):
        sm = SlotManager(transport, slot_count=1)
        for p in proxies:
            transport.preset_initialize(p, slot=0, return_value=True)
        results = sm.initialize_all(proxies)
        assert len(results) == 3
        assert all(results.values())

    def test_initialize_proxy_partial_failure(self, transport, proxy):
        sm = SlotManager(transport, slot_count=2)
        transport.preset_initialize(proxy, slot=0, return_value=True)
        transport.preset_initialize(proxy, slot=1, return_value=False)
        results = sm.initialize_proxy(proxy)
        assert len(results) == 2
        success_count = sum(1 for ok in results.values() if ok)
        assert success_count == 1

    def test_cleanup_proxy(self, transport, proxy):
        sm = SlotManager(transport, slot_count=2)
        sm.cleanup_proxy(proxy)
        assert transport.cleaned_count == 2

    def test_cleanup_all(self, transport, proxies):
        sm = SlotManager(transport, slot_count=1)
        sm.cleanup_all(proxies)
        assert transport.cleaned_count == 3