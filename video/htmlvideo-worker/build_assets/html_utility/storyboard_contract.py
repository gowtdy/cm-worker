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
        "re-read any skill reference files; go straight to composing and writing "
        "the two files.\n"
    )


def _prompt_footer() -> str:
    return (
        "Write outline-stage STORYBOARD.md (status: outline, required narrative "
        "fields filled) and SCRIPT.md in ONE response: issue both Write tool "
        "calls together in the same assistant turn (SCRIPT.md whenever any frame "
        "has a non-empty voiceover). Never split the two files across turns, "
        "never end the turn between the two Writes, and never re-read either "
        "file after writing. Then end with the heads-up summary: "
        "'This video tells [audience] that [message]' + a frame table "
        "(frame · beat (type, duration) · on screen · why).\n\n"
        "Do not write ## Video direction or time-coded Scene shot blocks — those "
        "belong to /aiflow-build-frame-visual. Stop when the gate passes: "
        "STORYBOARD.md exists with every frame carrying the required narrative "
        "fields, and SCRIPT.md exists whenever any frame has a non-empty "
        "voiceover.\n"
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
    for rel, label, required in (
        ("BRIEF.md", "## BRIEF.md", True),
        ("frame.md", "## frame.md", True),
        ("capture/extracted/visible-text.txt", "## visible-text.txt", False),
        ("user_script.txt", "## user_script.txt", False),
        ("VO_MODE", "## VO_MODE", False),
    ):
        abspath = os.path.join(project_dir, rel)
        text = _read_optional(abspath)
        if text:
            parts.append(f"{label}\n\n{text.strip()}")
        elif required:
            raise FileNotFoundError(f"{label.strip('# ')} missing: {abspath}")
    parts.append(_prompt_footer())
    return "\n\n".join(parts)