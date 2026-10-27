"""Tests for rsync_sync.callbacks."""

from rsync_sync.models import ProxyTarget, SingleSyncResult, SyncJob, SyncMetrics
from rsync_sync.callbacks import CallbackRecorder, NoOpCallback


class TestCallbackRecorder:
    """Test the CallbackRecorder test double."""

    def test_records_complete(self, callback, sync_job):
        metrics = SyncMetrics(
            results=[SingleSyncResult(
                proxy=sync_job.proxies[0], slot=0, ok=True,
                elapsed_sec=0.5, file_size_bytes=1024,
            )],
            total_elapsed_sec=0.5,
            file_size_bytes=1024,
        )
        callback.on_sync_complete(sync_job, metrics)
        assert len(callback.completed) == 1
        assert callback.completed[0] is sync_job
        assert len(callback.failed) == 0

    def test_records_failed(self, callback, sync_job):
        callback.on_sync_failed(sync_job, "test error")
        assert len(callback.failed) == 1
        assert callback.failed[0] is sync_job
        assert callback.errors == ["test error"]
        assert len(callback.completed) == 0

    def test_reset(self, callback, sync_job):
        metrics = SyncMetrics()
        callback.on_sync_complete(sync_job, metrics)
        callback.on_sync_failed(sync_job, "err")
        assert len(callback.completed) == 1
        assert len(callback.failed) == 1

        callback.reset()
        assert len(callback.completed) == 0
        assert len(callback.failed) == 0
        assert len(callback.errors) == 0


class TestNoOpCallback:
    """Test the NoOpCallback."""

    def test_does_nothing(self, sync_job):
        cb = NoOpCallback()
        cb.on_sync_complete(sync_job, SyncMetrics())  # should not raise
        cb.on_sync_failed(sync_job, "error")  # should not raise