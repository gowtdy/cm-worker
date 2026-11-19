#!/usr/bin/env python3
"""
Test harness for ``step_render`` (``npx hyperframes render``).

Writes ``--video-name`` (default ``{task_id}_harness_{YYYYmmddHHMMSS}.mp4``)
into the project directory via ``render_video``. Default is ``--in-place`` on
the 2026-11-19 failed project. Pass ``--clone`` to render into a copy that
omits existing ``*.mp4`` and ``snapshots/``.

Side effects: long-running render; creates or overwrites the mp4 in the
working copy. ``update_task_status`` is stubbed unless ``--live-status``.

Usage:
  python3 /app/scripts/test_step_render.py
  python3 /app/scripts/test_step_render.py --preflight-only
  python3 /app/scripts/test_step_render.py --video-name out.mp4 --videodir <project>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
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

_STEP = "render"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run step_render against an existing HyperFrames project."
    )
    add_common_args(parser, FAILED_PROJECT)
    add_clone_args(parser, default_clone=False)
    parser.add_argument(
        "--video-name",
        default="",
        help="mp4 filename inside the project (default: {task_id}_harness_{ts}.mp4)",
    )
    return parser.parse_args(argv)


def _frame_htmls(project_dir: Path) -> list[Path]:
    frames_dir = project_dir / "compositions" / "frames"
    if not frames_dir.is_dir():
        return []
    return sorted(frames_dir.glob("*.html"))


def _warn_optional(project_dir: Path, video_path: Path, logger) -> None:
    sheets = list((project_dir / "snapshots").glob("contact-sheet*.jpg"))
    if not sheets:
        logger.warning("contact sheets missing (snapshots/contact-sheet*.jpg)")
    if not (project_dir / "audio_meta.json").is_file():
        logger.warning("audio_meta.json missing")
    if not (project_dir / "caption_groups.json").is_file():
        logger.warning("caption_groups.json missing")
    if video_path.is_file():
        logger.warning(
            "mp4 already exists and will be overwritten: %s (%s bytes)",
            video_path,
            video_path.stat().st_size,
        )


def _preflight(project_dir: Path, video_path: Path, logger) -> int:
    missing = check_files(project_dir, ["hyperframes.json", "index.html"], logger)
    frames = _frame_htmls(project_dir)
    if not frames:
        logger.error("preflight failed — no compositions/frames/*.html")
        missing.append("compositions/frames/*.html")
    missing += check_bins(["npx"], logger)
    _warn_optional(project_dir, video_path, logger)
    if missing:
        return 1
    logger.info(
        "preflight ok — hyperframes.json, index.html, %s frame html, npx",
        len(frames),
    )
    return 0


def _log_ffprobe(video_path: Path, logger) -> None:
    if shutil.which("ffprobe") is None:
        logger.info("ffprobe not on PATH; skipped duration probe")
        return
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe failed: %s", exc)
        return
    if proc.returncode != 0:
        logger.warning("ffprobe rc=%s stderr=%s", proc.returncode, (proc.stderr or "").strip())
        return
    logger.info("ffprobe duration: %ss", (proc.stdout or "").strip())


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = get_logger("test_step_render", args.log_file)
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
    stamp = time.strftime("%Y%m%d%H%M%S")
    video_name = args.video_name or f"{task_id}_harness_{stamp}.mp4"
    video_path = project_dir / video_name
    if _preflight(project_dir, video_path, logger) != 0:
        return 1

    try:
        sys.path.insert(0, APP_ROOT)
        from html_utility.create_pipeline import step_render
        from html_utility.render import (
            RENDER_NODE_OPTIONS,
            RENDER_WORKERS,
            build_render_cmd,
            gpu_available,
        )
    except Exception as exc:
        logger.error("import of step_render failed: %s", exc)
        return 1

    stub_update_status(stub=not args.live_status, logger=logger)
    gpu = gpu_available()
    render_cmd = build_render_cmd(str(project_dir), str(video_path))
    log_env(
        logger,
        {
            "gpu_available": gpu,
            "RENDER_WORKERS": RENDER_WORKERS,
            "RENDER_NODE_OPTIONS": RENDER_NODE_OPTIONS,
            "build_render_cmd": " ".join(render_cmd),
        },
    )
    if args.preflight_only:
        logger.info("preflight-only: not running step_render")
        return 0

    logger.info(
        "step_render START task_id=%s project=%s video_name=%s",
        task_id,
        project_dir,
        video_name,
    )
    started = time.time()
    try:
        result = step_render(logger, str(project_dir), video_name, task_id)
    except Exception as exc:
        logger.error("step_render raised: %s", exc)
        return 1
    elapsed = time.time() - started
    log_step_result(logger, "render", result)
    size = video_path.stat().st_size if video_path.is_file() else 0
    logger.info("mp4: %s size=%s", video_path, size if video_path.is_file() else "missing")
    if video_path.is_file() and size > 0:
        _log_ffprobe(video_path, logger)
    ok = result.ret == 0 and video_path.is_file() and size > 0
    logger.info("step_render %s total=%.1fs", "PASS" if ok else "FAIL", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
