# -*- coding: utf-8 -*-
"""Storyboard planning helpers for htmlvideo worker."""

from __future__ import annotations

import logging
import os

from html_utility.claude_cli import run_claude

STORYBOARD_FILENAME = "STORYBOARD.md"
SCRIPT_FILENAME = "SCRIPT.md"
BRIEF_FILENAME = "BRIEF.md"
FRAME_FILENAME = "frame.md"


def _build_storyboard_prompt() -> str:
    return (
        "/aiflow-build-storyboard\n\n"
        "Work in this HyperFrames project root. BRIEF.md, frame.md, and\n"
        "capture/extracted/visible-text.txt already exist — the brief is settled;\n"
        "do not re-init, re-interrogate, invent a brief, or run build-frame / audio / render.\n\n"
        "Follow the aiflow-build-storyboard skill: narrative planning only. Read BRIEF.md\n"
        "(message + intent), visible-text.txt, and frame.md; write outline-stage\n"
        "STORYBOARD.md (status: outline, required narrative fields filled) and SCRIPT.md\n"
        "when narration is needed. Do not write ## Video direction or time-coded Scene\n"
        "shot sequences — those belong to /aiflow-build-frame-visual.\n\n"
        "flow is automation → autonomous: post a short frame-sequence summary as a\n"
        "heads-up and proceed without waiting for approval. Stop when the skill gate\n"
        "passes (STORYBOARD.md exists with required narrative fields per frame;\n"
        "SCRIPT.md when narration is needed).\n"
    )


def _validate_storyboard_outputs(project_dir: str) -> str:
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")
    return storyboard_path


def build_storyboard(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Call /aiflow-build-storyboard via claude --print.

    ``project_dir`` is the HyperFrames project root and must already have
    ``BRIEF.md`` and ``frame.md``. Raises on missing prerequisites, CLI
    failure, or missing/empty ``STORYBOARD.md``. Returns the path to
    ``STORYBOARD.md``. ``SCRIPT.md`` is optional (only when narration is
    needed).
    """
    brief_path = os.path.join(project_dir, BRIEF_FILENAME)
    frame_path = os.path.join(project_dir, FRAME_FILENAME)
    if not os.path.isfile(brief_path):
        raise FileNotFoundError(f"BRIEF.md missing: {brief_path}")
    if not os.path.isfile(frame_path):
        raise FileNotFoundError(f"frame.md missing: {frame_path}")

    # Avoid accepting stale outputs from a previous run.
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    script_path = os.path.join(project_dir, SCRIPT_FILENAME)
    for path in (storyboard_path, script_path):
        if os.path.isfile(path):
            os.remove(path)

    prompt = _build_storyboard_prompt()
    rc = run_claude(project_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude build-storyboard failed rc:{rc}")

    return _validate_storyboard_outputs(project_dir)
