"""Tests for rsync_sync.util."""

from rsync_sync.config import SyncConfig
from rsync_sync.models import ProxyTarget
from rsync_sync.util import (
    compute_speed_mbps,
    format_file_size,
    get_control_path,
    get_dir_key,
    get_proxy_control_path,
    get_proxy_server_key,
    get_server_key,
    parse_server_key,
)


class TestGetServerKey:
    def test_basic(self):
        assert get_server_key("alice", "host", 22) == "alice@host:22#0"

    def test_with_slot(self):
        assert get_server_key("bob", "example.com", 2222, slot=3) == "bob@example.com:2222#3"

    def test_port_cast(self):
        assert get_server_key("u", "h", "22") == "u@h:22#0"


class TestParseServerKey:
    def test_roundtrip(self):
        original = ("bob", "remote.example.com", 2222, 1)
        key = get_server_key(*original)
        assert parse_server_key(key) == original

    def test_without_slot(self):
        user, host, port, slot = parse_server_key("alice@host:22#0")
        assert user == "alice"
        assert host == "host"
        assert port == 22
        assert slot == 0


class TestGetControlPath:
    def test_default_config(self):
        path = get_control_path("u", "h", 22)
        assert "/app/data/ssh_control/control_" in path
        assert "u_at_h_22_0" in path  # last segment


class TestFormatFileSize:
    def test_bytes(self):
        assert format_file_size(0) == "0 B"
        assert format_file_size(512) == "512 B"
        assert format_file_size(1023) == "1023 B"

    def test_kb(self):
        assert format_file_size(1024) == "1.00 KB"
        assert format_file_size(1536) == "1.50 KB"

    def test_mb(self):
        assert format_file_size(1048576) == "1.00 MB"
        assert format_file_size(2097152) == "2.00 MB"


class TestComputeSpeedMBps:
    def test_normal(self):
        # 1 MB in 1 second = 1 MB/s
        assert abs(compute_speed_mbps(1048576, 1.0) - 1.0) < 0.01

    def test_zero_elapsed(self):
        assert compute_speed_mbps(1024, 0) == 0.0

    def test_negative_elapsed(self):
        assert compute_speed_mbps(1024, -1) == 0.0

    def test_zero_size(self):
        assert compute_speed_mbps(0, 1.0) == 0.0


class TestGetDirKey:
    def test_format(self):
        key = get_dir_key("u", "h", 2222, "/remote/path")
        assert key == "u@h:2222|/remote/path"


class TestProxyShorthand:
    def test_get_proxy_server_key(self):
        p = ProxyTarget("u", "h", 2222)
        assert get_proxy_server_key(p, slot=1) == "u@h:2222#1"

    def test_get_proxy_control_path(self):
        p = ProxyTarget("u", "h", 2222)
        path = get_proxy_control_path(p, slot=1)
        assert "control_u_at_h_2222_1" in path