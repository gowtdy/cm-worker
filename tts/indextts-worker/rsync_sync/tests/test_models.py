"""Tests for rsync_sync.models."""

from rsync_sync.models import (
    ProxyTarget,
    SingleSyncResult,
    SyncJob,
    SyncMetrics,
    TransferResult,
)


class TestProxyTarget:
    """Test the ProxyTarget dataclass."""

    def test_creation(self):
        p = ProxyTarget("alice", "example.com", 2222)
        assert p.user == "alice"
        assert p.host == "example.com"
        assert p.port == 2222

    def test_frozen(self):
        p = ProxyTarget("alice", "example.com", 2222)
        try:
            p.host = "other.com"
            assert False, "Should have raised FrozenInstanceError"
        except Exception:
            pass  # expected

    def test_server_property(self):
        p = ProxyTarget("alice", "example.com", 22)
        assert p.server == "alice@example.com:22"

    def test_connect_target_property(self):
        p = ProxyTarget("bob", "10.0.0.1", 2222)
        assert p.connect_target == "bob@10.0.0.1"

    def test_hashable(self):
        p1 = ProxyTarget("a", "h", 1)
        p2 = ProxyTarget("a", "h", 1)
        assert hash(p1) == hash(p2)
        assert p1 == p2


class TestSyncJob:
    """Test the SyncJob dataclass."""

    def test_creation(self):
        p = ProxyTarget("u", "h", 1)
        job = SyncJob(
            source_file="/tmp/x.wav",
            target_dir="/out",
            task_id="t1",
            proxies=[p],
            metadata={"is_eapi": True},
        )
        assert job.source_file == "/tmp/x.wav"
        assert job.target_dir == "/out"
        assert job.task_id == "t1"
        assert len(job.proxies) == 1
        assert job.metadata["is_eapi"] is True

    def test_defaults(self):
        job = SyncJob(source_file="/tmp/x.wav", target_dir="/out", task_id="t2")
        assert job.proxies == []
        assert job.metadata == {}

    def test_mutability(self):
        job = SyncJob(source_file="/tmp/a.wav", target_dir="/out", task_id="t3")
        job.metadata["new_key"] = "value"
        assert job.metadata["new_key"] == "value"


class TestTransferResult:
    """Test the TransferResult dataclass."""

    def test_success(self):
        r = TransferResult(ok=True, elapsed_sec=0.5)
        assert r.ok is True
        assert r.elapsed_sec == 0.5
        assert r.stderr == ""

    def test_failure(self):
        r = TransferResult(ok=False, elapsed_sec=5.0, stderr="timeout")
        assert r.ok is False
        assert r.stderr == "timeout"


class TestSyncMetrics:
    """Test SyncMetrics aggregation."""

    def test_empty(self):
        m = SyncMetrics()
        assert m.success_count == 0
        assert m.total_count == 0
        assert m.all_succeeded is False  # 0/0 is not "all succeeded"

    def test_all_succeeded(self, proxy):
        r1 = SingleSyncResult(proxy=proxy, slot=0, ok=True, elapsed_sec=0.5,
                              file_size_bytes=1024, avg_speed_mbps=2.0)
        r2 = SingleSyncResult(proxy=proxy, slot=1, ok=True, elapsed_sec=0.3,
                              file_size_bytes=1024, avg_speed_mbps=3.0)
        m = SyncMetrics(results=[r1, r2], total_elapsed_sec=1.0, file_size_bytes=1024)
        assert m.success_count == 2
        assert m.total_count == 2
        assert m.all_succeeded is True
        assert m.failed_proxies == []

    def test_partial_failure(self, proxy):
        r1 = SingleSyncResult(proxy=proxy, slot=0, ok=True, elapsed_sec=0.5,
                              file_size_bytes=1024)
        r2 = SingleSyncResult(proxy=ProxyTarget("u", "h2", 1), slot=0, ok=False,
                              elapsed_sec=5.0, file_size_bytes=1024,
                              error_msg="timeout")
        m = SyncMetrics(results=[r1, r2], total_elapsed_sec=5.5, file_size_bytes=1024)
        assert m.success_count == 1
        assert m.total_count == 2
        assert m.all_succeeded is False
        assert len(m.failed_proxies) == 1