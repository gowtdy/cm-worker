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
    "/app/.claude/skills/aiflow-build-storyboard/references/contract-card.md",
)

STORYBOARD_DURATION_BUDGET_ENV = "HTMLVIDEO_STORYBOARD_DURATION_BUDGET"
_VOICEOVER_LINE_RE = re.compile(r'^\s*-\s+voiceover:\s*"([^"]*)"', re.MULTILINE)
_CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_NON_CJK_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


def _duration_budget_enabled() -> bool:
    raw = os.environ.get(STORYBOARD_DURATION_BUDGET_ENV, "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def parse_target_duration_s(brief_text: str) -> float | None:
    raw = _brief_field(brief_text, "length").strip().strip("\"'")
    if not raw:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*s?$", raw, re.I)
    if not m:
        return None
    val = float(m.group(1))
    return val if val >= 10.0 else None


def _lang_bucket(language: str) -> str:
    lang = (language or "").strip().strip("\"'").lower()
    if lang.startswith("zh") or "chinese" in lang or "china" in lang or "中文" in lang:
        return "zh"
    if lang.startswith("ja") or "japan" in lang or "日" in lang:
        return "ja"
    return "en"


def compute_pacing_budget(duration_s: float, language: str = "en") -> dict:
    """Deterministically derive frame count and spoken unit budget from target runtime.

    Calibrated against empirical TTS outputs (6b083ee7: 2.46 wps, 6b082a5b: 2.31 wps,
    including frame tail padding).
    """
    bucket = _lang_bucket(language)
    if bucket == "zh":
        rate, unit_label = 3.9, "Chinese chars"
        per_lo, per_hi = 24, 34
    elif bucket == "ja":
        rate, unit_label = 4.8, "Japanese chars"
        per_lo, per_hi = 28, 40
    else:
        rate, unit_label = 2.35, "words"
        per_lo, per_hi = 15, 21

    target_units = max(20, int(round(duration_s * rate)))
    min_units = max(16, int(round(target_units * 0.88)))
    max_units = max(min_units + 10, int(round(target_units * 1.14)))
    gate_min_units = max(12, int(round(target_units * 0.75)))

    # Target ~6.5–7.5s average per frame, clamped to [4, 12]
    est_frames = duration_s / 6.8
    min_frames = max(4, min(11, int(round(est_frames - 1.0))))
    max_frames = max(min_frames + 1, min(12, int(round(est_frames + 1.2))))

    return {
        "duration_s": duration_s,
        "lang_bucket": bucket,
        "rate": rate,
        "unit_label": unit_label,
        "min_frames": min_frames,
        "max_frames": max_frames,
        "target_units": target_units,
        "min_units": min_units,
        "max_units": max_units,
        "gate_min_units": gate_min_units,
        "per_frame_min": per_lo,
        "per_frame_max": per_hi,
    }


def count_spoken_units(storyboard_text: str, language: str = "en") -> tuple[int, int]:
    """Return ``(spoken_frame_count, total_spoken_units)`` for a STORYBOARD.md."""
    bucket = _lang_bucket(language)
    spoken_frames = 0
    total = 0
    for vo in _VOICEOVER_LINE_RE.findall(storyboard_text or ""):
        s = vo.strip()
        if not s:
            continue
        spoken_frames += 1
        if bucket in ("zh", "ja"):
            cjk = len(_CJK_CHAR_RE.findall(s))
            latin = len(_NON_CJK_WORD_RE.findall(s))
            total += cjk + latin
        else:
            total += len(s.split())
    return spoken_frames, total


def format_pacing_budget_block(brief_text: str, vo_mode: str = "") -> str:
    """Build the ``## Pacing & duration budget`` prompt section, or ``""`` if skipped."""
    if not _duration_budget_enabled():
        return ""
    if "verbatim" in (vo_mode or "").lower():
        return ""
    dur = parse_target_duration_s(brief_text)
    if dur is None:
        return ""
    lang = _brief_field(brief_text, "language") or "en"
    b = compute_pacing_budget(dur, lang)
    return (
        "## Pacing & duration budget (computed from brief length — authoritative)\n\n"
        f"- Target runtime: **{int(round(dur))}s** (effective TTS pace ~{b['rate']} {b['unit_label']}/s incl. frame tail).\n"
        f"- Target frame count: **{b['min_frames']}–{b['max_frames']} total frames** "
        f"(~6–8s avg per frame, one clear job per frame).\n"
        f"- Target total spoken length across all `voiceover` lines: **{b['min_units']}–{b['max_units']} {b['unit_label']}** "
        f"(aim ~{b['target_units']} {b['unit_label']}; body frames ~{b['per_frame_min']}–{b['per_frame_max']} {b['unit_label']} each, hook/CTA punchier).\n"
        "- If the source prompt is short (e.g. a single question), reach the budget by unpacking concrete decision criteria, step-by-step checks, or worked contrasts — never repeat filler and never invent fake statistics."
    )


def _prompt_header() -> str:
    # Deliberately does NOT lead with the `/aiflow-build-storyboard` slash
    # command. Passing it makes Claude Code resolve the skill and re-inject
    # SKILL.md AND duplicate the whole command-args body into the prompt — the
    # 6b044297059bf6d4 trace showed the 17 KB contract+inputs inlined twice
    # plus 6.3 KB SKILL.md (~25k input tokens for a 6 KB deliverable). The
    # prompt below is self-contained; the profile denies the Skill tool so a
    # SessionStart "invoke a skill" hook can't re-inject SKILL.md later.
    return (
        "Work in this HyperFrames project root. The brief is settled — do not "
        "re-init, re-interrogate, invent a brief, or run build-frame / audio / render.\n"
        "flow is automation → autonomous mode: no board, no approval questions, "
        "no waiting.\n\n"
        "The complete storyboard contract card and all project inputs are inlined "
        "below. Treat the card as the authoritative contract for this run — do NOT "
        "re-read any skill reference files or re-cat project inputs (BRIEF.md / "
        "visible-text / frame.md / meta); go straight to composing STORYBOARD.md.\n"
    )


def _prompt_footer(text_out: bool = False) -> str:
    if text_out:
        return (
            "Output ONLY the complete `STORYBOARD.md` markdown document directly "
            "as your FINAL message text in ONE turn — no `Write` or other tool "
            "calls, no markdown code fences around the whole file, no preamble "
            "or trailing commentary. The harness extracts your markdown reply, "
            "writes `STORYBOARD.md`, normalizes `- src:`, and derives `SCRIPT.md` "
            "from each frame's `voiceover`.\n\n"
            "The STORYBOARD frontmatter is pre-computed and INLINED above (under "
            "`## Pre-seeded STORYBOARD frontmatter`) — start your output with that "
            "`---` YAML frontmatter copied EXACTLY, filling only `music` and any "
            "`<FILL>` slot. When `arc` is already a real structure (not `<FILL>`), "
            "treat it as the DECIDED story structure and expand it into frames — "
            "do not re-decide it. Follow immediately with `## Frame 1 — …` blocks "
            "(`status: outline`, all required narrative fields filled, crisp "
            "phrase-segmented `voiceover` lines).\n\n"
            "Keep thinking concise (beat list + key cues only; NEVER draft full "
            "frame blocks or voiceover sentences inside thinking — write them once "
            "in your visible output). Do not write `## Video direction` or "
            "time-coded `Scene` shot blocks — those belong to `/aiflow-build-frame-visual`.\n"
        )
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
        "After reasoning, Write STORYBOARD.md in the SAME response — do not "
        "emit a draft or summary text BEFORE the Write, and do not add any "
        "content-bearing turn AFTER it. Once the Write succeeds, reply with "
        "exactly one confirmation line (e.g. `STORYBOARD.md written — 7 "
        "frames, arc how-to-process`) and stop. Do NOT echo a frame table or "
        "re-summarize scene/voiceover/on screen/why — the file is the "
        "deliverable, and the harness derives SCRIPT.md and the plan summary "
        "itself.\n\n"
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
    text_out: bool = False,
) -> str:
    """Assemble the storyboard-stage prompt for one project.

    ``contract_card`` defaults to ``load_contract_card()``; pass an empty
    string to force no card (safe degradation to the legacy read-loop).
    ``text_out`` switches the output protocol from ``Write("STORYBOARD.md")``
    to 1-turn direct markdown output (eliminating the post-Write confirmation
    turn). Raises FileNotFoundError when a required input (BRIEF.md, frame.md)
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
    vo_mode_text = _read_optional(os.path.join(project_dir, "VO_MODE"))
    pacing_block = format_pacing_budget_block(brief_text, vo_mode=vo_mode_text)
    if pacing_block:
        parts.append(pacing_block)
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
    parts.append(_prompt_footer(text_out=text_out))
    return "\n\n".join(parts)