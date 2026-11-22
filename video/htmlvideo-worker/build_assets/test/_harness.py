#!/usr/bin/env python3
"""Shared helpers for htmlvideo create-pipeline step harnesses.

Mirrors the behaviors of ``test_parallel_caption_frames.py`` (logger, status
stub, preflight file/bin checks, project resolution) and adds clone / env /
storyboard summaries used by the per-step scripts.

Deployed at ``/app/test/_harness.py`` (Dockerfile: ``build_assets/`` → ``/app/``).
Full end-to-end invocation chain is documented in ``test_step_init.py`` docstring.

This module must not import ``html_utility`` at import time, so ``--help``
works on a machine that does not have ``/app`` on ``sys.path``. The status
stub imports ``html_utility.create_pipeline`` only when a script is about to
call a pipeline step.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path

APP_ROOT = "/app"
FAILED_PROJECT = "/app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914"
CLONE_ROOT = Path("/tmp/htmlvideo-harness")

_STORYBOARD_STATUSES = ("outline", "animated")


def get_logger(name: str, log_file: str | None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s-%(name)s[%(lineno)d]-%(levelname)s: %(message)s"
    )
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def _noop_status(logger, task_id, domain, task_status, payload_msg=None, **kwargs):
    logger.debug(
        "status (stubbed no-op): task_id=%s domain=%s status=%s",
        task_id,
        domain,
        getattr(task_status, "value", task_status),
    )
    return 0, "stubbed"


def stub_update_status(stub: bool, logger: logging.Logger) -> None:
    """Local test should not POST to userserver; swap in a no-op logger."""
    sys.path.insert(0, APP_ROOT)
    import html_utility.create_pipeline as cp

    if stub:
        cp.update_task_status = _noop_status
        logger.info("update_task_status stubbed → no userserver side effects")
    else:
        logger.info("update_task_status LIVE → will POST to userserver")


def add_common_args(parser: argparse.ArgumentParser, default_videodir: str | Path) -> None:
    parser.add_argument(
        "--videodir",
        type=Path,
        default=Path(default_videodir),
        help=f"project directory (default: {default_videodir})",
    )
    parser.add_argument(
        "--task-id",
        default="",
        help="task_id passed through (default: basename of --videodir)",
    )
    parser.add_argument(
        "--domain",
        default="",
        help="domain passed to update_task_status (ignored when stubbing)",
    )
    parser.add_argument(
        "--live-status",
        action="store_true",
        help="do NOT stub update_task_status; POST real status to userserver",
    )
    parser.add_argument("--log-file", default=None, help="optional file to also write logs to")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="check prerequisites + imports, then exit without running the step",
    )


def resolve_project(
    args: argparse.Namespace, logger: logging.Logger
) -> tuple[Path, str] | None:
    project_dir = args.videodir.expanduser().resolve()
    if not project_dir.is_dir():
        logger.error("videodir is not a directory: %s", project_dir)
        return None
    task_id = args.task_id or project_dir.name
    return project_dir, task_id


def check_files(
    project_dir: Path, rel_paths: list[str], logger: logging.Logger
) -> list[str]:
    """Return relative paths that are not files. Logs once when any are missing."""
    project_dir = Path(project_dir)
    missing = [rel for rel in rel_paths if not (project_dir / rel).is_file()]
    if missing:
        logger.error("preflight failed — missing files: %s", ", ".join(missing))
    return missing


def check_bins(names: list[str], logger: logging.Logger) -> list[str]:
    """Return names not on PATH. Absolute paths are accepted as existing files.

    ``/app/scripts/*.mjs`` is not on PATH; pass the absolute path and this
    checks ``Path.is_file()`` instead of ``shutil.which``.
    """
    missing: list[str] = []
    for name in names:
        if Path(name).is_absolute():
            ok = Path(name).is_file()
        else:
            ok = shutil.which(name) is not None
        if not ok:
            missing.append(name)
    if missing:
        logger.error("preflight failed — missing programs: %s", ", ".join(missing))
    return missing


def _ignore_clone(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name == "snapshots" or name.endswith(".mp4")]


def clone_project(
    src: Path, step: str, task_id: str, logger: logging.Logger
) -> Path:
    """Copy ``src`` to ``/tmp/htmlvideo-harness/{step}-{task_id}-{ts}``.

    Skips ``*.mp4`` and any ``snapshots/`` directory so a render or verify
    clone does not carry previous outputs.
    """
    src = Path(src)
    CLONE_ROOT.mkdir(parents=True, exist_ok=True)
    safe_id = task_id.replace("/", "_")
    stamp = time.strftime("%Y%m%d%H%M%S")
    dest = CLONE_ROOT / f"{step}-{safe_id}-{stamp}"
    if dest.exists():
        dest = CLONE_ROOT / f"{step}-{safe_id}-{stamp}-{time.time_ns()}"
    shutil.copytree(src, dest, ignore=_ignore_clone)
    logger.info(
        "cloned project %s -> %s (ignored *.mp4 and snapshots/)", src, dest
    )
    return dest


def add_clone_args(parser: argparse.ArgumentParser, default_clone: bool) -> None:
    """Add mutually exclusive ``--clone`` / ``--in-place``. Default is ``default_clone``."""
    group = parser.add_mutually_exclusive_group()
    clone_help = (
        "copy the project to /tmp/htmlvideo-harness/{step}-{task_id}-{ts} "
        "before running (ignores *.mp4 and snapshots/)"
    )
    in_place_help = "run against the resolved project directory"
    if default_clone:
        clone_help += " (default)"
    else:
        in_place_help += " (default)"
    group.add_argument("--clone", dest="clone", action="store_true", help=clone_help)
    group.add_argument(
        "--in-place", dest="clone", action="store_false", help=in_place_help
    )
    parser.set_defaults(clone=default_clone)


def log_env(logger: logging.Logger, extra: dict[str, object]) -> None:
    if not extra:
        logger.info("env knobs: <none>")
        return
    parts: list[str] = []
    for key, value in extra.items():
        text = "" if value is None else str(value)
        parts.append(f"{key}={text or '<default>'}")
    logger.info("env knobs: %s", " ".join(parts))


def claude_skill_knobs(skill: str) -> dict[str, str]:
    """Env overrides read by ``html_utility.claude_cli._resolve_profile``."""
    prefix = f"HTMLVIDEO_CLAUDE_{skill.upper()}"
    names = [
        f"{prefix}_EFFORT",
        f"{prefix}_THINKING_TOKENS",
        f"{prefix}_MAX_OUTPUT_TOKENS",
        f"{prefix}_MAX_TURNS",
        f"{prefix}_TIMEOUT",
        f"{prefix}_MODEL",
        "ANTHROPIC_LOG",
    ]
    return {name: os.environ.get(name, "") for name in names}


def storyboard_status_counts(project_dir: Path) -> dict[str, int]:
    storyboard = Path(project_dir) / "STORYBOARD.md"
    if not storyboard.is_file():
        return {}
    text = storyboard.read_text(encoding="utf-8")
    return {status: text.count(f"- status: {status}") for status in _STORYBOARD_STATUSES}


def frame_summary(project_dir: Path, logger: logging.Logger) -> None:
    project_dir = Path(project_dir)
    frames = sorted((project_dir / "compositions" / "frames").glob("*.html"))
    logger.info("frames on disk:")
    for frame in frames:
        logger.info("  %-34s %s bytes", frame.name, frame.stat().st_size)
    logger.info("STORYBOARD statuses: %s", storyboard_status_counts(project_dir))


def log_step_result(logger: logging.Logger, label: str, result: object) -> None:
    cost = getattr(result, "cost", 0.0) or 0.0
    logger.info(
        "%s: ret=%s cost=%.1fs path=%s msg=%s",
        label,
        getattr(result, "ret", None),
        float(cost),
        getattr(result, "path", None),
        getattr(result, "msg", ""),
    )
