import io
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import HTTPError

from utility.samearea_http_transport import SameAreaHttpTransport, samearea_x_filename


class FakeHTTPResponse:
    def __init__(self, status=200, body=b'{"status":"ok"}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _job(**kwargs):
    defaults = dict(
        source_file="/tmp/a.json",
        target_dir="/home/aigc/data/aioutput/video/transcribe/2026-09-13/user1",
        task_id="t1",
        short_uri_prefix="/video/transcribe/2026-09-13/user1",
        output_file="a_transcribe.json",
        transport="http",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSameAreaHttpTransport(unittest.TestCase):
    def _transport(self, urlopen, **kwargs):
        kwargs.setdefault("host", "192.168.3.9")
        kwargs.setdefault("port", 8960)
        kwargs.setdefault("path", "/upload")
        kwargs.setdefault("timeout_sec", 5)
        kwargs.setdefault("token", "")
        return SameAreaHttpTransport(
            urlopen=urlopen,
            sleep=lambda _s: None,
            **kwargs,
        )

    def _with_file(self, content=b"subs"):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, content)
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_x_filename_from_target_dir(self):
        self.assertEqual(
            samearea_x_filename(
                "/home/aigc/data/aioutput/video/transcribe/2026-09-13/user1",
                "a.json",
            ),
            "transcribe/2026-09-13/user1/a.json",
        )
        self.assertEqual(
            samearea_x_filename(
                "/other",
                "a.json",
                "/video/transcribe/2026-09-13/user1",
            ),
            "transcribe/2026-09-13/user1/a.json",
        )

    def test_200_ok_headers_without_authorization(self):
        urlopen = MagicMock(return_value=FakeHTTPResponse())
        path = self._with_file()
        ok, err = self._transport(urlopen).deliver(
            logging.getLogger("t"), _job(source_file=path)
        )
        self.assertTrue(ok)
        self.assertEqual(err, "")
        req = urlopen.call_args[0][0]
        self.assertEqual(
            req.get_header("X-filename"),
            "transcribe/2026-09-13/user1/a_transcribe.json",
        )
        self.assertEqual(req.get_header("X-reqid"), "t1")
        self.assertEqual(req.get_header("X-offset"), "0")
        self.assertEqual(req.get_header("X-total-size"), "4")
        self.assertIsNone(req.get_header("Authorization"))

    def test_sends_bearer_when_token_set(self):
        urlopen = MagicMock(return_value=FakeHTTPResponse())
        path = self._with_file()
        ok, err = self._transport(urlopen, token="secret").deliver(
            logging.getLogger("t"), _job(source_file=path)
        )
        self.assertTrue(ok)
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer secret")

    def test_401_no_retry(self):
        calls = {"n": 0}

        def urlopen(req, timeout=None):
            calls["n"] += 1
            raise HTTPError(
                "http://x/upload",
                401,
                "unauthorized",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"unauthorized"}'),
            )

        path = self._with_file()
        ok, err = self._transport(urlopen).deliver(
            logging.getLogger("t"), _job(source_file=path)
        )
        self.assertFalse(ok)
        self.assertEqual(err, "unauthorized")
        self.assertEqual(calls["n"], 1)

    def test_missing_host_not_sent(self):
        urlopen = MagicMock()
        path = self._with_file()
        ok, err = self._transport(urlopen, host="", port=0).deliver(
            logging.getLogger("t"), _job(source_file=path)
        )
        self.assertFalse(ok)
        self.assertIn("missing host or port", err)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
