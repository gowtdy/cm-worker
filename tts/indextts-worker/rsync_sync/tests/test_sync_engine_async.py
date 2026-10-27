"""Tests for AsyncQueueSyncStrategy."""

import time

from rsync_sync.slot_manager import SlotManager
from rsync_sync.sync_engine import AsyncQueueSyncStrategy
from rsync_sync.models import ProxyTarget, SyncJob


class TestAsyncQueueSyncStrategy:
    """Test the async queue-based sync strategy."""

    def test_start_and_shutdown(self, transport):
        sm = SlotManager(transport, slot_count=2)
        strategy = AsyncQueueSyncStrategy(transport, sm)
        assert not strategy.is_ready()
        strategy.start()
        assert strategy.is_ready()
        strategy.shutdown(timeout_sec=5)
        assert not strategy.is_ready()

    def test_enqueue_and_process(self, transport, proxy, callback, ok_transfer):
        transport.preset_ensure_remote_dir(proxy, "/out", slot=0, return_value=True)
        transport.preset_transfer("/tmp/a.wav", proxy, "/out", slot=0, return_value=ok_transfer)
        transport.preset_ensure_remote_dir(proxy, "/out", slot=1, return_value=True)
        transport.preset_transfer("/tmp/a.wav", proxy, "/out", slot=1, return_value=ok_transfer)

        sm = SlotManager(transport, slot_count=2)
        strategy = AsyncQueueSyncStrategy(transport, sm, callback=callback)

        strategy.start()
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t1", proxies=[proxy])
        ok = strategy.sync(job)  # enqueue
        assert ok

        # Give worker time to process
        time.sleep(0.3)

        strategy.shutdown(timeout_sec=5)
        assert len(callback.completed) == 1
        assert callback.completed[0].task_id == "t1"

    def test_enqueue_before_start_fails(self, transport):
        sm = SlotManager(transport, slot_count=1)
        strategy = AsyncQueueSyncStrategy(transport, sm)
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t1", proxies=[])
        assert not strategy.sync(job)  # not started

    def test_multiple_jobs(self, transport, proxy, callback, ok_transfer):
        for slot in range(2):
            transport.preset_ensure_remote_dir(proxy, "/out", slot=slot, return_value=True)
            transport.preset_transfer("/tmp/a.wav", proxy, "/out", slot=slot, return_value=ok_transfer)

        sm = SlotManager(transport, slot_count=2)
        strategy = AsyncQueueSyncStrategy(transport, sm, callback=callback)

        strategy.start()
        for i in range(3):
            job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                          task_id=f"t{i}", proxies=[proxy])
            assert strategy.sync(job)

        time.sleep(0.5)
        strategy.shutdown(timeout_sec=5)
        assert len(callback.completed) == 3

    def test_callback_failure_reports_to_callback(self, transport, proxy, callback, fail_transfer):
        transport.preset_ensure_remote_dir(proxy, "/out", slot=0, return_value=True)
        transport.preset_transfer("/tmp/a.wav", proxy, "/out", slot=0, return_value=fail_transfer)

        sm = SlotManager(transport, slot_count=1)
        strategy = AsyncQueueSyncStrategy(transport, sm, callback=callback)

        strategy.start()
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out",
                      task_id="t_fail", proxies=[proxy])
        strategy.sync(job)

        time.sleep(0.3)
        strategy.shutdown(timeout_sec=5)
        assert len(callback.failed) == 1
        assert "some proxies failed" in callback.errors[0]