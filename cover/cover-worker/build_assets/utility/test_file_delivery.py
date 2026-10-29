import io
import logging
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from utility.file_delivery import (
    SyncJob,
    complete_sync_job,
    enqueue_delivery_job,
    reset_file_delivery_for_tests,
    resolve_delivery_transport,
    start_file_delivery,
)
from utility.quic_http_transport import QuicHttpTransport, quic_x_filename
from utility.socat_rsyc_util import RsyncTransport
from utility.file_sync_config import get_file_sync_transport


def _job(**kwargs):
    defaults = dict(
        source_file="/tmp/a.mp3",
        target_dir="/out",
        task_id="t1",
        short_uri_prefix="/final/2026-09-13",
        output_file="song_cover.mp3",
        proxies=[],
        transport="quic",
        biz_type="cover",
    )
    defaults.update(kwargs)
    return SyncJob(**defaults)


class RecordingTransport:
    def __init__(self, ok=True, err=""):
        self.ok = ok
        self.err = err
        self.jobs = []
        self.slots = []
        self.event = threading.Event()

    def deliver(self, logger, job, slot=0):
        self.jobs.append(job)
        self.slots.append(slot)
        self.event.set()
        return self.ok, self.err


class TestResolveTransport(unittest.TestCase):
    def test_default_quic(self):
        old = os.environ.pop("FILE_SYNC_TRANSPORT", None)
        try:
            self.assertEqual(get_file_sync_transport(), "quic")
            self.assertEqual(resolve_delivery_transport("user@example.com"), "quic")
        finally:
            if old is not None:
                os.environ["FILE_SYNC_TRANSPORT"] = old

    def test_explicit_rsync(self):
        with patch.dict(os.environ, {"FILE_SYNC_TRANSPORT": "rsync"}):
            self.assertEqual(resolve_delivery_transport("user@example.com"), "rsync")

    def test_invalid_env_falls_back_to_quic(self):
        with patch.dict(os.environ, {"FILE_SYNC_TRANSPORT": "ftp"}):
            self.assertEqual(get_file_sync_transport(), "quic")

    def test_email_ignored(self):
        with patch.dict(os.environ, {"FILE_SYNC_TRANSPORT": "quic"}):
            self.assertEqual(resolve_delivery_transport("video_script_tts"), "quic")


class TestFileDeliveryQueue(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_file_delivery")
        self._complete_patcher = patch("utility.file_delivery.complete_sync_job")
        self._complete_patcher.start()
        reset_file_delivery_for_tests(self.logger)

    def tearDown(self):
        reset_file_delivery_for_tests(self.logger)
        self._complete_patcher.stop()

    def test_enqueue_before_start_fails(self):
        ok = enqueue_delivery_job(self.logger, _job())
        self.assertFalse(ok)

    def test_worker_dispatches_by_transport(self):
        rsync = RecordingTransport()
        quic = RecordingTransport()
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="q1", transport="quic")))
        self.assertTrue(quic.event.wait(timeout=2))
        self.assertEqual(len(quic.jobs), 1)
        self.assertEqual(len(rsync.jobs), 0)

    def test_rsync_job_uses_rsync_backend(self):
        rsync = RecordingTransport()
        quic = RecordingTransport()
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        job = _job(task_id="r1", transport="rsync")
        self.assertTrue(enqueue_delivery_job(self.logger, job))
        self.assertTrue(rsync.event.wait(timeout=2))
        self.assertEqual(len(quic.jobs), 0)
        self.assertEqual(rsync.jobs[0].task_id, "r1")

    def test_enqueue_timeout_when_queue_full(self):
        import utility.file_delivery as fd

        block = threading.Event()

        class SlowTransport:
            def deliver(self, logger, job, slot=0):
                block.wait(timeout=5)
                return True, ""

        orig_max = fd.SYNC_QUEUE_MAXSIZE
        orig_timeout = fd.SYNC_ENQUEUE_TIMEOUT_SEC
        fd.SYNC_QUEUE_MAXSIZE = 1
        fd.SYNC_ENQUEUE_TIMEOUT_SEC = 0.2
        try:
            start_file_delivery(self.logger, {"quic": SlowTransport(), "rsync": SlowTransport()})
            self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="a")))
            self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="b")))
            self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="c")))
            self.assertFalse(enqueue_delivery_job(self.logger, _job(task_id="d")))
        finally:
            block.set()
            fd.SYNC_QUEUE_MAXSIZE = orig_max
            fd.SYNC_ENQUEUE_TIMEOUT_SEC = orig_timeout

    def test_quic_success_skips_rsync_fallback(self):
        rsync = RecordingTransport()
        quic = RecordingTransport(ok=True)
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="ok1", transport="quic")))
        self.assertTrue(quic.event.wait(timeout=2))
        time.sleep(0.1)
        self.assertEqual(len(quic.jobs), 1)
        self.assertEqual(len(rsync.jobs), 0)

    def test_quic_fail_rsync_fallback_success(self):
        from utility.socat_rsyc_util import RsyncProxy

        rsync = RecordingTransport(ok=True)
        quic = RecordingTransport(ok=False, err="quic down")
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        proxies = [RsyncProxy(user="u", host="h", port=22)]
        with patch("utility.file_delivery.get_socat_proxy_configs", return_value=proxies):
            self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="fb1", transport="quic")))
            self.assertTrue(quic.event.wait(timeout=2))
            self.assertTrue(rsync.event.wait(timeout=2))
        self.assertEqual(len(quic.jobs), 1)
        self.assertEqual(len(rsync.jobs), 1)
        self.assertEqual(len(rsync.jobs[0].proxies), 1)
        self.assertEqual(rsync.jobs[0].transport, "rsync")

    def test_quic_fail_rsync_fallback_fail(self):
        from utility.socat_rsyc_util import RsyncProxy

        rsync = RecordingTransport(ok=False, err="rsync down")
        quic = RecordingTransport(ok=False, err="quic down")
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        proxies = [RsyncProxy(user="u", host="h", port=22)]
        with patch("utility.file_delivery.get_socat_proxy_configs", return_value=proxies):
            self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="fb2", transport="quic")))
            self.assertTrue(quic.event.wait(timeout=2))
            self.assertTrue(rsync.event.wait(timeout=2))
            time.sleep(0.15)
        self.assertEqual(len(quic.jobs), 1)
        self.assertEqual(len(rsync.jobs), 1)

    def test_rsync_primary_fail_no_fallback(self):
        rsync = RecordingTransport(ok=False, err="rsync fail")
        quic = RecordingTransport(ok=True)
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="r1", transport="rsync")))
        self.assertTrue(rsync.event.wait(timeout=2))
        time.sleep(0.1)
        self.assertEqual(len(rsync.jobs), 1)
        self.assertEqual(len(quic.jobs), 0)

    def test_quic_fail_no_proxies(self):
        rsync = RecordingTransport(ok=True)
        quic = RecordingTransport(ok=False, err="quic down")
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        with patch("utility.file_delivery.get_socat_proxy_configs", return_value=[]):
            self.assertTrue(enqueue_delivery_job(self.logger, _job(task_id="np1", transport="quic")))
            self.assertTrue(quic.event.wait(timeout=2))
            time.sleep(0.15)
        self.assertEqual(len(quic.jobs), 1)
        self.assertEqual(len(rsync.jobs), 0)


class TestDeliverJobFallback(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_deliver_job")
        reset_file_delivery_for_tests(self.logger)

    def tearDown(self):
        reset_file_delivery_for_tests(self.logger)

    def test_dual_fail_err_combines_both(self):
        from utility.file_delivery import deliver_job
        from utility.socat_rsyc_util import RsyncProxy

        rsync = RecordingTransport(ok=False, err="rsync down")
        quic = RecordingTransport(ok=False, err="quic down")
        start_file_delivery(self.logger, {"rsync": rsync, "quic": quic})
        proxies = [RsyncProxy(user="u", host="h", port=22)]
        with patch("utility.file_delivery.get_socat_proxy_configs", return_value=proxies):
            ok, err, used, fallback = deliver_job(self.logger, _job(transport="quic"), slot=0)
        self.assertFalse(ok)
        self.assertEqual(used, "rsync")
        self.assertEqual(fallback, 1)
        self.assertIn("quic failed: quic down", err)
        self.assertIn("rsync fallback failed: rsync down", err)

    def test_quic_fail_no_rsync_registered(self):
        from utility.file_delivery import deliver_job

        quic = RecordingTransport(ok=False, err="quic down")
        start_file_delivery(self.logger, {"quic": quic})
        ok, err, used, fallback = deliver_job(self.logger, _job(transport="quic"), slot=0)
        self.assertFalse(ok)
        self.assertEqual(fallback, 1)
        self.assertIn("rsync not registered", err)


class TestCompleteSyncJob(unittest.TestCase):
    def test_success_writes_outdt_complete(self):
        from utility.module_enum import TaskStatus

        logger = logging.getLogger("test_complete")
        job = _job()
        with patch("utility.file_delivery.update_task_status") as mock_update:
            complete_sync_job(logger, job, True)
        mock_update.assert_called_once_with(
            logger, "t1", "", "/outdt/final/2026-09-13/song_cover.mp3", TaskStatus.COMPLETE)

    def test_vocal_success_uri(self):
        from utility.module_enum import TaskStatus

        logger = logging.getLogger("test_complete")
        job = _job(
            short_uri_prefix="/sepvocal/2026-09-13",
            output_file="v.mp3",
            biz_type="extract_vocal",
        )
        with patch("utility.file_delivery.update_task_status") as mock_update:
            complete_sync_job(logger, job, True)
        mock_update.assert_called_once_with(
            logger, "t1", "", "/outdt/sepvocal/2026-09-13/v.mp3", TaskStatus.COMPLETE)

    def test_failure_writes_failed_not_covered(self):
        from utility.module_enum import TaskStatus

        logger = logging.getLogger("test_complete")
        job = _job()
        with patch("utility.file_delivery.update_task_status") as mock_update:
            complete_sync_job(logger, job, False, err_msg="quic timeout")
        mock_update.assert_called_once_with(
            logger, "t1", "", "quic timeout", TaskStatus.FAILED)
        self.assertNotIn("/covered", mock_update.call_args[0][3])


class TestRsyncTransport(unittest.TestCase):
    def test_deliver_calls_sync_files(self):
        logger = logging.getLogger("test_rsync")
        job = _job(transport="rsync")
        with patch(
            "utility.socat_rsyc_util.sync_files_to_socat_proxies", return_value=True
        ) as mock_sync:
            ok, err = RsyncTransport().deliver(logger, job, slot=1)
        self.assertTrue(ok)
        self.assertEqual(err, "")
        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args.kwargs.get("slot"), 1)


class FakeHTTPResponse:
    def __init__(self, code=200, body=b'{"status":"ok"}'):
        self.status = code
        self._body = body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestQuicHttpTransport(unittest.TestCase):
    def _transport(self, urlopen, **kwargs):
        kwargs.setdefault("biz_type", "cover")
        kwargs.setdefault("sender", "test-worker")
        return QuicHttpTransport(
            host="127.0.0.1",
            port=8470,
            path="/upload",
            timeout_sec=5,
            urlopen=urlopen,
            sleep=lambda _s: None,
            **kwargs,
        )

    def _with_file(self, content=b"audio"):
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.write(fd, content)
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_x_filename_cover_prefix(self):
        self.assertEqual(
            quic_x_filename("/final/2026-09-13", "song_cover.mp3", "cover"),
            "2026-09-13/song_cover.mp3",
        )

    def test_x_filename_extract_vocal(self):
        self.assertEqual(
            quic_x_filename("/sepvocal/2026-09-13", "v.mp3", "extract_vocal"),
            "2026-09-13/v.mp3",
        )

    def test_x_filename_extract_instrument(self):
        self.assertEqual(
            quic_x_filename("/sepinstrum/2026-09-13", "i.mp3", "extract_instrument"),
            "2026-09-13/i.mp3",
        )

    def test_empty_body_not_sent(self):
        urlopen = MagicMock()
        path = self._with_file(b"")
        job = _job(source_file=path)
        ok, err = self._transport(urlopen).deliver(logging.getLogger("t"), job)
        self.assertFalse(ok)
        self.assertIn("Empty", err)
        urlopen.assert_not_called()

    def test_200_ok_uses_job_biz_type(self):
        urlopen = MagicMock(return_value=FakeHTTPResponse())
        path = self._with_file()
        job = _job(
            source_file=path,
            short_uri_prefix="/sepvocal/2026-09-13",
            output_file="v.mp3",
            biz_type="extract_vocal",
        )
        ok, err = self._transport(urlopen, biz_type="cover").deliver(
            logging.getLogger("t"), job
        )
        self.assertTrue(ok)
        self.assertEqual(err, "")
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_header("X-filename"), "2026-09-13/v.mp3")
        self.assertEqual(req.get_header("X-sender"), "test-worker")
        self.assertEqual(req.get_header("X-biz-type"), "extract_vocal")

    def test_200_ok_cover(self):
        urlopen = MagicMock(return_value=FakeHTTPResponse())
        path = self._with_file()
        ok, err = self._transport(urlopen).deliver(
            logging.getLogger("t"), _job(source_file=path)
        )
        self.assertTrue(ok)
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_header("X-filename"), "2026-09-13/song_cover.mp3")
        self.assertEqual(req.get_header("X-biz-type"), "cover")

    def test_504_no_retry(self):
        calls = {"n": 0}

        def urlopen(req, timeout=None):
            calls["n"] += 1
            raise HTTPError(
                "http://x/upload", 504, "timeout", hdrs=None, fp=io.BytesIO(b'{"status":"timeout"}')
            )

        path = self._with_file()
        ok, err = self._transport(urlopen).deliver(logging.getLogger("t"), _job(source_file=path))
        self.assertFalse(ok)
        self.assertEqual(err, "timeout")
        self.assertEqual(calls["n"], 1)

    def test_503_retries_once_then_ok(self):
        calls = {"n": 0}

        def urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(
                    "http://x/upload", 503, "down", hdrs=None, fp=io.BytesIO(b'{"status":"error"}')
                )
            return FakeHTTPResponse()

        path = self._with_file()
        ok, err = self._transport(urlopen).deliver(logging.getLogger("t"), _job(source_file=path))
        self.assertTrue(ok)
        self.assertEqual(calls["n"], 2)

    def test_connection_error_retries(self):
        calls = {"n": 0}

        def urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise URLError("connection refused")
            return FakeHTTPResponse()

        path = self._with_file()
        ok, err = self._transport(urlopen).deliver(logging.getLogger("t"), _job(source_file=path))
        self.assertTrue(ok)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
