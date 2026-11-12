# -*- coding: utf-8 -*-
"""Frame HTML build helpers for htmlvideo worker."""

from __future__ import annotations

import logging
import os
import re
import subprocess

from html_utility.claude_cli import run_claude

STORYBOARD_FILENAME = "STORYBOARD.md"
FRAME_FILENAME = "frame.md"
HYPERFRAMES_JSON = "hyperframes.json"
INDEX_FILENAME = "index.html"
VIDEO_DIRECTION_HEADING = "## Video direction"
FRAME_PACKETS_DIR = os.path.join(".hyperframes", "frame-packets")
FRAME_PACKETS_ROLE = "_role.md"
STATUS_RE = re.compile(r"^- status:\s*(\w+)", re.MULTILINE)


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
   re-dispatch that worker once with the same prompt; still missing → stop
   and report.
5. After each worker lands its HTML, mark that frame animated in
   STORYBOARD.md (you own storyboard status; workers never edit it).

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
    workers. Assembles ``index.html`` if the skill did not. Raises on missing
    prerequisites, CLI failure, or gate failure. Returns the path to
    ``index.html``.
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

    prompt = _build_frame_html_prompt()
    rc = run_claude(project_dir, prompt, logger)
    if rc != 0:
        raise RuntimeError(f"claude build-frame-html failed rc:{rc}")

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