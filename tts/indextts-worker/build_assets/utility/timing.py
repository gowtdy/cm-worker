"""
Structured timing events for index-tts-clone pipeline monitoring.

Events are written as JSONL to /app/logs/timing/{YYYY-MM-DD}.jsonl (configurable).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

_lock = threading.Lock()
_env_cache: Optional[Dict[str, Any]] = None
_run_context: Dict[str, Dict[str, Any]] = {}


def is_enabled() -> bool:
    return os.environ.get("TIMING_ENABLED", "1") not in ("0", "false", "False", "no", "NO")


def timing_log_dir() -> Path:
    base = os.environ.get("TIMING_LOG_DIR", "/app/logs/timing")
    return Path(base)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _collect_env() -> Dict[str, Any]:
    global _env_cache
    if _env_cache is not None:
        return dict(_env_cache)

    env: Dict[str, Any] = {
        "hostname": socket.gethostname(),
        "timing_env": os.environ.get("TIMING_ENV", "dev"),
    }
    try:
        from config.voice_config import get_device
        env["device"] = get_device()
    except Exception:
        env["device"] = "unknown"

    try:
        import torch
        if torch.cuda.is_available():
            dev_idx = 0
            device = env.get("device", "")
            if isinstance(device, str) and ":" in device:
                dev_idx = int(device.split(":")[-1])
            props = torch.cuda.get_device_properties(dev_idx)
            env["gpu_name"] = props.name
            env["gpu_mem_gb"] = round(props.total_memory / (1024 ** 3), 2)
    except Exception:
        pass

    _env_cache = env
    return dict(env)


def _jsonl_path() -> Path:
    day = datetime.now().strftime("%Y-%m-%d")
    return timing_log_dir() / f"{day}.jsonl"


def _write_jsonl(record: Dict[str, Any]) -> None:
    if not is_enabled():
        return
    try:
        path = _jsonl_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        pass


def _write_summary(run_id: str, summary: Dict[str, Any]) -> None:
    if not is_enabled():
        return
    path = timing_log_dir() / f"summary_{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)


def start_run(
    clone_method: str = "index-tts",
    clone_version: str = "v2.5",
    run_id: Optional[str] = None,
    **context: Any,
) -> str:
    """Start a timing run; returns run_id."""
    if run_id is None:
        run_id = str(uuid.uuid4())
    env = _collect_env()
    env["clone_method"] = clone_method
    env["clone_version"] = clone_version
    _run_context[run_id] = {
        "env": env,
        "context": dict(context),
        "started_at": _now_iso(),
        "stages": [],
    }
    _write_jsonl({
        "ts": _now_iso(),
        "run_id": run_id,
        "event": "run_start",
        "env": env,
        "context": dict(context),
    })
    return run_id


def set_run_context(run_id: Optional[str], **context: Any) -> None:
    if not run_id or run_id not in _run_context:
        return
    _run_context[run_id]["context"].update(context)


def end_run(run_id: Optional[str], status: str = "ok", error: Optional[str] = None) -> None:
    if not run_id or not is_enabled():
        return
    ctx = _run_context.pop(run_id, None)
    record: Dict[str, Any] = {
        "ts": _now_iso(),
        "run_id": run_id,
        "event": "run_end",
        "status": status,
        "env": (ctx or {}).get("env", _collect_env()),
        "context": (ctx or {}).get("context", {}),
    }
    if error:
        record["error"] = error
    _write_jsonl(record)
    if ctx:
        summary = {
            "run_id": run_id,
            "status": status,
            "started_at": ctx.get("started_at"),
            "ended_at": _now_iso(),
            "env": ctx.get("env"),
            "context": ctx.get("context"),
            "stages": ctx.get("stages", []),
        }
        if error:
            summary["error"] = error
        _write_summary(run_id, summary)


def emit_event(
    run_id: Optional[str],
    stage: str,
    duration_sec: float,
    status: str = "ok",
    context: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> None:
    if not is_enabled():
        return
    ctx = _run_context.get(run_id, {}) if run_id else {}
    merged_context = dict(ctx.get("context", {}))
    if context:
        merged_context.update(context)
    merged_context.update(extra)

    record: Dict[str, Any] = {
        "ts": _now_iso(),
        "run_id": run_id,
        "event": "stage_end",
        "stage": stage,
        "duration_sec": round(duration_sec, 4),
        "status": status,
        "env": ctx.get("env", _collect_env()),
        "context": merged_context,
    }
    _write_jsonl(record)
    if run_id and run_id in _run_context:
        _run_context[run_id]["stages"].append({
            "stage": stage,
            "duration_sec": round(duration_sec, 4),
            "status": status,
        })


@contextmanager
def stage(
    run_id: Optional[str],
    stage_name: str,
    context: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> Iterator[None]:
    """Context manager that records stage duration on exit."""
    t0 = time.perf_counter()
    status = "ok"
    err: Optional[str] = None
    try:
        yield
    except Exception as exc:
        status = "error"
        err = str(exc)
        raise
    finally:
        duration = time.perf_counter() - t0
        emit_extra = dict(extra)
        if err:
            emit_extra["error"] = err
        emit_event(run_id, stage_name, duration, status=status, context=context, **emit_extra)


def emit_stages(run_id: Optional[str], prefix: str, stages: Dict[str, float], **extra: Any) -> None:
    """Emit multiple substages sharing a prefix, e.g. prefix='infer', key='gpt_gen' -> infer.gpt_gen."""
    for key, duration in stages.items():
        if duration is None:
            continue
        emit_event(run_id, f"{prefix}.{key}", duration, **extra)


def timing_marker(run_id: Optional[str], stage: str, duration_sec: float, **extra: Any) -> str:
    """Return a stdout marker line for subprocess parent parsing."""
    emit_event(run_id, stage, duration_sec, **extra)
    payload = {"run_id": run_id, "stage": stage, "duration_sec": round(duration_sec, 4)}
    payload.update(extra)
    return f"__TIMING__{json.dumps(payload, ensure_ascii=False, default=str)}"


def parse_timing_markers(text: str) -> list:
    """Extract timing marker dicts from subprocess stdout/stderr."""
    results = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("__TIMING__"):
            try:
                results.append(json.loads(line[len("__TIMING__"):]))
            except json.JSONDecodeError:
                pass
    return results
