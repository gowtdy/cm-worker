# -*- coding: utf-8 -*-
"""Non-interactive Claude Code CLI wrapper for htmlvideo worker."""

from __future__ import annotations

import logging
import shutil
import subprocess


def run_claude(project_dir: str, prompt: str, logger: logging.Logger) -> int:
    """Invoke Claude Code non-interactively (same flags as run_aiflow_build_skills)."""
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "text",
        prompt,
    ]
    logger.info(
        "claude skill start cwd=%s cmd=claude --print "
        "--dangerously-skip-permissions --permission-mode bypassPermissions "
        "--output-format text <prompt>",
        project_dir,
    )
    proc = subprocess.run(cmd, cwd=project_dir)
    return proc.returncode
