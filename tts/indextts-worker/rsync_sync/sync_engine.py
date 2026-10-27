"""Sync strategies for distributing files across multiple proxies.

SequentialSyncStrategy — iterate proxies one at a time, one slot
AsyncQueueSyncStrategy — bounded queue with N worker threads, each pinned to a slot

Both use the SSHTransport interface (not NativeSSHTransport directly),
making them testable with MockSSHTransport.
"""

import os
import queue
import threading
import time
from typing import List, Optional

from .config import SyncConfig
from .interfaces import CompletionCallback, SSHTransport, SyncStrategy
from .models import ProxyTarget, SingleSyncResult, SyncJob, SyncMetrics, TransferResult
from .slot_manager import SlotManager
from .util import compute_speed_mbps, format_file_size

_SYNC_SENTINEL = object()


class SequentialSyncStrategy(SyncStrategy):
    """Sync to each proxy one by one using a single ControlMaster slot.

    Simple and predictable. Suitable for low-throughput scenarios
    or when async overhead is not justified.
    """

    def __init__(
        self,
        transport: SSHTransport,
        slot_manager: SlotManager,
        callback: Optional[CompletionCallback] = None,
        logger=None,
    ):
        self._transport = transport
        self._slot_manager = slot_manager
        self._callback = callback
        import logging
        self._logger = logger or logging.getLogger("root")

    def sync(self, job: SyncJob) -> SyncMetrics:
        """Execute a full sync: source_file → all proxies, sequentially."""
        if not job.proxies:
            self._logger.warning(f"sync_skip task_id={job.task_id} reason=no_proxies")
            return SyncMetrics()

        file_size = os.path.getsize(job.source_file) if os.path.exists(job.source_file) else 0
        results: List[SingleSyncResult] = []
        start_time = time.monotonic()
        slot = 0  # sequential always uses slot 0

        for proxy in job.proxies:
            transfer = self._transport.transfer_file(
                job.source_file, proxy, job.target_dir, slot=slot
            )
            sr = SingleSyncResult(
                proxy=proxy,
                slot=slot,
                ok=transfer.ok,
                elapsed_sec=transfer.elapsed_sec,
                file_size_bytes=file_size,
                avg_speed_mbps=compute_speed_mbps(file_size, transfer.elapsed_sec),
                error_msg=transfer.stderr if not transfer.ok else "",
            )
            results.append(sr)
            self._logger.info(
                f"sync_proxy task_id={job.task_id} proxy={proxy.server} "
                f"ok={transfer.ok} elapsed_s={transfer.elapsed_sec:.2f}"
            )

        total_elapsed = time.monotonic() - start_time
        metrics = SyncMetrics(
            results=results,
            total_elapsed_sec=total_elapsed,
            file_size_bytes=file_size,
        )

        if self._callback:
            if metrics.all_succeeded:
                try:
                    self._callback.on_sync_complete(job, metrics)
                except Exception as e:
                    self._logger.error(f"callback_on_sync_complete error={e}")
            else:
                try:
                    self._callback.on_sync_failed(
                        job, f"some proxies failed: {metrics.failed_proxies}"
                    )
                except Exception as e:
                    self._logger.error(f"callback_on_sync_failed error={e}")

        return metrics

    def is_ready(self) -> bool:
        return True

    def shutdown(self, timeout_sec: float = 30.0) -> None:
        # Sequential has nothing to drain
        pass


class AsyncQueueSyncStrategy(SyncStrategy):
    """Bounded-queue sync with N worker daemon threads.

    Each worker is pinned to one ControlMaster slot.
    Jobs are enqueued; workers dequeue and execute.
    When the queue is full, enqueue blocks up to config.enqueue_timeout_sec.

    Equivalent to the current socat_rsyc_util.py worker loop pattern.
    """

    def __init__(
        self,
        transport: SSHTransport,
        slot_manager: SlotManager,
        config: SyncConfig = None,
        callback: Optional[CompletionCallback] = None,
        logger=None,
    ):
        import logging
        self._logger = logger or logging.getLogger("root")
        self._transport = transport
        self._slot_manager = slot_manager
        self._config = config or SyncConfig()
        self._callback = callback

        self._queue: Optional[queue.Queue] = None
        self._threads: List[threading.Thread] = []
        self._started = False
        self._lock = threading.Lock()

    @property
    def slot_count(self) -> int:
        return self._slot_manager.slot_count

    def sync(self, job: SyncJob) -> bool:
        """Enqueue a sync job. This IS the sync entry point for async mode.

        Blocks up to config.enqueue_timeout_sec if the queue is full.

        Returns:
            True if enqueued, False if timeout
        """
        if not self._started or self._queue is None:
            self._logger.error(f"enqueue_fail task_id={job.task_id} reason=not_started")
            return False
        try:
            self._queue.put(job, timeout=self._config.enqueue_timeout_sec)
            self._logger.info(
                f"enqueue_ok task_id={job.task_id} queue_depth={self._queue.qsize()} "
                f"file={job.source_file}"
            )
            return True
        except queue.Full:
            self._logger.error(
                f"enqueue_timeout task_id={job.task_id} "
                f"timeout_s={self._config.enqueue_timeout_sec} "
                f"queue_maxsize={self._config.queue_maxsize}"
            )
            return False

    def is_ready(self) -> bool:
        return self._started

    def start(self) -> None:
        """Start the worker threads and create the queue."""
        with self._lock:
            if self._started:
                self._logger.debug("async workers already started")
                return
            self._queue = queue.Queue(maxsize=self._config.queue_maxsize)
            self._threads = []
            for worker_idx in range(self.slot_count):
                slot = self._slot_manager.get_slot_for_worker(worker_idx)
                t = threading.Thread(
                    target=self._worker_loop,
                    args=(slot,),
                    name=f"rsync-sync-slot-{slot}",
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
            self._started = True
            self._logger.info(
                f"async_workers_started workers={self.slot_count} "
                f"queue_maxsize={self._config.queue_maxsize}"
            )

    def shutdown(self, timeout_sec: float = 30.0) -> None:
        """Send sentinels to all workers and join them."""
        with self._lock:
            if not self._started or self._queue is None:
                return
            for _ in range(len(self._threads)):
                try:
                    self._queue.put(_SYNC_SENTINEL, timeout=5)
                except queue.Full:
                    self._logger.warning("async_stop queue full while sending sentinel")
            for t in self._threads:
                t.join(timeout=timeout_sec)
            self._started = False
            self._logger.info("async_workers_stopped")

    # ---- internal ----

    def _worker_loop(self, slot: int) -> None:
        """Worker thread main loop: dequeue job, sync, invoke callback."""
        self._logger.info(f"async_worker_start slot={slot}")
        while True:
            job: SyncJob = self._queue.get()
            try:
                if job is _SYNC_SENTINEL:
                    self._logger.info(f"async_worker_stop slot={slot}")
                    return

                queue_depth = self._queue.qsize()
                file_size = (
                    os.path.getsize(job.source_file)
                    if os.path.exists(job.source_file) else 0
                )
                size_str = format_file_size(file_size)
                start_time = time.monotonic()
                self._logger.info(
                    f"async_sync_begin task_id={job.task_id} slot={slot} "
                    f"size={size_str} queue_depth={queue_depth} file={job.source_file}"
                )

                metrics = self._sync_to_all_proxies(job, slot)
                elapsed = time.monotonic() - start_time
                avg_mbps = compute_speed_mbps(file_size, elapsed)

                self._logger.info(
                    f"async_sync_end task_id={job.task_id} slot={slot} "
                    f"ok={metrics.all_succeeded} elapsed_s={elapsed:.2f} "
                    f"avg_MBps={avg_mbps:.2f} queue_depth={self._queue.qsize()} "
                    f"size={size_str}"
                )

                if self._callback:
                    if metrics.all_succeeded:
                        try:
                            self._callback.on_sync_complete(job, metrics)
                        except Exception as e:
                            self._logger.error(f"callback_error slot={slot} error={e}")
                    else:
                        try:
                            self._callback.on_sync_failed(
                                job,
                                f"some proxies failed: {metrics.failed_proxies}",
                            )
                        except Exception as e:
                            self._logger.error(f"callback_error slot={slot} error={e}")
            except Exception as e:
                self._logger.error(f"async_worker_exception slot={slot} error={e}")
                if self._callback and job is not _SYNC_SENTINEL:
                    try:
                        self._callback.on_sync_failed(
                            job, f"worker exception: {e}"
                        )
                    except Exception as cb_err:
                        self._logger.error(f"callback_error slot={slot} error={cb_err}")
            finally:
                if job is not _SYNC_SENTINEL:
                    self._queue.task_done()

    def _sync_to_all_proxies(self, job: SyncJob, slot: int) -> SyncMetrics:
        """Sync a single job to all proxies on a given slot."""
        if not job.proxies:
            return SyncMetrics()

        file_size = (
            os.path.getsize(job.source_file)
            if os.path.exists(job.source_file) else 0
        )
        results: List[SingleSyncResult] = []
        start_time = time.monotonic()

        for proxy in job.proxies:
            proxy_start = time.monotonic()
            transfer = self._transport.transfer_file(
                job.source_file, proxy, job.target_dir, slot=slot
            )
            elapsed = transfer.elapsed_sec if transfer.ok else time.monotonic() - proxy_start
            sr = SingleSyncResult(
                proxy=proxy,
                slot=slot,
                ok=transfer.ok,
                elapsed_sec=elapsed,
                file_size_bytes=file_size,
                avg_speed_mbps=compute_speed_mbps(file_size, elapsed),
                error_msg=transfer.stderr if not transfer.ok else "",
            )
            results.append(sr)

            status = "ok" if transfer.ok else "fail"
            self._logger.info(
                f"sync_proxy_result task_id={job.task_id} proxy={proxy.server} "
                f"slot={slot} status={status} elapsed_s={elapsed:.2f}"
            )

        total_elapsed = time.monotonic() - start_time
        return SyncMetrics(
            results=results,
            total_elapsed_sec=total_elapsed,
            file_size_bytes=file_size,
        )