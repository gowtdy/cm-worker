"""Shared fixtures for rsync_sync tests.

Uses plain functions (no pytest dependency) so tests can run without pytest.
"""

from rsync_sync.config import SyncConfig
from rsync_sync.models import ProxyTarget, SyncJob, TransferResult
from rsync_sync.transport import MockSSHTransport
from rsync_sync.callbacks import CallbackRecorder


def config():
    """Default test config (in-memory, no /app paths needed)."""
    return SyncConfig(
        control_dir="/tmp/test_ssh_control",
        slot_count=2,
        queue_maxsize=4,
        enqueue_timeout_sec=2,
        rsync_timeout_sec=10,
        ssh_init_timeout_sec=5,
    )


def proxy():
    """A single test proxy."""
    return ProxyTarget(user="testuser", host="testhost.example.com", port=2222)


def proxies():
    """Multiple test proxies."""
    return [
        ProxyTarget(user="testuser", host="srv1.example.com", port=2222),
        ProxyTarget(user="testuser", host="srv2.example.com", port=2222),
        ProxyTarget(user="testuser", host="srv3.example.com", port=2222),
    ]


def sync_job(proxy):
    """A basic sync job."""
    return SyncJob(
        source_file="/tmp/test_audio.wav",
        target_dir="/remote/output",
        task_id="test-task-001",
        proxies=[proxy],
        metadata={
            "is_eapi": False,
            "short_uri_prefix": "/prefix",
            "output_file": "audio.wav",
        },
    )


def transport():
    """A fresh MockSSHTransport."""
    return MockSSHTransport()


def callback():
    """A fresh CallbackRecorder."""
    return CallbackRecorder()


def ok_transfer():
    """Preset for a successful transfer."""
    return TransferResult(ok=True, elapsed_sec=0.5, stderr="")


def fail_transfer():
    """Preset for a failed transfer."""
    return TransferResult(ok=False, elapsed_sec=5.0, stderr="Connection timeout")