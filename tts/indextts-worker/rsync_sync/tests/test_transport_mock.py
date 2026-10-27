"""Tests for MockSSHTransport."""

from rsync_sync.models import ProxyTarget, TransferResult


class TestMockTransport:
    """Test the MockSSHTransport preset and call semantics."""

    def test_preset_initialize(self, transport, proxy):
        transport.preset_initialize(proxy, slot=0, return_value=True)
        assert transport.initialize_connection(proxy, slot=0) is True

    def test_preset_initialize_false(self, transport, proxy):
        transport.preset_initialize(proxy, slot=0, return_value=False)
        assert transport.initialize_connection(proxy, slot=0) is False

    def test_initialize_missing_preset_raises(self, transport, proxy):
        try:
            transport.initialize_connection(proxy, slot=0)
            assert False, "Should have raised AssertionError"
        except AssertionError:
            pass  # expected

    def test_preset_transfer(self, transport, proxy, ok_transfer):
        transport.preset_transfer("/tmp/x.wav", proxy, "/out", slot=0, return_value=ok_transfer)
        transport.preset_ensure_remote_dir(proxy, "/out", slot=0, return_value=True)
        result = transport.transfer_file("/tmp/x.wav", proxy, "/out", slot=0)
        assert result.ok is True
        assert result.elapsed_sec == 0.5

    def test_preset_transfer_failure(self, transport, proxy, fail_transfer):
        transport.preset_transfer("/tmp/x.wav", proxy, "/out", slot=0, return_value=fail_transfer)
        transport.preset_ensure_remote_dir(proxy, "/out", slot=0, return_value=True)
        result = transport.transfer_file("/tmp/x.wav", proxy, "/out", slot=0)
        assert result.ok is False
        assert "timeout" in result.stderr

    def test_cleanup_connection(self, transport, proxy):
        transport.cleanup_connection(proxy, slot=0)
        assert transport.cleaned_count == 1

    def test_cleanup_all(self, transport, proxy):
        transport.preset_initialize(proxy, slot=0, return_value=True)
        transport.initialize_connection(proxy, slot=0)
        transport.cleanup_all()
        assert transport.cleaned_count == 1
        assert transport.initialized_count == 0

    def test_ensure_remote_dir_preset(self, transport, proxy):
        transport.preset_ensure_remote_dir(proxy, "/some/dir", slot=0, return_value=True)
        assert transport.ensure_remote_dir(proxy, "/some/dir", slot=0) is True

    def test_ensure_remote_dir_missing_preset(self, transport, proxy):
        try:
            transport.ensure_remote_dir(proxy, "/missing", slot=0)
            assert False, "Should have raised AssertionError"
        except AssertionError:
            pass  # expected

    def test_call_log(self, transport, proxy):
        transport.preset_initialize(proxy, slot=0, return_value=True)
        transport.initialize_connection(proxy, slot=0)
        assert len(transport.call_log) == 1
        assert transport.call_log[0][0] == "initialize_connection"

    def test_get_control_path(self, transport, proxy):
        path = transport.get_control_path(proxy, slot=1)
        assert "/fake/" in path
        assert "testuser_at_testhost.example.com_2222_1" in path

    def test_verify_all_presets_consumed_passes(self, transport, proxy):
        transport.preset_initialize(proxy, slot=0, return_value=True)
        transport.initialize_connection(proxy, slot=0)
        transport.verify_all_presets_consumed()  # should not raise

    def test_verify_all_presets_consumed_fails(self, transport, proxy):
        transport.preset_initialize(proxy, slot=0, return_value=True)
        # Never called -> unconsumed
        try:
            transport.verify_all_presets_consumed()
            assert False, "Should have raised"
        except AssertionError:
            pass  # expected