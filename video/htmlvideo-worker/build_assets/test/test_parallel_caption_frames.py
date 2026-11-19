#!/usr/bin/env python3
"""
Test harness for `parallel_caption_frames` (caption ‖ packets + frame-html).

Runs the create-pipeline stage that parallelizes `caption_generate` with
`build_frame_packets` + `build_frame_html`, against an existing HyperFrames
project. Defaults to the 2026-11-19 project that failed the frame-html stage —
useful for confirming the result-parsing fix (ANTHROPIC_LOG=debug stdout
pollution) lets the driver detect `done OK <frame_id>` and stop re-dispatching.

What it exercises (in one call):
  branch A  caption_generate       -> node captions.mjs build -> caption_groups.json
  branch B  build_frame_packets    -> node frame-packets.mjs  -> .dispatch.md + skeletons
            build_frame_html       -> per-frame claude workers (driver + fixed parser)
                                      -> mark animated -> assemble-index.mjs

Full chain: see test_step_init.py docstring. This step alone
(deployed at /app/test/ — Dockerfile copies build_assets/ → /app/):
  python3 /app/test/test_parallel_caption_frames.py
  python3 /app/test/test_parallel_caption_frames.py \
    --videodir /app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914
  python3 /app/test/test_parallel_caption_frames.py --live-status --domain mydomain
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

APP_ROOT = "/app"
FAILED_PROJECT = "/app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914"


def _logger(log_file: str | None) -> logging.Logger:
    logger = logging.getLogger("test_parallel_caption_frames")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s-%(name)s[%(lineno)d]-%(levelname)s: %(message)s"
    )
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
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


def _stub_update_status(stub: bool, logger) -> None:
    """Local test should not POST to userserver; swap in a no-op logger."""
    sys.path.insert(0, APP_ROOT)
    import html_utility.create_pipeline as cp

    if stub:
        cp.update_task_status = _noop_status
        logger.info("update_task_status stubbed → no userserver side effects")
    else:
        logger.info("update_task_status LIVE → will POST to userserver")


def _preflight(project_dir: Path, logger) -> int:
    """Check the project has the inputs both branches of parallel_caption_frames need."""
    required = [
        project_dir / "STORYBOARD.md",
        project_dir / "frame.md",
        project_dir / "hyperframes.json",
        project_dir / "audio_meta.json",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    packets = project_dir / ".hyperframes" / "frame-packets"
    if not packets.is_dir() or not any(packets.glob("*.md")):
        missing.append(str(packets) + " (no *.md packets)")
    if missing:
        logger.error("preflight failed — missing: %s", ", ".join(missing))
        return 1
    logger.info("preflight ok — project has storyboard/frame/hyperframes/audio/packets")
    return 0


def _print_env_knobs(logger) -> None:
    knobs = {
        "HTMLVIDEO_FRAME_HTML_DRIVER": os.environ.get("HTMLVIDEO_FRAME_HTML_DRIVER", ""),
        "HTMLVIDEO_FRAME_HTML_SKELETON": os.environ.get("HTMLVIDEO_FRAME_HTML_SKELETON", ""),
        "HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY": os.environ.get(
            "HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY", ""
        ),
        "HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER": os.environ.get(
            "HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER", ""
        ),
        "ANTHROPIC_LOG": os.environ.get("ANTHROPIC_LOG", "<unset>"),
        "HTMLVIDEO_CLAUDE_PROFILE": os.environ.get("HTMLVIDEO_CLAUDE_PROFILE", ""),
    }
    logger.info("env knobs: %s", " ".join(f"{k}={v or '<default>'}" for k, v in knobs.items()))


def _frame_summary(project_dir: Path, logger) -> None:
    frames = sorted((project_dir / "compositions" / "frames").glob("*.html"))
    storyboard = project_dir / "STORYBOARD.md"
    statuses: dict[str, int] = {}
    if storyboard.is_file():
        text = storyboard.read_text(encoding="utf-8")
        statuses = {
            s: text.count(f"- status: {s}") for s in ("outline", "animated")
        }
    logger.info("frames on disk:")
    for f in frames:
        logger.info("  %-34s %s bytes", f.name, f.stat().st_size)
    logger.info("STORYBOARD statuses: %s", statuses)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run parallel_caption_frames against an existing HyperFrames project."
    )
    p.add_argument(
        "--videodir",
        type=Path,
        default=Path(FAILED_PROJECT),
        help="project directory (default: the 2026-11-19 failed project)",
    )
    p.add_argument(
        "--task-id",
        default="",
        help="task_id passed through (default: basename of --videodir)",
    )
    p.add_argument(
        "--domain",
        default="",
        help="domain passed to update_task_status (ignored when stubbing)",
    )
    p.add_argument(
        "--live-status",
        action="store_true",
        help="do NOT stub update_task_status; POST real status to userserver",
    )
    p.add_argument("--log-file", default=None, help="optional file to also write logs to")
    p.add_argument(
        "--preflight-only",
        action="store_true",
        help="check prerequisites + imports, then exit without running claude",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    project_dir = args.videodir.expanduser().resolve()
    logger = _logger(args.log_file)

    if not project_dir.is_dir():
        logger.error("videodir is not a directory: %s", project_dir)
        return 1
    task_id = args.task_id or project_dir.name

    if _preflight(project_dir, logger) != 0:
        return 1

    try:
        sys.path.insert(0, APP_ROOT)
        from html_utility.create_pipeline import parallel_caption_frames
    except Exception as exc:
        logger.error("import of parallel_caption_frames failed: %s", exc)
        return 1

    _stub_update_status(stub=not args.live_status, logger=logger)
    _print_env_knobs(logger)

    if args.preflight_only:
        logger.info("preflight-only: not running parallel_caption_frames")
        return 0

    logger.info(
        "parallel_caption_frames START task_id=%s project=%s domain=%r",
        task_id,
        project_dir,
        args.domain,
    )
    t0 = time.time()
    try:
        result = parallel_caption_frames(logger, str(project_dir), task_id, args.domain)
    except Exception as exc:
        logger.error("parallel_caption_frames raised: %s", exc)
        return 1
    elapsed = time.time() - t0

    ca = result.caption
    fr = result.frames
    extra = fr.extra_costs or {}
    logger.info(
        "caption: ret=%s cost=%.1fs msg=%s",
        ca.ret,
        ca.cost,
        ca.msg,
    )
    logger.info(
        "frames:  ret=%s cost=%.1fs (packets=%.1fs frame_html=%.1fs) path=%s msg=%s",
        fr.ret,
        fr.cost,
        extra.get("packets", 0.0),
        extra.get("frame_html", 0.0),
        fr.path,
        fr.msg,
    )
    _frame_summary(project_dir, logger)

    ok = ca.ret == 0 and fr.ret == 0
    logger.info(
        "parallel_caption_frames %s total=%.1fs",
        "PASS" if ok else "FAIL",
        elapsed,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())