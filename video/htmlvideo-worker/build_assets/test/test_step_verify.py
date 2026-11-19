#!/usr/bin/env python3
"""
Test harness for ``step_verify`` (``/aiflow-verify-frame`` via claude).

Requires ``hyperframes.json``, a non-empty ``STORYBOARD.md``, ``index.html``,
and at least one ``compositions/frames/*.html``. The skill may edit frame HTML
and writes ``snapshots/contact-sheet*.jpg``. Default is ``--in-place`` on the
2026-11-19 failed project. Pass ``--clone`` to leave the source untouched
(the clone drops ``snapshots/`` and ``*.mp4``).

Side effects: lint/check/snapshot, possible frame HTML edits, contact sheets
under ``snapshots/``. ``update_task_status`` is stubbed unless ``--live-status``.

Full chain: see test_step_init.py docstring. This step alone:
  python3 /app/test/test_step_verify.py
  python3 /app/test/test_step_verify.py --preflight-only
  python3 /app/test/test_step_verify.py --videodir <project>
  python3 /app/test/test_step_verify.py --clone --videodir <project>
"""

from __future__ import annotations

import argparse
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
    frame_summary,
    get_logger,
    log_env,
    log_step_result,
    resolve_project,
    storyboard_status_counts,
    stub_update_status,
)

_STEP = "verify"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run step_verify against an existing HyperFrames project."
    )
    add_common_args(parser, FAILED_PROJECT)
    add_clone_args(parser, default_clone=False)
    return parser.parse_args(argv)


def _frame_htmls(project_dir: Path) -> list[Path]:
    frames_dir = project_dir / "compositions" / "frames"
    if not frames_dir.is_dir():
        return []
    return sorted(frames_dir.glob("*.html"))


def _preflight(project_dir: Path, logger) -> int:
    missing = check_files(
        project_dir, ["hyperframes.json", "index.html", "STORYBOARD.md"], logger
    )
    storyboard = project_dir / "STORYBOARD.md"
    if storyboard.is_file() and storyboard.stat().st_size == 0:
        logger.error("preflight failed — STORYBOARD.md is empty")
        missing.append("STORYBOARD.md (empty)")
    frames = _frame_htmls(project_dir)
    if not frames:
        logger.error("preflight failed — no compositions/frames/*.html")
        missing.append("compositions/frames/*.html")
    missing += check_bins(["claude", "npx"], logger)
    if not (project_dir / "caption_groups.json").is_file():
        logger.warning("caption_groups.json missing (verify can still run)")
    counts = storyboard_status_counts(project_dir)
    outline = counts.get("outline", 0)
    if outline:
        logger.warning("STORYBOARD has %s outline frame(s)", outline)
    if missing:
        return 1
    logger.info(
        "preflight ok — hyperframes.json, index.html, STORYBOARD.md, %s frame html, claude, npx",
        len(frames),
    )
    return 0


def _log_contact_sheets(project_dir: Path, logger) -> None:
    sheets = sorted((project_dir / "snapshots").glob("contact-sheet*.jpg"))
    if not sheets:
        logger.info("contact sheets: none")
        return
    logger.info("contact sheets:")
    for sheet in sheets:
        logger.info("  %-34s %s bytes", sheet.name, sheet.stat().st_size)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = get_logger("test_step_verify", args.log_file)
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
        from html_utility.create_pipeline import step_verify
    except Exception as exc:
        logger.error("import of step_verify failed: %s", exc)
        return 1

    stub_update_status(stub=not args.live_status, logger=logger)
    log_env(logger, claude_skill_knobs("verify"))
    if args.preflight_only:
        logger.info("preflight-only: not running step_verify")
        return 0

    logger.info("step_verify START task_id=%s project=%s", task_id, project_dir)
    started = time.time()
    try:
        result = step_verify(logger, str(project_dir))
    except Exception as exc:
        logger.error("step_verify raised: %s", exc)
        return 1
    elapsed = time.time() - started
    log_step_result(logger, "verify", result)
    _log_contact_sheets(project_dir, logger)
    frame_summary(project_dir, logger)
    ok = result.ret == 0
    logger.info("step_verify %s total=%.1fs", "PASS" if ok else "FAIL", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
