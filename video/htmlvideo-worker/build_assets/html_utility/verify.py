# -*- coding: utf-8 -*-
"""Verify assembled video helpers for htmlvideo worker."""

from __future__ import annotations

import glob
import logging
import os

from html_utility.claude_cli import run_claude

HYPERFRAMES_JSON = "hyperframes.json"
INDEX_FILENAME = "index.html"
STORYBOARD_FILENAME = "STORYBOARD.md"
FRAMES_DIR = os.path.join("compositions", "frames")
SNAPSHOTS_DIR = "snapshots"
CONTACT_SHEET = os.path.join(SNAPSHOTS_DIR, "contact-sheet.jpg")
# `snapshot` paginates into contact-sheet-1..N.jpg (9 per page) when more than
# 9 frames are captured; the bare contact-sheet.jpg only exists for a single page.
CONTACT_SHEET_GLOB = os.path.join(SNAPSHOTS_DIR, "contact-sheet*.jpg")


def _build_verify_frame_prompt() -> str:
    return (
        """
/aiflow-verify-frame

Work in this HyperFrames project root. hyperframes.json, index.html,
STORYBOARD.md, and compositions/frames/*.html already exist — do not re-init,
do not invent a storyboard, do not write HTML frames, do not assemble index,
do not run preview or final render.

Follow the aiflow-verify-frame skill: inject transitions, run hyperframes
lint/check, snapshot frame midpoints, glance at the contact sheet. If a
command fails, surface stderr, make the cheapest safe edit to
compositions/frames/NN-*.html, then rerun the failed check. Treat caption
text_box_overflow (~1–4px on #caption-word-* / .caption-line) as expected
false-positives; only act when overflow names a frame element (#el-NN-*).

flow is automation → autonomous: proceed without waiting for approval.
Stop when the skill gate passes (lint and check passed, snapshots inspected).
Do not preview or render.
        """
    )


def _validate_prerequisites(project_dir: str) -> None:
    hyperframes_path = os.path.join(project_dir, HYPERFRAMES_JSON)
    index_path = os.path.join(project_dir, INDEX_FILENAME)
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    frames_dir = os.path.join(project_dir, FRAMES_DIR)

    if not os.path.isfile(hyperframes_path):
        raise FileNotFoundError(f"hyperframes.json missing: {hyperframes_path}")
    if not os.path.isfile(index_path):
        raise FileNotFoundError(f"index.html missing: {index_path}")
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"compositions/frames missing: {frames_dir}")
    html_frames = [
        name for name in os.listdir(frames_dir) if name.endswith(".html")
    ]
    if not html_frames:
        raise FileNotFoundError(
            f"no compositions/frames/*.html: {frames_dir}"
        )


def _validate_verify_outputs(project_dir: str) -> str:
    """Return the contact sheet(s) written by ``snapshot``.

    ``hyperframes snapshot`` emits a single ``snapshots/contact-sheet.jpg``
    when <=9 frames were captured, or paginated ``contact-sheet-1..N.jpg``
    sheets (9 per page) when more. Accept either form.
    """
    snapshots_dir = os.path.join(project_dir, SNAPSHOTS_DIR)
    sheets = sorted(glob.glob(os.path.join(snapshots_dir, "contact-sheet*.jpg")))
    if not sheets:
        raise FileNotFoundError(
            f"snapshots/contact-sheet*.jpg missing after verify: {snapshots_dir}"
        )
    non_empty = [s for s in sheets if os.path.getsize(s) > 0]
    if not non_empty:
        raise ValueError(
            f"all snapshots/contact-sheet*.jpg are empty: {snapshots_dir}"
        )
    if len(non_empty) == 1:
        return non_empty[0]
    # Joining keeps the caller's single-value log line informative for
    # paginated sheets without changing verify_frame's str return contract.
    return ", ".join(non_empty)


def verify_frame(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Call /aiflow-verify-frame via claude --print.

    ``project_dir`` is the HyperFrames project root and must already have
    ``hyperframes.json``, ``index.html``, ``STORYBOARD.md``, and
    ``compositions/frames/*.html``. Raises on missing prerequisites, CLI
    failure, or missing contact sheet. Returns the path to
    ``snapshots/contact-sheet.jpg``, or a comma-joined list of paths when
    the sheet was paginated (``contact-sheet-1..N.jpg``). Does not render.
    """
    _validate_prerequisites(project_dir)

    prompt = _build_verify_frame_prompt()
    rc = run_claude(project_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude verify-frame failed rc:{rc}")

    return _validate_verify_outputs(project_dir)
