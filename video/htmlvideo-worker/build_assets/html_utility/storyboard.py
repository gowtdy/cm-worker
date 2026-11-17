# -*- coding: utf-8 -*-
"""Storyboard planning helpers for htmlvideo worker."""

from __future__ import annotations

import logging
import os
import re

from html_utility.claude_cli import run_claude
from html_utility.storyboard_contract import build_storyboard_prompt

STORYBOARD_FILENAME = "STORYBOARD.md"
SCRIPT_FILENAME = "SCRIPT.md"
BRIEF_FILENAME = "BRIEF.md"
FRAME_FILENAME = "frame.md"

# `- voiceover: "..."` on its own line; the quoted value is the spoken guide.
_VOICEOVER_RE = re.compile(r'^\s*-\s+voiceover:\s*"([^"]*)"', re.MULTILINE)


def _has_spoken_frames(storyboard_text: str) -> bool:
    """True when any frame carries a non-empty voiceover (narration exists)."""
    return any(value.strip() for value in _VOICEOVER_RE.findall(storyboard_text))


def _validate_storyboard_outputs(project_dir: str) -> str:
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")

    # Gate on the dual-write contract: narration (any spoken frame) requires
    # SCRIPT.md from the same run; a narration-free video has none.
    with open(storyboard_path, encoding="utf-8") as f:
        storyboard_text = f.read()
    script_path = os.path.join(project_dir, SCRIPT_FILENAME)
    if _has_spoken_frames(storyboard_text) and not os.path.isfile(script_path):
        raise FileNotFoundError(
            f"SCRIPT.md missing but STORYBOARD.md has spoken voiceover "
            f"frames (dual-write gate failed): {script_path}"
        )
    if os.path.isfile(script_path) and os.path.getsize(script_path) == 0:
        raise ValueError(f"SCRIPT.md is empty: {script_path}")
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

    prompt = build_storyboard_prompt(project_dir, logger)
    rc = run_claude(project_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude build-storyboard failed rc:{rc}")

    return _validate_storyboard_outputs(project_dir)
