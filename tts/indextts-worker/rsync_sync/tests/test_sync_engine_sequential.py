"""Tests for SequentialSyncStrategy."""

from rsync_sync.slot_manager import SlotManager
from rsync_sync.sync_engine import SequentialSyncStrategy
from rsync_sync.models import ProxyTarget, SyncJob


class TestSequentialSyncStrategy:
    """Test the sequential (one-by-one) sync strategy."""

    def test_single_proxy_success(self, transport, proxy, callback, ok_transfer):
        transport.preset_ensure_remote_dir(proxy, "/out", slot=0, return_value=True)
        transport.preset_transfer("/tmp/a.wav", proxy, "/out", slot=0, return_value=ok_transfer)

        sm = SlotManager(transport, slot_count=1)
        strategy = SequentialSyncStrategy(transport, sm, callback=callback)
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t1", proxies=[proxy])

        metrics = strategy.sync(job)
        assert metrics.all_succeeded
        assert metrics.success_count == 1
        assert metrics.total_count == 1
        assert len(callback.completed) == 1
        assert callback.completed[0].task_id == "t1"

    def test_multiple_proxies(self, transport, proxies, callback, ok_transfer):
        for p in proxies:
            transport.preset_ensure_remote_dir(p, "/out", slot=0, return_value=True)
            transport.preset_transfer("/tmp/a.wav", p, "/out", slot=0, return_value=ok_transfer)

        sm = SlotManager(transport, slot_count=1)
        strategy = SequentialSyncStrategy(transport, sm, callback=callback)
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t2", proxies=proxies)

        metrics = strategy.sync(job)
        assert metrics.all_succeeded
        assert metrics.success_count == 3
        assert len(callback.completed) == 1

    def test_partial_failure(self, transport, callback, ok_transfer, fail_transfer):
        p1 = ProxyTarget("u", "good", 2222)
        p2 = ProxyTarget("u", "bad", 2222)
        transport.preset_ensure_remote_dir(p1, "/out", slot=0, return_value=True)
        transport.preset_ensure_remote_dir(p2, "/out", slot=0, return_value=True)
        transport.preset_transfer("/tmp/a.wav", p1, "/out", slot=0, return_value=ok_transfer)
        transport.preset_transfer("/tmp/a.wav", p2, "/out", slot=0, return_value=fail_transfer)

        sm = SlotManager(transport, slot_count=1)
        strategy = SequentialSyncStrategy(transport, sm, callback=callback)
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t3", proxies=[p1, p2])

        metrics = strategy.sync(job)
        assert not metrics.all_succeeded
        assert metrics.success_count == 1
        assert metrics.total_count == 2
        assert len(callback.failed) == 1

    def test_empty_proxies(self, transport, callback):
        sm = SlotManager(transport, slot_count=1)
        strategy = SequentialSyncStrategy(transport, sm, callback=callback)
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t4", proxies=[])

        metrics = strategy.sync(job)
        assert metrics.total_count == 0
        assert not metrics.all_succeeded  # 0/0

    def test_is_ready(self, transport):
        sm = SlotManager(transport, slot_count=1)
        strategy = SequentialSyncStrategy(transport, sm)
        assert strategy.is_ready()  # always ready

    def test_shutdown_noop(self, transport):
        sm = SlotManager(transport, slot_count=1)
        strategy = SequentialSyncStrategy(transport, sm)
        strategy.shutdown()  # should not raise