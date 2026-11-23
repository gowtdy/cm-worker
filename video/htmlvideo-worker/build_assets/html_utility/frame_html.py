# -*- coding: utf-8 -*-
"""Frame HTML build helpers for htmlvideo worker."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from html_utility.claude_cli import (
    CANCELLED_RC,
    TRUNCATED_RC,
    _ALLOWED_EFFORTS,
    _env_positive_int,
    _resolve_profile,
    run_claude,
    run_claude_result,
)
from html_utility.fonts import run_font_imports

STORYBOARD_FILENAME = "STORYBOARD.md"
FRAME_FILENAME = "frame.md"
HYPERFRAMES_JSON = "hyperframes.json"
INDEX_FILENAME = "index.html"
VIDEO_DIRECTION_HEADING = "## Video direction"
FRAME_PACKETS_DIR = os.path.join(".hyperframes", "frame-packets")
FRAME_PACKETS_ROLE = "_role.md"
STATUS_RE = re.compile(r"^- status:\s*(\w+)", re.MULTILINE)
_LOGGER = logging.getLogger(__name__)

# legacy path — only used when HTMLVIDEO_FRAME_HTML_DRIVER=0.
# Project-local worker agent definition. The orchestrator must dispatch frame
# workers as this agent type (never the built-in catch-all): on CLI >= 2.1.325
# the built-in agent path skips the CLAUDE_CODE_SUBAGENT_EFFORT default
# (binary: $cs() early-returns on source "built-in"), so the cap only holds
# for a non-built-in definition. The frontmatter effort pin is the primary
# lever; the env injection in build_frame_html() is the fallback (it applies
# to any non-built-in agent without an explicit effort).
FRAME_WORKER_AGENT_NAME = "frame-html-worker"
FRAME_WORKER_AGENT_MARKDOWN = """---
name: frame-html-worker
description: Per-frame worker for the aiflow-build-frame-html skill. Writes exactly one compositions/frames/<frame_id>.html from its dispatch packet (role file + frame packet + dispatch context), runs the frame-selfcheck gate, replies `done OK <frame_id>` or `fail <frame_id>: <reason>`. Never edits STORYBOARD.md, never touches other frames.
effort: high
---

You are one per-frame HTML worker. Your dispatch prompt is authoritative — read it fully, then build. The aiflow role document (`_role.md` in your packet) carries the complete contract.
"""


# legacy path — only used when HTMLVIDEO_FRAME_HTML_DRIVER=0.
def _ensure_frame_worker_agent(project_dir: str) -> str:
    """Write the project-local worker agent definition (idempotent,
    content-checked) so the orchestrator dispatches a non-built-in agent with
    pinned effort. Returns the agent file path.
    """
    agents_dir = os.path.join(project_dir, ".claude", "agents")
    os.makedirs(agents_dir, exist_ok=True)
    agent_path = os.path.join(agents_dir, f"{FRAME_WORKER_AGENT_NAME}.md")
    needs_write = True
    if os.path.isfile(agent_path):
        with open(agent_path, "r", encoding="utf-8") as f:
            needs_write = f.read() != FRAME_WORKER_AGENT_MARKDOWN
    if needs_write:
        with open(agent_path, "w", encoding="utf-8") as f:
            f.write(FRAME_WORKER_AGENT_MARKDOWN)
    return agent_path


# legacy path — only used when HTMLVIDEO_FRAME_HTML_DRIVER=0.
def _build_frame_html_prompt() -> str:
    return (
        """
/aiflow-build-frame-html

Work in this HyperFrames project directory. hyperframes.json, frame.md, and a
visually designed STORYBOARD.md (shot sequences + ## Video direction) already
exist — do not re-init, do not invent visual design, do not run audio, assemble
index, or render.

Follow the aiflow-build-frame-html skill and
../hyperframes-core/references/subagent-dispatch.md. Sketches were skipped
upstream — none on disk.

Hard constraints (do not violate):
1. You are the orchestrator ONLY. Never write or edit
   compositions/frames/*.html yourself — every frame HTML must come from a
   dispatched per-frame worker.
2. Before the first dispatch, list storyboard frames and the matching packets
   under .hyperframes/frame-packets/ (plus _role.md). One worker per frame.
3. Parallel fan-out: in the SAME turn, DISPATCH all frame workers at once
   (or in waves of the harness concurrency cap — never serial one-then-next
   unless the harness hard-caps active children). Do not merge frames into
   one worker.
4. WAIT on disk artifacts, not on harness completion text: a frame is done
   only when compositions/frames/<frame_id>.html exists. Missing file →
   re-dispatch that worker once, but with a convergence note appended (never
   the bare same prompt): tell it the prior attempt produced no frame — most
   likely its extended thinking was hard-truncated against the output cap —
   so it MUST write a minimal skeleton in its first 1–2 turns, then iterate
   with Edit; forbid single-turn marathon deliberation. Still missing after
   one re-dispatch → stop and report the failing frame id.
5. After each worker lands its HTML, mark that frame animated in
   STORYBOARD.md (you own storyboard status; workers never edit it).
6. Every frame-worker dispatch must use subagent_type "frame-html-worker" —
   the project-local agent definition (.claude/agents/frame-html-worker.md,
   effort pinned). Never dispatch frame workers as the built-in catch-all
   agent: built-ins bypass the per-worker effort cap on this CLI.

flow is automation → autonomous: proceed without waiting for confirmation.
Stop when the skill gate passes (every frame has compositions/frames/*.html
on disk and status: animated).
        """
    )

def _validate_frame_html_outputs(project_dir: str) -> str:
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        content = f.read()
    statuses = STATUS_RE.findall(content)
    if not statuses:
        raise ValueError(f"STORYBOARD.md has no frame status lines: {storyboard_path}")
    not_animated = [s for s in statuses if s != "animated"]
    if not_animated:
        raise ValueError(
            f"STORYBOARD.md frames not all animated: {not_animated} "
            f"path:{storyboard_path}"
        )

    frames_dir = os.path.join(project_dir, "compositions", "frames")
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"compositions/frames missing: {frames_dir}")
    html_frames = [
        name for name in os.listdir(frames_dir) if name.endswith(".html")
    ]
    if not html_frames:
        raise FileNotFoundError(
            f"no compositions/frames/*.html after build-frame-html: {frames_dir}"
        )
  
    return frames_dir


def build_frame_html(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """
    Call /aiflow-build-frame-html via claude --print.

    ``project_dir`` is the HyperFrames project root and must already have
    ``hyperframes.json``, ``frame.md``, and visually designed ``STORYBOARD.md``.
    Runs ``frame-packets.mjs`` first (skill will not), then dispatches frame
    workers (driver or legacy orchestrator). Always assembles ``index.html``
    after either path succeeds. Raises on missing prerequisites, CLI failure,
    or gate failure. Returns the path to ``index.html``.
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
        storyboard_text = f.read()
    if VIDEO_DIRECTION_HEADING not in storyboard_text:
        raise ValueError(
            f"STORYBOARD.md missing {VIDEO_DIRECTION_HEADING!r} "
            f"(needed from /aiflow-build-frame-visual): {storyboard_path}"
        )

    if _env_on(DRIVER_ENV):
        _build_frame_html_driver(project_dir, logger)
    else:
        prompt = _build_frame_html_prompt()
        # Effort cap for this skill's per-frame workers (the default "high"
        # tier; the env-level max drives 32k-token deliberation stalls on
        # ambiguous frames). Two layers: the project-local agent definition
        # pins it (primary), and this env var is the fallback — on CLI >=
        # 2.1.325 the built-in catch-all agent bypasses it, so the
        # orchestrator is instructed to dispatch the non-built-in
        # "frame-html-worker" agent above.
        _ensure_frame_worker_agent(project_dir)
        rc = run_claude(
            project_dir,
            prompt,
            logger,
            extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
            skill="frame_html",
        )
        if rc != 0:
            raise RuntimeError(f"claude build-frame-html failed rc:{rc}")

    # Driver-owned font delivery. Workers write family names only; this
    # inserts the marked @import / brand @font-face before assemble.
    # Non-fatal: run_font_imports never raises (logger may be None).
    run_font_imports(project_dir, logger, scope="frames")
    _assemble_index(project_dir, logger)
    return _validate_frame_html_outputs(project_dir)

def build_frame_packets(
    logger: logging.Logger,
    project_dir: str,
    task_id: str = "",
) -> tuple[int, str]:
    ret = 0
    msg = "ok"

    script_path = f'/app/scripts/frame-packets.mjs'
    if not os.path.exists(script_path):
        ret = 1
        msg = f"script_path:{script_path} not found"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg

    cmd = [
        "node",
        script_path,
        "--project",
        str(project_dir),
        "--storyboard",
        f"{project_dir}/STORYBOARD.md",
    ]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg

    packets_dir = os.path.join(project_dir, FRAME_PACKETS_DIR)
    role_path = os.path.join(packets_dir, FRAME_PACKETS_ROLE)
    if not os.path.isdir(packets_dir):
        ret = 1
        msg = f"frame-packets dir missing: {packets_dir}"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg
    if not os.path.isfile(role_path):
        ret = 1
        msg = f"frame-packets _role.md missing: {role_path}"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg

def build_index_html(
    logger: logging.Logger,
    project_dir: str,
    task_id: str = "",
) -> tuple[int, str]:
    ret = 0
    msg = "ok"

    script_path = f'/app/scripts/assemble-index.mjs'
    if not os.path.exists(script_path):
        ret = 1
        msg = f"script_path:{script_path} not found"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg
    cmd = [
        "node",
        script_path,
        "--videodir", project_dir]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg
    
    index_path = os.path.join(project_dir, INDEX_FILENAME)
    if not os.path.isfile(index_path):
        ret = 1
        msg = f"index.html missing: {index_path}"
        logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg


SKELETON_MARK = "skeleton-v1"
DRIVER_ENV = "HTMLVIDEO_FRAME_HTML_DRIVER"
SKELETON_ENV = "HTMLVIDEO_FRAME_HTML_SKELETON"
DISPATCH_SUFFIX = ".dispatch.md"
# Bounded model→selfcheck loop per frame (default 3 attempts). Driver runs
# frame-selfcheck after each worker return; FAIL findings go into the next
# prompt. Never unbounded — HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS overrides.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_WORKERS = 16
DEFAULT_REPAIR_ROUNDS = 1  # assemble repair; HTMLVIDEO_FRAME_HTML_REPAIR_ROUNDS overrides
# Kept as alias for older tests/docs that counted "1 retry".
MAX_RETRY_ROUNDS = DEFAULT_MAX_ATTEMPTS - 1
FRAME_SELFCHECK_SCRIPT = "/app/scripts/frame-selfcheck.mjs"
# Per-frame pipeline (default) vs. legacy wave barrier (WAVE_MODE=1 rollback).
WAVE_MODE_ENV = "HTMLVIDEO_FRAME_HTML_WAVE_MODE"
# No new attempt is submitted once the stage has run this long (seconds).
STAGE_DEADLINE_ENV = "HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE"
DEFAULT_STAGE_DEADLINE = 2400
# Kill a worker whose turn hit the output cap with thinking only (rc 125) and
# re-dispatch it with TRUNC_RETRY_MAX_OUTPUT.
TRUNC_KILL_ENV = "HTMLVIDEO_FRAME_HTML_TRUNC_KILL"
TRUNC_RETRY_MAX_OUTPUT = "64000"
# rc 124/125 with a changed, selfcheck-clean file → short finish pass.
SALVAGE_ENV = "HTMLVIDEO_FRAME_HTML_SALVAGE"
SALVAGE_MAX_OUTPUT = "16000"
SALVAGE_MAX_TURNS = 10
SALVAGE_TIMEOUT = 300
# P2-4: driver-level cap/effort override for every normal worker attempt
# (salvage/degraded/phase-A keep their own caps). Unset = frame_html profile
# default (64000 cap / high effort). medium×64000 A/B procedure:
# docs/2026-11-22-frame-html-effort-medium-ab.md (do not lower the cap).
MAX_OUTPUT_ENV = "HTMLVIDEO_FRAME_HTML_MAX_OUTPUT"
EFFORT_ENV = "HTMLVIDEO_FRAME_HTML_EFFORT"
# P2-2: attempt 1 split into Phase A (style + body) → Phase B (script).
# Default OFF (explicit 1 enables): on the single-request-marathon backend
# A+B pays two cold starts for no thinking saved.
TWO_PHASE_ENV = "HTMLVIDEO_FRAME_HTML_TWO_PHASE"
TWO_PHASE_A_MAX_OUTPUT = "24000"
TWO_PHASE_A_MAX_TURNS = 20
# P2-3: hedge the last running frame when it shows no Write after HEDGE_AFTER s.
HEDGE_ENV = "HTMLVIDEO_FRAME_HTML_HEDGE"
HEDGE_AFTER_ENV = "HTMLVIDEO_FRAME_HTML_HEDGE_AFTER"
DEFAULT_HEDGE_AFTER = 660
HEDGE_POLL_SECONDS = 5
HEDGE_DIR = os.path.join(".hyperframes", "hedge")
# P2-5: retries started with < DEGRADE_WINDOW s left before the stage deadline
# use the minimal-motion prompt.
DEGRADE_ENV = "HTMLVIDEO_FRAME_HTML_DEGRADE"
DEGRADE_WINDOW_ENV = "HTMLVIDEO_FRAME_HTML_DEGRADE_WINDOW"
DEFAULT_DEGRADE_WINDOW = 600
DEGRADE_MAX_OUTPUT = "32000"
DEGRADE_MAX_TURNS = 30
DEGRADE_MIN_TIMEOUT = 300
RECIPES_SNIPPET_HEADING = "## Rule recipes (snippets)"
RECIPES_FULL_HEADING = "## Rule recipes (full)"
# Line-start only: the rule card mentions these headings in backticks.
_RECIPES_HEADING_RE = re.compile(r"^## Rule recipes \((snippets|full)\)[ \t]*$", re.M)
RICH_SKELETON_MARK = f"{SKELETON_MARK} (rich)"
CUT_CATALOG_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "skills",
    "aiflow-build-frame-html", "references", "cut-catalog.md"))


def _env_on(name, default=True):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


def _frame_ids(project_dir):
    pkt = os.path.join(project_dir, FRAME_PACKETS_DIR)
    return sorted(
        name[:-3] for name in os.listdir(pkt)
        if name.endswith(".md") and name != FRAME_PACKETS_ROLE
        and not name.endswith(DISPATCH_SUFFIX)
    )


def _is_rich_skeleton(project_dir: str, frame_id: str) -> bool:
    try:
        with open(_frame_path(project_dir, frame_id), encoding="utf-8") as f:
            return RICH_SKELETON_MARK in f.read(400)
    except OSError:
        return False


def _load_dispatch_text(project_dir: str, frame_id: str) -> str:
    path = os.path.join(
        project_dir, FRAME_PACKETS_DIR, f"{frame_id}{DISPATCH_SUFFIX}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"missing dispatch doc: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _recipes_mode(dispatch_text: str) -> str | None:
    """``"full"`` / ``"snippets"`` when the dispatch carries a real line-start
    recipes heading, else ``None`` (index-only)."""
    modes = {m.group(1) for m in _RECIPES_HEADING_RE.finditer(dispatch_text)}
    if "full" in modes:
        return "full"
    return "snippets" if "snippets" in modes else None


def _worker_prompt(frame_id, skeleton_mode, project_dir=None, dispatch_text=None):
    """Build the per-frame worker prompt with dispatch inlined.

    ``dispatch_text`` overrides the on-disk ``.dispatch.md`` (tests). Production
    callers pass ``project_dir`` so the driver reads and embeds the file — the
    model must not Read the dispatch path again.
    """
    if dispatch_text is None:
        if not project_dir:
            raise ValueError(
                "_worker_prompt requires project_dir or dispatch_text")
        dispatch_text = _load_dispatch_text(project_dir, frame_id)
    frame_file = f"compositions/frames/{frame_id}.html"
    fill_line = (
        f"A pre-built skeleton already exists at {frame_file} (a `{SKELETON_MARK}` "
        "comment marks it). Your job is to FILL it, not design from scratch: after "
        "reading the skeleton, write the complete file in ONE Write (or one large "
        "Edit) when it will be under ~15 KB — do not split style → body → script "
        "into separate edits. Only a larger file is chunked; any single Write input "
        "must stay under ~20 KB (InputValidationError). You may add/remove any node "
        "or rewrite the file wholly (last write wins). Do NOT write a new minimal "
        "skeleton first — it is already there."
        if skeleton_mode else
        f"You must create {frame_file}: write it in ONE Write when it will be under "
        "~15 KB; only a larger file is chunked, and any single Write input must stay "
        "under ~20 KB (InputValidationError)."
    )
    if skeleton_mode and project_dir and _is_rich_skeleton(project_dir, frame_id):
        fill_line += (
            " The skeleton already carries a full-duration ground clip "
            "(`data-skeleton=\"bg\"`, track 0) and Scene labels (`scene1`, "
            "`scene2`, …) on the registered `tl` — style and reuse them, and drop "
            "`data-skeleton` from the ground clip once it is styled.")
    recipes_mode = _recipes_mode(dispatch_text)
    if recipes_mode == "full":
        recipe_line = (
            "What the dispatch inlines: the frame.md design-token excerpt, the rule "
            "card, your storyboard block, the selected blueprint body, and every "
            "cited rule recipe IN FULL under `## Rule recipes (full)` (the "
            "`## Rule index` lists their RULES_DIR paths for reference only). Do "
            "NOT Read any recipe; never open frame.md or _role.md — they cannot "
            "add what the dispatch already carries.")
    elif recipes_mode == "snippets":
        recipe_line = (
            "What the dispatch inlines: the frame.md design-token excerpt, the rule "
            "card, your storyboard block, the selected blueprint body, and a "
            "mechanism + core-code SNIPPET of every cited recipe under "
            "`## Rule recipes (snippets)`. The `## Rule index` still lists the "
            "`RULES_DIR/<id>.md` paths: do NOT batch-read them — Read a full "
            "recipe only when a detail you need is missing from its snippet (at "
            "most once per recipe); never open frame.md or _role.md — they cannot "
            "add what the dispatch already carries.")
    else:
        recipe_line = (
            "What the dispatch inlines: the frame.md design-token excerpt, the rule "
            "card, your storyboard block, and the selected blueprint body. Rule "
            "recipes are NOT inlined — the dispatch carries only a `## Rule index` "
            "of `RULES_DIR/<id>.md` paths. In your FIRST tool turn, Read every "
            "recipe listed in the Rule index in one parallel batch (all Read calls "
            "in that same turn), then do not re-read them; never open frame.md or "
            "_role.md — they cannot add what the dispatch already carries.")
    return f"""You are one per-frame HTML worker for a HyperFrames video.

The complete merged dispatch for your frame is INLINED below — treat it as the
authoritative contract. Do NOT Read `.hyperframes/frame-packets/{frame_id}.dispatch.md`
or any other packet/dispatch file; re-reading only wastes turns. {fill_line}

Scope is exactly one file: {frame_file}. Never read, open, or write any other
file under compositions/frames/ (sibling workers own those frames), any other
frame's .dispatch.md / packet, or STORYBOARD.md — exploring sibling frames
burns turns and cannot help this frame.

Do NOT run frame-selfcheck and do NOT use Bash — the driver runs
`node {FRAME_SELFCHECK_SCRIPT} {frame_file}` after you finish and will
re-dispatch you (bounded) with FAIL findings if needed. When you have written
the fill, your final reply is ONLY this one line: done OK {frame_id}
On an unrecoverable failure instead reply only: fail {frame_id}: <reason>
No summary, checklist, or explanation — any extra text is wasted wall time.

{recipe_line} Optional, at most once: {CUT_CATALOG_PATH}
(only for within-scene cut seams).

Conflict priority (higher wins): the `## Resolved conflicts` rulings at the end
of the dispatch (when present — they already settle the contradictions, do not
re-deliberate) > the time-coded Scene list in your storyboard block >
`## Video direction` / design contract > the blueprint body > frame.md
narrative prose. frame.md token VALUES (hex, font families) are always exact.
If the block lists several `- blueprint:` lines, the one under
`## Selected blueprint` is authoritative.

Never edit STORYBOARD.md. Turn discipline: every turn ends with a tool call or
visible text; your first edit must land by your second turn (right after the
recipe batch read, if any); keep each turn's thinking to at most one short
paragraph of layout/beat notes — never burn a turn on marathon deliberation.
Never draft CSS/HTML/JS in thinking: code goes only into Edit/Write arguments.
Place elements on the card / frame.md cqw·cqh grid and tokens; no per-pixel
math, no re-verifying positions.

## Dispatch (authoritative — already complete)

{dispatch_text.rstrip()}
"""


def _worker_done_ok(frame_id: str, text: str | None) -> bool:
    """True when the worker result contains ``done OK <frame_id>``.

    Frame ids may end with a non-word character (e.g. ``01-`` when the title
    slug collapsed). A trailing ``\\b`` then fails at EOL, so we require the id
    followed by end-of-string or whitespace instead.
    """
    if not text:
        return False
    return bool(
        re.search(rf"\bdone\s+OK\s+{re.escape(frame_id)}(?!\S)", text, re.I)
    )


def _run_frame_selfcheck(project_dir: str, frame_id: str) -> tuple[bool, str]:
    """Run ``frame-selfcheck.mjs`` on one frame. Returns ``(pass, output)``."""
    frame_rel = f"compositions/frames/{frame_id}.html"
    frame_abs = os.path.join(project_dir, frame_rel)
    if not os.path.isfile(frame_abs):
        return False, f"[FAIL] file not found: {frame_rel}"
    proc = subprocess.run(
        ["node", FRAME_SELFCHECK_SCRIPT, frame_abs],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not out:
        out = f"(frame-selfcheck exit {proc.returncode})"
    return proc.returncode == 0, out


def _failure_note(
    frame_id: str,
    rc: int,
    text: str | None,
    sc_ok: bool,
    selfcheck_out: str = "",
) -> str:
    parts: list[str] = []
    if rc == 124:
        parts.append("prior attempt hit its wall-clock timeout")
    elif rc == TRUNCATED_RC:
        parts.append(
            "prior attempt was killed: a turn hit the output cap with thinking "
            "only (hard truncation, nothing visible). This retry has a larger "
            "output cap — still write early and keep each turn's thinking short")
    elif rc != 0:
        parts.append(f"prior attempt exited non-zero (rc={rc})")
    elif not _worker_done_ok(frame_id, text):
        tail = (text or "").strip()[-300:]
        parts.append(
            "prior attempt finished without done OK "
            f"(possibly truncated); last words: {tail or '∅'}"
        )
    if not sc_ok and selfcheck_out:
        parts.append("frame-selfcheck findings:\n" + selfcheck_out[-2000:])
    return " | ".join(parts) if parts else "prior attempt failed gate"


_STORYBOARD_LOCK = threading.Lock()


def _mark_animated_storyboard(project_dir, frame_id):
    with _STORYBOARD_LOCK:
        return _mark_animated_storyboard_locked(project_dir, frame_id)


def _mark_animated_storyboard_locked(project_dir, frame_id):
    path = os.path.join(project_dir, STORYBOARD_FILENAME)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    block_re = re.compile(
        r"^## (?:Frame|Beat|Scene)\b[^\n]*$(.*?)(?=^## (?:Frame|Beat|Scene)\b|^## Video direction|\Z)",
        re.M | re.S)
    needle = re.compile(rf"^- src:\s*.*{re.escape(frame_id)}\.html", re.M)
    for m in block_re.finditer(text):
        block = m.group(1)
        if not needle.search(block):
            continue
        new_block, n = re.subn(r"^- status:\s*\w+", "- status: animated",
                               block, count=1, flags=re.M)
        if n:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text[:m.start(1)] + new_block + text[m.end(1):])
            return True
        _LOGGER.warning("status bullet not found/updated for frame id=%s", frame_id)
    return False


def _preflight_dispatch(project_dir, frame_ids, skeleton_mode):
    pkt = os.path.join(project_dir, FRAME_PACKETS_DIR)
    for fid in frame_ids:
        if not os.path.isfile(os.path.join(pkt, f"{fid}{DISPATCH_SUFFIX}")):
            raise RuntimeError(
                f"missing dispatch doc for {fid} — re-run frame-packets with "
                "HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER >= 2")
    if skeleton_mode:
        for fid in frame_ids:
            p = os.path.join(project_dir, "compositions", "frames", f"{fid}.html")
            if not os.path.isfile(p):
                raise RuntimeError(
                    f"skeleton missing for {fid} while HTMLVIDEO_FRAME_HTML_SKELETON "
                    "is on — re-run frame-packets or set HTMLVIDEO_FRAME_HTML_SKELETON=0")
            with open(p, encoding="utf-8") as f:
                if SKELETON_MARK not in f.read():
                    raise RuntimeError(
                        f"frame file for {fid} is not a pre-built skeleton while "
                        "HTMLVIDEO_FRAME_HTML_SKELETON is on")


def _repair_prompt(assemble_msg):
    return (f"You are repairing a HyperFrames assembled project. The assemble-index "
            f"step failed with:\n{(assemble_msg or '')[-2000:]}\n"
            f"Make the CHEAPEST safe structural edit to compositions/frames/*.html that "
            f"fixes the reported problem (do not touch STORYBOARD.md). Stop after the "
            f"edit — the pipeline re-runs assemble itself.")


def _assemble_index(project_dir, logger):
    log = logger.info if logger else (lambda *a, **k: None)
    max_repair = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_REPAIR_ROUNDS", DEFAULT_REPAIR_ROUNDS,
        logger or logging.getLogger("frame-html"))
    ret, msg = 1, ""
    for round_no in range(1 + max_repair):
        ret, msg = build_index_html(logger, project_dir)
        if ret == 0:
            return
        if "script_path" in (msg or ""):
            # a script-path error is not repairable by editing frames
            raise RuntimeError(f"assemble failed: {msg}")
        if round_no < max_repair:
            log("assemble FAILED, repair round %d/%d", round_no + 1, max_repair)
            run_claude(project_dir, _repair_prompt(msg), logger, skill="repair")
    raise RuntimeError(f"assemble failed after {max_repair} repair round(s): {msg}")


def _frame_path(project_dir, frame_id):
    return os.path.join(project_dir, "compositions", "frames", f"{frame_id}.html")


def _file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _redispatch_suffix(note):
    return (
        f"\n[RE-DISPATCH] {note}\n"
        "The frame file may already hold a skeleton or your own "
        "partial content — fix the findings above; first edit by your second "
        "turn; no single-turn marathon. Do NOT run selfcheck "
        "yourself — the driver will re-check after you reply."
    )


def _salvage_prompt(frame_id, project_dir, reason):
    frame_file = f"compositions/frames/{frame_id}.html"
    try:
        with open(_frame_path(project_dir, frame_id), encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = "(unreadable)"
    dispatch_text = _load_dispatch_text(project_dir, frame_id)
    return f"""You are finishing ONE HyperFrames frame: {frame_file}.
The previous worker session for this frame was cut off ({reason}) after it had
already written substantial work; the file passes the frame-selfcheck structure
gates (at most the timeline is still unfilled). This is a short FINISH pass,
not a rebuild:

- Compare the current file (inlined below) against the dispatch (inlined
  below). Complete only what is missing or obviously unfinished (Scenes with
  no timeline phase, placeholder comments, empty containers). Keep everything
  that already works; do not redesign or rewrite from scratch.
- Do NOT Read anything — no recipe, packet, dispatch, frame.md or sibling
  frame; everything you need is inlined. Edit only {frame_file}.
- Do NOT run frame-selfcheck and do NOT use Bash; the driver re-checks.
- Use a few small Edits at most, keep thinking short, then reply exactly:
  done OK {frame_id}
  If the file already covers every Scene, reply done OK {frame_id} at once.
  If it cannot be finished: fail {frame_id}: <reason>

## Current file ({frame_file})

```html
{current.rstrip()}
```

## Dispatch (authoritative)

{dispatch_text.rstrip()}
"""


def _salvageable(sc_ok: bool, sc_out: str) -> bool:
    """Selfcheck PASS, or the only FAIL is skeleton_unfilled with real #root
    content (body written, timeline not yet) — worth a finish pass."""
    if sc_ok:
        return True
    fails = [ln for ln in (sc_out or "").splitlines() if ln.startswith("[FAIL]")]
    return bool(fails) and all(
        "(skeleton_unfilled)" in ln and "root-empty" not in ln for ln in fails)


def _phase_a_suffix(frame_id):
    return (
        f"\n[TWO-PHASE — PHASE A of 2] This session writes ONLY the `<style>` "
        f"block and the `#root` body markup of compositions/frames/{frame_id}.html "
        "(every Scene's elements in their rest layout, clips with timing attrs). "
        "Do NOT write timeline tweens and do NOT read rule recipes — leave the "
        "script block as the skeleton has it; Phase B adds the motion in a "
        f"separate session. When style + body are written, reply exactly: "
        f"phase-a OK {frame_id}\n"
    )


def _phase_b_suffix(frame_id, project_dir):
    try:
        with open(_frame_path(project_dir, frame_id), encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = "(unreadable)"
    return (
        "\n[TWO-PHASE — PHASE B of 2] Phase A already wrote the `<style>` block "
        "and the `#root` body markup (current file inlined below). Write the "
        "timeline script now: every Scene becomes a timeline phase on the "
        "registered paused `tl`. Touch markup/styles only for small hooks the "
        "motion needs; do not redesign. Then reply done OK "
        f"{frame_id}.\n\n## Current file (compositions/frames/{frame_id}.html)\n\n"
        f"```html\n{current.rstrip()}\n```\n"
    )


def _degrade_suffix(note, remaining_s):
    return (
        f"\n[DEGRADED — stage deadline in ~{int(max(remaining_s, 0))}s] "
        f"Prior attempts failed ({(note or 'gate failed')[-600:]}). Build the "
        "SIMPLEST faithful version now: keep every Scene as one timeline phase "
        "with at most 1–2 simple tweens per element (rise/fade/scale-pop), a "
        "static layout per Scene, no 3D, no particles, no counters beyond a "
        "plain number swap. Do NOT Read anything — no recipe, no cut catalog. "
        "Priority: a valid file with every Scene present > visual richness. "
        "First edit in your FIRST turn; a few large Writes/Edits, then reply "
        "done OK.\n"
    )


def _base_worker_env(logger):
    """P2-4 cap/effort overrides applied to normal worker attempts."""
    env = {"CLAUDE_CODE_SUBAGENT_EFFORT": "high"}
    raw_cap = os.environ.get(MAX_OUTPUT_ENV, "").strip()
    if raw_cap:
        cap = _env_positive_int(MAX_OUTPUT_ENV, 0, logger)
        if cap:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(cap)
    effort = os.environ.get(EFFORT_ENV, "").strip()
    if effort:
        if effort in _ALLOWED_EFFORTS:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        else:
            logger.warning("invalid %s=%r ignored (allowed: %s)", EFFORT_ENV,
                           effort, ",".join(sorted(_ALLOWED_EFFORTS)))
    return env


def _trunc_retry_cap(base_env):
    try:
        base = int(base_env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "0"))
    except ValueError:
        base = 0
    return str(max(int(TRUNC_RETRY_MAX_OUTPUT), base))


class _StageStats:
    """Counters for the one-line stage summary (P1-4)."""

    def __init__(self, frame_ids):
        self.started = time.monotonic()
        self.frames = len(frame_ids)
        self.attempts = 0
        self.truncations = 0
        self.timeouts = 0
        self.salvages = 0
        self.degraded = 0
        self.hedges = 0
        self.hedge_wins = 0
        self.finished_at: dict[str, float] = {}

    def record(self, res):
        self.attempts += 1
        if res["rc"] == TRUNCATED_RC:
            self.truncations += 1
        elif res["rc"] == 124:
            self.timeouts += 1
        mode = res.get("mode")
        if mode == "salvage":
            self.salvages += 1
        elif mode == "degraded":
            self.degraded += 1
        elif mode == "hedge":
            self.hedges += 1

    def finish(self, fid):
        self.finished_at[fid] = time.monotonic() - self.started

    def line(self, ok, failed):
        crit = max(self.finished_at, key=self.finished_at.get) if self.finished_at else "-"
        crit_s = self.finished_at.get(crit, 0.0)
        return (
            f"frame-html stage summary frames={self.frames} ok={ok} "
            f"failed={failed} attempts={self.attempts} "
            f"truncations={self.truncations} salvages={self.salvages} "
            f"timeouts={self.timeouts} degraded={self.degraded} "
            f"hedges={self.hedges} hedge_wins={self.hedge_wins} "
            f"critical_path_frame={crit} critical_path_s={crit_s:.1f} "
            f"total_s={time.monotonic() - self.started:.1f}"
        )


def _prepare_hedge_dir(project_dir, fid, attempt_no):
    root = os.path.join(project_dir, HEDGE_DIR)
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f"{fid}-{attempt_no}-", dir=root)
    os.makedirs(os.path.join(work, "compositions", "frames"), exist_ok=True)
    shutil.copyfile(_frame_path(project_dir, fid), _frame_path(work, fid))
    for name in (FRAME_FILENAME, "CLAUDE.md", "AGENTS.md"):
        src = os.path.join(project_dir, name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(work, name))
    return work


def _build_frame_html_driver(project_dir, logger):
    if _env_on(WAVE_MODE_ENV, default=False):
        return _build_frame_html_driver_waves(project_dir, logger)
    return _build_frame_html_driver_pipeline(project_dir, logger)


def _build_frame_html_driver_pipeline(project_dir, logger):
    """Per-frame pipeline: each frame retries independently (no wave barrier)."""
    log = logger.info if logger else lambda *a, **k: None
    env_logger = logger or logging.getLogger("frame-html")
    frame_ids = _frame_ids(project_dir)
    if not frame_ids:
        raise RuntimeError(f"no frame packets found under {project_dir}/{FRAME_PACKETS_DIR}")
    skeleton_mode = _env_on(SKELETON_ENV)
    _preflight_dispatch(project_dir, frame_ids, skeleton_mode)
    max_workers = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY", DEFAULT_MAX_WORKERS, env_logger)
    max_attempts = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, env_logger)
    deadline_s = _env_positive_int(
        STAGE_DEADLINE_ENV, DEFAULT_STAGE_DEADLINE, env_logger)
    trunc_kill = _env_on(TRUNC_KILL_ENV)
    salvage_on = _env_on(SALVAGE_ENV)
    # TWO_PHASE default OFF (=1 enables); HEDGE default ON (=0 disables).
    two_phase = _env_on(TWO_PHASE_ENV, default=False) and skeleton_mode
    hedge_on = _env_on(HEDGE_ENV, default=True)
    hedge_after = _env_positive_int(HEDGE_AFTER_ENV, DEFAULT_HEDGE_AFTER, env_logger)
    degrade_on = _env_on(DEGRADE_ENV)
    degrade_window = _env_positive_int(
        DEGRADE_WINDOW_ENV, DEFAULT_DEGRADE_WINDOW, env_logger)
    base_env = _base_worker_env(env_logger)
    worker_cap = base_env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS") or _resolve_profile(
        "frame_html", env_logger).get("max_output") or "-"
    stats = _StageStats(frame_ids)
    started = stats.started
    log(
        "frame-html driver start mode=pipeline frames=%s skeleton=%s "
        "max_attempts=%s deadline=%ss trunc_kill=%s salvage=%s two_phase=%s "
        "worker_cap=%s hedge=%s(after=%ss) degrade=%s(window=%ss) env_override=%s",
        len(frame_ids), skeleton_mode, max_attempts, deadline_s,
        trunc_kill, salvage_on, two_phase, worker_cap, hedge_on, hedge_after,
        degrade_on, degrade_window,
        {k: v for k, v in base_env.items() if k != "CLAUDE_CODE_SUBAGENT_EFFORT"} or "-",
    )

    def call(workdir, prompt, extra_env, label, cancel_event=None, **kw):
        if trunc_kill:
            kw["session_id"] = str(uuid.uuid4())
            kw["trunc_kill"] = True
        if cancel_event is not None:
            kw["cancel_event"] = cancel_event
        return run_claude_result(
            workdir, prompt, logger,
            extra_env=extra_env, skill="frame_html", label=label, **kw)

    def run_attempt(fid, attempt_no, note, prev_rc, mode,
                    workdir=None, cancel_event=None, remaining=None):
        """mode ∈ normal | salvage | degraded | hedge (hedge runs in workdir)."""
        wd = workdir or project_dir
        frame_path = _frame_path(wd, fid)
        hash_before = _file_hash(frame_path)
        extra_env = dict(base_env)
        kw = {}
        label = f"{fid}#{attempt_no}" + ("" if mode == "normal" else f"-{mode}")
        if mode == "salvage":
            prompt = _salvage_prompt(fid, project_dir, note or "cut off")
            extra_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = SALVAGE_MAX_OUTPUT
            kw["max_turns"] = SALVAGE_MAX_TURNS
            kw["timeout"] = SALVAGE_TIMEOUT
        else:
            prompt = _worker_prompt(fid, skeleton_mode, project_dir=project_dir)
            if mode == "degraded":
                prompt += _degrade_suffix(note, remaining or 0)
                extra_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = DEGRADE_MAX_OUTPUT
                kw["max_turns"] = DEGRADE_MAX_TURNS
                kw["timeout"] = max(DEGRADE_MIN_TIMEOUT, int(remaining or 0))
            elif note:
                prompt += _redispatch_suffix(note)
            if mode != "degraded" and prev_rc == TRUNCATED_RC:
                extra_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = _trunc_retry_cap(base_env)
        phased = two_phase and mode == "normal" and attempt_no == 1
        if phased:
            env_a = dict(extra_env, CLAUDE_CODE_MAX_OUTPUT_TOKENS=TWO_PHASE_A_MAX_OUTPUT)
            rc, text = call(wd, prompt + _phase_a_suffix(fid), env_a,
                            f"{label}-A", cancel_event,
                            max_turns=TWO_PHASE_A_MAX_TURNS)
            if rc == 0:
                rc, text = call(wd, prompt + _phase_b_suffix(fid, wd), extra_env,
                                f"{label}-B", cancel_event)
        else:
            rc, text = call(wd, prompt, extra_env, label, cancel_event, **kw)
        sc_ok, sc_out = _run_frame_selfcheck(wd, fid)
        done = _worker_done_ok(fid, text)
        ok = rc == 0 and done and sc_ok and os.path.isfile(frame_path)
        if ok and mode != "hedge":
            _mark_animated_storyboard(project_dir, fid)
        changed = _file_hash(frame_path) != hash_before
        return {
            "fid": fid, "rc": rc, "text": text, "sc_ok": sc_ok, "sc_out": sc_out,
            "done": done, "ok": ok, "changed": changed,
            "salvage": mode == "salvage", "mode": mode, "phased": phased,
        }

    attempts = {fid: 0 for fid in frame_ids}
    notes: dict[str, str] = {}
    failed: dict[str, str] = {}
    ok_set: set[str] = set()
    running: dict = {}   # future -> {fid, role, started, hash0, cancel, ...}
    races: dict = {}     # fid -> {primary, hedge, winner, workdir}
    hedged: set[str] = set()
    pool = ThreadPoolExecutor(max_workers=max_workers + (1 if hedge_on else 0))

    def submit(fid, n, note, prev_rc, mode, remaining=None):
        cancel = threading.Event() if hedge_on else None
        fut = pool.submit(run_attempt, fid, n, note, prev_rc, mode,
                          None, cancel, remaining)
        running[fut] = {
            "fid": fid, "role": "primary", "started": time.monotonic(),
            "hash0": _file_hash(_frame_path(project_dir, fid)), "cancel": cancel,
            "n": n, "note": note, "prev_rc": prev_rc, "mode": mode,
        }

    def maybe_hedge():
        now = time.monotonic()
        if now - started >= deadline_s:
            return
        live = {i["fid"] for i in running.values()}
        if len(live) != 1:
            return
        fid = next(iter(live))
        if fid in hedged or fid in races:
            return
        if any(f not in ok_set for f in frame_ids if f != fid):
            return
        prim = next((f for f, i in running.items()
                     if i["fid"] == fid and i["role"] == "primary"), None)
        if prim is None:
            return
        info = running[prim]
        if info["mode"] == "salvage" or now - info["started"] < hedge_after:
            return
        if _file_hash(_frame_path(project_dir, fid)) != info["hash0"]:
            return  # the primary already wrote — no hedge
        hedged.add(fid)
        work = _prepare_hedge_dir(project_dir, fid, info["n"])
        cancel = threading.Event()
        fut = pool.submit(run_attempt, fid, info["n"], info["note"],
                          info["prev_rc"], "hedge", work, cancel, None)
        running[fut] = {"fid": fid, "role": "hedge", "started": now,
                        "hash0": None, "cancel": cancel}
        races[fid] = {"primary": prim, "hedge": fut, "winner": None,
                      "workdir": work}
        log("hedge start %s after %ss without a Write (workdir=%s)",
            fid, int(now - info["started"]), work)

    def finish_ok(fid, kind, n):
        ok_set.add(fid)
        stats.finish(fid)
        log("worker ok %s (%s %s/%s selfcheck=PASS)", fid, kind, n, max_attempts)

    def end_race(fid):
        race = races.pop(fid, None)
        if race:
            shutil.rmtree(race["workdir"], ignore_errors=True)

    def promote_hedge(fid):
        race = races[fid]
        shutil.copyfile(_frame_path(race["workdir"], fid),
                        _frame_path(project_dir, fid))
        sc_ok, sc_out = _run_frame_selfcheck(project_dir, fid)
        if not sc_ok:
            log("hedge promote %s failed selfcheck: %s", fid, sc_out[-300:])
            return False
        _mark_animated_storyboard(project_dir, fid)
        stats.hedge_wins += 1
        return True

    def on_race_result(fid, role, res):
        """None = consumed by the race; otherwise the primary's result, to run
        through the normal fail/retry path."""
        race = races[fid]
        other = race["hedge"] if role == "primary" else race["primary"]
        other_live = other in running
        if race["winner"] == "primary":
            end_race(fid)  # the cancelled hedge returned
            return None
        if race["winner"] == "hedge":  # the primary returned after losing
            if res["ok"] or promote_hedge(fid):
                end_race(fid)
                finish_ok(fid, "attempt" if res["ok"] else "hedge", attempts[fid])
                return None
            end_race(fid)
            return res
        if res["ok"]:
            race["winner"] = role
            if role == "primary":
                if other_live:
                    running[other]["cancel"].set()
                else:
                    end_race(fid)
                finish_ok(fid, "attempt", attempts[fid])
                return None
            if other_live:
                log("hedge won %s — cancelling primary", fid)
                running[other]["cancel"].set()
                return None  # promoted once the primary is gone
            promoted = promote_hedge(fid)
            prim_res = race.get("primary_res")
            end_race(fid)
            if promoted:
                finish_ok(fid, "hedge", attempts[fid])
                return None
            return prim_res
        if role == "primary":
            race["primary_res"] = res
        if other_live:
            log("race member %s/%s failed rc=%s — waiting on the other",
                fid, role, res["rc"])
            return None
        prim_res = race.get("primary_res", res)
        end_race(fid)
        return prim_res

    try:
        for fid in frame_ids:
            attempts[fid] = 1
            submit(fid, 1, None, None, "normal")
        while running:
            done_set, _ = wait(
                list(running), return_when=FIRST_COMPLETED,
                timeout=HEDGE_POLL_SECONDS if hedge_on else None)
            for fut in done_set:
                info = running.pop(fut)
                fid, role = info["fid"], info["role"]
                res = fut.result()
                stats.record(res)
                if fid in races:
                    res = on_race_result(fid, role, res)
                    if res is None:
                        continue
                elif role == "hedge":
                    continue
                n = attempts[fid]
                kind = res["mode"] if res["mode"] != "normal" else "attempt"
                if res["ok"]:
                    finish_ok(fid, kind, n)
                    continue
                log(
                    "worker fail %s rc=%s done_ok=%s selfcheck=%s changed=%s "
                    "(%s %s/%s)",
                    fid, res["rc"], res["done"],
                    "PASS" if res["sc_ok"] else "FAIL", res["changed"],
                    kind, n, max_attempts,
                )
                note = _failure_note(
                    fid, res["rc"], res["text"], res["sc_ok"], res["sc_out"])
                notes[fid] = note
                if n >= max_attempts:
                    failed[fid] = note
                    stats.finish(fid)
                    continue
                elapsed = time.monotonic() - started
                if elapsed >= deadline_s:
                    failed[fid] = (
                        f"{note} | stage deadline {deadline_s}s reached "
                        f"(elapsed {int(elapsed)}s) — no new attempt")
                    stats.finish(fid)
                    log("stage deadline reached; not retrying %s", fid)
                    continue
                salvage = (
                    salvage_on
                    and not res["salvage"]
                    and res["rc"] in (124, TRUNCATED_RC)
                    and res["changed"]
                    and _salvageable(res["sc_ok"], res["sc_out"])
                )
                remaining = deadline_s - elapsed
                mode = "salvage" if salvage else (
                    "degraded" if degrade_on and remaining < degrade_window
                    else "normal")
                attempts[fid] = n + 1
                log("retry %s as %s %s/%s degraded=%s remaining=%ss", fid,
                    "attempt" if mode == "normal" else mode, n + 1,
                    max_attempts, int(mode == "degraded"), int(remaining))
                submit(fid, n + 1, note, res["rc"], mode, remaining)
            if hedge_on:
                maybe_hedge()
    except BaseException:
        for fut, info in running.items():
            if info.get("cancel") is not None:
                info["cancel"].set()
            fut.cancel()
        raise
    finally:
        pool.shutdown(wait=True)
        for fid in list(races):
            end_race(fid)
        log("%s", stats.line(len(ok_set), len(failed)))
    if failed:
        reasons = ", ".join(f"{fid}: {failed[fid]}" for fid in sorted(failed))
        raise RuntimeError(
            f"frames still broken after {max_attempts} attempts: {reasons}")


def _build_frame_html_driver_waves(project_dir, logger):
    """Legacy wave loop (HTMLVIDEO_FRAME_HTML_WAVE_MODE=1 rollback)."""
    log = logger.info if logger else lambda *a, **k: None
    frame_ids = _frame_ids(project_dir)
    if not frame_ids:
        raise RuntimeError(f"no frame packets found under {project_dir}/{FRAME_PACKETS_DIR}")
    skeleton_mode = _env_on(SKELETON_ENV)
    _preflight_dispatch(project_dir, frame_ids, skeleton_mode)
    max_workers = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY", DEFAULT_MAX_WORKERS,
        logger or logging.getLogger("frame-html"))
    max_attempts = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS,
        logger or logging.getLogger("frame-html"))
    remaining = list(frame_ids)
    notes: dict[str, str] = {}
    stats = _StageStats(frame_ids)
    log(
        "frame-html driver start frames=%s skeleton=%s max_attempts=%s",
        len(remaining), skeleton_mode, max_attempts,
    )
    for attempt in range(max_attempts):
        def run_one(fid):
            prompt = _worker_prompt(
                fid, skeleton_mode, project_dir=project_dir)
            if notes.get(fid):
                prompt += _redispatch_suffix(notes[fid])
            rc, text = run_claude_result(
                project_dir, prompt, logger,
                extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
                skill="frame_html",
                label=fid)
            return fid, rc, text

        failures: dict[str, tuple[int, str | None, bool, str]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fid, rc, text in pool.map(run_one, remaining):
                frame_path = os.path.join(
                    project_dir, "compositions", "frames", f"{fid}.html")
                stats.record({"rc": rc, "mode": "normal"})
                sc_ok, sc_out = _run_frame_selfcheck(project_dir, fid)
                done = _worker_done_ok(fid, text)
                ok = (
                    rc == 0
                    and done
                    and sc_ok
                    and os.path.isfile(frame_path)
                )
                if ok:
                    _mark_animated_storyboard(project_dir, fid)
                    remaining.remove(fid)
                    stats.finish(fid)
                    log(
                        "worker ok %s (attempt %s/%s selfcheck=PASS)",
                        fid, attempt + 1, max_attempts,
                    )
                else:
                    failures[fid] = (rc, text, sc_ok, sc_out)
                    log(
                        "worker fail %s rc=%s done_ok=%s selfcheck=%s "
                        "(attempt %s/%s)",
                        fid, rc, done, "PASS" if sc_ok else "FAIL",
                        attempt + 1, max_attempts,
                    )
        if not remaining:
            break
        notes = {
            fid: _failure_note(fid, rc, text, sc_ok, sc_out)
            for fid, (rc, text, sc_ok, sc_out) in failures.items()
        }
        log("retry pending=%s after attempt %s", remaining, attempt + 1)
    for fid in remaining:
        stats.finish(fid)
    log("%s", stats.line(len(frame_ids) - len(remaining), len(remaining)))
    if remaining:
        reasons = ", ".join(
            f"{fid}: {notes.get(fid, 'unresolved')}" for fid in sorted(remaining))
        raise RuntimeError(
            f"frames still broken after {max_attempts} attempts: {reasons}")
