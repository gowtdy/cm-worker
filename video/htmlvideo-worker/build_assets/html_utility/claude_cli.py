# -*- coding: utf-8 -*-
"""Non-interactive Claude Code CLI wrapper for htmlvideo worker."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time

# Per-skill effort / thinking budget. CLAUDE_CODE_EFFORT_LEVEL in the child
# env overrides any --effort flag, and the binary reads MAX_THINKING_TOKENS
# (not CLAUDE_CODE_MAX_THINKING_TOKENS). NOTE (2026-11-17, api-bodies): this
# backend runs the model in adaptive-thinking mode (request body
# thinking:{"type":"adaptive","display":"omitted"}), so the gateway strips
# MAX_THINKING_TOKENS budget_tokens — lowering "thinking" below has NO effect.
# The only lever that reaches the request is CLAUDE_CODE_MAX_OUTPUT_TOKENS
# (sets max_tokens, hard-caps thinking + visible output). frame_html therefore
# pins an explicit max_output.
_PROFILES: dict[str, dict[str, object]] = {
    "intent": {
        "effort": "high",
        # Small positive budget: effort + thinking=0 can raise
        # effort_requires_thinking (400) on Claude Code.
        "thinking": "32000",
        "max_turns": 100,
        "timeout": 1800,
        "deny_agent": True,
    },
    "storyboard": {
        "effort": "high",
        "thinking": "64000",
        "max_turns": 100,
        "timeout": 1800,
        "deny_agent": True,
    },
    "visual": {
        "effort": "high",
        "thinking": "64000",
        "max_turns": 100,
        "timeout": 1800,
        "deny_agent": True,
    },
    "frame_html": {
        "effort": "high",
        "thinking": "64000",
        # The only lever that actually caps the 8–16m marathon-thinking turns
        # (request body shows thinking:{type:adaptive}, budget_tokens stripped).
        # 16000 ≈ ~5m/turn at ~50 tok/s; A/B via HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS.
        "max_output": "16000",
        "max_turns": 150,
        "timeout": 1800,
        "deny_agent": False,
    },
    "verify": {
        "effort": "medium",
        "thinking": "32000",
        "max_turns": 100,
        "timeout": 1800,
        "deny_agent": True,
    },
}
_BASE_DENY = ["WebFetch", "WebSearch", "NotebookEdit", "TodoWrite"]
_AGENT_DENY = ["Agent", "Task"]
_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

_DEFAULT_MAX_OUTPUT_TOKENS = 128000
_THINKING_SAFE_CAP = 127000
_LOG_LIMIT = 127000
_TIMEOUT_GRACE_SECONDS = 5

_LEGACY_CMD_LOG = (
    "claude skill start cwd=%s cmd=claude --print "
    "--dangerously-skip-permissions --permission-mode bypassPermissions "
    "--output-format text <prompt>"
)

# Bun-compiled Claude binary preload: expand util.inspect so ANTHROPIC_LOG=debug
# prints full messages/system/tools instead of [Object ...].
_INSPECT_PRELOAD = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "claude_inspect_full.js"
)
_DEBUG_REQUEST_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _profile_off() -> bool:
    return os.environ.get("HTMLVIDEO_CLAUDE_PROFILE", "").strip().lower() == "off"


def _env_positive_int(name: str, default: int, logger: logging.Logger) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("invalid %s=%r; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%s <= 0; using %s", name, value, default)
        return default
    return value


def _resolve_effort(name: str, default: str, logger: logging.Logger) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    effort = raw.strip()
    if not effort:
        return default
    if effort not in _ALLOWED_EFFORTS:
        logger.warning(
            "invalid %s=%r; using %s (allowed: %s)",
            name,
            raw,
            default,
            ",".join(sorted(_ALLOWED_EFFORTS)),
        )
        return default
    return effort


def _output_token_limit(env: dict[str, str]) -> int:
    raw = str(env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "")).strip()
    if not raw:
        return _DEFAULT_MAX_OUTPUT_TOKENS
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_MAX_OUTPUT_TOKENS
    return n if n > 0 else _DEFAULT_MAX_OUTPUT_TOKENS


def _clamp_thinking(
    thinking: str,
    fallback: str,
    max_output: int,
    logger: logging.Logger,
) -> str:
    """Keep the thinking budget strictly under the output-token limit.

    A value that meets or exceeds the assumed/default output cap is lowered
    to ``_THINKING_SAFE_CAP`` when that cap still fits, otherwise to
    ``max_output - 1``. ``0`` disables thinking and is kept.
    """
    try:
        n = int(str(thinking).strip())
    except (TypeError, ValueError):
        logger.warning(
            "invalid MAX_THINKING_TOKENS=%r; using %s", thinking, fallback
        )
        n = int(fallback)
    if n < 0:
        logger.warning("MAX_THINKING_TOKENS %s < 0; using 0", n)
        return "0"
    if n == 0:
        return "0"
    if n < max_output and n < _DEFAULT_MAX_OUTPUT_TOKENS:
        return str(n)
    if _THINKING_SAFE_CAP < max_output:
        clamped = _THINKING_SAFE_CAP
    else:
        clamped = max(0, max_output - 1)
    logger.warning(
        "MAX_THINKING_TOKENS %s >= %s; clamped to %s",
        n,
        min(max_output, _DEFAULT_MAX_OUTPUT_TOKENS),
        clamped,
    )
    return str(clamped)


def _resolve_profile(skill: str, logger: logging.Logger) -> dict[str, object]:
    base = _PROFILES[skill]
    prefix = f"HTMLVIDEO_CLAUDE_{skill.upper()}"
    effort = _resolve_effort(
        f"{prefix}_EFFORT", str(base["effort"]), logger
    )
    thinking = os.environ.get(
        f"{prefix}_THINKING_TOKENS", str(base["thinking"])
    ).strip()
    if not thinking:
        thinking = str(base["thinking"])
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    max_output = os.environ.get(
        f"{prefix}_MAX_OUTPUT_TOKENS", str(base.get("max_output", ""))
    ).strip()
    return {
        "effort": effort,
        "thinking": thinking,
        "max_output": max_output,
        "max_turns": _env_positive_int(
            f"{prefix}_MAX_TURNS", int(base["max_turns"]), logger
        ),
        "timeout": _env_positive_int(
            f"{prefix}_TIMEOUT", int(base["timeout"]), logger
        ),
        "deny_agent": bool(base["deny_agent"]),
        "model": model,
        "thinking_fallback": str(base["thinking"]),
    }


def _legacy_cmd(prompt: str) -> list[str]:
    return [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "text",
        prompt,
    ]


def _profile_cmd(
    prompt: str,
    max_turns: int,
    deny_agent: bool,
    model: str,
) -> list[str]:
    deny = list(_BASE_DENY)
    if deny_agent:
        deny.extend(_AGENT_DENY)
    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--exclude-dynamic-system-prompt-sections",
        "--disallowedTools",
        *deny,
    ]
    if model:
        cmd.extend(["--model", model])
    # `--` keeps the prompt from being swallowed by --disallowedTools.
    cmd.extend(["--", prompt])
    return cmd


def _log_parsed_result(
    logger: logging.Logger,
    skill: str,
    rc: int,
    stdout: str,
) -> int:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        logger.info(
            "claude skill stdout skill=%s rc=%s stdout=%s",
            skill,
            rc,
            (stdout or "")[-_LOG_LIMIT:],
        )
        return rc

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    subtype = payload.get("subtype")
    is_error = payload.get("is_error")
    logger.info(
        "claude skill done skill=%s rc=%s subtype=%s is_error=%s num_turns=%s "
        "duration_ms=%s input=%s output=%s cache_read=%s cache_create=%s",
        skill,
        rc,
        subtype,
        is_error,
        payload.get("num_turns"),
        payload.get("duration_ms"),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cache_creation_input_tokens"),
    )
    result = payload.get("result", "")
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    logger.info(
        "claude skill result skill=%s result=%s", skill, result[:_LOG_LIMIT]
    )
    if subtype == "error_max_turns":
        logger.warning(
            "claude skill max turns skill=%s subtype=%s", skill, subtype
        )
    if rc == 0 and is_error is True:
        return 1
    return rc


def _kill_process_group(proc: subprocess.Popen[str], logger: logging.Logger) -> None:
    """Terminate the whole session (claude + tool children), then escalate."""
    if proc.pid is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + _TIMEOUT_GRACE_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=_TIMEOUT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "claude skill process group still alive after SIGKILL pgid=%s",
            pgid,
        )


def _apply_debug_request_env(env: dict[str, str]) -> None:
    """When HTMLVIDEO_CLAUDE_DEBUG_REQUEST is on, dump full Anthropic request bodies.

    Claude Code is a Bun-compiled binary, so we set BUN_OPTIONS=--preload=...
    (equals form) rather than NODE_OPTIONS=--require.
    """
    raw = env.get("HTMLVIDEO_CLAUDE_DEBUG_REQUEST", "")
    if raw.strip().lower() not in _DEBUG_REQUEST_TRUTHY:
        return
    env["ANTHROPIC_LOG"] = "debug"
    preload_flag = f"--preload={_INSPECT_PRELOAD}"
    existing = env.get("BUN_OPTIONS", "").strip()
    if preload_flag in existing.split():
        return
    env["BUN_OPTIONS"] = (
        f"{existing} {preload_flag}".strip() if existing else preload_flag
    )


def _child_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build the Claude child environment.

    Always drops built-in git/PR workflow text from the system prompt for this
    non-interactive pipeline. ``extra_env`` wins last, then debug-request
    expansion runs so the flag can come from either the parent env or
    ``extra_env``.
    """
    env = {
        **os.environ,
        "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
    }
    if extra_env:
        env.update(extra_env)
    _apply_debug_request_env(env)
    return env


def _run_legacy(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None,
) -> int:
    logger.info(_LEGACY_CMD_LOG, project_dir)
    proc = subprocess.run(
        _legacy_cmd(prompt), cwd=project_dir, env=_child_env(extra_env)
    )
    return proc.returncode


def _run_profile(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None,
    skill: str,
) -> int:
    profile = _resolve_profile(skill, logger)
    env = _child_env(extra_env)
    env["CLAUDE_CODE_EFFORT_LEVEL"] = str(profile["effort"])
    env["MAX_THINKING_TOKENS"] = str(profile["thinking"])
    if profile.get("max_output"):
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(profile["max_output"])
    # Re-apply extra_env so caller overrides still beat profile defaults.
    if extra_env:
        env.update(extra_env)
    # Re-run after the second extra_env merge so BUN_OPTIONS is not wiped.
    _apply_debug_request_env(env)
    env["MAX_THINKING_TOKENS"] = _clamp_thinking(
        str(env.get("MAX_THINKING_TOKENS", profile["thinking"])),
        str(profile["thinking_fallback"]),
        _output_token_limit(env),
        logger,
    )
    max_turns = int(profile["max_turns"])
    timeout = int(profile["timeout"])
    model = str(profile["model"])
    logger.info(
        "claude skill start cwd=%s skill=%s effort=%s thinking=%s "
        "max_turns=%s model=%s timeout=%s",
        project_dir,
        skill,
        env.get("CLAUDE_CODE_EFFORT_LEVEL", profile["effort"]),
        env["MAX_THINKING_TOKENS"],
        max_turns,
        model or "-",
        timeout,
    )
    cmd = _profile_cmd(
        prompt,
        max_turns,
        bool(profile["deny_agent"]),
        model,
    )
    proc = subprocess.Popen(
        cmd,
        cwd=project_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error(
            "claude skill timeout skill=%s timeout=%s", skill, timeout
        )
        _kill_process_group(proc, logger)
        try:
            proc.communicate(timeout=_TIMEOUT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return 124
    return _log_parsed_result(logger, skill, proc.returncode or 0, stdout or "")


def run_claude(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None = None,
    skill: str | None = None,
) -> int:
    """Invoke Claude Code non-interactively.

    ``skill`` selects a per-skill effort, thinking budget, turn cap, and
    wall-clock timeout. ``skill=None`` or ``HTMLVIDEO_CLAUDE_PROFILE=off``
    keeps the legacy command and environment. ``extra_env`` is applied last
    on top of the inherited environment; the thinking budget is then clamped
    so it stays under ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``.
    """
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    if skill is None or _profile_off():
        return _run_legacy(project_dir, prompt, logger, extra_env)
    if skill not in _PROFILES:
        raise ValueError(f"unknown claude skill profile: {skill}")
    return _run_profile(project_dir, prompt, logger, extra_env, skill)
