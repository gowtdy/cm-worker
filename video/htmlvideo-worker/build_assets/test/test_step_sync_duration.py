#!/usr/bin/env python3
"""
Test harness for ``step_sync_duration`` (``audio.mjs sync-durations``).

Rewrites ``- duration:`` lines in ``STORYBOARD.md`` from ``audio_meta.json``.
Default is ``--in-place`` on the 2026-11-19 failed project. Pass ``--clone``
to keep the source storyboard unchanged.

Side effects: rewrites duration lines in the working copy's ``STORYBOARD.md``.
Does not call claude. ``update_task_status`` is stubbed unless ``--live-status``.

Usage:
  python3 /app/scripts/test_step_sync_duration.py
  python3 /app/scripts/test_step_sync_duration.py --preflight-only
  python3 /app/scripts/test_step_sync_duration.py --clone --videodir <project>
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import (  # noqa: E402
    APP_ROOT,
    FAILED_PROJECT,
    add_clone_args,
    add_common_args,
    check_bins,
    check_files,
    clone_project,
    get_logger,
    log_env,
    log_step_result,
    resolve_project,
    stub_update_status,
)

_DURATION_RE = re.compile(r"^\s*-\s+duration:\s*(.+)$", re.MULTILINE)
_AUDIO_MJS = "/app/scripts/audio.mjs"
_STEP = "sync-duration"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run step_sync_duration against an existing HyperFrames project."
    )
    add_common_args(parser, FAILED_PROJECT)
    add_clone_args(parser, default_clone=False)
    return parser.parse_args(argv)


def _preflight(project_dir: Path, logger) -> int:
    missing = check_files(project_dir, ["STORYBOARD.md", "audio_meta.json"], logger)
    missing += check_bins(["node", _AUDIO_MJS], logger)
    if missing:
        return 1
    logger.info("preflight ok — STORYBOARD.md, audio_meta.json, audio.mjs, node")
    return 0


def _durations(project_dir: Path) -> list[str]:
    storyboard = project_dir / "STORYBOARD.md"
    if not storyboard.is_file():
        return []
    text = storyboard.read_text(encoding="utf-8")
    return [match.group(1).strip() for match in _DURATION_RE.finditer(text)]


def _log_duration_change(before: list[str], after: list[str], logger) -> None:
    logger.info("durations %s → %s", len(before), len(after))
    width = max(len(before), len(after))
    for index in range(width):
        left = before[index] if index < len(before) else "<missing>"
        right = after[index] if index < len(after) else "<missing>"
        logger.info("  frame %s: %s → %s", index + 1, left, right)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = get_logger("test_step_sync_duration", args.log_file)
    resolved = resolve_project(args, logger)
    if resolved is None:
        return 1
    project_dir, task_id = resolved
    if args.clone:
        try:
            project_dir = clone_project(project_dir, _STEP, task_id, logger)
        except Exception as exc:
            logger.error("clone failed: %s", exc)
            return 1
    if _preflight(project_dir, logger) != 0:
        return 1

    try:
        sys.path.insert(0, APP_ROOT)
        from html_utility.create_pipeline import step_sync_duration
    except Exception as exc:
        logger.error("import of step_sync_duration failed: %s", exc)
        return 1

    stub_update_status(stub=not args.live_status, logger=logger)
    log_env(logger, {})
    if args.preflight_only:
        logger.info("preflight-only: not running step_sync_duration")
        return 0

    before = _durations(project_dir)
    logger.info(
        "step_sync_duration START task_id=%s project=%s duration_lines=%s",
        task_id,
        project_dir,
        len(before),
    )
    started = time.time()
    try:
        result = step_sync_duration(logger, str(project_dir), task_id)
    except Exception as exc:
        logger.error("step_sync_duration raised: %s", exc)
        return 1
    elapsed = time.time() - started
    log_step_result(logger, "sync_duration", result)
    _log_duration_change(before, _durations(project_dir), logger)
    ok = result.ret == 0
    logger.info("step_sync_duration %s total=%.1fs", "PASS" if ok else "FAIL", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
