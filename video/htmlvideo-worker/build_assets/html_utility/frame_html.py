# -*- coding: utf-8 -*-
"""Frame HTML build helpers for htmlvideo worker."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from html_utility.claude_cli import (
    _env_positive_int,
    run_claude,
    run_claude_result,
)

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
# One re-dispatch after the initial batch: with the 1500s per-worker timeout
# this bounds frame-html at 2 rounds (~50 min worst case, was ~75 min at 3
# rounds). Frames that pass exit the loop before re-entering it.
MAX_RETRY_ROUNDS = 1
DEFAULT_MAX_WORKERS = 16
DEFAULT_REPAIR_ROUNDS = 1  # assemble repair; HTMLVIDEO_FRAME_HTML_REPAIR_ROUNDS overrides
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


def _load_dispatch_text(project_dir: str, frame_id: str) -> str:
    path = os.path.join(
        project_dir, FRAME_PACKETS_DIR, f"{frame_id}{DISPATCH_SUFFIX}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"missing dispatch doc: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


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
        "comment marks it). Your job is to FILL it, not design from scratch: edit in "
        "chunks — style block → body → script block, each payload under ~20 KB. You may "
        "add/remove any node or rewrite the file wholly (last write wins)."
        if skeleton_mode else
        f"You must create {frame_file}: build it in chunks — style block → body → "
        "script block, each payload under ~20 KB."
    )
    return f"""You are one per-frame HTML worker for a HyperFrames video.

The complete merged dispatch for your frame is INLINED below — treat it as the
authoritative contract. Do NOT Read `.hyperframes/frame-packets/{frame_id}.dispatch.md`
or any other packet/dispatch file; re-reading only wastes turns. {fill_line}

Scope is exactly one file: {frame_file}. Never read, open, or write any other
file under compositions/frames/ (sibling workers own those frames), any other
frame's .dispatch.md / packet, or STORYBOARD.md — exploring sibling frames
burns turns and cannot help this frame.

Then run the structural gate and fix every FAIL:
  node /app/scripts/frame-selfcheck.mjs {frame_file}

Your terminal reply after the gate passes is exactly: done OK {frame_id}
On an unrecoverable failure instead reply: fail {frame_id}: <reason>

The dispatch is self-sufficient: it already inlines the frame.md design-token
excerpt, this rule card, your blueprint, and every rule recipe this frame cites.
Do not re-derive what it states, and never open frame.md or _role.md — open
those only costs turns and cannot add what the dispatch already carries.

Escape hatches — open ONLY when a specific recipe is genuinely absent from the
dispatch, and read at most the one you need:
  RULES_DIR/<id>.md                              (a cited motion recipe missing from your packet)
  {CUT_CATALOG_PATH}   (only for within-scene cut seams)
Never edit STORYBOARD.md. Turn discipline: every turn ends with a tool call or
visible text; your first edit must land within your first 1–2 turns; keep each
turn's thinking to at most one short paragraph — never burn a turn on marathon
deliberation.

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


def _dispatch_note(rc, text):
    if rc == 124:
        return "prior attempt hit its wall-clock timeout"
    if rc != 0:
        return f"prior attempt exited non-zero (rc={rc})"
    tail = (text or "").strip()[-300:]
    return ("prior attempt finished without passing its gate (possibly truncated); "
            "last words: " + (tail or "∅"))


def _mark_animated_storyboard(project_dir, frame_id):
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


def _build_frame_html_driver(project_dir, logger):
    log = logger.info if logger else lambda *a, **k: None
    frame_ids = _frame_ids(project_dir)
    if not frame_ids:
        raise RuntimeError(f"no frame packets found under {project_dir}/{FRAME_PACKETS_DIR}")
    skeleton_mode = _env_on(SKELETON_ENV)
    _preflight_dispatch(project_dir, frame_ids, skeleton_mode)
    max_workers = _env_positive_int(
        "HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY", DEFAULT_MAX_WORKERS,
        logger or logging.getLogger("frame-html"))
    remaining = list(frame_ids)
    notes = {}
    log("frame-html driver start frames=%s skeleton=%s", len(remaining), skeleton_mode)
    for round_no in range(1 + MAX_RETRY_ROUNDS):
        def run_one(fid):
            prompt = _worker_prompt(
                fid, skeleton_mode, project_dir=project_dir)
            if notes.get(fid):
                prompt += (
                    f"\n[RE-DISPATCH] {notes[fid]}. The frame file may already hold a "
                    "skeleton or your own partial content — fill it directly; first "
                    "edit within 1–2 turns; no single-turn marathon.")
            rc, text = run_claude_result(
                project_dir, prompt, logger,
                extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
                skill="frame_html",
                label=fid)
            return fid, rc, text

        failures = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fid, rc, text in pool.map(run_one, remaining):
                ok = rc == 0 and _worker_done_ok(fid, text)
                if (not skeleton_mode) and ok and not os.path.isfile(
                        os.path.join(project_dir, "compositions", "frames", f"{fid}.html")):
                    ok = False
                if ok:
                    _mark_animated_storyboard(project_dir, fid)
                    remaining.remove(fid)
                    log("worker ok %s (round %s)", fid, round_no)
                else:
                    failures[fid] = (rc, text)
                    log("worker fail %s rc=%s (round %s)", fid, rc, round_no)
        if not remaining:
            break
        notes = {fid: _dispatch_note(*t) for fid, t in failures.items()}
        log("retry round %s pending=%s", round_no + 1, remaining)
    if remaining:
        reasons = ", ".join(
            f"{fid}: {notes[fid]}" for fid in sorted(remaining))
        raise RuntimeError(
            f"frames still broken after {1 + MAX_RETRY_ROUNDS} rounds: {reasons}")
