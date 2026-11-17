# -*- coding: utf-8 -*-
"""Non-interactive Claude Code CLI wrapper for htmlvideo worker."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess


def run_claude(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Invoke Claude Code non-interactively (same flags as run_aiflow_build_skills).

    ``extra_env`` adds per-skill environment overrides on top of the worker's
    inherited environment (e.g. ``CLAUDE_CODE_SUBAGENT_EFFORT`` to cap the
    reasoning effort of subagents for a specific skill only).
    """
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
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    proc = subprocess.run(cmd, cwd=project_dir, env=env)
    return proc.returncode
