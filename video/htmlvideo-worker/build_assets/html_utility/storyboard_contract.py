# -*- coding: utf-8 -*-
"""Storyboard-stage prompt builder: contract card + inlined project inputs.

Optimization (from build-storyboard-5.7m-analysis.md):
the classic prompt sent the model on 3 serial read rounds of 8 cross-skill
reference docs (87 KB) plus 1 round of project inputs -> ~25s + a large
reasoning surface. This builder inlines the compact contract card and the
project inputs directly into the prompt so the model goes straight to
composing and writing in one response.
"""

from __future__ import annotations

import logging
import os
import re

# Canonical card lives in the hyperframes skills copy and is synced (cp -a)
# to the .claude skills copy -- same dual-copy convention as SKILL.md edits.
_CONTRACT_CARD_CANDIDATES = (
    "/app/skills/aiflow-build-storyboard/references/contract-card.md",
    "/app/skills/aiflow-build-storyboard/references/contract-card.md",
)


def _prompt_header() -> str:
    return (
        "/aiflow-build-storyboard\n\n"
        "Work in this HyperFrames project root. The brief is settled — do not "
        "re-init, re-interrogate, invent a brief, or run build-frame / audio / render.\n"
        "flow is automation → autonomous mode: no board, no approval questions, "
        "no waiting.\n\n"
        "The complete storyboard contract card and all project inputs are inlined "
        "below. Treat the card as the authoritative contract for this run — do NOT "
        "re-read any skill reference files or re-cat project inputs (BRIEF.md / "
        "visible-text / frame.md / meta); go straight to composing STORYBOARD.md.\n"
    )


def _prompt_footer() -> str:
    return (
        "First tool call MUST be Write(\"STORYBOARD.md\"). Any Bash/Read/Glob/Grep "
        "of project or skill files is a protocol fail — every input is already "
        "inlined above. Do NOT Write SCRIPT.md; the harness derives it from each "
        "frame's voiceover after you finish.\n\n"
        "The STORYBOARD frontmatter is pre-computed and INLINED above (under "
        "`## Pre-seeded STORYBOARD frontmatter`) — copy it EXACTLY into STORYBOARD.md. "
        "Keep `format`/`duration`/`message`/`audience`/`mode` as written. When `arc` "
        "is already a real structure (not `<FILL>`), treat it as the DECIDED story "
        "structure and expand it into frames — do not re-decide it. Fill only "
        "`music` and any `<FILL>` value, then write the frame blocks "
        "(status: outline, required narrative fields filled, quality voiceover "
        "lines — those lines become SCRIPT.md).\n\n"
        "After the Write succeeds, end with the heads-up summary: "
        "'This video tells [audience] that [message]' + a frame table "
        "(frame · beat (type, duration) · on screen · why).\n\n"
        "Do not write ## Video direction or time-coded Scene shot blocks — those "
        "belong to /aiflow-build-frame-visual. Stop when the gate passes: "
        "STORYBOARD.md exists with every frame carrying the required narrative "
        "fields.\n"
    )


def load_contract_card(logger: logging.Logger | None = None) -> str:
    """Return the contract card text, or "" when no copy is on disk."""
    for path in _CONTRACT_CARD_CANDIDATES:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    msg = "contract-card.md not found in either skills copy; prompt will fall back to skill-reference reads"
    if logger is not None:
        logger.warning(msg)
    return ""


def _frame_register(frame_md: str) -> str:
    """Extract frame.md's identity + register (name/description/unit/principle)
    and drop the design tokens (colors/typography/spacing/components).

    The full frame.md is ~17KB of visual spec (palette/type/components) that
    only the visual/frame-html stages consume; the storyboard stage needs just
    the voice/register hint to tune narration tone, so inline the identity block
    and leave the rest out of the reasoning surface.
    """
    if not frame_md:
        return ""
    m = re.match(r"---\r?\n([\s\S]*?)\r?\n---", frame_md)
    if not m:
        # No frontmatter delimiter — fall back to a bounded head.
        return frame_md[:400]
    fm = m.group(1)
    idx = fm.find("\ncolors:")
    if idx != -1:
        fm = fm[:idx]
    return fm.strip()


def _brief_field(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip() if m else ""


def build_skeleton_frontmatter(brief_text: str) -> str:
    """Deterministic STORYBOARD.md frontmatter, pre-derived from BRIEF.md.

    The frame structure (arc) and narration stay with the model, but the fixed
    fields — format/duration/message/audience/mode — are mechanics a script can
    pin so the model copies instead of re-derives. ``arc`` / ``music`` are left as
    fill slots (both are creative).
    """
    fmt = _brief_field(brief_text, "aspect") or "1920x1080"
    duration = _brief_field(brief_text, "length") or "60s"
    message = _brief_field(brief_text, "message")
    audience = _brief_field(brief_text, "audience")
    # arc 上移: identify-intent 已产出故事结构时直接预填,省掉 storyboard 模型这步推导。
    arc = _brief_field(brief_text, "arc") or "<FILL>"
    return (
        "---\n"
        f"format: {fmt}\n"
        f"duration: {duration}\n"
        f"message: {message}\n"
        f"audience: {audience}\n"
        "mode: autonomous\n"
        f"arc: {arc}\n"
        "music: <FILL>\n"
        "---\n"
    )


def _read_optional(path: str) -> str:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""


def build_storyboard_prompt(
    project_dir: str,
    logger: logging.Logger | None = None,
    contract_card: str | None = None,
) -> str:
    """Assemble the storyboard-stage prompt for one project.

    ``contract_card`` defaults to ``load_contract_card()``; pass an empty
    string to force no card (safe degradation to the legacy read-loop).
    Raises FileNotFoundError when a required input (BRIEF.md, frame.md)
    is missing.
    """
    card = load_contract_card(logger) if contract_card is None else contract_card
    parts = [_prompt_header()]
    if card:
        parts.append("## Contract card (authoritative)\n\n" + card.strip())
    else:
        parts.append(
            "NOTE: the contract card could not be loaded — follow this skill's "
            "Quick Reference and Prerequisites reads as written in SKILL.md, and "
            "still write both files in one response.\n"
        )
    # BRIEF.md — needed both inlined AND as the source for the pre-seeded frontmatter.
    brief_path = os.path.join(project_dir, "BRIEF.md")
    brief_text = _read_optional(brief_path)
    if not brief_text:
        raise FileNotFoundError(f"BRIEF.md missing: {brief_path}")
    parts.append("## BRIEF.md\n\n" + brief_text.strip())
    # Pre-seed the frontmatter INLINE instead of writing a file the model Reads.
    parts.append(
        "## Pre-seeded STORYBOARD frontmatter (copy EXACTLY, fill only <FILL>)\n\n"
        + build_skeleton_frontmatter(brief_text)
    )
    for rel, label in (
        ("capture/extracted/visible-text.txt", "## visible-text.txt"),
        ("user_script.txt", "## user_script.txt"),
        ("VO_MODE", "## VO_MODE"),
    ):
        text = _read_optional(os.path.join(project_dir, rel))
        if text:
            parts.append(f"{label}\n\n{text.strip()}")
    # frame.md — register-only inline (see _frame_register): the visual spec is
    # dead weight for narrative planning; keep only the voice/register hint.
    frame_path = os.path.join(project_dir, "frame.md")
    frame_md = _read_optional(frame_path)
    if frame_md:
        parts.append(
            "## frame.md register (voice/style guide — full visual spec omitted)\n\n"
            + _frame_register(frame_md)
        )
    else:
        raise FileNotFoundError(f"frame.md missing: {frame_path}")
    parts.append(_prompt_footer())
    return "\n\n".join(parts)