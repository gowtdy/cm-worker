# -*- coding: utf-8 -*-
"""Storyboard planning helpers for htmlvideo worker."""

from __future__ import annotations

import hashlib
import logging
import os
import re

from html_utility.claude_cli import run_claude, run_claude_result
from html_utility.storyboard_contract import (
    build_storyboard_prompt,
    compute_pacing_budget,
    count_spoken_units,
    parse_target_duration_s,
)

STORYBOARD_FILENAME = "STORYBOARD.md"
SCRIPT_FILENAME = "SCRIPT.md"
BRIEF_FILENAME = "BRIEF.md"
FRAME_FILENAME = "frame.md"
STORYBOARD_TEXT_OUT_ENV = "HTMLVIDEO_STORYBOARD_TEXT_OUT"
STORYBOARD_DURATION_GATE_ENV = "HTMLVIDEO_STORYBOARD_DURATION_GATE"
STORYBOARD_TEXT_OUT_MAX_TURNS = 2

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
    ``done OK <id>`` word-boundary match. Non-ASCII titles get a stable
    ``zh`` + 8-hex short hash so parallel frames stay unique (not all
    ``NN-frame``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if slug:
        return slug
    digest = hashlib.sha1((title or "").strip().encode("utf-8")).hexdigest()[:8]
    return f"zh{digest}"


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


def _storyboard_text_out_on() -> bool:
    raw = os.environ.get(STORYBOARD_TEXT_OUT_ENV, "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "off", "no")


def _extract_storyboard_markdown(text: str | None) -> str | None:
    """Extract a valid STORYBOARD.md document from a 1-turn text-out reply."""
    if not text:
        return None
    s = text.strip()
    fence = re.match(r"^```(?:markdown|md|yaml)?\s*\n([\s\S]*?)\n```\s*$", s, re.I)
    if fence:
        s = fence.group(1).strip()
    # Strip any accidental leading prose before `---` frontmatter or `## Frame 1`.
    if not s.startswith("---") and not _FRAME_HEADING_RE.match(s):
        fm_match = re.search(r"^---\r?\n[\s\S]*?\r?\n---", s, re.M)
        fh_match = _FRAME_HEADING_RE.search(s)
        cut = -1
        if fm_match and fh_match and fm_match.start() < fh_match.start():
            cut = fm_match.start()
        elif fh_match:
            cut = fh_match.start()
        if cut >= 0:
            s = s[cut:].strip()
    if not _FRAME_HEADING_RE.search(s):
        return None
    return s + "\n"


def _storyboard_duration_gate_on() -> bool:
    raw = os.environ.get(STORYBOARD_DURATION_GATE_ENV, "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _check_duration_shortfall(
    project_dir: str,
    storyboard_text: str,
) -> tuple[dict, int, int] | None:
    """Return ``(budget, spoken_frames, units)`` when a spoken board falls below
    75% of the target spoken budget; ``None`` when acceptable or exempted."""
    if not _storyboard_duration_gate_on():
        return None
    if not _has_spoken_frames(storyboard_text):
        return None
    vo_mode_path = os.path.join(project_dir, "VO_MODE")
    if os.path.isfile(vo_mode_path):
        with open(vo_mode_path, encoding="utf-8") as f:
            if "verbatim" in f.read().lower():
                return None
    brief_path = os.path.join(project_dir, BRIEF_FILENAME)
    if not os.path.isfile(brief_path):
        return None
    with open(brief_path, encoding="utf-8") as f:
        brief_text = f.read()
    dur = parse_target_duration_s(brief_text)
    if dur is None:
        return None
    lang = _brief_language(project_dir) or "en"
    budget = compute_pacing_budget(dur, lang)
    spoken_frames, units = count_spoken_units(storyboard_text, lang)
    if units < budget["gate_min_units"]:
        return budget, spoken_frames, units
    return None


def _duration_expansion_suffix(budget: dict, spoken_frames: int, units: int) -> str:
    est_s = units / max(0.5, budget["rate"])
    return (
        "\n\n## Duration shortfall retry (IMPORTANT)\n\n"
        f"Your previous draft had only **{spoken_frames} spoken frames / {units} {budget['unit_label']}** "
        f"(~{est_s:.0f}s at {budget['rate']} {budget['unit_label']}/s), which is far below the "
        f"**{int(round(budget['duration_s']))}s** target.\n"
        f"Rewrite the complete `STORYBOARD.md` so it has **{budget['min_frames']}–{budget['max_frames']} frames** "
        f"and **{budget['min_units']}–{budget['max_units']} {budget['unit_label']}** total "
        f"(~{budget['per_frame_min']}–{budget['per_frame_max']} {budget['unit_label']} per body frame). "
        "Deepen thin beats with concrete decision criteria, step-by-step checks, or worked contrasts — "
        "do NOT add repetitive filler and do NOT invent fake statistics."
    )


def build_storyboard(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Call storyboard planner via claude --print.

    ``project_dir`` is the HyperFrames project root and must already have
    ``BRIEF.md`` and ``frame.md``. Default (`HTMLVIDEO_STORYBOARD_TEXT_OUT=1`)
    uses 1-turn text-out (`--tools ""`, `max_turns=2`) so the model emits
    `STORYBOARD.md` in one API turn without paying a 2nd post-Write turn; `=0`
    rolls back to the `Write` tool flow. ``SCRIPT.md`` is derived from voiceover
    fields when narration is needed.
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

    if _storyboard_text_out_on():
        prompt = build_storyboard_prompt(project_dir, logger, text_out=True)
        rc, reply = run_claude_result(
            project_dir,
            prompt,
            logger,
            skill="storyboard",
            tools="",
            max_turns=STORYBOARD_TEXT_OUT_MAX_TURNS,
            trunc_kill=True,
        )
        extracted = _extract_storyboard_markdown(reply) if rc == 0 else None
        if extracted:
            shortfall = _check_duration_shortfall(project_dir, extracted)
            if shortfall is not None:
                budget, spoken_frames, units = shortfall
                if logger is not None:
                    logger.warning(
                        "storyboard duration shortfall: %d frames / %d %s < gate %d (target %ds) — running 1 expansion retry",
                        spoken_frames,
                        units,
                        budget["unit_label"],
                        budget["gate_min_units"],
                        int(round(budget["duration_s"])),
                    )
                retry_prompt = prompt + _duration_expansion_suffix(budget, spoken_frames, units)
                r_rc, r_reply = run_claude_result(
                    project_dir,
                    retry_prompt,
                    logger,
                    skill="storyboard",
                    label="storyboard-expand",
                    tools="",
                    max_turns=STORYBOARD_TEXT_OUT_MAX_TURNS,
                    trunc_kill=True,
                )
                r_extracted = _extract_storyboard_markdown(r_reply) if r_rc == 0 else None
                if r_extracted:
                    lang = _brief_language(project_dir) or "en"
                    _, r_units = count_spoken_units(r_extracted, lang)
                    if r_units > units:
                        extracted = r_extracted
                        units = r_units
                    if units < budget["gate_min_units"] and logger is not None:
                        logger.warning(
                            "storyboard still short after expansion retry (%d %s < %d) — accepting best draft",
                            units,
                            budget["unit_label"],
                            budget["gate_min_units"],
                        )
            with open(storyboard_path, "w", encoding="utf-8") as f:
                f.write(extracted)
            return finalize_storyboard_outputs(project_dir, logger)
        if logger is not None:
            logger.warning(
                "storyboard text-out failed (rc=%s, extracted=%s) — falling back to Write tool mode",
                rc,
                bool(extracted),
            )

    prompt = build_storyboard_prompt(project_dir, logger, text_out=False)
    rc = run_claude(project_dir, prompt, logger, skill="storyboard")
    if rc != 0:
        raise RuntimeError(f"claude build-storyboard failed rc:{rc}")

    return finalize_storyboard_outputs(project_dir, logger)
