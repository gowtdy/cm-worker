# -*- coding: utf-8 -*-
"""Frame visual design helpers for htmlvideo worker."""

from __future__ import annotations

import logging
import os

from html_utility.claude_cli import run_claude

STORYBOARD_FILENAME = "STORYBOARD.md"
FRAME_FILENAME = "frame.md"
HYPERFRAMES_JSON = "hyperframes.json"
VIDEO_DIRECTION_HEADING = "## Video direction"


def _build_frame_visual_prompt() -> str:
    return (
        "/aiflow-build-frame-visual\n\n"
        "Work in this HyperFrames project root. hyperframes.json, frame.md, and an\n"
        "outline-stage STORYBOARD.md already exist — do not init, invent a storyboard,\n"
        "re-interrogate, write HTML, dispatch frame workers, run audio, assemble\n"
        "index.html, or render.\n\n"
        "Follow the aiflow-build-frame-visual skill: frame visual design only. Skip the\n"
        "sketch pass (autonomous mode). Edit STORYBOARD.md in place — do not create\n"
        "another storyboard. Use frame.md as source of truth for color, type, layout\n"
        "feel, and style. Read references/visual-design.md,\n"
        "../hyperframes-animation/blueprints-index.md, references/motion-language.md,\n"
        "and ../hyperframes-animation/rules-index.md.\n\n"
        "For every frame, write a time-coded shot sequence paced to the voiceover\n"
        "(no front-loading), with invented focal/roles named inline Layout and motion\n"
        "per Scene. Add one video-wide ## Video direction block. Do not change story,\n"
        "script, transition_in, status, or the source text.\n\n"
        "flow is automation → autonomous: proceed without waiting for sketch/approval.\n"
        "Stop when the skill gate passes (every frame has a time-coded shot sequence;\n"
        "## Video direction exists). Do not build HTML.\n"
    )


def _validate_frame_visual_outputs(project_dir: str) -> str:
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        content = f.read()
    if VIDEO_DIRECTION_HEADING not in content:
        raise ValueError(
            f"STORYBOARD.md missing {VIDEO_DIRECTION_HEADING!r}: {storyboard_path}"
        )
    return storyboard_path


def build_frame_visual(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Call /aiflow-build-frame-visual via claude --print.

    ``project_dir`` is the HyperFrames project root and must already have
    ``hyperframes.json``, ``frame.md``, and outline-stage ``STORYBOARD.md``.
    Edits ``STORYBOARD.md`` in place. Raises on missing prerequisites, CLI
    failure, or gate failure. Returns the path to ``STORYBOARD.md``.
    """
    hyperframes_path = os.path.join(project_dir, HYPERFRAMES_JSON)
    frame_path = os.path.join(project_dir, FRAME_FILENAME)
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(hyperframes_path):
        raise FileNotFoundError(f"hyperframes.json missing: {hyperframes_path}")
    if not os.path.isfile(frame_path):
        raise FileNotFoundError(f"frame.md missing: {frame_path}")
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")

    prompt = _build_frame_visual_prompt()
    rc = run_claude(project_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude build-frame-visual failed rc:{rc}")

    return _validate_frame_visual_outputs(project_dir)
