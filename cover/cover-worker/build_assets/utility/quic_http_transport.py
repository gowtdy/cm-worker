"""HTTP client for the QUIC sidecar POST /upload API."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple

from utility.file_sync_config import (
    get_quic_client_host,
    get_quic_client_port,
    get_quic_sender,
    get_quic_upload_path,
    get_quic_upload_timeout_sec,
)

_BIZ_URI_PREFIXES = {
    "tts": "/wavcl",
    "sound": "/sounds/tk",
    "extract_audio": "/sepaudio",
    "cover": "/final",
    "extract_vocal": "/sepvocal",
    "extract_instrument": "/sepinstrum",
}


def _normalize_label(value: str, default: str, max_len: int) -> str:
    cleaned = (value or "").strip().lower().replace("\n", "").replace("\r", "")
    if not cleaned:
        return default
    return cleaned[:max_len]


def quic_x_filename(
    short_uri_prefix: str, output_file: str, biz_type: str = "cover"
) -> str:
    """Relative storage path for x-filename (strip biz-type URI root)."""
    biz = _normalize_label(biz_type, "cover", 32)
    strip_prefix = _BIZ_URI_PREFIXES.get(biz, "")
    prefix = short_uri_prefix or ""
    if strip_prefix and prefix.startswith(strip_prefix):
        rel = prefix[len(strip_prefix) :]
    else:
        rel = prefix
    rel = rel.lstrip("/")
    name = (output_file or "").lstrip("/")
    if rel and name:
        return f"{rel}/{name}"
    return name or rel


class QuicHttpTransport:
    """FileTransport: POST raw audio to the QUIC sidecar /upload endpoint."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        path: Optional[str] = None,
        timeout_sec: Optional[int] = None,
        urlopen: Callable = urllib.request.urlopen,
        sleep: Callable = time.sleep,
        retry_delay_sec: float = 0.5,
        biz_type: str = "cover",
        sender: Optional[str] = None,
    ):
        self.host = host if host is not None else get_quic_client_host()
        self.port = port if port is not None else get_quic_client_port()
        self.path = path if path is not None else get_quic_upload_path()
        self.timeout_sec = (
            timeout_sec if timeout_sec is not None else get_quic_upload_timeout_sec()
        )
        self._urlopen = urlopen
        self._sleep = sleep
        self.retry_delay_sec = retry_delay_sec
        self.biz_type = _normalize_label(biz_type, "cover", 32)
        if sender is None:
            self.sender = get_quic_sender()
        else:
            self.sender = _normalize_label(sender, "unknown", 64)

    def deliver(self, logger, job, slot: int = 0) -> Tuple[bool, str]:
        del slot  # QUIC sidecar is a single hop; slot is unused.
        if not self.host or not self.port:
            return False, "QUIC connection not available: missing host or port"

        source = job.source_file
        if not os.path.exists(source) or os.path.getsize(source) == 0:
            return False, "Empty body"

        with open(source, "rb") as f:
            body = f.read()
        if not body:
            return False, "Empty body"

        job_biz = getattr(job, "biz_type", None) or self.biz_type
        biz = _normalize_label(job_biz, self.biz_type, 32)
        filename = quic_x_filename(job.short_uri_prefix, job.output_file, biz)
        last_err = "error"
        for attempt in range(2):
            ok, err, retryable = self._post_once(
                logger, body, filename, job.task_id, biz
            )
            if ok:
                return True, ""
            last_err = err
            if not retryable or attempt == 1:
                return False, last_err
            logger.warning(
                f"quic_upload_retry task_id={job.task_id} attempt={attempt + 1} err={err}"
            )
            self._sleep(self.retry_delay_sec)
        return False, last_err

    def _post_once(self, logger, body: bytes, filename: str, task_id: str, biz_type: str):
        url = f"http://{self.host}:{int(self.port)}{self.path}"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(len(body)))
        req.add_header("x-sender", self.sender)
        req.add_header("x-biz-type", biz_type)
        if filename:
            req.add_header("x-filename", filename)

        try:
            with self._urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read()
                code = getattr(resp, "status", None) or resp.getcode()
                payload = _parse_json(raw)
                if code == 200 and payload.get("status") == "ok":
                    logger.info(
                        f"quic_upload_ok task_id={task_id} filename={filename} "
                        f"sender={self.sender} biz_type={biz_type}"
                    )
                    return True, "", False
                detail = payload.get("detail") or payload.get("status") or f"http_{code}"
                return False, str(detail), False
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read()
            except Exception:
                pass
            payload = _parse_json(raw)
            status = payload.get("status")
            detail = payload.get("detail") or status or f"http_{e.code}"
            if e.code == 504 or status == "timeout":
                return False, "timeout", False
            if e.code == 503:
                return False, str(detail), True
            return False, str(detail), False
        except urllib.error.URLError as e:
            return False, f"QUIC connection not available: {e}", True
        except TimeoutError as e:
            return False, f"timeout: {e}", False
        except OSError as e:
            return False, f"QUIC connection not available: {e}", True


def _parse_json(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
