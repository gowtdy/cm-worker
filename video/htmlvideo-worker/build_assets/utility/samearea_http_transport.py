"""HTTP client for the LAN same-area receiver POST /upload API."""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple

from utility.file_sync_config import (
    get_samearea_host,
    get_samearea_port,
    get_samearea_timeout_sec,
    get_samearea_token,
    get_samearea_upload_path,
)

SAMEAREA_AIOUTPUT_VIDEO_PREFIX = "/home/aigc/data/aioutput/video/"


def samearea_x_filename(
    target_dir: str, output_file: str, short_uri_prefix: str = ""
) -> str:
    """Relative dest path without the video/ layer."""
    target = (target_dir or "").strip().replace("\\", "/")
    name = (output_file or "").lstrip("/")
    prefix = SAMEAREA_AIOUTPUT_VIDEO_PREFIX.rstrip("/")
    if target == prefix or target.startswith(prefix + "/"):
        rel = target[len(prefix) :].lstrip("/")
    else:
        rel = (short_uri_prefix or "").strip().lstrip("/")
        if rel.startswith("video/"):
            rel = rel[len("video/") :]
    if rel and name:
        return f"{rel}/{name}"
    return name or rel


class SameAreaHttpTransport:
    """FileTransport: POST raw file to the LAN same-area /upload endpoint."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        path: Optional[str] = None,
        timeout_sec: Optional[int] = None,
        token: Optional[str] = None,
        urlopen: Callable = urllib.request.urlopen,
        sleep: Callable = time.sleep,
        retry_delay_sec: float = 0.5,
    ):
        self.host = host if host is not None else get_samearea_host()
        self.port = port if port is not None else get_samearea_port()
        self.path = path if path is not None else get_samearea_upload_path()
        self.timeout_sec = (
            timeout_sec if timeout_sec is not None else get_samearea_timeout_sec()
        )
        self.token = token if token is not None else get_samearea_token()
        self._urlopen = urlopen
        self._sleep = sleep
        self.retry_delay_sec = retry_delay_sec

    def deliver(self, logger, job, slot: int = 0) -> Tuple[bool, str]:
        del slot
        if not self.host or not self.port:
            return False, "HTTP samearea connection not available: missing host or port"

        source = job.source_file
        if not os.path.exists(source) or os.path.getsize(source) == 0:
            return False, "Empty body"

        with open(source, "rb") as f:
            body = f.read()
        if not body:
            return False, "Empty body"

        filename = samearea_x_filename(
            job.target_dir, job.output_file, getattr(job, "short_uri_prefix", "")
        )
        last_err = "error"
        for attempt in range(2):
            ok, err, retryable = self._post_once(
                logger, body, filename, job.task_id
            )
            if ok:
                return True, ""
            last_err = err
            if not retryable or attempt == 1:
                return False, last_err
            logger.warning(
                f"samearea_upload_retry task_id={job.task_id} attempt={attempt + 1} err={err}"
            )
            self._sleep(self.retry_delay_sec)
        return False, last_err

    def _post_once(self, logger, body: bytes, filename: str, task_id: str):
        url = f"http://{self.host}:{int(self.port)}{self.path}"
        total = len(body)
        file_id = hashlib.sha256(f"{filename}:{total}".encode("utf-8")).hexdigest()[:32]
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(total))
        req.add_header("x-reqid", str(task_id or ""))
        req.add_header("x-file-id", file_id)
        req.add_header("x-total-size", str(total))
        req.add_header("x-offset", "0")
        if filename:
            req.add_header("x-filename", filename)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with self._urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read()
                code = getattr(resp, "status", None) or resp.getcode()
                payload = _parse_json(raw)
                if 200 <= int(code) < 300 and not payload.get("error"):
                    logger.info(
                        f"samearea_upload_ok task_id={task_id} filename={filename} "
                        f"file_id={file_id} http={code}"
                    )
                    return True, "", False
                detail = (
                    payload.get("error")
                    or payload.get("detail")
                    or payload.get("status")
                    or f"http_{code}"
                )
                return False, str(detail), False
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read()
            except Exception:
                pass
            payload = _parse_json(raw)
            detail = (
                payload.get("error")
                or payload.get("detail")
                or payload.get("status")
                or f"http_{e.code}"
            )
            if e.code == 503:
                return False, str(detail), True
            return False, str(detail), False
        except urllib.error.URLError as e:
            return False, f"HTTP samearea connection not available: {e}", True
        except TimeoutError as e:
            return False, f"timeout: {e}", False
        except OSError as e:
            return False, f"HTTP samearea connection not available: {e}", True


def _parse_json(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
