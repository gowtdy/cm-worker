"""Tests for rsync_sync.config."""

import os

from rsync_sync.config import SyncConfig


class TestSyncConfig:
    """Test SyncConfig defaults and customization."""

    def test_defaults(self):
        c = SyncConfig()
        assert c.control_dir == "/app/data/ssh_control"
        assert c.control_persist == "yes"
        assert c.slot_count == 2
        assert c.queue_maxsize == 6
        assert c.enqueue_timeout_sec == 60
        assert c.rsync_timeout_sec == 120
        assert c.connect_timeout_sec == 5
        assert c.connection_attempts == 1
        assert c.rsync_flags == "-rt --whole-file --partial"

    def test_custom_values(self):
        c = SyncConfig(slot_count=4, queue_maxsize=10, rsync_flags="-av --compress")
        assert c.slot_count == 4
        assert c.queue_maxsize == 10
        assert c.rsync_flags == "-av --compress"

    def test_ensure_control_dir_creates_directory(self):
        import tempfile
        control_dir = os.path.join(tempfile.mkdtemp(), "ssh_control")
        c = SyncConfig(control_dir=control_dir)
        c.ensure_control_dir()
        assert os.path.isdir(control_dir)

    def test_ssh_conn_opts_list(self):
        c = SyncConfig(connect_timeout_sec=3, connection_attempts=2,
                       server_alive_interval_sec=10, server_alive_count_max=1)
        opts = c.ssh_conn_opts_list
        assert opts == [
            "-o", "ConnectTimeout=3",
            "-o", "ConnectionAttempts=2",
            "-o", "ServerAliveInterval=10",
            "-o", "ServerAliveCountMax=1",
        ]

    def test_ssh_base_opts_includes_base_and_conn(self):
        c = SyncConfig()
        base = c.ssh_base_opts_list
        assert "-o" in base
        assert "StrictHostKeyChecking=no" in base
        assert "BatchMode=yes" in base
        assert "GSSAPIAuthentication=no" in base
        assert "Compression=no" in base