# -*- coding: utf-8 -*-
"""Intent extraction helpers for htmlvideo worker."""

from __future__ import annotations

import json
import logging
import os
import re

from html_utility.claude_cli import run_claude_result

INTENT_REQUIRED_FIELDS = ("message", "angle", "audience", "name", "summary")
INTENT_FILENAME = "intent.json"


def _build_identify_intent_prompt(content: str) -> str:
    return (
        "/aiflow-identify-intent\n\n"
        "Source text is inlined below — nothing to read, no files to create.\n\n"
        f"{content}\n\n"
        "Respond with ONLY the JSON object as your final message text (extract\n"
        "message, angle, audience, name, summary, and optional customization/notes;\n"
        "prefer the input language). No markdown code fences, no preamble, no prose,\n"
        "no tool calls — the caller captures your text reply and writes/validates\n"
        "intent.json itself. flow is automation → autonomous.\n"
    )


def _validate_intent(intent: dict) -> dict:
    missing = [
        k for k in INTENT_REQUIRED_FIELDS if not str(intent.get(k, "")).strip()
    ]
    if missing:
        raise ValueError(f"intent missing required fields: {missing}")
    return intent


def _parse_intent_json(text: str) -> dict:
    """Locate and validate the JSON object in the model's free-text reply."""
    if not text:
        raise ValueError("claude identify-intent returned no text")
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    # Clean fast path: the whole reply is one JSON object.
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return _validate_intent(obj)
    except json.JSONDecodeError:
        pass
    # Fallback: scan for the first `{...}` that decodes to an object
    # (the model may have wrapped the JSON in prose).
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stripped, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return _validate_intent(obj)
    raise ValueError(f"no JSON object found in intent reply: {text[:300]}")


def identify_intent(
    output_file: str,
    content: str,
    logger: logging.Logger,
) -> dict:
    """
    Extract intent from ``content`` via claude in no-tools mode and write the JSON.

    ``content`` must already be the full source text (caller converts uri/upload
    to text before invoking). ``output_file`` is the full path of the intent
    JSON. The intent profile denies Write/Edit/Bash/Read, so the model replies
    with the JSON as plain text — one request, no tool round-trips — and this
    function parses, validates, and writes the file. Raises on CLI failure or
    invalid/empty output.
    """
    work_dir = os.path.dirname(output_file) or "."
    os.makedirs(work_dir, exist_ok=True)
    # Avoid accepting a stale file if a previous run left one behind.
    if os.path.isfile(output_file):
        os.remove(output_file)

    prompt = _build_identify_intent_prompt(content)
    rc, result_text = run_claude_result(work_dir, prompt, logger, skill="intent")
    if rc != 0:
        raise RuntimeError(f"claude identify-intent failed rc:{rc}")

    intent = _parse_intent_json(result_text)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(intent, f, ensure_ascii=False, indent=2)
    return intent