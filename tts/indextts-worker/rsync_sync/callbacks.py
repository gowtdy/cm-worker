"""Completion callback implementations for the rsync sync subsystem.

These are the business-logic hooks that the sync module calls after a
sync job completes or fails. The sync module itself never imports
database code — these callbacks are instantiated by the caller and
injected into the orchestrator.

NoOpCallback      — does nothing (for testing or standalone use)
CallbackRecorder  — test double that records calls for assertions

For production callbacks with database dependencies (DbUpdateCallback),
see build_assets/gpu_indextts_tasks.py in the parent project.
"""

from typing import List

from .interfaces import CompletionCallback
from .models import SyncJob, SyncMetrics


class NoOpCallback(CompletionCallback):
    """Silent callback that does nothing. Use for standalone or testing."""

    def on_sync_complete(self, job: SyncJob, result: SyncMetrics) -> None:
        pass

    def on_sync_failed(self, job: SyncJob, error: str) -> None:
        pass


class CallbackRecorder(CompletionCallback):
    """Test double that records callback invocations for assertions.

    Usage:
        recorder = CallbackRecorder()
        # ... run sync ...
        assert len(recorder.completed) == 1
        assert recorder.completed[0].task_id == "task-123"
        assert len(recorder.failed) == 0
    """

    def __init__(self):
        self.completed: List[SyncJob] = []
        self.failed: List[SyncJob] = []
        self.errors: List[str] = []

    def on_sync_complete(self, job: SyncJob, result: SyncMetrics) -> None:
        self.completed.append(job)

    def on_sync_failed(self, job: SyncJob, error: str) -> None:
        self.failed.append(job)
        self.errors.append(error)

    def reset(self) -> None:
        """Clear all recorded calls."""
        self.completed.clear()
        self.failed.clear()
        self.errors.clear()