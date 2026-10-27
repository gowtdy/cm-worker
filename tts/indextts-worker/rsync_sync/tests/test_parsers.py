"""Tests for rsync_sync.parsers."""

import tempfile

from rsync_sync.parsers import parse_proxy_config


class TestParseProxyConfig:
    """Test the proxy config text parser."""

    def test_single_valid(self):
        text = "alice@example.com:2222"
        proxies = parse_proxy_config(text)
        assert len(proxies) == 1
        p = proxies[0]
        assert p.user == "alice"
        assert p.host == "example.com"
        assert p.port == 2222

    def test_multiple_valid(self):
        text = """
        alice@host1.example.com:2222
        bob@host2.example.com:3333
        """
        proxies = parse_proxy_config(text)
        assert len(proxies) == 2
        assert proxies[0].user == "alice"
        assert proxies[1].user == "bob"

    def test_empty(self):
        assert parse_proxy_config("") == []
        assert parse_proxy_config("  \n  \n") == []

    def test_comments_ignored(self):
        text = """
        # This is a comment
        alice@host.com:1234
        # bob@host.com:5678
        """
        proxies = parse_proxy_config(text)
        assert len(proxies) == 1
        assert proxies[0].user == "alice"

    def test_whitespace_trimmed(self):
        text = "  alice@host.com:1234  "
        proxies = parse_proxy_config(text)
        assert len(proxies) == 1

    def test_invalid_format(self):
        text = "not-a-valid-line"
        proxies = parse_proxy_config(text)
        assert proxies == []

    def test_invalid_port(self):
        text = "alice@host.com:not-a-number"
        proxies = parse_proxy_config(text)
        assert proxies == []

    def test_port_out_of_range(self):
        text = "alice@host.com:99999"
        proxies = parse_proxy_config(text)
        assert proxies == []

    def test_port_zero(self):
        text = "alice@host.com:0"
        proxies = parse_proxy_config(text)
        assert proxies == []

    def test_mixed_valid_invalid(self):
        text = """
        valid@host.com:2222
        invalid-line
        also-invalid
        valid2@host.com:3333
        """
        proxies = parse_proxy_config(text)
        assert len(proxies) == 2

    def test_preserves_order(self):
        text = """
        first@host.com:1111
        second@host.com:2222
        third@host.com:3333
        """
        proxies = parse_proxy_config(text)
        assert [p.host for p in proxies] == ["host.com"] * 3
        assert [p.port for p in proxies] == [1111, 2222, 3333]