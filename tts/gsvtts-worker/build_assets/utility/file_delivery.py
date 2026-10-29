"""Transport-agnostic bounded queue for delivering TTS output files.

Workers dispatch by SyncJob.transport to rsync or QUIC backends.
On quic failure, one rsync fallback attempt is made (socat warmed at startup).
Completion updates task records via UserServer /inapi/update_task; GPU inference does not wait.
"""

import atexit
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Protocol, Tuple

from utility.log import clear_reqid, set_reqid
from utility.module_enum import TaskStatus
from utility.socat_rsyc_util import RsyncProxy, get_socat_proxy_configs
from utility.userserver_client import update_task_status
from utility.file_sync_config import get_file_sync_transport

VIDEO_SCRIPT_TTS_EMAIL = "video_script_tts"


def resolve_delivery_transport(email: str) -> str:
    """VIDEO_SCRIPT_TTS always http; cross-region follows FILE_SYNC_TRANSPORT."""
    if email == VIDEO_SCRIPT_TTS_EMAIL:
        return "http"
    return get_file_sync_transport()

SYNC_SLOT_COUNT = 2
SYNC_QUEUE_MAXSIZE = 6
SYNC_ENQUEUE_TIMEOUT_SEC = 60
_SYNC_SENTINEL = object()

_sync_queue: Optional[queue.Queue] = None
_sync_threads: List[threading.Thread] = []
_sync_workers_started = False
_sync_workers_lock = threading.Lock()
_transports: Dict[str, "FileTransport"] = {}


@dataclass
class SyncJob:
    source_file: str
    target_dir: str
    task_id: str
    short_uri_prefix: str
    output_file: str
    proxies: List[RsyncProxy] = field(default_factory=list)
    transport: str = "quic"
    domain: str = ""


class FileTransport(Protocol):
    def deliver(self, logger, job: SyncJob, slot: int = 0) -> Tuple[bool, str]:
        ...


def _format_file_size(file_size: int) -> str:
    if file_size < 1024:
        return f"{file_size} B"
    if file_size < 1024 * 1024:
        return f"{file_size / 1024:.2f} KB"
    return f"{file_size / (1024 * 1024):.2f} MB"


def deliver_job(
    logger, job: SyncJob, slot: int = 0
) -> Tuple[bool, str, str, int]:
    """Deliver via job.transport; on quic failure, try rsync once.

    Returns (ok, err_msg, used_transport, fallback) where fallback is 1 if
    rsync backup was attempted (or would have been but could not run).
    """
    transport_name = (job.transport or "quic").strip().lower()
    backend = _transports.get(transport_name)
    if backend is None:
        return (
            False,
            f"unknown or unregistered transport={transport_name} task_id={job.task_id}",
            transport_name,
            0,
        )

    ok, err_msg = backend.deliver(logger, job, slot=slot)
    if ok or transport_name != "quic":
        return ok, err_msg or "", transport_name, 0

    quic_err = err_msg or f"deliver failed task_id={job.task_id}"
    rsync_backend = _transports.get("rsync")
    if rsync_backend is None:
        return (
            False,
            f"quic failed: {quic_err}; rsync fallback failed: rsync not registered",
            transport_name,
            1,
        )

    try:
        proxies = get_socat_proxy_configs()
    except Exception as e:
        return (
            False,
            f"quic failed: {quic_err}; rsync fallback failed: proxies error: {e}",
            transport_name,
            1,
        )
    if not proxies:
        return (
            False,
            f"quic failed: {quic_err}; rsync fallback failed: no proxies",
            transport_name,
            1,
        )

    logger.warning(
        f"async_sync_fallback_begin task_id={job.task_id} from=quic to=rsync "
        f"quic_err={quic_err}"
    )
    fallback_start = time.time()
    job_for_rsync = replace(job, proxies=list(proxies), transport="rsync")
    ok2, err2 = rsync_backend.deliver(logger, job_for_rsync, slot=slot)
    fallback_elapsed = time.time() - fallback_start
    logger.info(
        f"async_sync_fallback_end task_id={job.task_id} ok={ok2} "
        f"elapsed_s={fallback_elapsed:.2f}"
    )
    if ok2:
        return True, "", "rsync", 1
    rsync_err = err2 or f"deliver failed task_id={job.task_id}"
    return (
        False,
        f"quic failed: {quic_err}; rsync fallback failed: {rsync_err}",
        "rsync",
        1,
    )


def complete_sync_job(
    logger,
    job: SyncJob,
    ok: bool,
    err_msg: str = "",
    used_transport: Optional[str] = None,
    fallback: int = 0,
):
    used = used_transport if used_transport is not None else job.transport
    if ok:
        uri = f"/outdt{job.short_uri_prefix}/{job.output_file}"
        update_task_status(logger, job.task_id, job.domain, uri, TaskStatus.COMPLETE)
        logger.info(
            f"async_sync_complete task_id={job.task_id} uri={uri} "
            f"transport={job.transport} used_transport={used} fallback={fallback}"
        )
    else:
        msg = err_msg or "sync_files failed"
        update_task_status(logger, job.task_id, job.domain, msg, TaskStatus.FAILED)
        logger.error(
            f"async_sync_failed task_id={job.task_id} msg={msg} "
            f"transport={job.transport} used_transport={used} fallback={fallback}"
        )


def _worker_loop(logger, slot: int) -> None:
    logger.info(f"async_sync_worker_start slot={slot}")
    while True:
        job = _sync_queue.get()
        try:
            if job is _SYNC_SENTINEL:
                logger.info(f"async_sync_worker_stop slot={slot}")
                return

            set_reqid(job.task_id)
            queue_depth = _sync_queue.qsize()
            file_size = os.path.getsize(job.source_file) if os.path.exists(job.source_file) else 0
            size_str = _format_file_size(file_size)
            start_time = time.time()
            transport_name = (job.transport or "quic").strip().lower()
            logger.info(
                f"async_sync_begin task_id={job.task_id} slot={slot} "
                f"transport={transport_name} size={size_str} queue_depth={queue_depth} "
                f"file={job.source_file}"
            )

            ok, err_msg, used_transport, fallback = deliver_job(logger, job, slot=slot)

            elapsed = time.time() - start_time
            avg_speed_mbps = 0.0
            if ok and elapsed > 0 and file_size > 0:
                avg_speed_mbps = (file_size / (1024 * 1024)) / elapsed

            logger.info(
                f"async_sync_end task_id={job.task_id} slot={slot} "
                f"transport={transport_name} used_transport={used_transport} "
                f"fallback={fallback} ok={ok} elapsed_s={elapsed:.2f} "
                f"avg_MBps={avg_speed_mbps:.2f} queue_depth={_sync_queue.qsize()} "
                f"size={size_str}"
            )
            complete_sync_job(
                logger,
                job,
                ok,
                err_msg="" if ok else (err_msg or f"deliver failed task_id={job.task_id}"),
                used_transport=used_transport,
                fallback=fallback,
            )
        except Exception as e:
            logger.error(f"async_sync_worker_exception slot={slot} error={e}")
            if job is not _SYNC_SENTINEL:
                try:
                    complete_sync_job(logger, job, False, err_msg=f"deliver exception: {e}")
                except Exception as update_err:
                    logger.error(f"async_sync_fail_update_error error={update_err}")
        finally:
            if job is not _SYNC_SENTINEL:
                clear_reqid()
            _sync_queue.task_done()


def start_file_delivery(logger, transports: Dict[str, FileTransport]) -> None:
    """Start bounded-queue delivery workers. Rsync backend must be registered."""
    global _sync_queue, _sync_threads, _sync_workers_started, _transports
    with _sync_workers_lock:
        _transports = dict(transports)
        if _sync_workers_started:
            logger.debug("async_sync_workers already started")
            return
        _sync_queue = queue.Queue(maxsize=SYNC_QUEUE_MAXSIZE)
        _sync_threads = []
        for slot in range(SYNC_SLOT_COUNT):
            t = threading.Thread(
                target=_worker_loop,
                args=(logger, slot),
                name=f"file-delivery-slot-{slot}",
                daemon=True,
            )
            t.start()
            _sync_threads.append(t)
        _sync_workers_started = True
        logger.info(
            f"async_sync_workers_started workers={SYNC_SLOT_COUNT} "
            f"queue_maxsize={SYNC_QUEUE_MAXSIZE} transports={list(_transports.keys())}"
        )


def stop_file_delivery(logger=None) -> None:
    """Signal delivery workers to stop and join (best-effort)."""
    global _sync_workers_started
    log = logger or logging.getLogger("root")
    with _sync_workers_lock:
        if not _sync_workers_started or _sync_queue is None:
            return
        for _ in range(len(_sync_threads)):
            try:
                _sync_queue.put(_SYNC_SENTINEL, timeout=5)
            except queue.Full:
                log.warning("async_sync_stop queue full while sending sentinel")
        for t in _sync_threads:
            t.join(timeout=30)
        _sync_workers_started = False
        log.info("async_sync_workers_stopped")


def reset_file_delivery_for_tests(logger=None) -> None:
    """Stop workers and clear transports. Tests only."""
    global _transports
    stop_file_delivery(logger)
    _transports = {}


def enqueue_delivery_job(logger, job: SyncJob) -> bool:
    """Enqueue a delivery job. Blocks up to SYNC_ENQUEUE_TIMEOUT_SEC if full."""
    if not _sync_workers_started or _sync_queue is None:
        logger.error(f"enqueue_sync_job failed: workers not started task_id={job.task_id}")
        return False
    try:
        _sync_queue.put(job, timeout=SYNC_ENQUEUE_TIMEOUT_SEC)
        logger.info(
            f"enqueue_sync_ok task_id={job.task_id} transport={job.transport} "
            f"queue_depth={_sync_queue.qsize()} file={job.source_file}"
        )
        return True
    except queue.Full:
        logger.error(
            f"enqueue_sync_timeout task_id={job.task_id} "
            f"timeout_s={SYNC_ENQUEUE_TIMEOUT_SEC} queue_maxsize={SYNC_QUEUE_MAXSIZE}"
        )
        return False


def _atexit_stop_workers():
    log = logging.getLogger("root")
    try:
        stop_file_delivery(log)
    except Exception:
        pass


atexit.register(_atexit_stop_workers)
