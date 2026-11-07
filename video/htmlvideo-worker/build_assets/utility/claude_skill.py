# -*- coding: utf-8 -*-
"""Non-interactive Claude Code skill runners for htmlvideo worker."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

INTENT_REQUIRED_FIELDS = ("message", "angle", "author", "name", "summary")
INTENT_FILENAME = "intent.json"


def _build_identify_intent_prompt(project_dir: str, content: str) -> str:
    intent_path = os.path.join(project_dir, INTENT_FILENAME)
    return (
        f"/aiflow-identify-intent {project_dir}\n\n"
        f"Work in directory {project_dir}. Write output only to "
        f"{intent_path} (filename must be {INTENT_FILENAME}).\n\n"
        f"Source text:\n\n{content}\n\n"
        "Follow the aiflow-identify-intent skill: extract message, angle, author, "
        "name, summary (and optional customization/notes). Prefer the input language.\n\n"
        "flow is automation → autonomous: proceed without waiting for approval.\n"
        f"Stop when {intent_path} exists and is valid JSON with required fields.\n"
    )


def _run_claude(project_dir: str, prompt: str, logger: logging.Logger) -> int:
    """Invoke Claude Code non-interactively (same flags as run_aiflow_build_skills)."""
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "text",
        prompt,
    ]
    logger.info(
        "claude skill start cwd=%s cmd=claude --print "
        "--dangerously-skip-permissions --permission-mode bypassPermissions "
        "--output-format text <prompt>",
        project_dir,
    )
    proc = subprocess.run(cmd, cwd=project_dir)
    return proc.returncode


def _load_and_validate_intent(intent_file: str) -> dict:
    if not os.path.isfile(intent_file):
        raise FileNotFoundError(f"intent.json missing: {intent_file}")
    with open(intent_file, "r", encoding="utf-8") as f:
        intent = json.load(f)
    if not isinstance(intent, dict):
        raise ValueError("intent.json is not a JSON object")
    missing = [
        k for k in INTENT_REQUIRED_FIELDS if not str(intent.get(k, "")).strip()
    ]
    if missing:
        raise ValueError(f"intent.json missing required fields: {missing}")
    return intent


def run_identify_intent(
    project_dir: str,
    content: str,
    logger: logging.Logger,
) -> dict:
    """
    Call /aiflow-identify-intent via claude --print.

    ``content`` must already be the full source text (caller converts uri/upload
    to text before invoking). Writes (and returns) validated intent from
    ``{project_dir}/intent.json``. Raises on CLI failure or invalid/missing output.
    """
    os.makedirs(project_dir, exist_ok=True)
    intent_file = os.path.join(project_dir, INTENT_FILENAME)
    # Avoid accepting a stale file if a previous run left one behind.
    if os.path.isfile(intent_file):
        os.remove(intent_file)

    prompt = _build_identify_intent_prompt(project_dir, content)
    rc = _run_claude(project_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude identify-intent failed rc:{rc}")

    return _load_and_validate_intent(intent_file)
