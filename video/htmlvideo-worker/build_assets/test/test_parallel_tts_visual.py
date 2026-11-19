#!/usr/bin/env python3
"""
Test harness for ``parallel_tts_visual`` (tts_generate ‖ build_frame_visual).

TTS runs ``node /app/scripts/audio.mjs`` and writes ``audio_meta.json``. Visual
edits ``STORYBOARD.md`` in place and must leave a ``## Video direction`` block.
Default is ``--clone`` because both branches mutate the project. Pass
``--in-place`` to edit the original.

Default project: the 2026-11-19 project that failed the frame-html stage.
``--modelcat`` defaults to ``english``; ``--modelname`` defaults to empty
(voice id ``english|``).

Side effects: on the working copy, writes audio assets / ``audio_meta.json``
and rewrites ``STORYBOARD.md``. ``update_task_status`` is stubbed unless
``--live-status`` (the pipeline reports VIDEO_TTSED / VIDEO_VISUALED).

Usage:
  python3 /app/scripts/test_parallel_tts_visual.py
  python3 /app/scripts/test_parallel_tts_visual.py --preflight-only
  python3 /app/scripts/test_parallel_tts_visual.py --modelcat english --modelname ""
"""

from __future__ import annotations

import argparse
import json
import os
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
    stub_update_status,
)

# Mirrors html_utility.video_constant.LANGUAGE_MAP keys.
_MODELCAT_CHOICES = ("english", "china", "japan", "spanish", "arab")
_VIDEO_DIRECTION = "## Video direction"
_AUDIO_MJS = "/app/scripts/audio.mjs"
_STEP = "tts-visual"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run parallel_tts_visual against an existing HyperFrames project."
    )
    add_common_args(parser, FAILED_PROJECT)
    add_clone_args(parser, default_clone=True)
    parser.add_argument(
        "--modelcat",
        default="english",
        choices=_MODELCAT_CHOICES,
        help="voice prefix passed to audio.mjs (default: english)",
    )
    parser.add_argument(
        "--modelname",
        default="",
        help='voice name after the "|" in audio.mjs --voice (default: empty)',
    )
    return parser.parse_args(argv)


def _preflight(project_dir: Path, logger) -> int:
    missing = check_files(
        project_dir, ["hyperframes.json", "frame.md", "STORYBOARD.md"], logger
    )
    storyboard = project_dir / "STORYBOARD.md"
    if storyboard.is_file() and storyboard.stat().st_size == 0:
        logger.error("preflight failed — STORYBOARD.md is empty")
        missing.append("STORYBOARD.md (empty)")
    elif storyboard.is_file() and _VIDEO_DIRECTION in storyboard.read_text(encoding="utf-8"):
        logger.warning(
            "STORYBOARD.md already contains %s; visual edits it in place",
            _VIDEO_DIRECTION,
        )
    if not (project_dir / "SCRIPT.md").is_file():
        logger.info("SCRIPT.md absent (narration-free boards have no script)")
    missing += check_bins(["node", "claude", _AUDIO_MJS], logger)
    if missing:
        return 1
    logger.info("preflight ok — hyperframes.json, frame.md, STORYBOARD.md, audio.mjs, node, claude")
    return 0


def _log_audio_meta(project_dir: Path, logger) -> None:
    path = project_dir / "audio_meta.json"
    if not path.is_file():
        logger.info("audio_meta.json: missing")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("audio_meta.json unreadable: %s", exc)
        return
    voices = data.get("voices") if isinstance(data, dict) else None
    if isinstance(voices, list):
        count: object = len(voices)
    else:
        count = f"unparsed ({type(voices).__name__})"
    logger.info("audio_meta.json: %s bytes voices=%s", path.stat().st_size, count)


def _log_video_direction(project_dir: Path, logger) -> None:
    storyboard = project_dir / "STORYBOARD.md"
    text = storyboard.read_text(encoding="utf-8") if storyboard.is_file() else ""
    logger.info(
        "%s: %s",
        _VIDEO_DIRECTION,
        "present" if _VIDEO_DIRECTION in text else "absent",
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = get_logger("test_parallel_tts_visual", args.log_file)
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
        from html_utility.create_pipeline import parallel_tts_visual
    except Exception as exc:
        logger.error("import of parallel_tts_visual failed: %s", exc)
        return 1

    stub_update_status(stub=not args.live_status, logger=logger)
    knobs = claude_skill_knobs("visual")
    knobs["HF_MEDIA_ENGINE"] = os.environ.get("HF_MEDIA_ENGINE", "")
    knobs["modelcat"] = args.modelcat
    knobs["modelname"] = args.modelname if args.modelname else '""'
    log_env(logger, knobs)
    if args.preflight_only:
        logger.info("preflight-only: not running parallel_tts_visual")
        return 0

    logger.info(
        "parallel_tts_visual START task_id=%s project=%s modelcat=%s modelname=%r domain=%r",
        task_id,
        project_dir,
        args.modelcat,
        args.modelname,
        args.domain,
    )
    started = time.time()
    try:
        result = parallel_tts_visual(
            logger,
            str(project_dir),
            args.modelcat,
            args.modelname,
            task_id,
            args.domain,
        )
    except Exception as exc:
        logger.error("parallel_tts_visual raised: %s", exc)
        return 1
    elapsed = time.time() - started
    log_step_result(logger, "tts", result.tts)
    log_step_result(logger, "visual", result.visual)
    _log_audio_meta(project_dir, logger)
    _log_video_direction(project_dir, logger)
    ok = result.tts.ret == 0 and result.visual.ret == 0
    logger.info("parallel_tts_visual %s total=%.1fs", "PASS" if ok else "FAIL", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
