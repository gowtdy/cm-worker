# -*- coding: utf-8 -*-
"""Verify assembled video helpers for htmlvideo worker.

``verify-all.mjs`` step 0 runs ``font-imports.mjs`` (scope=all) before the
transitions inject guard. That step is non-fatal and is skipped when
``HTMLVIDEO_FONT_IMPORTS`` is ``0`` / ``false`` / ``off`` / ``no``.
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import subprocess

from html_utility.claude_cli import _env_positive_int, run_claude

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


def _run_verify_all(project_dir: str, logger: logging.Logger) -> tuple[int, str]:
    """Deterministic mechanical scan (no model). Returns ``(rc, output)``.

    ``rc`` mirrors ``verify-all.mjs`` / ``verify-report.mjs``:
      0 = gate pass + snapshot written (+ pixels clean)
      1 = findings to fix
      2 = infrastructure / project blocker

    The model never runs the scan itself — running it here removes the model's
    per-turn orchestration tax (TTFB + turn latency) and the redundant
    find/cat/ls exploration turns it spent re-discovering the playbook.
    """
    cmd = ["node", VERIFY_ALL_SCRIPT, "--videodir", project_dir]
    logger.info("verify-all scan: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True, timeout=900
        )
    except subprocess.TimeoutExpired:
        logger.error("verify-all scan timed out")
        return 2, "verify-all.mjs timed out"
    out = proc.stdout or ""
    if out and not out.endswith("\n"):
        out += "\n"
    if proc.stderr:
        out += "\n[stderr]\n" + proc.stderr
    logger.info("verify-all scan done rc=%d (out %d bytes)", proc.returncode, len(out))
    return proc.returncode, out


# The mechanical fix rules are inlined instead of loading the whole
# /aiflow-verify-frame skill — the skill's "Verify" section tells the model to
# run verify-all/lint/check, which is dead weight for the fix role (the harness
# already did the scan). A ~10KB skill load blew the fix session to ~93K input
# tokens; this cheatsheet is ~500 tokens and keeps the fix self-contained.
_FIX_PLAYBOOK = """Mechanical fix rules — apply the matching one, then stop:
- digit-led selectors (`#07-…`, `.01-…`, `"#06-…"` inside CSS/JS selector strings)
  → attribute form `[id="…"]` / `[class~="…"]`; leave `getElementById` untouched.
- `font_family_without_font_face` → residual only (verify-all step 0 already
  ran font-imports). Drop dead fallback names (e.g. Didot) that are not the
  primary family; if a ramp family is still missing, paste the marked block
  (both `aiflow:font-imports` marker lines) printed in the scan's actionable
  summary. Do not add `@font-face` or hunt font files.
- root/clip `data-duration="Ns"` (unit suffix) → strip to plain number
  `data-duration="N"`; do NOT diagnose transitions by reading scripts.
- `content_overlap` → follow the scan's triage line: `designed_overlay` (badge /
  stamp / overlay / watermark …) → `data-layout-allow-overlap` on the text or an
  inner container (not the scene root); `real_collision` (text vs text) →
  reposition / shrink, do NOT allow-overlap; `ambiguous` → read the frame's
  layout intent first, then pick one. Never blanket-allow every overlap.
- caption `text_box_overflow` (~1–4px on `#caption-word-*` / `.caption-line`) → false positive, ignore.
- `id_requires_css_escape` / `google_fonts_import` warnings → non-blockers, ignore.
- transitions verify / inject mismatches → only strip duration unit suffixes if
  present; harness re-injects on the next scan. Do NOT re-run inject yourself.
- any finding not listed above → cheapest safe edit, one line of note."""

_FIX_HARD_CONSTRAINTS = """Hard constraints for this fix role (override project CLAUDE.md / plugins):
- Do NOT Read `/app/scripts/*`, `/app/hyperframes/**`, or any CLI/gate source.
- Do NOT invoke the Skill tool; do NOT explore the codebase first.
- Do NOT run verify-all / lint / check / snapshot / `npm run check`.
- Project CLAUDE.md "Always invoke skill" / "ALWAYS RUN check" do NOT apply here.
- Edit `compositions/**/*.html` only, in ONE batched pass (parallel Edits), then stop.
"""


def _build_fix_prompt(scan_output: str) -> str:
    """Prompt for the one model turn that is still genuinely needed: apply the
    mechanical fixes the scan already enumerated. The scan output is inlined so
    the model edits directly instead of re-running the scan / re-investigating,
    and the playbook cheatsheet replaces the full skill load.
    """
    return (
        "Fix the verification findings below in this HyperFrames project root. "
        "The deterministic scan (`node /app/scripts/verify-all.mjs`) has ALREADY "
        "run and returned gate findings (exit 1); its output — findings plus a "
        "ready-to-use «actionable summary» — is appended.\n\n"
        + _FIX_HARD_CONSTRAINTS
        + "\nApply the cheapest safe fix to compositions/frames/NN-*.html in ONE "
        "batched pass, using absolute paths. Do NOT re-run verify-all / lint / "
        "check / snapshot (the harness re-runs the scan itself); do NOT re-init, "
        "build, assemble, preview or render.\n\n"
        + _FIX_PLAYBOOK
        + "\n\nStop after the fixes are applied, then list — one line each — what you changed.\n\n"
        "── scan output (already run — do not re-run) ──\n"
        + scan_output
        + "\n── end scan output ──\n"
    )


_GATE_FAIL_RE = re.compile(r"^GATE:\s*FAIL\b", re.M)
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

    Control flow is Python-driven, not model-driven: ``verify-all.mjs`` runs as
    a plain subprocess (no claude), so a clean project never invokes the model
    at all. Only when the scan reports findings (exit 1) does the model get a
    single batched-fix turn, after which the scan re-runs; this repeats up to
    ``_MAX_FIX_ROUNDS``.

    Step 0 of that scan is ``font-imports.mjs --scope all`` (non-fatal;
    ``HTMLVIDEO_FONT_IMPORTS=0/false/off/no`` skips it). Residuals are what
    the fix prompt is allowed to hand-edit.
    """
    _validate_prerequisites(project_dir)
    max_fix = _env_positive_int("HTMLVIDEO_VERIFY_FIX_ROUNDS", _MAX_FIX_ROUNDS, logger)

    rc, output = _run_verify_all(project_dir, logger)
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
        # rc == 1 but nothing actionable → do not burn a model turn
        # (parser miss / empty playbook). Treat as infrastructure.
        if scan_looks_unactionable(output):
            raise RuntimeError(
                "verify-all gate FAIL with 0 actionable findings — refusing to "
                "invoke the model on an empty fix list (upstream scan/parser "
                "bug). Tail:\n" + output[-2000:]
            )
        # rc == 1: findings. One model turn applies the mechanical fixes.
        logger.info(
            "verify-all findings (round %d) — invoking claude for batched fixes",
            round_no,
        )
        before_digest = _composition_html_digest(project_dir)
        prompt = _build_fix_prompt(output)
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
        rc, output = _run_verify_all(project_dir, logger)

    raise RuntimeError(
        f"verify-frame gate still failing after {_MAX_FIX_ROUNDS} fix rounds"
    )