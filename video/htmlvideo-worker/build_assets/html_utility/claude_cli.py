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

# Per-skill effort / thinking / output budget. CLAUDE_CODE_EFFORT_LEVEL in the
# child env overrides any --effort flag, and the binary reads
# MAX_THINKING_TOKENS (not CLAUDE_CODE_MAX_THINKING_TOKENS). NOTE
# (2026-11-17, api-bodies): this backend runs the model in adaptive-thinking
# mode (request body thinking:{"type":"adaptive","display":"omitted"}), so the
# gateway strips MAX_THINKING_TOKENS budget_tokens — lowering "thinking" below
# has NO effect. The only lever that reaches the request is
# CLAUDE_CODE_MAX_OUTPUT_TOKENS (sets max_tokens, hard-caps thinking + visible
# output), so every profile below pins an explicit max_output:
# - serial stages (intent/storyboard/visual/verify): 32000, the 2026-11-18 A/B
#   floor (intent 16.7s / storyboard 3.0m / visual 7.1m, all historical best).
# - frame_html: 64000 — enough output budget that a worker reading ~10k tokens
#   of dispatch + skeleton-Editing never truncates, far enough below 128k that
#   marathon thinking loses its silent runway. 128000 is the documented
#   one-line rollback.
# - repair: 16000 — small budget, reserved for assemble-failure fix-up sessions.
# NOTE (M0 conclusion fill-in point): the 58m-outage profile showed
# max_output=16000 while the SDK result read 32000; Task 2 verifies the
# profile-env -> worker-process injection chain and records the conclusion
# here.
_PROFILES: dict[str, dict[str, object]] = {
    "intent": {
        "effort": "high", "thinking": "32000",
        "max_output": "32000",  # 2026-11-18 A/B 实证的地板档（intent 16.7s 历史最优）
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
        # 免工具模式(P3):intent 只需产出 JSON 文本,禁文件/命令工具 → 单请求 ~4-6s。
        # 模型只回文本,Python 侧 parse + 落盘 + 校验(详见 intent.py)。
        "deny_tools": ["Write", "Edit", "Bash", "Read", "Glob", "Grep"],
    },
    "storyboard": {
        "effort": "high", "thinking": "64000",
        # Delivers ~3.8k tokens (STORYBOARD.md ~3.4k + SCRIPT.md ~0.4k), so 16000
        # leaves ~12k for thinking per turn — tight enough to kill the single-turn
        # marathon thinking (6.1m run showed a 317s turn), 4x the deliverable so
        # no truncation. Same rationale as verify's 32000→16000 A/B.
        "max_output": "16000",
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
    "visual": {
        "effort": "high", "thinking": "64000",
        "max_output": "32000",  # 同上（visual 7.1m 历史最优）
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
    "frame_html": {
        "effort": "high", "thinking": "64000",
        # Per-worker session cap. 48000: enough output budget that a worker
        # reading ~10k tokens of dispatch + skeleton-Editing never truncates,
        # far enough below 128k that marathon thinking loses its silent runway.
        # Tightened from 64000 (2026-11-20 P2 thinking-cap lever) — the 32k
        # truncation lesson says A/B before going lower; 48000 is the probe.
        # 64000 is the documented one-line rollback; serial stages stay 32000.
        "max_output": "48000",
        "max_turns": 60,       # per-frame fill: read + ~5 edits + selfcheck fixes
        # Per-worker wall kill-switch (a skeleton keeps a valid file on disk).
        # 2026-11-19 test: 720s was too short — on the adaptive backend the
        # thinking cap is stripped (see header note), so a worker burns 20–35k
        # thinking tokens/turn; content-dense frames need >12 min and 4/6
        # workers were killed at 720s mid-write. 1500s gives ~25 min headroom.
        "timeout": 1500,
        "deny_agent": True,    # B architecture: no stage dispatches subagents anymore
    },
    "repair": {
        "effort": "high", "thinking": "40000",
        "max_output": "16000", "max_turns": 40, "timeout": 600, "deny_agent": True,
    },
    "verify": {
        "effort": "medium", "thinking": "32000",
        # 2026-11-19: 32000 太宽 — 单回合可烧 ~27k thinking token(≈100s)才触顶,
        # verify 实测被深思考尖峰拖到 22.7m(76%=思考)。verify 不生成大内容(读报告 +
        # 小 Edit:改色值/加 data-layout-allow-overlap),16000 足够「思考 + 数个批量
        # Edit」,把尖峰压到 ~60s。A/B 更激进档 8000 走 HTMLVIDEO_CLAUDE_VERIFY_MAX_OUTPUT_TOKENS。
        "max_output": "16000", "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
}
# TodoWrite is the legacy name; task-tracking now lives in TaskCreate/Update/
# List/Get. All of them are pure turn churn for these short deterministic skill
# flows — a verify run spent 7 turns (≈34s) just creating/ticking a todo list.
# Deny the whole family so the model does the work instead of bookkeeping it.
_BASE_DENY = [
    "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
]
_AGENT_DENY = ["Agent", "Task"]
_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

_DEFAULT_MAX_OUTPUT_TOKENS = 128000
_THINKING_SAFE_CAP = 127000
# Documented in docs/claude-cli-skill-profiles.md §7: parse-failure stdout tail
# and parsed result text are both logged truncated to 2000 chars.
_LOG_LIMIT = 2000
_TIMEOUT_GRACE_SECONDS = 5

_LEGACY_CMD_LOG = (
    "claude skill start label=%s cwd=%s cmd=claude --print "
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
    """Clamp thinking against the *pre-injection* output-token ceiling.

    ``_run_profile`` calls this BEFORE the per-skill ``max_output`` is
    injected, so the ceiling seen here is the caller-inherited or
    ``extra_env`` value of ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` (default 128000)
    — not the profile cap. A value that meets or exceeds that ceiling is
    lowered to ``_THINKING_SAFE_CAP`` when it still fits, otherwise to
    ``max_output - 1``. ``0`` disables thinking and is kept.

    The profile value is applied afterwards (and ``extra_env`` re-applies
    ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` on top, else the profile wins), so a
    profile-default thinking budget may meet or exceed the final child output
    cap. That is inert on this backend: the gateway strips thinking
    budget_tokens (adaptive thinking), so only the output cap reaches the
    request.
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
        "deny_tools": list(base.get("deny_tools", [])),
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
    extra_deny: list[str] | None = None,
) -> list[str]:
    deny = list(_BASE_DENY)
    if deny_agent:
        deny.extend(_AGENT_DENY)
    if extra_deny:
        deny.extend(extra_deny)
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


def _extract_result_payload(stdout: str):
    """Return the CLI terminal result JSON object from ``stdout`` (or None).

    With ``ANTHROPIC_LOG=debug`` inherited by the child CLI process, the CLI
    interleaves ``console.log`` HTTP-fetch dumps (request/response bodies) into
    the ``--output-format json`` stream, so a whole-string ``json.loads`` fails
    even though the real result object is emitted last. Fast-path a clean
    single-object stdout; otherwise scan back-to-front for the last object that
    decodes and carries a ``result`` key (debug dumps use non-JSON syntax like
    ``body: K {`` and unquoted keys, so ``raw_decode`` skips them).
    """
    if not stdout:
        return None
    # Fast path: clean stdout is exactly one JSON object (the result).
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return payload
    # Polluted stdout: the result object is the last JSON value. Scan each
    # '{' back-to-front; only a dict with a ``result`` key counts.
    decoder = json.JSONDecoder()
    for i in range(len(stdout) - 1, -1, -1):
        if stdout[i] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stdout, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "result" in obj:
            return obj
    return None


def _log_label(label: str | None) -> str:
    return label if label else "-"


def _log_parsed_result(
    logger: logging.Logger,
    skill: str,
    rc: int,
    stdout: str,
    label: str | None = None,
) -> tuple[int, str]:
    tag = _log_label(label)
    payload = _extract_result_payload(stdout)
    if payload is None:
        logger.info(
            "claude skill stdout label=%s skill=%s rc=%s stdout=%s",
            tag,
            skill,
            rc,
            (stdout or "")[-_LOG_LIMIT:],
        )
        return rc, ""

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    subtype = payload.get("subtype")
    is_error = payload.get("is_error")
    logger.info(
        "claude skill done label=%s skill=%s rc=%s subtype=%s is_error=%s "
        "num_turns=%s duration_ms=%s input=%s output=%s cache_read=%s "
        "cache_create=%s",
        tag,
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
        "claude skill result label=%s skill=%s result=%s",
        tag,
        skill,
        result[:_LOG_LIMIT],
    )
    if subtype == "error_max_turns":
        logger.warning(
            "claude skill max turns label=%s skill=%s subtype=%s",
            tag,
            skill,
            subtype,
        )
    if rc == 0 and is_error is True:
        return 1, result
    return rc, result


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
    label: str | None = None,
) -> int:
    logger.info(_LEGACY_CMD_LOG, _log_label(label), project_dir)
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
    label: str | None = None,
) -> tuple[int, str]:
    profile = _resolve_profile(skill, logger)
    env = _child_env(extra_env)
    env["CLAUDE_CODE_EFFORT_LEVEL"] = str(profile["effort"])
    env["MAX_THINKING_TOKENS"] = str(profile["thinking"])
    # Re-apply extra_env so caller overrides still beat profile defaults.
    if extra_env:
        env.update(extra_env)
    # Clamp the thinking budget against the output limit the caller inherited
    # or injected (default 128000). The per-skill max_output cap is applied
    # AFTER the clamp: CLAUDE_CODE_MAX_OUTPUT_TOKENS hard-caps thinking +
    # visible output at the API layer, so the clamp only needs to keep
    # MAX_THINKING_TOKENS below that request ceiling (the adaptive backend
    # strips budget_tokens anyway — see the header note).
    env["MAX_THINKING_TOKENS"] = _clamp_thinking(
        str(env.get("MAX_THINKING_TOKENS", profile["thinking"])),
        str(profile["thinking_fallback"]),
        _output_token_limit(env),
        logger,
    )
    if profile.get("max_output"):
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(profile["max_output"])
    # extra_env stays authoritative for the output cap (caller override beats
    # the profile default). MAX_THINKING_TOKENS is not re-applied here: the
    # clamp above must stay authoritative for the thinking budget.
    if extra_env and "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in extra_env:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = extra_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
    # Re-run after the last env merge so BUN_OPTIONS is not wiped.
    _apply_debug_request_env(env)
    max_turns = int(profile["max_turns"])
    timeout = int(profile["timeout"])
    model = str(profile["model"])
    tag = _log_label(label)
    logger.info(
        "claude skill start label=%s cwd=%s skill=%s effort=%s thinking=%s "
        "max_turns=%s model=%s timeout=%s",
        tag,
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
        profile.get("deny_tools"),
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
            "claude skill timeout label=%s skill=%s timeout=%s",
            tag,
            skill,
            timeout,
        )
        _kill_process_group(proc, logger)
        try:
            proc.communicate(timeout=_TIMEOUT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # Timeout: rc 124 and result text "" — the partially captured stdout
        # is discarded (nothing parseable survives the kill).
        return 124, ""
    return _log_parsed_result(
        logger, skill, proc.returncode or 0, stdout or "", label=label
    )


def run_claude_result(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None = None,
    skill: str | None = None,
    label: str | None = None,
) -> tuple[int, str]:
    """run_claude + the parsed terminal 'result' text.

    Frame workers gate on their final reply (`done OK <frame_id>`), which the
    CLI surfaces in the JSON stdout's `result` field — callers that need that
    text (driver collect/retry logic) use this; everyone else keeps run_claude.

    ``label`` is an optional caller tag (e.g. frame id) echoed in every skill
    log line so concurrent workers stay distinguishable.

    The result text is ``""`` whenever it cannot be produced: JSON parse
    failure, the legacy (profile-off) path, or a wall-clock timeout (which
    yields ``(124, "")``). Callers must gate on the exit code for those cases.
    """
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    if skill is None or _profile_off():
        return _run_legacy(project_dir, prompt, logger, extra_env, label), ""
    if skill not in _PROFILES:
        raise ValueError(f"unknown claude skill profile: {skill}")
    return _run_profile(project_dir, prompt, logger, extra_env, skill, label)


def run_claude(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None = None,
    skill: str | None = None,
    label: str | None = None,
) -> int:
    """Invoke Claude Code non-interactively.

    ``skill`` selects a per-skill effort, thinking budget, output cap, turn
    cap, and wall-clock timeout. ``skill=None`` or
    ``HTMLVIDEO_CLAUDE_PROFILE=off`` keeps the legacy command and environment.
    ``extra_env`` is applied last on top of the inherited environment. The
    thinking budget is clamped against the *pre-injection* output ceiling —
    the ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` value inherited or injected via
    ``extra_env`` — before the profile's own ``max_output`` is applied. On
    this backend the thinking budget is inert anyway (adaptive thinking
    strips budget_tokens; the output cap is the effective lever).
    ``label`` is forwarded to skill logs (see ``run_claude_result``).
    """
    rc, _result = run_claude_result(
        project_dir, prompt, logger, extra_env, skill, label
    )
    return rc
