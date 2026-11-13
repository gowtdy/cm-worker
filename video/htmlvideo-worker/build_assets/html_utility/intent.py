# -*- coding: utf-8 -*-
"""Intent extraction helpers for htmlvideo worker."""

from __future__ import annotations

import json
import logging
import os
from html_utility.claude_cli import run_claude

INTENT_REQUIRED_FIELDS = ("message", "angle", "audience", "name", "summary")
INTENT_FILENAME = "intent.json"

def _build_identify_intent_prompt(output_file: str, content: str) -> str:
    return (
        f"/aiflow-identify-intent {output_file}\n\n"
        f"Write output only to {output_file}.\n\n"
        f"Source text:\n\n{content}\n\n"
        "Follow the aiflow-identify-intent skill: extract message, angle, author, "
        "name, summary (and optional customization/notes). Prefer the input language.\n\n"
        "flow is automation → autonomous: proceed without waiting for approval.\n"
        f"Stop when {output_file} exists and is valid JSON with required fields.\n"
    )

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


def identify_intent(
    output_file: str,
    content: str,
    logger: logging.Logger,
) -> dict:
    """
    Call /aiflow-identify-intent via claude --print.

    ``content`` must already be the full source text (caller converts uri/upload
    to text before invoking). ``output_file`` is the full path of the intent
    JSON. Raises on CLI failure or invalid/missing output.
    """
    work_dir = os.path.dirname(output_file) or "."
    os.makedirs(work_dir, exist_ok=True)
    # Avoid accepting a stale file if a previous run left one behind.
    if os.path.isfile(output_file):
        os.remove(output_file)

    prompt = _build_identify_intent_prompt(output_file, content)
    rc = run_claude(work_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude identify-intent failed rc:{rc}")

    return _load_and_validate_intent(output_file)
