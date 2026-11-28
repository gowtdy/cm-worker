# -*- coding: utf-8 -*-
"""Verify assembled video helpers for htmlvideo worker.

``verify-all.mjs --phase prepare`` runs once per ``verify_frame`` session
(known-failure autofix → ``font-imports.mjs --scope all`` (non-fatal;
``HTMLVIDEO_FONT_IMPORTS=0/false/off/no`` skips) → idempotent transitions
inject); the fix loop only reruns ``--phase verify``.

``--phase verify`` is two gates, in order, not separate model rounds.
The mechanical gate is ``verify-report`` (lint + check + structural +
transitions). Only after it passes does the visual gate run (snapshot +
verify-pixels). A mechanical FAIL returns before snapshot and does not print
``VISUAL_GATE:``. A pixels FAIL prints ``VISUAL_GATE: FAIL`` and takes the
visual fix prompt, not the mechanical actionable-summary playbook.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import glob
import hashlib
import json
import logging
import os
import re
import subprocess

from html_utility.claude_cli import (
    TRUNCATED_RC,
    _env_positive_int,
    run_claude_result,
)
from html_utility.verify_overlap_autofix import (
    auto_overlap_enabled,
    autofix_caption_keepout,
    autofix_designed_overlays,
    parse_findings_payload,
    strip_findings_payload,
)

HYPERFRAMES_JSON = "hyperframes.json"
INDEX_FILENAME = "index.html"
STORYBOARD_FILENAME = "STORYBOARD.md"
FRAMES_DIR = os.path.join("compositions", "frames")
SNAPSHOTS_DIR = "snapshots"
CONTACT_SHEET = os.path.join(SNAPSHOTS_DIR, "contact-sheet.jpg")
# `snapshot` paginates into contact-sheet-1..N.jpg (9 per page) when more than
# 9 frames are captured; the bare contact-sheet.jpg only exists for a single page.
CONTACT_SHEET_GLOB = os.path.join(SNAPSHOTS_DIR, "contact-sheet*.jpg")

VERIFY_ALL_SCRIPT = "/app/scripts/verify-all.mjs"
_MAX_FIX_ROUNDS = 3  # default; HTMLVIDEO_VERIFY_FIX_ROUNDS overrides
_PREPARE_TIMEOUT_S = 300
_VERIFY_TIMEOUT_S = 900
_VISUAL_TIMEOUT_S = 360
_INLINE_FILE_MAX_BYTES = 16_384
_SCAN_LOG_LIMIT = 2000
# 2026-11-26 (6b0795df): one retry at a higher output cap when a fix worker hit
# TRUNCATED_RC without landing any edit. HTMLVIDEO_VERIFY_TRUNC_RETRY_MAX_OUTPUT
# overrides; 0 disables the retry (pre-2026-11-26 fail-fast behaviour).
_TRUNC_RETRY_MAX_OUTPUT = 48000
# P1-2 (2026-11-28): Non-fatal mechanical findings that may degrade to a
# warning pass after fix rounds are exhausted (or stalled), provided the visual
# pixel gate still passes and thresholds hold.
_SOFT_FAIL_CODES = frozenset({
    "content_overlap",
    "text_occluded",
    "escaped_container",
    "canvas_overflow",
    "contrast_aa_failure",
    "text_box_overflow",
})
_SOFT_FAIL_MAX_ERRORS = 3  # HTMLVIDEO_VERIFY_SOFT_FAIL_MAX_ERRORS overrides; 0 disables
_SOFT_FAIL_MAX_FRAMES = 2  # HTMLVIDEO_VERIFY_SOFT_FAIL_MAX_FRAMES overrides
_CAPTION_WORD_SEL_RE = re.compile(r"#caption-word-\d+-\d+|caption-word", re.IGNORECASE)
DEGRADED_MARKER_FILENAME = os.path.join(SNAPSHOTS_DIR, "verify-degraded.json")


def _trunc_retry_max_output(logger: logging.Logger) -> int:
    raw = os.environ.get("HTMLVIDEO_VERIFY_TRUNC_RETRY_MAX_OUTPUT", "").strip()
    if not raw:
        return _TRUNC_RETRY_MAX_OUTPUT
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "invalid HTMLVIDEO_VERIFY_TRUNC_RETRY_MAX_OUTPUT=%r; using %d",
            raw, _TRUNC_RETRY_MAX_OUTPUT,
        )
        return _TRUNC_RETRY_MAX_OUTPUT


def _run_verify_phase(project_dir: str, phase: str, timeout: int, logger: logging.Logger) -> tuple[int, str]:
    cmd = ["node", VERIFY_ALL_SCRIPT, "--videodir", project_dir, "--phase", phase]
    logger.info("verify-all %s: %s", phase, " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        logger.error("verify-all %s timed out", phase)
        return 2, f"verify-all.mjs --phase {phase} timed out"
    out = proc.stdout or ""
    if out and not out.endswith("\n"):
        out += "\n"
    if proc.stderr:
        out += "\n[stderr]\n" + proc.stderr
    logger.info("verify-all %s done rc=%d (out %d bytes)", phase, proc.returncode, len(out))
    if proc.returncode != 0:
        if phase in ("verify", "visual"):
            summary = (
                _trim_scan_for_visual(out)
                if phase == "visual" or classify_scan_failure(out) == "visual"
                else _trim_scan_for_fix(out)
            )
        else:
            summary = out[-_SCAN_LOG_LIMIT:]
        summary = summary.strip()
        if summary:
            logger.info(
                "verify-all %s findings:\n%s",
                phase,
                summary[:_SCAN_LOG_LIMIT],
            )
    return proc.returncode, out


def _run_prepare(project_dir: str, logger: logging.Logger) -> tuple[int, str]:
    return _run_verify_phase(project_dir, "prepare", _PREPARE_TIMEOUT_S, logger)


def _run_verify(project_dir: str, logger: logging.Logger) -> tuple[int, str]:
    """Deterministic two-gate scan (no model). Returns ``(rc, output)``.

    Order inside ``verify-all.mjs --phase verify``:
      1. mechanical gate — ``verify-report`` (lint + check + structural +
         transitions). Non-zero returns immediately: no snapshot, no
         ``VISUAL_GATE:`` line.
      2. visual gate — only after mechanical exit 0: midpoints, snapshot,
         verify-pixels. Prints ``VISUAL_GATE: PASS`` or ``VISUAL_GATE: FAIL``.

    ``rc`` mirrors ``verify-all.mjs``:
      0 = both gates pass (snapshot written, pixels clean)
      1 = findings to fix (mechanical report, or pixels FAIL)
      2 = infrastructure / project blocker

    The model never runs the scan itself — running it here removes the model's
    per-turn orchestration tax (TTFB + turn latency) and the redundant
    find/cat/ls exploration turns it spent re-discovering the playbook.

    prepare already ran once in this session. This phase does not modify files
    except the snapshot, and only after the mechanical gate passes.
    """
    return _run_verify_phase(project_dir, "verify", _VERIFY_TIMEOUT_S, logger)


def _run_visual_only(project_dir: str, logger: logging.Logger) -> tuple[int, str]:
    """Run only the visual gate (midpoints → snapshot → verify-pixels)."""
    return _run_verify_phase(project_dir, "visual", _VISUAL_TIMEOUT_S, logger)


# Per-failure fix rules live in ONE place — /app/scripts/known-failures.mjs —
# shared with verify-report.mjs (which renders each matched rule into its
# "actionable summary") and verify-all.mjs (whose prepare phase runs the
# deterministic autofixes once per session). `_trim_scan_for_fix` already
# forwards that summary into this prompt, so the fix role carries only the
# generic discipline below, never a
# second copy of the per-failure knowledge that used to drift out of sync.
_FIX_PLAYBOOK = """The scan's «actionable summary» below already lists the matched known-failure
rule(s) — every named finding is paired with the exact fix. All per-failure
knowledge lives in /app/scripts/known-failures.mjs; nothing here overrides it.
- Apply the rule that names your finding, then stop. Do not invent a different fix.
- A finding with no matching rule → cheapest safe edit, one line of note.
- `id_requires_css_escape` / `google_fonts_import` warnings and the caption
  `text_box_overflow` finding are non-blockers — do not act on them.
- Decide each finding in ≤2 sentences and emit every Edit in ONE batched pass on
  your FIRST turn. Do NOT re-derive cqw/cqh geometry or re-trace the GSAP
  timeline — the scan already measured the boxes, so trust its classification."""

_FIX_HARD_CONSTRAINTS = """Hard constraints for this fix role (override project CLAUDE.md / plugins):
- Do NOT Read `/app/scripts/*`, `/app/hyperframes/**`, or any CLI/gate source.
- Do NOT invoke the Skill tool; do NOT explore the codebase first.
- Do NOT run verify-all / lint / check / snapshot / `npm run check`.
- Project CLAUDE.md "Always invoke skill" / "ALWAYS RUN check" do NOT apply here.
- Edit `compositions/**/*.html` only, in ONE batched pass (parallel Edits), then stop.
- Read each target file (absolute path) at most ONCE, then Edit it (skip Read when the file is already inlined below). Do NOT
  Grep / Glob / list directories or open files the findings do not name.
"""

_FIX_REPLY = (
    "After the Edits, reply with exactly `fixed <n>` (n = number of edits), or "
    "`fixed 0` if nothing to do — no summary."
)

_ACTIONABLE_HEADER = "── actionable summary ──"
_ERROR_LINE_RE = re.compile(r"^\s+✗ ")
# The one stable anchor in every finding line, regardless of whether it was a
# lint error (code then file) or a check finding (`in <file>`).
_FRAME_FILE_RE = re.compile(r"compositions/frames/[A-Za-z0-9._-]+\.html")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "on", "yes")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _error_blocks(head: str) -> list[str]:
    """``✗`` lines plus the deeper-indented detail lines right under them
    (e.g. transitions verify failure lines), up to a blank or shallower line.
    """
    out: list[str] = []
    depth = -1
    for line in head.splitlines():
        if _ERROR_LINE_RE.match(line):
            out.append(line)
            depth = _indent(line)
        elif depth >= 0 and line.strip() and _indent(line) > depth:
            out.append(line)
        else:
            depth = -1
    return out


def _trim_scan_for_fix(scan_output: str) -> str:
    """Error lines (with their detail lines) + actionable summary through the
    end of the scan.

    Drops the ✓ waterfall (font-imports, transitions inject,
    prerequisites, warnings/infos, midpoints) the fix role never acts on.
    Falls back to the full scan when the summary header is missing.
    """
    text = strip_findings_payload(scan_output)
    idx = text.find(_ACTIONABLE_HEADER)
    if idx < 0:
        return text
    head, tail = text[:idx], text[idx:]
    errors = _error_blocks(head)
    parts = []
    if errors:
        parts.append("── error findings ──\n" + "\n".join(errors) + "\n\n")
    parts.append(tail.strip("\n") + "\n")
    return "".join(parts)


def _fix_prompt_prefix(root_line: str, label: str) -> str:
    """Fixed header/hard-constraints/playbook shared by every fix prompt."""
    return (
        "Fix the verification findings below in this HyperFrames project root. "
        "The deterministic scan (`node /app/scripts/verify-all.mjs --phase verify`, after a one-time prepare pass) has ALREADY "
        "run and returned gate findings (exit 1); its error findings and "
        "«actionable summary» are appended.\n\n"
        + root_line
        + _FIX_HARD_CONSTRAINTS
        + "\nApply the cheapest safe fix to compositions/frames/NN-*.html in ONE "
        "batched pass, using absolute paths. Do NOT re-run verify-all / lint / "
        "check / snapshot (the harness re-runs the scan itself); do NOT re-init, "
        "build, assemble, preview or render.\n\n"
        + _FIX_PLAYBOOK
        + "\n\n" + _FIX_REPLY + "\n\n"
        + f"── {label} (already run — do not re-run) ──\n"
    )


_PAREN_GROUP_RE = re.compile(r"(×\d+\s*)?\(([^()\n]*compositions/frames/[^()\n]*)\)")
_BULLET_LINE_RE = re.compile(r"^\s+-\s+")
_NUMBERED_RULE_RE = re.compile(r"^ {2}\d+\)\s+")


def _inline_file_enabled() -> bool:
    raw = os.environ.get("HTMLVIDEO_VERIFY_INLINE_FILE", "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _inline_target_file_block(project_dir: str, rel: str | None) -> str:
    """Inline a small single target frame file so the fix worker can Edit on
    turn 1 without spending an extra Read round-trip."""
    if not project_dir or not rel or rel == "__other__" or not _inline_file_enabled():
        return ""
    if not _FRAME_FILE_RE.fullmatch(rel):
        return ""
    comp_root = os.path.realpath(os.path.join(project_dir, "compositions"))
    abs_path = os.path.realpath(os.path.join(project_dir, rel))
    if not abs_path.startswith(comp_root + os.sep) or not os.path.isfile(abs_path):
        return ""
    try:
        if os.path.getsize(abs_path) > _INLINE_FILE_MAX_BYTES:
            return ""
        with open(abs_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    numbered = "\n".join(f"{i}\t{line}" for i, line in enumerate(text.splitlines(), 1))
    return (
        f"\nThe target file `{abs_path}` is already inlined below — emit your Edit(s) "
        "directly on turn 1 without calling Read first.\n\n"
        f"── current file ({abs_path}) ──\n"
        f"{numbered}\n"
        "── end current file ──\n"
    )


def _single_target_frame(scan_text: str) -> str | None:
    """Return the sole `compositions/frames/*.html` referenced in error lines, else None."""
    idx = scan_text.find(_ACTIONABLE_HEADER)
    head = scan_text[:idx] if idx >= 0 else scan_text
    found: set[str] = set()
    for line in _error_blocks(head):
        if _ERROR_LINE_RE.match(line):
            m = _FRAME_FILE_RE.search(line)
            if not m:
                return None
            found.add(m.group(0))
    return next(iter(found)) if len(found) == 1 else None


def _filter_summary_line_for_file(line: str, rel: str) -> tuple[str | None, bool]:
    """Filter parenthesized `where(...)` file lists in an actionable-summary line
    down to entries for ``rel``.

    Returns ``(new_line_or_None, had_frame_refs)``. ``new_line_or_None`` is
    ``None`` when the line named other frame files and zero entries for ``rel``.
    """
    had_frame_refs = False
    empty_match = False

    def _replace(m: re.Match) -> str:
        nonlocal had_frame_refs, empty_match
        had_frame_refs = True
        count_prefix = m.group(1) or ""
        inner = m.group(2)
        parts = [p.strip() for p in inner.split(";")]
        kept = []
        for p in parts:
            if not p or p.startswith("…+"):
                continue
            files_in_part = _FRAME_FILE_RE.findall(p)
            if not files_in_part or rel in files_in_part:
                kept.append(p)
        if not kept:
            empty_match = True
            return m.group(0)
        if count_prefix:
            ws = count_prefix[len(count_prefix.rstrip()):]
            count_prefix = f"×{len(kept)}{ws}"
        return f"{count_prefix}({'; '.join(kept)})"

    new_line = _PAREN_GROUP_RE.sub(_replace, line)
    if empty_match:
        return None, True
    if not had_frame_refs:
        files_in_line = _FRAME_FILE_RE.findall(line)
        if files_in_line and rel not in files_in_line:
            return None, True
    return new_line, had_frame_refs


def _filter_summary_for_file(tail: str, rel: str) -> str:
    """Filter the shared ``── actionable summary ──`` tail for a single frame
    worker so sibling files never leak into its prompt (2026-11-24 R1 bug:
    worker 1 edited both 01 and 07, leaving worker 2 confused for 35s)."""
    if rel == "__other__":
        return tail
    lines = tail.splitlines(keepends=True)
    out: list[str] = []
    skip_until_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if skip_until_indent is not None:
            if stripped and _indent(line) > skip_until_indent:
                continue
            skip_until_indent = None
        if _NUMBERED_RULE_RE.match(line):
            new_line, _ = _filter_summary_line_for_file(line, rel)
            if new_line is None:
                skip_until_indent = _indent(line)
                continue
            out.append(new_line)
            continue
        if _BULLET_LINE_RE.match(line):
            new_line, _ = _filter_summary_line_for_file(line, rel)
            if new_line is None:
                continue
            out.append(new_line)
            continue
        new_line, _ = _filter_summary_line_for_file(line, rel)
        if new_line is None:
            continue
        out.append(new_line)
    return "".join(out)


_ANTI_SILENCING_REJECTED_NOTE = (
    "[PREVIOUS EDIT REJECTED BY ANTI-SILENCING GUARD] Your prior attempt added "
    "`data-layout-allow-overlap` / `data-layout-allow-occlusion` without fixing "
    "`<style>` or `<script>`, and was automatically reverted. You MUST modify CSS "
    "positioning/spacing or GSAP timeline (e.g. fade covered text out with "
    "`autoAlpha: 0` when a badge/pill enters) — adding `data-layout-allow-*` alone "
    "is blocked.\n\n"
)


def _build_fix_prompt_from_fragment(
    fragment: str,
    project_dir: str,
    label: str,
    target_file: str | None = None,
    anti_silencing_rejected: bool = False,
) -> str:
    root_line = f"Project root (absolute): {project_dir}\n\n" if project_dir else ""
    scope_line = ""
    if target_file and target_file != "__other__":
        abs_target = os.path.join(project_dir, target_file) if project_dir else target_file
        scope_line = (
            f"Scope: fix ONLY `{target_file}` (`{abs_target}`). "
            "Do NOT Read or Edit any other frame file (parallel sibling workers own other files).\n\n"
        )
    rejected_line = _ANTI_SILENCING_REJECTED_NOTE if anti_silencing_rejected else ""
    inline_block = _inline_target_file_block(project_dir, target_file)
    return (
        rejected_line
        + _fix_prompt_prefix(root_line + scope_line, label)
        + fragment
        + f"\n── end {label} ──\n"
        + inline_block
    )


def _build_fix_prompt(
    scan_output: str,
    project_dir: str = "",
    anti_silencing_rejected: bool = False,
) -> str:
    """Prompt for the one model turn that is still genuinely needed: apply the
    mechanical fixes the scan already enumerated. Only the actionable part of
    the scan is inlined (``HTMLVIDEO_VERIFY_FIX_FULL_SCAN=1`` restores the full
    output) so the model edits directly instead of re-investigating.
    """
    if _env_flag("HTMLVIDEO_VERIFY_FIX_FULL_SCAN"):
        scan_text, label = strip_findings_payload(scan_output), "scan output"
        target_file = None
    else:
        scan_text, label = _trim_scan_for_fix(scan_output), "scan findings"
        target_file = _single_target_frame(scan_text)
        if target_file:
            idx = scan_text.find(_ACTIONABLE_HEADER)
            if idx >= 0:
                scan_text = (
                    scan_text[:idx]
                    + _filter_summary_for_file(scan_text[idx:], target_file)
                )
    return _build_fix_prompt_from_fragment(
        scan_text,
        project_dir,
        label,
        target_file=target_file,
        anti_silencing_rejected=anti_silencing_rejected,
    )


def _split_scan_by_file(scan_text: str) -> tuple[dict[str, str], str]:
    """Split the trimmed scan findings into one error block per frame file.

    Returns ``(files: dict[rel_path -> fragment], shared_tail)``. Each per-file
    fragment filters ``shared_tail`` to drop sibling-file references so a
    worker never edits another worker's frame. When the scan carries findings
    in only one file (or cannot be attributed to distinct frame files) the dict
    is empty and the caller falls back to the whole scan.
    """
    idx = scan_text.find(_ACTIONABLE_HEADER)
    if idx < 0:
        return {}, scan_text
    head, tail = scan_text[:idx], scan_text[idx:]
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in _error_blocks(head):
        if _ERROR_LINE_RE.match(line):
            m = _FRAME_FILE_RE.search(line)
            current = m.group(0) if m else "__other__"
            if current not in groups:
                groups[current] = []
                order.append(current)
        if current is not None:
            groups[current].append(line)
    # A single (or zero) group is not worth splitting.
    if len(order) <= 1:
        return {}, scan_text
    fragments = {
        f: (
            "── error findings ──\n"
            + "\n".join(groups[f])
            + "\n\n"
            + _filter_summary_for_file(tail, f)
        )
        for f in order
    }
    return fragments, tail


_VISUAL_SECTION = "── visual gate (snapshot + pixels) ──"


def _trim_scan_for_visual(scan_output: str) -> str:
    """Keep the visual-gate / verify-pixels report, not the mechanical waterfall.

    Prefers the ``── visual gate`` section. Otherwise keeps stable pixels
    markers (``VISUAL_GATE:``, ``frame-*.png``, ``SUMMARY:``). Falls back to
    the full scan only when none of those are present.
    """
    text = scan_output or ""
    idx = text.find(_VISUAL_SECTION)
    if idx >= 0:
        return text[idx:].strip("\n") + "\n"
    kept: list[str] = []
    for line in text.splitlines():
        if (
            "VISUAL_GATE:" in line
            or "frame-" in line
            or "verify-pixels" in line
            or line.startswith("SUMMARY:")
            or "MECHANICAL PASS" in line
        ):
            kept.append(line)
    if kept:
        return "\n".join(kept) + "\n"
    return text


def _build_visual_fix_prompt(scan_output: str, project_dir: str = "") -> str:
    """Prompt for a pixels FAIL after the mechanical gate already passed.

    Based only on the verify-pixels report and the ``snapshots/frame-*.png``
    lines it names. One batched Edit of those frames. The harness reruns the
    verify phase; this role must not rerun verify-all / lint / check / snapshot.
    """
    scan_text = _trim_scan_for_visual(scan_output)
    root_line = f"Project root (absolute): {project_dir}\n\n" if project_dir else ""
    return (
        "Fix the visual-gate pixels findings below in this HyperFrames project root. "
        "The mechanical gate (verify-report: lint, check, structural, transitions) "
        "already PASSED. verify-pixels then failed (VISUAL_GATE: FAIL).\n\n"
        "collapse means layout collapse; empty means missing content (empty main stage "
        "means the top 83% hero region above the caption band rendered blank at the "
        "frame midpoint — ensure the hero is visible and scaled to 35–55% of canvas). "
        "Only edit frames a pixels FAIL line names. "
        "Map `snapshots/frame-NN.png` (or a `frame-NN.png` line) to "
        "`compositions/frames/NN-*.html`. Leave every other frame untouched.\n\n"
        + root_line
        + _FIX_HARD_CONSTRAINTS
        + "\nApply that fix to the FAIL-named `compositions/frames/*.html` files in ONE "
        "batched pass (parallel Edits), using absolute paths. Do NOT re-run verify-all / "
        "lint / check / snapshot (the harness re-runs the verify phase itself); do NOT "
        "re-init, build, assemble, preview or render.\n\n"
        + _FIX_REPLY + "\n\n"
        "── verify-pixels report (already run — do not re-run) ──\n"
        + scan_text
        + "── end verify-pixels report ──\n"
    )


_GATE_FAIL_RE = re.compile(r"^GATE:\s*FAIL\b", re.M)
_VISUAL_GATE_FAIL_RE = re.compile(r"^VISUAL_GATE:\s*FAIL\b", re.M)
_VISUAL_GATE_ANY_RE = re.compile(r"^VISUAL_GATE:", re.M)
_REPORT_EXIT_1_RE = re.compile(r"\breport exit 1\b")
_MECH_GATE_FAIL_RE = re.compile(r"mechanical gate FAIL")
_ZERO_ERROR_FINDINGS_RE = re.compile(r"\b0\s+error finding\(s\)", re.I)
_NUMBERED_ACTION_RE = re.compile(r"^ {2}\d+\) ", re.M)
_PARSER_INFRA_RE = re.compile(r"PARSER/INFRA:", re.I)


def scan_looks_unactionable(output: str) -> bool:
    """True when the scan failed the gate but listed nothing the model can fix.

    Matches the 2026-11-22 failure mode: ``GATE: FAIL`` + ``0 error finding(s)``
    while the actionable summary only says ``No action`` (no ``  N)`` items).
    Calling the model then burns ~minutes and digests to a zero-edit raise.
    """
    text = output or ""
    if _PARSER_INFRA_RE.search(text):
        return True
    if not _GATE_FAIL_RE.search(text):
        return False
    if not _ZERO_ERROR_FINDINGS_RE.search(text):
        return False
    if _NUMBERED_ACTION_RE.search(text):
        return False
    return True


def classify_scan_failure(output: str) -> str | None:
    """Classify an exit-1 scan as ``mechanical`` or ``visual``.

    Stable markers only (not a full-text match of the verdict sentence):

    - ``visual`` — a ``VISUAL_GATE: FAIL`` line. The mechanical gate already
      passed and verify-pixels failed.
    - ``mechanical`` — ``GATE: FAIL``, ``report exit 1``, or
      ``mechanical gate FAIL``, and no ``VISUAL_GATE:`` line at all (the visual
      gate was not opened).
    - ``None`` — anything else; callers keep the existing rc branch.
    """
    text = output or ""
    if _VISUAL_GATE_FAIL_RE.search(text):
        return "visual"
    if _VISUAL_GATE_ANY_RE.search(text):
        return None
    if (
        _GATE_FAIL_RE.search(text)
        or _REPORT_EXIT_1_RE.search(text)
        or _MECH_GATE_FAIL_RE.search(text)
    ):
        return "mechanical"
    return None


_ALLOW_ATTR_RE = re.compile(r"\bdata-layout-allow-(?:overlap|occlusion|overflow)\b", re.IGNORECASE)
_STYLE_OR_SCRIPT_RE = re.compile(r"<(style|script)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)


def _reject_blanket_allow_enabled() -> bool:
    raw = os.environ.get("HTMLVIDEO_VERIFY_REJECT_BLANKET_ALLOW", "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _snapshot_composition_html(project_dir: str) -> dict[str, str]:
    """Read current contents of compositions/**/*.html for anti-silencing diff guard."""
    root = os.path.join(project_dir, "compositions")
    snap: dict[str, str] = {}
    if not os.path.isdir(root):
        return snap
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".html"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, project_dir).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    snap[rel] = fh.read()
            except OSError:
                continue
    return snap


def _style_script_signature(html: str) -> str:
    return "\n".join(m.group(0) for m in _STYLE_OR_SCRIPT_RE.finditer(html))


def _detect_and_revert_blanket_allow(
    project_dir: str,
    before_snapshot: dict[str, str],
    logger: logging.Logger,
) -> list[str]:
    """Revert files where a fix worker silenced layout findings via blanket
    ``data-layout-allow-overlap`` / ``data-layout-allow-occlusion`` attributes
    (>=2 newly added, or 1 newly added with zero ``<style>``/``<script>`` edits).
    """
    reverted: list[str] = []
    after_snapshot = _snapshot_composition_html(project_dir)
    for rel, after_text in after_snapshot.items():
        before_text = before_snapshot.get(rel)
        if before_text is None or after_text == before_text:
            continue
        before_cnt = len(_ALLOW_ATTR_RE.findall(before_text))
        after_cnt = len(_ALLOW_ATTR_RE.findall(after_text))
        delta = after_cnt - before_cnt
        if delta <= 0:
            continue
        style_script_changed = (
            _style_script_signature(before_text) != _style_script_signature(after_text)
        )
        if delta >= 2 or (delta == 1 and not style_script_changed):
            abs_path = os.path.join(project_dir, rel)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(before_text)
            reverted.append(rel)
            logger.warning(
                "verify anti-silencing guard reverted %s (+%d data-layout-allow-* attr(s), "
                "style_script_changed=%s) — must fix CSS/GSAP layout instead",
                rel, delta, style_script_changed,
            )
    return reverted


def _composition_html_digest(project_dir: str) -> str:
    """Stable digest of compositions/**/*.html for zero-edit detection."""
    root = os.path.join(project_dir, "compositions")
    digest = hashlib.sha256()
    paths: list[str] = []
    if os.path.isdir(root):
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                if name.endswith(".html"):
                    paths.append(os.path.join(dirpath, name))
    for path in sorted(paths):
        rel = os.path.relpath(path, project_dir).replace(os.sep, "/")
        digest.update(rel.encode())
        digest.update(b"\0")
        with open(path, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


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


def _auto_fix_overlaps(
    project_dir: str,
    rc: int,
    output: str,
    round_no: int,
    logger: logging.Logger,
) -> tuple[int, str]:
    """Deterministically mark safe ``designed_overlay`` rows and delete
    redundant in-frame caption strips, then rescan if any file was written.
    Returns the (possibly unchanged) ``(rc, output)``; an auto-fix crash is
    logged and never fails verify.
    """
    changed = False
    for autofix, name in (
        (autofix_designed_overlays, "auto-overlap"),
        (autofix_caption_keepout, "auto-caption"),
    ):
        try:
            result = autofix(project_dir, output, logger)
        except Exception:
            logger.exception(
                "%s (round %d) crashed — continuing without it", name, round_no
            )
            continue
        if not result.changed:
            continue
        changed = True
        logger.info(
            "%s (round %d): wrote %d node(s) across %d file(s) [%s]; %d unresolved",
            name, round_no, result.nodes_marked, len(result.files_changed),
            ", ".join(result.files_changed), len(result.unresolved),
        )
    if not changed:
        return rc, output
    return _run_verify(project_dir, logger)


def _run_fix_worker(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    label: str | None,
    max_output: int | None = None,
) -> int:
    """Dispatch one fix turn with the truncation watcher armed (P0 fail-fast).

    ``trunc_kill`` kills a thinking-only turn that hit the output cap in seconds
    (rc 125) instead of letting the model re-think into the cap for minutes and
    return with zero edits — the 2026-11-23 runaway (2× 16000-token thinking-only
    turns, no Edit). Kept as a thin, easily-mocked seam for the unit tests.
    ``max_output`` raises the output cap for the one truncation retry.
    """
    extra_env = (
        {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output)} if max_output else None
    )
    fix_rc, _result = run_claude_result(
        project_dir, prompt, logger, skill="verify", label=label, trunc_kill=True,
        extra_env=extra_env,
    )
    return fix_rc


def _dispatch_fix(
    project_dir: str,
    prompt: str,
    round_no: int,
    logger: logging.Logger,
    label: str | None,
    max_output: int | None = None,
) -> int:
    """One batched model turn, raising on non-truncation non-zero exit.

    Returns ``fix_rc`` (``0`` or ``TRUNCATED_RC``) so callers can check whether
    edits already landed on disk before raising on truncation.
    """
    if max_output:
        fix_rc = _run_fix_worker(project_dir, prompt, logger, label, max_output=max_output)
    else:
        fix_rc = _run_fix_worker(project_dir, prompt, logger, label)
    if fix_rc not in (0, TRUNCATED_RC):
        raise RuntimeError(f"claude verify fix failed rc:{fix_rc} [{label or '-'}]")
    return fix_rc


def _retry_truncated(
    project_dir: str,
    before_digest: str,
    prompts: dict[str, tuple[str, str | None]],
    truncated_labels: list[str],
    round_no: int,
    logger: logging.Logger,
) -> list[str]:
    """Re-run truncated zero-edit workers once at a higher output cap.

    Only fires when NOTHING landed on disk (a partial landing proceeds to the
    rescan as before). Returns the labels still truncated after the retry.
    """
    if not truncated_labels or _composition_html_digest(project_dir) != before_digest:
        return truncated_labels
    cap = _trunc_retry_max_output(logger)
    if cap <= 0:
        return truncated_labels
    logger.warning(
        "verify fix round %d [%s] truncated with zero edits — retrying once at "
        "max_output=%d",
        round_no, ", ".join(truncated_labels), cap,
    )
    still: list[str] = []
    for key in truncated_labels:
        prompt, label = prompts[key]
        if _dispatch_fix(project_dir, prompt, round_no, logger, label, max_output=cap) == TRUNCATED_RC:
            still.append(key)
    return still


def _apply_model_fix(
    project_dir: str,
    scan_output: str,
    round_no: int,
    logger: logging.Logger,
    reverted_files: set[str] | None = None,
) -> tuple[int, str]:
    """Batched model fixes, then rescan. Raises on a zero-edit digest.

    When the mechanical findings span more than one frame file they are split
    into one small self-contained worker per file (P2) and run in parallel:
    a single worker facing 10 findings across 4 files runs its adaptive
    thinking into the output cap; a per-file worker of 1–2 findings edits on
    turn 1. Falls back to the whole scan for a single-file failure or
    ``HTMLVIDEO_VERIFY_FIX_FULL_SCAN=1``.
    """
    before_digest = _composition_html_digest(project_dir)
    before_snapshot = (
        _snapshot_composition_html(project_dir)
        if _reject_blanket_allow_enabled()
        else None
    )
    prior_reverted = set(reverted_files or ())
    fragments: dict[str, str] = {}
    if not _env_flag("HTMLVIDEO_VERIFY_FIX_FULL_SCAN"):
        fragments, _tail = _split_scan_by_file(_trim_scan_for_fix(scan_output))
    truncated_labels: list[str] = []
    prompts: dict[str, tuple[str, str | None]] = {}
    if fragments:
        max_workers = min(len(fragments), 4)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {}
            for rel, fragment in fragments.items():
                prompt = _build_fix_prompt_from_fragment(
                    fragment,
                    project_dir,
                    "scan findings",
                    target_file=rel,
                    anti_silencing_rejected=(rel in prior_reverted),
                )
                prompts[rel] = (prompt, rel)
                futs[ex.submit(_dispatch_fix, project_dir, prompt, round_no, logger, rel)] = rel
            for fut in as_completed(futs):
                rel = futs[fut]
                rc = fut.result()
                if rc == TRUNCATED_RC:
                    truncated_labels.append(rel)
    else:
        target_label = (
            None
            if _env_flag("HTMLVIDEO_VERIFY_FIX_FULL_SCAN")
            else _single_target_frame(_trim_scan_for_fix(scan_output))
        )
        was_reverted = bool(
            prior_reverted and (target_label is None or target_label in prior_reverted)
        )
        prompt = _build_fix_prompt(
            scan_output, project_dir, anti_silencing_rejected=was_reverted
        )
        key = target_label or "-"
        prompts[key] = (prompt, target_label)
        rc = _dispatch_fix(project_dir, prompt, round_no, logger, target_label)
        if rc == TRUNCATED_RC:
            truncated_labels.append(key)
    truncated_labels = _retry_truncated(
        project_dir, before_digest, prompts, truncated_labels, round_no, logger
    )
    return _require_changed_and_rescan(
        project_dir,
        before_digest,
        round_no,
        logger,
        truncated_labels=truncated_labels,
        before_snapshot=before_snapshot,
        scan_output=scan_output,
        reverted_out=reverted_files,
    )


def _require_changed_and_rescan(
    project_dir: str,
    before_digest: str,
    round_no: int,
    logger: logging.Logger,
    truncated_labels: list[str] | None = None,
    before_snapshot: dict[str, str] | None = None,
    scan_output: str | None = None,
    reverted_out: set[str] | None = None,
) -> tuple[int, str]:
    """Zero-edit guard + anti-silencing diff guard + rescan.

    If a worker hit ``TRUNCATED_RC`` AFTER writing edits to disk (2026-11-24
    6b05ba2b5d081581), accept the landed edits and rescan instead of throwing
    away a completed fix.
    """
    reverted: list[str] = []
    if before_snapshot is not None and _reject_blanket_allow_enabled():
        reverted = _detect_and_revert_blanket_allow(project_dir, before_snapshot, logger)
    after_digest = _composition_html_digest(project_dir)
    if after_digest == before_digest:
        if reverted:
            if reverted_out is not None:
                reverted_out.update(reverted)
            if scan_output is not None:
                logger.warning(
                    "verify fix round %d reverted blanket allow-* silencing in [%s] "
                    "— reusing scan findings without rescan to force CSS/GSAP layout fix",
                    round_no,
                    ", ".join(reverted),
                )
                return 1, scan_output
            logger.warning(
                "verify fix round %d reverted blanket allow-* silencing in [%s] "
                "— rescanning to force CSS/GSAP layout fix",
                round_no,
                ", ".join(reverted),
            )
            return _run_verify(project_dir, logger)
        if truncated_labels:
            joined = ", ".join(truncated_labels)
            raise RuntimeError(
                f"verify fix round {round_no} [{joined}] truncated: the model "
                "burned its output budget on thinking without emitting an Edit"
            )
        raise RuntimeError(
            f"verify fix round {round_no} made no changes to "
            "compositions/**/*.html — refusing to re-dispatch the same scan "
            "(model produced no Edit, or scan findings were non-actionable)"
        )
    if reverted_out is not None:
        reverted_out.clear()
        if reverted:
            reverted_out.update(reverted)
    if truncated_labels:
        logger.warning(
            "verify fix round %d [%s] hit truncation after writing edits to disk "
            "— proceeding to rescan",
            round_no,
            ", ".join(truncated_labels),
        )
    return _run_verify(project_dir, logger)


def _apply_visual_fix(
    project_dir: str,
    prompt: str,
    round_no: int,
    logger: logging.Logger,
) -> tuple[int, str]:
    """One pixels-fix model turn (no file split), zero-edit guard, then rescan."""
    before_digest = _composition_html_digest(project_dir)
    rc = _dispatch_fix(project_dir, prompt, round_no, logger, None)
    truncated = ["-"] if rc == TRUNCATED_RC else []
    truncated = _retry_truncated(
        project_dir, before_digest, {"-": (prompt, None)}, truncated, round_no, logger
    )
    return _require_changed_and_rescan(
        project_dir, before_digest, round_no, logger, truncated_labels=truncated or None
    )


def _env_nonneg_int(name: str, default: int, logger: logging.Logger) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default


def evaluate_soft_fail(
    scan_output: str,
    max_errors: int = _SOFT_FAIL_MAX_ERRORS,
    max_frames: int = _SOFT_FAIL_MAX_FRAMES,
) -> tuple[bool, str, list[dict]]:
    """Decide whether a mechanical gate FAIL contains ONLY bounded, non-fatal
    layout/contrast residuals eligible for a degraded warning pass.

    Returns ``(eligible, detail_or_reason, remaining_rows)``.
    """
    if max_errors <= 0 or max_frames <= 0:
        return False, "disabled", []
    if scan_looks_unactionable(scan_output):
        return False, "unactionable_scan", []
    if classify_scan_failure(scan_output) != "mechanical":
        return False, "not_mechanical", []
    payload = parse_findings_payload(scan_output)
    if not payload:
        return False, "missing_payload", []
    if int(payload.get("lint_errors") or 0) > 0:
        return False, f"lint_errors={payload.get('lint_errors')}", []
    if int(payload.get("digit_led_selectors") or 0) > 0:
        return False, f"digit_led_selectors={payload.get('digit_led_selectors')}", []
    if int(payload.get("selfcheck_other_fails") or 0) > 0:
        return False, f"selfcheck_other_fails={payload.get('selfcheck_other_fails')}", []
    if payload.get("transitions_ok") is not True:
        return False, f"transitions_ok={payload.get('transitions_ok')!r}", []

    other_cnt = int(payload.get("other_check_errors") or 0)
    non_overlap = payload.get("non_overlap_errors")
    if other_cnt > 0 and (not isinstance(non_overlap, list) or len(non_overlap) < other_cnt):
        return False, "other_check_errors_unlisted", []

    rows: list[dict] = []
    for key in ("designed_overlay", "real_collision", "ambiguous", "non_overlap_errors"):
        bucket = payload.get(key)
        if isinstance(bucket, list):
            for item in bucket:
                if isinstance(item, dict):
                    rows.append(item)
    if not rows:
        return False, "zero_check_rows", []
    if len(rows) > max_errors:
        return False, f"too_many_errors={len(rows)}>{max_errors}", []

    files: set[str] = set()
    parts: list[str] = []
    for row in rows:
        section = str(row.get("section") or "").strip().lower()
        if section == "runtime":
            return False, "runtime_error", []
        code = str(row.get("code") or "").strip()
        if code not in _SOFT_FAIL_CODES:
            return False, f"fatal_code={code or 'unknown'}", []
        sel = str(row.get("selector") or "")
        con = str(row.get("container") or "")
        if _CAPTION_WORD_SEL_RE.search(sel) or _CAPTION_WORD_SEL_RE.search(con):
            return False, f"caption_keepout={code}", []
        rel = str(row.get("file") or "").strip()
        if rel:
            files.add(rel)
        parts.append(f"{code}({rel or '?'}:{sel or '?'})")
    if len(files) > max_frames:
        return False, f"too_many_frames={len(files)}>{max_frames}", []
    return True, ", ".join(parts), rows


def _write_degraded_marker(
    project_dir: str,
    trigger: str,
    rows: list[dict],
    logger: logging.Logger,
) -> None:
    marker_path = os.path.join(project_dir, DEGRADED_MARKER_FILENAME)
    payload = {
        "status": "degraded_pass",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "count": len(rows),
        "frames": sorted({str(r.get("file") or "?") for r in rows}),
        "findings": [
            {
                "file": r.get("file", ""),
                "code": r.get("code", ""),
                "section": r.get("section", ""),
                "selector": r.get("selector", ""),
                "container": r.get("container", ""),
            }
            for r in rows
        ],
    }
    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("failed to write %s: %s", marker_path, exc)


def _try_degraded_pass(
    project_dir: str,
    scan_output: str,
    trigger: str,
    logger: logging.Logger,
) -> tuple[str | None, int, str]:
    """Attempt a warning-only degraded pass if only bounded non-fatal mechanical
    findings remain AND the visual pixel gate passes.

    Returns ``(contact_sheet_or_None, visual_rc, visual_output)``.
    ``visual_rc == -1`` means the mechanical scan was not eligible for soft-fail.
    """
    max_errors = _env_nonneg_int(
        "HTMLVIDEO_VERIFY_SOFT_FAIL_MAX_ERRORS", _SOFT_FAIL_MAX_ERRORS, logger
    )
    max_frames = _env_nonneg_int(
        "HTMLVIDEO_VERIFY_SOFT_FAIL_MAX_FRAMES", _SOFT_FAIL_MAX_FRAMES, logger
    )
    eligible, detail, rows = evaluate_soft_fail(
        scan_output, max_errors=max_errors, max_frames=max_frames
    )
    if not eligible:
        logger.info("verify soft-fail declined (%s): %s", trigger, detail)
        return None, -1, ""
    logger.warning(
        "verify mechanical soft-fail eligible (%s, %d finding(s): %s) — running "
        "visual gate (--phase visual) to confirm pixels",
        trigger,
        len(rows),
        detail,
    )
    v_rc, v_out = _run_visual_only(project_dir, logger)
    if v_rc == 0:
        sheet = _validate_verify_outputs(project_dir)
        _write_degraded_marker(project_dir, trigger, rows, logger)
        frames = sorted({str(r.get("file") or "?") for r in rows})
        logger.warning(
            "VERIFY_DEGRADED_PASS trigger=%s count=%d frames=%s findings=[%s] contact_sheet=%s",
            trigger,
            len(rows),
            ",".join(frames),
            detail,
            sheet,
        )
        return sheet, 0, v_out
    logger.warning(
        "verify soft-fail blocked by visual gate rc=%d (%s)", v_rc, trigger
    )
    return None, v_rc, v_out


def verify_frame(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Verify the assembled video via a deterministic scan + model-driven fixes.

    ``project_dir`` is the HyperFrames project root and must already have
    ``hyperframes.json``, ``index.html``, ``STORYBOARD.md``, and
    ``compositions/frames/*.html``. Raises on missing prerequisites, a blocker,
    or a missing contact sheet. Returns the path to
    ``snapshots/contact-sheet.jpg`` (or a comma-joined list when paginated).

    Control flow is Python-driven, not model-driven: ``verify-all.mjs --phase verify`` runs as
    a plain subprocess (no claude), so a clean project (both gates pass) never
    invokes the model at all. Exit 1 is one of two gates, and each gets at most
    one batched model turn before the scan re-runs, up to ``_MAX_FIX_ROUNDS``:

    - mechanical (``GATE: FAIL`` / ``report exit 1``, no ``VISUAL_GATE:``) —
      ``designed_overlay`` auto-overlap, then the actionable-summary prompt.
    - visual (``VISUAL_GATE: FAIL``) — pixels fix prompt only. No auto-overlap
      and no mechanical playbook. Same round cap and zero-edit digest guard.

    On a mechanical FAIL, ``designed_overlay`` rows from the scan's
    ``VERIFY_FINDINGS_JSON`` line with ``autofix_ok`` + ``mark_side`` are
    fixed deterministically on that side only (attribute
    insert + rescan); a scan that then passes never reaches the model. The
    rescan after the last model round is checked too, with one more auto-fix
    pass if it still fails and the failure is not visual.
    ``HTMLVIDEO_VERIFY_AUTO_OVERLAP=0`` disables this.

    ``verify-all.mjs --phase prepare`` runs once per session (known-failure
    autofix, then ``font-imports.mjs --scope all`` — non-fatal;
    ``HTMLVIDEO_FONT_IMPORTS=0/false/off/no`` skips it — then idempotent
    transitions inject). A non-zero prepare result raises immediately and does
    not call the model. Residuals are what the fix prompt is allowed to hand-edit.
    """
    _validate_prerequisites(project_dir)
    max_fix = _env_positive_int("HTMLVIDEO_VERIFY_FIX_ROUNDS", _MAX_FIX_ROUNDS, logger)

    prep_rc, prep_out = _run_prepare(project_dir, logger)
    if prep_rc != 0:
        raise RuntimeError(
            f"verify-all prepare failure rc:{prep_rc} (not a model fix): " + prep_out[-2000:]
        )

    rc, output = _run_verify(project_dir, logger)
    reverted_files: set[str] = set()
    for round_no in range(1, max_fix + 1):
        if rc == 0:
            logger.info(
                "verify-all gate PASS (%s) — snapshots written, no model needed",
                "first scan" if round_no == 1 else f"round {round_no}",
            )
            return _validate_verify_outputs(project_dir)
        if rc == 2:
            raise RuntimeError(
                "verify-all infrastructure failure: " + output[-2000:]
            )
        # Visual FAIL: pixels only. Do not run designed_overlay auto-overlap
        # and do not use the mechanical actionable-summary prompt.
        if classify_scan_failure(output) != "visual":
            if auto_overlap_enabled():
                rc, output = _auto_fix_overlaps(project_dir, rc, output, round_no, logger)
                if rc == 0:
                    logger.info(
                        "verify-all gate PASS after designed_overlay auto-fix "
                        "(round %d) — no model needed",
                        round_no,
                    )
                    return _validate_verify_outputs(project_dir)
                if rc == 2:
                    raise RuntimeError(
                        "verify-all infrastructure failure: " + output[-2000:]
                    )
            if classify_scan_failure(output) != "visual":
                # rc == 1 but nothing actionable → do not burn a model turn
                # (parser miss / empty playbook). Treat as infrastructure.
                if scan_looks_unactionable(output):
                    raise RuntimeError(
                        "verify-all gate FAIL with 0 actionable findings — refusing to "
                        "invoke the model on an empty fix list (upstream scan/parser "
                        "bug). Tail:\n" + output[-2000:]
                    )
                logger.info(
                    "verify-all mechanical findings (round %d) — invoking claude for batched fixes",
                    round_no,
                )
                try:
                    rc, output = _apply_model_fix(
                        project_dir, output, round_no, logger, reverted_files=reverted_files
                    )
                except RuntimeError as exc:
                    msg = str(exc)
                    if "made no changes" in msg or "truncated:" in msg:
                        sheet, v_rc, v_out = _try_degraded_pass(
                            project_dir, output, f"round_{round_no}_stalled", logger
                        )
                        if sheet is not None:
                            return sheet
                        if (
                            v_rc == 1
                            and round_no < max_fix
                            and classify_scan_failure(v_out) == "visual"
                        ):
                            logger.warning(
                                "verify soft-fail visual gate FAIL on round %d — "
                                "handing off to visual fix loop",
                                round_no,
                            )
                            rc, output = 1, v_out
                            continue
                    raise
                continue
        logger.info(
            "verify-all visual findings (round %d) — invoking claude for pixels fix",
            round_no,
        )
        prompt = _build_visual_fix_prompt(output, project_dir)
        rc, output = _apply_visual_fix(project_dir, prompt, round_no, logger)

    if (
        rc == 1
        and auto_overlap_enabled()
        and classify_scan_failure(output) != "visual"
    ):
        rc, output = _auto_fix_overlaps(project_dir, rc, output, max_fix + 1, logger)
    if rc == 0:
        logger.info("verify-all gate PASS after final rescan — snapshots written")
        return _validate_verify_outputs(project_dir)
    if rc == 2:
        raise RuntimeError("verify-all infrastructure failure: " + output[-2000:])
    if rc == 1:
        sheet, _v_rc, _v_out = _try_degraded_pass(
            project_dir, output, f"max_rounds_{max_fix}", logger
        )
        if sheet is not None:
            return sheet
    raise RuntimeError(
        f"verify-frame gate still failing after {max_fix} fix rounds"
    )