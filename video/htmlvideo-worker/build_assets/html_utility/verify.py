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

import glob
import hashlib
import logging
import os
import re
import subprocess

from html_utility.claude_cli import _env_positive_int, run_claude
from html_utility.verify_overlap_autofix import (
    auto_overlap_enabled,
    autofix_designed_overlays,
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
  `text_box_overflow` finding are non-blockers — do not act on them."""

_FIX_HARD_CONSTRAINTS = """Hard constraints for this fix role (override project CLAUDE.md / plugins):
- Do NOT Read `/app/scripts/*`, `/app/hyperframes/**`, or any CLI/gate source.
- Do NOT invoke the Skill tool; do NOT explore the codebase first.
- Do NOT run verify-all / lint / check / snapshot / `npm run check`.
- Project CLAUDE.md "Always invoke skill" / "ALWAYS RUN check" do NOT apply here.
- Edit `compositions/**/*.html` only, in ONE batched pass (parallel Edits), then stop.
- Read each target file (absolute path) at most ONCE, then Edit it. Do NOT
  Grep / Glob / list directories or open files the findings do not name.
"""

_FIX_REPLY = (
    "After the Edits, reply with exactly `fixed <n>` (n = number of edits), or "
    "`fixed 0` if nothing to do — no summary."
)

_ACTIONABLE_HEADER = "── actionable summary ──"
_ERROR_LINE_RE = re.compile(r"^\s+✗ ")


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


def _build_fix_prompt(scan_output: str, project_dir: str = "") -> str:
    """Prompt for the one model turn that is still genuinely needed: apply the
    mechanical fixes the scan already enumerated. Only the actionable part of
    the scan is inlined (``HTMLVIDEO_VERIFY_FIX_FULL_SCAN=1`` restores the full
    output) so the model edits directly instead of re-investigating.
    """
    if _env_flag("HTMLVIDEO_VERIFY_FIX_FULL_SCAN"):
        scan_text, label = strip_findings_payload(scan_output), "scan output"
    else:
        scan_text, label = _trim_scan_for_fix(scan_output), "scan findings"
    root_line = f"Project root (absolute): {project_dir}\n\n" if project_dir else ""
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
        f"── {label} (already run — do not re-run) ──\n"
        + scan_text
        + f"\n── end {label} ──\n"
    )


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
        "collapse means layout collapse; empty means missing content. "
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
    """Deterministically mark safe ``designed_overlay`` rows, then rescan if
    any file was written. Returns the (possibly unchanged) ``(rc, output)``;
    an auto-fix crash is logged and never fails verify.
    """
    try:
        result = autofix_designed_overlays(project_dir, output, logger)
    except Exception:
        logger.exception("auto-overlap (round %d) crashed — continuing without it", round_no)
        return rc, output
    if not result.changed:
        return rc, output
    logger.info(
        "auto-overlap (round %d): marked %d node(s) in %d file(s) [%s] for %d "
        "designed_overlay row(s); %d unresolved — rescanning",
        round_no, result.nodes_marked, len(result.files_changed),
        ", ".join(result.files_changed), result.handled, len(result.unresolved),
    )
    return _run_verify(project_dir, logger)


def _apply_model_fix(
    project_dir: str,
    prompt: str,
    round_no: int,
    logger: logging.Logger,
) -> tuple[int, str]:
    """One batched model turn, then rescan. Raises on a zero-edit digest."""
    before_digest = _composition_html_digest(project_dir)
    fix_rc = run_claude(project_dir, prompt, logger, skill="verify")
    if fix_rc != 0:
        raise RuntimeError(f"claude verify fix failed rc:{fix_rc}")
    after_digest = _composition_html_digest(project_dir)
    if after_digest == before_digest:
        raise RuntimeError(
            f"verify fix round {round_no} made no changes to "
            "compositions/**/*.html — refusing to re-dispatch the same scan "
            "(model produced no Edit, or scan findings were non-actionable)"
        )
    return _run_verify(project_dir, logger)


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
                prompt = _build_fix_prompt(output, project_dir)
                rc, output = _apply_model_fix(project_dir, prompt, round_no, logger)
                continue
        logger.info(
            "verify-all visual findings (round %d) — invoking claude for pixels fix",
            round_no,
        )
        prompt = _build_visual_fix_prompt(output, project_dir)
        rc, output = _apply_model_fix(project_dir, prompt, round_no, logger)

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
    raise RuntimeError(
        f"verify-frame gate still failing after {max_fix} fix rounds"
    )