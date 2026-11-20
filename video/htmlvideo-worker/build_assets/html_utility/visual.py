# -*- coding: utf-8 -*-
"""Frame visual design helpers for htmlvideo worker.

Deterministic fan-out (mirrors frame_html.py): a single "design contract"
session produces the video-wide `## Video direction` + per-frame blueprint
assignment, then N parallel workers each append ONE frame's shot-sequence lines
(blueprint/focal/roles/sfx/Adapt/Scene) to its OWN fragment file. Python re-
assembles STORYBOARD.md, so the narrative fields are preserved byte-exact by
construction (the model never re-types them). Rollback: HTMLVIDEO_FRAME_VISUAL_DRIVER=0.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from html_utility.claude_cli import _env_positive_int, run_claude, run_claude_result

STORYBOARD_FILENAME = "STORYBOARD.md"
FRAME_FILENAME = "frame.md"
HYPERFRAMES_JSON = "hyperframes.json"
VIDEO_DIRECTION_HEADING = "## Video direction"

VISUAL_DRIVER_ENV = "HTMLVIDEO_FRAME_VISUAL_DRIVER"
VISUAL_CONCURRENCY_ENV = "HTMLVIDEO_FRAME_VISUAL_MAX_CONCURRENCY"
DEFAULT_MAX_WORKERS = 16
MAX_RETRY_ROUNDS = 1

DESIGN_FILE = os.path.join(".hyperframes", "visual", "_design.md")
ANIMATION_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "skills", "hyperframes-animation")

_SPLIT_RE = re.compile(r"(?=^## Frame\s+\d+)", re.M)
_FRAME_HEAD_RE = re.compile(r"^## Frame\s+(\d+)", re.M)
_SCENES_RE = re.compile(r"^Scene\s+\d+", re.M)
_FOCAL_RE = re.compile(r"^-\s*focal:", re.M)
_ROLES_RE = re.compile(r"^-\s*roles:", re.M)
_DONE_RE = re.compile(r"\bdone\s+OK\s+(\d+)\b")


def _env_on(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


def _split_storyboard(text: str):
    """Return (preamble, blocks{num:block}, order[num...]) from STORYBOARD text.

    The preamble is everything before the first `## Frame N` (the YAML front
    matter; on an outline board it has no `## Video direction` yet). Blocks are
    keyed by frame number, order preserves document order.
    """
    parts = _SPLIT_RE.split(text)
    preamble = parts[0].rstrip()
    blocks: dict[str, str] = {}
    order: list[str] = []
    for part in parts[1:]:
        m = _FRAME_HEAD_RE.match(part)
        if not m:
            continue
        blocks[m.group(1)] = part.strip()
        order.append(m.group(1))
    return preamble, blocks, order


def _frame_md_tokens(frame_md: str) -> str:
    """Extract the frame.md frontmatter `colors:` + `typography:` blocks (the exact
    hex/font VALUES). Mirrors frame-packets-core.mjs `frontmatterBlock`: the values
    live in YAML frontmatter, while the `## Colors` / `## Typography` narrative
    sections only reference them symbolically ({colors.cream}) — so a No-tools
    design phase needs them inlined or it hallucinates the palette."""
    if not frame_md:
        return ""
    m = re.search(r"^---\r?\n([\s\S]*?)\r?\n---", frame_md, re.M)
    if not m:
        return ""
    lines = m.group(1).split("\n")
    _top_key = re.compile(r"^[A-Za-z][\w .-]*:")
    out = []
    for key in ("colors", "typography"):
        idx = next((i for i, l in enumerate(lines) if l.startswith(key + ":")), None)
        if idx is None:
            continue
        block = [lines[idx]]
        for j in range(idx + 1, len(lines)):
            line = lines[j]
            if line.strip() and not line[:1].isspace() and _top_key.match(line):
                break
            block.append(line)
        out.append("\n".join(block).rstrip())
    return "\n\n".join(out).strip()


def _build_design_prompt(preamble: str, frame_nums: list[str], tokens: str = "") -> str:
    joined = ", ".join(frame_nums)
    intro = (
        "The frame.md design tokens are already inlined below — do NOT read frame.md. "
        "Use them and the outline below." if tokens else
        "Read frame.md (design truth: palette/type/tokens) and the outline below."
    )
    return (
        "/aiflow-build-frame-visual\n\n"
        "You are the GLOBAL design phase only. " + intro + "\n"
        + (f"{tokens}\n" if tokens else "")
        + "Produce ONLY two sections, in this order:\n\n"
        "## Video direction\n"
        "<the video-wide palette system (from frame.md, never invented), motion grammar, "
        "rhythm/held-frames (name the SPECIFIC frame numbers that are breathers vs "
        "reveal cascade), caption band, and negative list>\n\n"
        "## Blueprint assignment\n"
        f"<one line per frame, frames are: {joined}. Format: `F<num> = <blueprint> "
        "(Adapt|Reproduce)`>\n\n"
        "Output these two sections as your FINAL message text (no tools, no Write, no "
        "per-frame Scene lines — the per-frame workers do those). flow is automation → "
        "autonomous.\n\n"
        "── outline preamble ──\n"
        + preamble
        + "\n── end outline ──\n"
    )


def _split_design(design: str):
    """Split the Phase-A contract on `## Blueprint assignment` → (direction, assignment)."""
    idx = design.find("## Blueprint assignment")
    if idx == -1:
        return design, ""
    return design[:idx].strip(), design[idx:].strip()


def _frame_blueprint(assignment: str, frame_num: str) -> str:
    """The frame's blueprint id from its `F<num> = <id> (…)` assignment line."""
    m = re.search(rf"^F{frame_num}\s*=\s*([\w-]+)", assignment, re.M)
    return m.group(1) if m else ""


def _frame_assignment_line(assignment: str, frame_num: str) -> str:
    m = re.search(rf"^F{frame_num}\s*=.*$", assignment, re.M)
    return m.group(0) if m else ""


def _blueprint_body(blueprint_id: str) -> str:
    """Inline the frame's blueprint body (removes a worker Read); empty for `compose`."""
    if not blueprint_id or blueprint_id.lower() == "compose":
        return ""
    path = os.path.join(ANIMATION_DIR, "blueprints", f"{blueprint_id}.md")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _build_worker_prompt(
    frame_num: str,
    block: str,
    design: str,
    blueprint_body: str = "",
) -> str:
    bp_section = (
        "\n[SELECTED BLUEPRINT — instantiate this shape, keep its signature move]\n"
        + blueprint_body
        if blueprint_body else ""
    )
    return (
        "/aiflow-build-frame-visual\n\n"
        "You are ONE per-frame worker for the visual design phase. Your single job is "
        f"to design Frame {frame_num}'s shot sequence and output ONLY the lines to "
        "APPEND after the frame's existing block — never re-type any existing field.\n\n"
        "Output, in this exact order, as your FINAL message text:\n"
        f"  done OK {frame_num}\n"
        "  - blueprint: <blueprint (Adapt|Reproduce)>        (from the assignment below)\n"
        "  - focal: <the invented focal element>\n"
        "  - roles: <focal = foreground · ... · ... = background wallpaper>\n"
        "  - sfx: <soft sfx names, comma-separated>\n"
        "  <an 'Adapt:'/'Reproduce:' line only when the blueprint needs instantiation>\n"
        "  Scene 1 (0.0–Xs): <layout + motion, inline vocab, from-state-to-reveal>\n"
        "  Scene 2 (...): ...  (≥2 Scenes, monotonic windows, coverage ≥90% of duration, "
        "first starts at 0.0 — voiceover-paced, never front-loaded)\n\n"
        "Do NOT emit the `- scene`, `- voiceover`, `- duration`, `- transition_in`, "
        "`- status`, `- src`, `- type`, `- persuasion`, or `- beat` lines — the harness "
        "keeps those byte-exact. Do NOT Write or edit any file, do NOT redesign other "
        f"frames. Use valid motion/blueprint vocabulary only. The existing frame block "
        "(read-only context, do not echo it back):\n\n"
        "[CONTEXT]\n"
        + block
        + "\n\n[DESIGN CONTRACT]\n"
        + design
        + bp_section
        + "\n"
    )


def _design_contract_split(design_text: str):
    """Split the Phase-A reply into (direction_block, rest).

    ``direction_block`` is the `## Video direction` section plus the blueprint
    assignment — both are inlined verbatim into every worker prompt as the
    shared design contract (cross-frame consistency anchor).
    """
    text = design_text.strip()
    idx = text.find(VIDEO_DIRECTION_HEADING)
    if idx == -1:
        raise RuntimeError("design contract missing ## Video direction")
    direction_block = text[idx:].strip()
    return direction_block


def _parse_worker_additions(frame_num: str, text: str) -> str | None:
    """Validate a worker reply and return the additions block (without the
    `done OK` marker). Returns None when the reply failed its completion gate."""
    if not text:
        return None
    if not _SCENES_RE.search(text):
        return None
    if not _FOCAL_RE.search(text) or not _ROLES_RE.search(text):
        return None
    done = _DONE_RE.search(text)
    if not done or done.group(1) != frame_num:
        return None
    # Strip the `done OK <num>` marker line; the rest is the additions.
    body = _DONE_RE.sub("", text, count=1).strip()
    return body if body else None


def _dispatch_note(rc: int, text: str) -> str:
    if rc == 124:
        return "prior attempt hit its wall-clock timeout"
    if rc != 0:
        return f"prior attempt exited non-zero (rc={rc})"
    tail = (text or "").strip()[-200:]
    return "(prior attempt produced no valid additions; last words: " + (tail or "∅") + ")"


def _legacy_build_frame_visual(project_dir: str, logger: logging.Logger) -> str:
    """Original single-session flow (rollback when HTMLVIDEO_FRAME_VISUAL_DRIVER=0)."""
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    prompt = (
        "/aiflow-build-frame-visual\n\n"
        "Work in this HyperFrames project directory. hyperframes.json, frame.md, and an "
        "outline-stage STORYBOARD.md already exist — do not re-init, do not invent a "
        "storyboard, do not write HTML, do not run audio, assemble index, or render.\n\n"
        "Follow the aiflow-build-frame-visual skill: enrich STORYBOARD.md with time-coded "
        "shot sequences and a ## Video direction block (skip sketch; autonomous). Do not "
        "mark frames animated; do not write compositions/frames/*.html.\n\n"
        "flow is automation → autonomous: proceed without waiting for sketch confirmation. "
        "Stop when the skill gate passes (shot sequences + ## Video direction).\n"
    )
    rc = run_claude(project_dir, prompt, logger, skill="visual")
    if rc != 0:
        raise RuntimeError(f"claude build-frame-visual failed rc:{rc}")
    return storyboard_path


def _build_frame_visual_driver(project_dir: str, logger: logging.Logger) -> str:
    log = logger.info if logger else (lambda *a, **k: None)
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    with open(storyboard_path, "r", encoding="utf-8") as f:
        text = f.read()
    preamble, blocks, order = _split_storyboard(text)
    if not order:
        raise RuntimeError("STORYBOARD.md has no ## Frame N blocks")

    # ── Phase A: global design contract (one session) ─────────────────────
    log("visual driver: design-contract phase (%d frames)", len(order))
    frame_md = ""
    try:
        with open(os.path.join(project_dir, FRAME_FILENAME), "r", encoding="utf-8") as f:
            frame_md = f.read()
    except OSError:
        pass
    design_prompt = _build_design_prompt(preamble, order, _frame_md_tokens(frame_md))
    drc, design_text = run_claude_result(
        project_dir, design_prompt, logger, skill="visual", label="design"
    )
    if drc != 0:
        raise RuntimeError(f"claude visual design-contract failed rc:{drc}")
    design = _design_contract_split(design_text)
    design_dir = os.path.join(project_dir, ".hyperframes", "visual")
    os.makedirs(design_dir, exist_ok=True)
    with open(os.path.join(design_dir, "_design.md"), "w", encoding="utf-8") as f:
        f.write(design + "\n")
    log("visual driver: design contract ready (%d chars)", len(design))

    # ── Phase B: parallel per-frame workers ───────────────────────────────
    max_workers = _env_positive_int(
        VISUAL_CONCURRENCY_ENV, DEFAULT_MAX_WORKERS, logger)
    remaining = list(order)
    additions: dict[str, str] = {}
    notes: dict[str, str] = {}

    # Input reduction (mirrors frame-html P4): give each worker only the shared
    # Video direction + ITS OWN blueprint assignment line (drop the other frames'
    # lines), and inline its blueprint body so it does not Read the blueprint file.
    direction, assignment = _split_design(design)

    for round_no in range(1 + MAX_RETRY_ROUNDS):
        def run_one(fn):
            bp = _frame_blueprint(assignment, fn)
            bp_body = _blueprint_body(bp)
            line = _frame_assignment_line(assignment, fn) or f"F{fn} = compose"
            trimmed = f"{direction}\n\n## Blueprint assignment\n{line}"
            prompt = _build_worker_prompt(fn, blocks[fn], trimmed, bp_body)
            if notes.get(fn):
                prompt += f"\n[RE-DISPATCH] {notes[fn]} — output the additions now."
            rc, reply = run_claude_result(
                project_dir, prompt, logger, skill="visual", label=f"F{fn}")
            addition = _parse_worker_additions(fn, reply)
            return fn, rc, addition

        failures = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fn, rc, addition in pool.map(run_one, remaining):
                if addition is not None:
                    additions[fn] = addition
                    remaining.remove(fn)
                    log("worker ok F%s (round %s)", fn, round_no)
                else:
                    failures[fn] = (rc, addition)
                    log("worker fail F%s rc=%s (round %s)", fn, rc, round_no)
        if not remaining:
            break
        notes = {fn: _dispatch_note(rc, "") for fn, (rc, _) in failures.items()}
        log("visual retry round %s pending=%s", round_no + 1, len(remaining))
    if remaining:
        raise RuntimeError(
            "frames still missing visual design after "
            f"{1 + MAX_RETRY_ROUNDS} rounds: {sorted(remaining)}")

    # ── Merge (deterministic; narrative fields never re-typed) ────────────
    parts = [preamble, "", design, ""]
    for fn in order:
        parts.append(blocks[fn])
        parts.append("")
        parts.append(additions[fn])
        parts.append("")
    assembled = "\n".join(parts).strip() + "\n"
    with open(storyboard_path, "w", encoding="utf-8") as f:
        f.write(assembled)
    log("visual driver: merged STORYBOARD (%d chars)", len(assembled))

    # ── Gate: storyboard-gate.mjs (deterministic, ~1s) ────────────────────
    gate = subprocess.run(
        ["node", "/app/scripts/storyboard-gate.mjs", "--videodir", project_dir],
        capture_output=True, text=True, timeout=120,
    )
    if gate.returncode != 0:
        raise RuntimeError(
            "storyboard-gate failed after visual merge:\n"
            + (gate.stdout or "") + (gate.stderr or ""))
    return storyboard_path


def _validate_frame_visual_outputs(project_dir: str) -> str:
    """Post-check the final STORYBOARD carries the video-wide direction block."""
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path) or os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md missing/empty after visual: {storyboard_path}")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        content = f.read()
    if VIDEO_DIRECTION_HEADING not in content:
        raise ValueError(
            f"STORYBOARD.md missing {VIDEO_DIRECTION_HEADING!r} after visual: {storyboard_path}"
        )
    return storyboard_path


def build_frame_visual(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """Add time-coded shot sequences + `## Video direction` to STORYBOARD.md.

    Two paths (env-gated):
      - ``HTMLVIDEO_FRAME_VISUAL_DRIVER=0`` → legacy single-session flow.
      - otherwise (default) → deterministic fan-out: one design-contract
        session + N parallel per-frame workers + byte-exact merge + gate.

    ``project_dir`` must have ``hyperframes.json``, ``frame.md``, and an
    outline-stage ``STORYBOARD.md``. Raises on missing prerequisites, CLI
    failure, or gate failure. Returns the path to ``STORYBOARD.md``.
    """
    hyperframes_path = os.path.join(project_dir, HYPERFRAMES_JSON)
    frame_path = os.path.join(project_dir, FRAME_FILENAME)
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(hyperframes_path):
        raise FileNotFoundError(f"hyperframes.json missing: {hyperframes_path}")
    if not os.path.isfile(frame_path):
        raise FileNotFoundError(f"frame.md missing: {frame_path}")
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        content = f.read()
    if VIDEO_DIRECTION_HEADING in content:
        raise ValueError(
            f"STORYBOARD.md already has {VIDEO_DIRECTION_HEADING!r}: {storyboard_path} "
            "(design already consumed — do not redesign)"
        )

    if _env_on(VISUAL_DRIVER_ENV):
        _build_frame_visual_driver(project_dir, logger)
    else:
        _legacy_build_frame_visual(project_dir, logger)
    return _validate_frame_visual_outputs(project_dir)