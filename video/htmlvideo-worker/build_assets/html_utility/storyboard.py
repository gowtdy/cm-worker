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
_DURATION_RE = re.compile(r"^\s*-\s+duration:\s*([0-9.]+)\s*s?", re.MULTILINE)
_BRIEF_LANGUAGE_RE = re.compile(r"^language:\s*(.+?)\s*$", re.MULTILINE)


def _has_spoken_frames(storyboard_text: str) -> bool:
    """True when any frame carries a non-empty voiceover (narration exists)."""
    return any(value.strip() for value in _VOICEOVER_RE.findall(storyboard_text))


def _slugify(title: str) -> str:
    """ASCII slug for frame file ids. Never empty — Chinese-only titles used to
    collapse to ``\"\"`` and yield ``01-.html``, which then breaks the driver's
    ``done OK <id>`` word-boundary match."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "frame"


_FRAME_HEADING_RE = re.compile(
    r"^## (?:Frame|Beat|Scene)\s+(\d+)\s*[-—–]\s*([^\n]+)", re.MULTILINE)
_SRC_RE = re.compile(r"^- src:.*$", re.MULTILINE)


def _normalize_frame_srcs(project_dir: str) -> None:
    """Deterministic `src` derivation: rewrite each frame's `- src:` to
    `compositions/frames/NN-slug(title).html`. The `src` is the loaded field that
    frame-html's packet builder derives the frame id from, so the harness pins it
    instead of trusting the model's copy (原则:确定性的事脚本做)."""
    path = os.path.join(project_dir, STORYBOARD_FILENAME)
    with open(path, encoding="utf-8") as f:
        text = f.read()

    split_re = re.compile(r"(?=^## (?:Frame|Beat|Scene)\b)", re.MULTILINE)
    parts = split_re.split(text)
    out = [parts[0]]
    for block in parts[1:]:
        m = _FRAME_HEADING_RE.match(block)
        if not m:
            out.append(block)
            continue
        num = int(m.group(1))
        slug = _slugify(m.group(2))
        new_src = f"- src: compositions/frames/{num:02d}-{slug}.html"
        out.append(_SRC_RE.sub(new_src, block, count=1))
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))


def _iter_frame_blocks(storyboard_text: str):
    """Yield (frame_num, title, block) for each ## Frame/Beat/Scene heading."""
    split_re = re.compile(r"(?=^## (?:Frame|Beat|Scene)\b)", re.MULTILINE)
    for block in split_re.split(storyboard_text):
        m = _FRAME_HEADING_RE.match(block)
        if not m:
            continue
        yield int(m.group(1)), m.group(2).strip(), block


def _parse_duration_s(block: str) -> float:
    m = _DURATION_RE.search(block)
    if not m:
        return 5.0
    try:
        return float(m.group(1))
    except ValueError:
        return 5.0


def _brief_language(project_dir: str) -> str:
    brief_path = os.path.join(project_dir, BRIEF_FILENAME)
    if not os.path.isfile(brief_path):
        return ""
    with open(brief_path, encoding="utf-8") as f:
        text = f.read()
    m = _BRIEF_LANGUAGE_RE.search(text)
    return (m.group(1).strip().strip("\"'") if m else "")


def _script_voice_header(project_id: str, language: str) -> str:
    lang = language.lower()
    if lang.startswith("zh") or "chinese" in lang or "中文" in language:
        voice = "Rachel (ElevenLabs，中文普通话)"
        direction = "温暖、笃定、带一点轻快。"
    else:
        voice = "Rachel (ElevenLabs)"
        direction = "Warm, assured, a little playful."
    return (
        f"# SCRIPT — {project_id}\n\n"
        f"**Voice:** {voice}\n"
        "**Voice settings:** stability 0.35 · similarity 0.75 · style 0.20\n"
        f"**Voice direction:** {direction}\n"
    )


def generate_script_from_storyboard(
    project_dir: str,
    logger: logging.Logger | None = None,
) -> str | None:
    """Derive ``SCRIPT.md`` from each frame's ``voiceover`` field.

    Returns the SCRIPT.md path when narration exists; removes any stale
    SCRIPT.md and returns ``None`` for narration-free boards. Overwrites a
    model-written SCRIPT.md when present — the harness is authoritative.
    """
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    script_path = os.path.join(project_dir, SCRIPT_FILENAME)
    with open(storyboard_path, encoding="utf-8") as f:
        storyboard_text = f.read()

    lines: list[tuple[int, str, str, float]] = []
    for num, title, block in _iter_frame_blocks(storyboard_text):
        vo_m = _VOICEOVER_RE.search(block)
        voiceover = vo_m.group(1).strip() if vo_m else ""
        if not voiceover:
            continue
        lines.append((num, title, voiceover, _parse_duration_s(block)))

    if not lines:
        if os.path.isfile(script_path):
            os.remove(script_path)
            if logger is not None:
                logger.info("SCRIPT.md removed (no spoken voiceover frames)")
        return None

    project_id = os.path.basename(os.path.abspath(project_dir))
    parts = [_script_voice_header(project_id, _brief_language(project_dir))]
    t0 = 0.0
    for i, (num, title, voiceover, dur) in enumerate(lines, start=1):
        t1 = t0 + dur
        parts.append(
            f"## Line {i} — {title} (Frame {num})\n\n"
            f"**Time:** {t0:.1f} – {t1:.1f}s\n"
            "**Delivery:** Match the voiceover phrasing; land key terms clearly.\n\n"
            f"    {voiceover}\n"
        )
        t0 = t1

    text = "\n".join(parts).rstrip() + "\n"
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(text)
    if logger is not None:
        logger.info(
            "SCRIPT.md derived from STORYBOARD voiceovers: lines=%s path=%s",
            len(lines),
            script_path,
        )
    return script_path


def _validate_storyboard_outputs(project_dir: str) -> str:
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")

    with open(storyboard_path, encoding="utf-8") as f:
        storyboard_text = f.read()
    script_path = os.path.join(project_dir, SCRIPT_FILENAME)
    # Gate: narration (any spoken frame) requires a non-empty SCRIPT.md that
    # the harness derived from voiceover fields.
    if _has_spoken_frames(storyboard_text):
        if not os.path.isfile(script_path):
            raise FileNotFoundError(
                f"SCRIPT.md missing but STORYBOARD.md has spoken voiceover "
                f"frames (harness derive failed): {script_path}"
            )
        if os.path.getsize(script_path) == 0:
            raise ValueError(f"SCRIPT.md is empty: {script_path}")
    elif os.path.isfile(script_path):
        raise ValueError(
            f"SCRIPT.md present but STORYBOARD.md has no spoken voiceover "
            f"(silent board must omit SCRIPT.md): {script_path}"
        )
    return storyboard_path


def finalize_storyboard_outputs(
    project_dir: str,
    logger: logging.Logger | None = None,
) -> str:
    """Normalize ``src``, derive ``SCRIPT.md``, then gate. Returns STORYBOARD path."""
    _normalize_frame_srcs(project_dir)
    generate_script_from_storyboard(project_dir, logger)
    return _validate_storyboard_outputs(project_dir)


def build_storyboard(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Call /aiflow-build-storyboard via claude --print.

    ``project_dir`` is the HyperFrames project root and must already have
    ``BRIEF.md`` and ``frame.md``. Raises on missing prerequisites, CLI
    failure, or missing/empty ``STORYBOARD.md``. Returns the path to
    ``STORYBOARD.md``. ``SCRIPT.md`` is derived from voiceover fields when
    narration is needed (not written by the model).
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
    rc = run_claude(project_dir, prompt, logger, skill="storyboard")
    if rc != 0:
        raise RuntimeError(f"claude build-storyboard failed rc:{rc}")

    return finalize_storyboard_outputs(project_dir, logger)
