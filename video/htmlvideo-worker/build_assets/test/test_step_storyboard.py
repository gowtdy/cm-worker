#!/usr/bin/env python3
"""
Test harness for ``step_storyboard`` (``/aiflow-build-storyboard`` via claude).

``build_storyboard`` deletes an existing ``STORYBOARD.md`` and ``SCRIPT.md``
before the CLI run, then writes them back. Default is ``--clone`` so the
source project is not modified. Pass ``--in-place`` to edit the original.

Default project: the 2026-11-19 project that failed the frame-html stage.

Side effects: on the working copy (clone or in-place), removes then rewrites
``STORYBOARD.md`` and possibly ``SCRIPT.md``. ``update_task_status`` is stubbed
unless ``--live-status``.

Full chain: see test_step_init.py docstring. This step alone:
  python3 /app/test/test_step_storyboard.py
  python3 /app/test/test_step_storyboard.py --preflight-only
  python3 /app/test/test_step_storyboard.py --in-place --videodir <project>
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
    claude_skill_knobs,
    clone_project,
    get_logger,
    log_env,
    log_step_result,
    resolve_project,
    storyboard_status_counts,
    stub_update_status,
)

_FRAME_HEADING = re.compile(r"^## Frame\s+\d+", re.MULTILINE)
_STEP = "storyboard"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run step_storyboard against an existing HyperFrames project."
    )
    add_common_args(parser, FAILED_PROJECT)
    add_clone_args(parser, default_clone=True)
    return parser.parse_args(argv)


def _preflight(project_dir: Path, logger) -> int:
    missing = check_files(project_dir, ["BRIEF.md", "frame.md"], logger)
    missing += check_bins(["claude"], logger)
    if missing:
        return 1
    logger.info("preflight ok — BRIEF.md, frame.md, claude")
    return 0


def _summarize(project_dir: Path, logger) -> None:
    storyboard = project_dir / "STORYBOARD.md"
    text = storyboard.read_text(encoding="utf-8") if storyboard.is_file() else ""
    script = project_dir / "SCRIPT.md"
    logger.info("SCRIPT.md: %s", "present" if script.is_file() else "absent")
    logger.info("## Frame count: %s", len(_FRAME_HEADING.findall(text)))
    logger.info("STORYBOARD statuses: %s", storyboard_status_counts(project_dir))


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = get_logger("test_step_storyboard", args.log_file)
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
        from html_utility.create_pipeline import step_storyboard
    except Exception as exc:
        logger.error("import of step_storyboard failed: %s", exc)
        return 1

    stub_update_status(stub=not args.live_status, logger=logger)
    log_env(logger, claude_skill_knobs("storyboard"))
    if args.preflight_only:
        logger.info("preflight-only: not running step_storyboard")
        return 0

    logger.info(
        "step_storyboard START task_id=%s project=%s",
        task_id,
        project_dir,
    )
    started = time.time()
    try:
        result = step_storyboard(logger, str(project_dir))
    except Exception as exc:
        logger.error("step_storyboard raised: %s", exc)
        return 1
    elapsed = time.time() - started
    log_step_result(logger, "storyboard", result)
    _summarize(project_dir, logger)
    storyboard = Path(result.path) if result.path else None
    ok = (
        result.ret == 0
        and storyboard is not None
        and storyboard.is_file()
        and storyboard.stat().st_size > 0
    )
    if result.ret == 0 and not ok:
        logger.error("storyboard result missing a non-empty STORYBOARD.md path: %s", result.path)
    logger.info("step_storyboard %s total=%.1fs", "PASS" if ok else "FAIL", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
