# -*- coding: utf-8 -*-
"""Non-interactive Claude Code CLI wrapper for htmlvideo worker."""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid

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
        # 2026-11-25 (6b06 review, deepseek-v4-pro-0813): intent emits ~160 tokens
        # of JSON; self-contained prompt + Skill denied skips slash/hook injection,
        # and medium×12000 caps runaway thinking while leaving >50x output headroom.
        "effort": "medium", "thinking": "12000",
        "max_output": "12000",
        "max_turns": 4, "timeout": 1800, "deny_agent": True,
        "tools": "",
        "deny_tools": ["Write", "Edit", "Bash", "Read", "Glob", "Grep", "Skill"],
    },
    "storyboard": {
        "effort": "high", "thinking": "16000",
        # Delivers ~3.4k tokens (STORYBOARD.md only; SCRIPT.md is derived in
        # Python from voiceover fields), so 16000 leaves ~12k for thinking per
        # turn — tight enough to kill the single-turn marathon thinking, 4x the
        # deliverable so no truncation. Default driver runs 1-turn text-out
        # (--tools "", max_turns=2); HTMLVIDEO_STORYBOARD_TEXT_OUT=0 uses Write.
        "max_output": "16000",
        "max_turns": 8, "timeout": 1800, "deny_agent": True,
        "tools": "Write",
        "deny_tools": ["Bash", "Read", "Glob", "Grep", "Edit", "Skill"],
    },
    "visual_design": {
        # Phase A global design contract (2026-11-26): visible output is ~900-1300
        # tokens (including ## Shared stage spec); 24000 avoids Turn-1 cap hits on
        # adaptive-thinking Qianfan while max_turns=1 eliminates 100%-amnesia
        # Turn-2 re-rolls when unsigned thinking is dropped. Zero tools (--tools "").
        # Rollback: HTMLVIDEO_CLAUDE_VISUAL_DESIGN_EFFORT=high / _MAX_OUTPUT_TOKENS=32000.
        "effort": "medium", "thinking": "24000",
        "max_output": "24000",
        "max_turns": 1, "timeout": 1800, "deny_agent": True,
        "tools": "",
        "deny_tools": ["Bash", "Glob", "Grep", "Write", "Edit", "Read", "Skill"],
    },
    "visual": {
        # 2026-11-26: per-frame shot-sequence worker emits ~350-600 tokens; all
        # inputs (design contract, blueprint, candidate rules) are inlined, so run
        # 1-turn pure text-out (--tools "", max_turns=1) at medium×20000.
        # Rollback: HTMLVIDEO_CLAUDE_VISUAL_EFFORT=high / _MAX_OUTPUT_TOKENS=32000.
        "effort": "medium", "thinking": "20000",
        "max_output": "20000",
        "max_turns": 1, "timeout": 1800, "deny_agent": True,
        "tools": "",
        "deny_tools": ["Bash", "Glob", "Grep", "Write", "Edit", "Read", "Skill"],
    },
    "visual_legacy": {
        # HTMLVIDEO_FRAME_VISUAL_DRIVER=0 single-session flow: the model edits
        # STORYBOARD.md itself via the slash skill, so it needs Write/Edit.
        "effort": "high", "thinking": "64000",
        "max_output": "32000",
        "max_turns": 100, "timeout": 1800, "deny_agent": True,
    },
    "frame_html": {
        # 2026-11-26: visible <template> HTML is 3.1k-4.5k tokens and complex frames
        # need 18k-32k thinking. 28000 triggered 50% Turn-1 cap hits (F2/F4/F5) and
        # amnesia Turn-2 re-rolls; 48000 restores >90% Attempt-1 completion while
        # TRUNCATED_RC escalates retry #2 to 64000.
        # Rollback: HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS=64000.
        "effort": "medium", "thinking": "48000",
        "max_output": "48000",
        "max_turns": 30,       # fill only; driver owns frame-selfcheck (TEXT_OUT overrides to 1)
        "timeout": 1500,
        "deny_agent": True,    # B architecture: no stage dispatches subagents anymore
        "tools": "Read,Write,Edit",
        "deny_tools": ["Bash", "Glob", "Grep", "Skill"],
    },
    "repair": {
        "effort": "high", "thinking": "40000",
        "max_output": "16000", "max_turns": 40, "timeout": 600, "deny_agent": True,
    },
    "verify": {
        # Default stays medium; A/B lower effort via HTMLVIDEO_CLAUDE_VERIFY_EFFORT=low.
        # 2026-11-26 (6b0795df): 16000 < thinking 32000 let a fix turn hit the cap
        # with zero visible output; Qianfan's unsigned thinking made Claude Code's
        # auto-resume re-think from scratch under the same cap (2×16000, 603s).
        # Align the cap with the thinking budget and kill on the FIRST empty
        # capped turn (trunc_streak=1); verify.py retries once at a higher cap.
        "effort": "medium", "thinking": "32000",
        "max_output": "32000",
        "max_turns": 40, "timeout": 1800, "deny_agent": True,
        "tools": "Read,Write,Edit",
        "deny_tools": ["Bash", "Glob", "Grep", "Skill"],
        "trunc_streak": 1,
    },
}
# TodoWrite is the legacy name; task-tracking now lives in TaskCreate/Update/
# List/Get. All of them are pure turn churn for these short deterministic skill
# flows — a verify run spent 7 turns (≈34s) just creating/ticking a todo list.
# 2026-11-24 (6b05 api-bodies): Workflow (22.9KB), ScheduleWakeup (5.0KB),
# SendMessage (4.9KB), Cron* (4.9KB), Enter/ExitWorktree (6.6KB),
# ReportFindings (2.3KB), TaskOutput/Stop (2.4KB), ListAgents (1.2KB) added
# ~50KB (~93.7%) of unused tool schemas on every non-interactive call.
# Deny the whole set so the model only sees the tools the pipeline uses.
_BASE_DENY = [
    "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "Workflow", "ScheduleWakeup", "SendMessage",
    "CronCreate", "CronDelete", "CronList",
    "EnterWorktree", "ExitWorktree", "ReportFindings",
    "ListAgents", "TaskOutput", "TaskStop",
]
_AGENT_DENY = ["Agent", "Task"]
_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

_DEFAULT_MAX_OUTPUT_TOKENS = 128000
_THINKING_SAFE_CAP = 127000
# Documented in docs/claude-cli-skill-profiles.md §7: parse-failure stdout tail
# and parsed result text are both logged truncated to 2000 chars.
_LOG_LIMIT = 2000
_TIMEOUT_GRACE_SECONDS = 5
# Whitelist for invoke-log env dumps (avoid secrets + noise like HEADLESS).
_ENV_LOG_KEYS = (
    "CLAUDE_CODE_EFFORT_LEVEL",
    "MAX_THINKING_TOKENS",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_SUBAGENT_EFFORT",
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS",
    "ANTHROPIC_LOG",
    "BUN_OPTIONS",
)

# Bun-compiled Claude binary preload: expand util.inspect so ANTHROPIC_LOG=debug
# prints full messages/system/tools instead of [Object ...].
_INSPECT_PRELOAD = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "claude_inspect_full.js"
)
_DEBUG_REQUEST_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DEBUG_REQUEST_FALSY = frozenset({"0", "false", "no", "off"})
# Long adaptive-thinking turns can sit silent for minutes after "skill invoke".
# Heartbeat + stderr progress lines keep htmlvideo_worker.log moving.
_HEARTBEAT_DEFAULT_SECONDS = 15
_STDERR_LINE_LIMIT = 300
_PROGRESS_RE = re.compile(
    r"tool_dispatch|tool_use|tool_result|unrecognized_model|"
    r"API:timing|API REQUEST|durationMs|num_turns|content_block|"
    r"thinking_tokens|\[claude-code:|Stall\]",
    re.I,
)


# Exit code for a session killed by the truncation watcher (124 = wall-clock).
TRUNCATED_RC = 125
_TRUNC_META_PREFIX = "[Your previous response had no visible output"
_THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})
_TRUNC_POLL_SECONDS = 2.0
_DEFAULT_TRUNC_KILL_STREAK = 2  # allow 1 Claude Code max_tokens auto-resume before killing


# Exit code for a session killed because the caller set its cancel_event
# (hedge race loser).
CANCELLED_RC = 130


class _TruncatedKill(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _CancelledKill(Exception):
    pass


def _session_jsonl_candidates(config_dir: str, project_dir: str, session_id: str) -> list[str]:
    projects = os.path.join(config_dir, "projects")
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(project_dir))
    direct = os.path.join(projects, slug, f"{session_id}.jsonl")
    if os.path.isfile(direct):
        return [direct]
    return glob.glob(os.path.join(projects, "*", f"{session_id}.jsonl"))


class _TruncationWatcher:
    """Tail one session jsonl and flag repeated hard-truncated (thinking-only) turns.

    2026-11-24 (6b05ba2b5d081581): newer Claude Code auto-resumes after a
    ``stop_reason="max_tokens"`` thinking-only turn without writing a ``user``
    isMeta nudge to the JSONL. The old watcher waited for the next ``mid`` to
    close the prior group, so it fired 24s *after* the model had already
    recovered, written its Edit, and finished with ``end_turn``.

    Fixed semantics:
    - Detect ``stop_reason="max_tokens"`` thinking-only turns immediately on
      the assistant entry itself (don't wait for the next message).
    - Consume the entire newly flushed chunk before deciding to kill — any
      visible block (``tool_use`` / ``text``) resets the streak to 0 so an
      already-recovered session is never retroactively killed.
    - ``max_streak`` (default 2, override ``HTMLVIDEO_CLAUDE_TRUNC_KILL_STREAK``)
      allows 1 built-in Claude Code auto-resume and kills immediately on the
      2nd consecutive thinking-only cap hit (true runaway).
    Any I/O or parse problem disables the watcher silently — never fails the
    session.
    """

    def __init__(self, session_id: str, project_dir: str, config_dir: str,
                 cap: int, logger: logging.Logger,
                 max_streak: int = _DEFAULT_TRUNC_KILL_STREAK) -> None:
        self.session_id = session_id
        self.project_dir = project_dir
        self.config_dir = config_dir
        self.cap = cap
        self.logger = logger
        self._max_streak = max(1, int(max_streak))
        self.disabled = False
        self._path: str | None = None
        self._pos = 0
        self._partial = ""
        self._group_id = None
        self._group_types: set[str] = set()
        self._group_max_out = 0
        self._group_stop = None
        self._group_counted = False
        self._streak = 0
        self._last_reason: str | None = None

    def _record_truncation(self, reason: str) -> None:
        self._streak += 1
        self._last_reason = reason
        if self._streak < self._max_streak:
            self.logger.info(
                "claude skill thinking-only truncation %d/%d session=%s (%s) "
                "— allowing auto-resume",
                self._streak,
                self._max_streak,
                self.session_id,
                reason,
            )

    def _close_group(self) -> None:
        if self._group_id is None:
            return
        visible = bool(self._group_types - _THINKING_TYPES)
        hit_cap = self._group_max_out >= self.cap or self._group_stop == "max_tokens"
        counted = self._group_counted
        out = self._group_max_out
        self._group_id = None
        self._group_types = set()
        self._group_max_out = 0
        self._group_stop = None
        self._group_counted = False
        if visible:
            self._streak = 0
            self._last_reason = None
        elif hit_cap and not counted:
            self._record_truncation(
                f"thinking-only turn hit output cap (output_tokens={out} cap={self.cap})"
            )

    def _feed(self, entry: dict) -> None:
        kind = entry.get("type")
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if kind == "assistant":
            mid = msg.get("id")
            if mid != self._group_id:
                self._close_group()
                self._group_id = mid if mid is not None else object()
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type"):
                    self._group_types.add(str(block["type"]))
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            try:
                self._group_max_out = max(self._group_max_out, int(usage.get("output_tokens") or 0))
            except (TypeError, ValueError):
                pass
            if msg.get("stop_reason"):
                self._group_stop = msg.get("stop_reason")
            visible = bool(self._group_types - _THINKING_TYPES)
            hit_cap = (
                self._group_stop == "max_tokens"
                or (
                    self._group_stop == "end_turn"
                    and self.cap > 0
                    and self._group_max_out >= int(self.cap * 0.98)
                )
            )
            if visible:
                # Recovery succeeded: visible output or tool_use emitted.
                self._streak = 0
                self._last_reason = None
            elif hit_cap and not self._group_counted:
                # Terminal cap hit (Anthropic max_tokens or Qianfan end_turn at
                # max_tokens) with thinking only — count immediately instead of
                # waiting for the next message.
                self._group_counted = True
                self._record_truncation(
                    f"thinking-only turn hit output cap "
                    f"(output_tokens={self._group_max_out} cap={self.cap})"
                )
            return
        if kind == "user":
            content = msg.get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    str(b.get("text", "")) for b in content
                    if isinstance(b, dict) and b.get("type") == "text")
            had_visible = bool(self._group_types - _THINKING_TYPES)
            was_counted = self._group_counted
            streak_before = self._streak
            self._close_group()
            if entry.get("isMeta") and text.lstrip().startswith(_TRUNC_META_PREFIX):
                # Do not double-count if _close_group or the assistant entry
                # already counted the immediately preceding assistant turn.
                if not was_counted and self._streak == streak_before and not had_visible:
                    self._record_truncation("no-visible-output nudge (thinking-only turn)")
                else:
                    self._last_reason = "no-visible-output nudge (thinking-only turn)"
            return

    def poll(self) -> str | None:
        if self.disabled:
            return None
        try:
            if self._path is None:
                found = _session_jsonl_candidates(
                    self.config_dir, self.project_dir, self.session_id)
                if not found:
                    return None
                self._path = found[0]
            with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
            if not chunk:
                return None
            data = self._partial + chunk
            lines = data.split("\n")
            self._partial = lines.pop()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    self._feed(entry)
            if self._streak >= self._max_streak and self._last_reason:
                return self._last_reason
        except Exception as exc:  # never break the session on watcher trouble
            self.disabled = True
            self.logger.debug("truncation watcher disabled session=%s: %s",
                              self.session_id, exc)
        return None


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


def _resolve_profile(
    skill: str,
    logger: logging.Logger,
    thinking_default: str | None = None,
) -> dict[str, object]:
    """``thinking_default`` replaces the profile's thinking default for one call;
    an explicit ``HTMLVIDEO_CLAUDE_<SKILL>_THINKING_TOKENS`` still wins."""
    base = _PROFILES[skill]
    prefix = f"HTMLVIDEO_CLAUDE_{skill.upper()}"
    effort = _resolve_effort(
        f"{prefix}_EFFORT", str(base["effort"]), logger
    )
    default_thinking = str(thinking_default or base["thinking"]).strip()
    thinking = os.environ.get(
        f"{prefix}_THINKING_TOKENS", default_thinking
    ).strip()
    if not thinking:
        thinking = default_thinking
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
        "tools": base.get("tools"),
        "model": model,
        "thinking_fallback": str(base["thinking"]),
        "trunc_streak": base.get("trunc_streak"),
    }


def _profile_cmd(
    prompt: str,
    max_turns: int,
    deny_agent: bool,
    model: str,
    extra_deny: list[str] | None = None,
    session_id: str | None = None,
    tools: str | None = None,
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
    ]
    # Non-interactive self-contained skills deny Skill and should not load
    # SessionStart plugin hooks (e.g. superpowers:using-superpowers, ~3.7KB of
    # contradictory "MUST invoke skill" instructions) or slash commands.
    if extra_deny and "Skill" in extra_deny:
        cmd.extend([
            "--disable-slash-commands",
            "--settings",
            '{"disableAllHooks":true,"enabledPlugins":{"superpowers@claude-plugins-official":false}}',
        ])
    if tools is not None:
        cmd.extend(["--tools", tools])
    cmd.extend([
        "--disallowedTools",
        *deny,
    ])
    if model:
        cmd.extend(["--model", model])
    if session_id:
        cmd.extend(["--session-id", session_id])
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


def _thinking_usage_fields(usage: dict) -> dict[str, object]:
    """Thinking/reasoning counters from a result ``usage`` block, flattened one
    level (``output_tokens_details.reasoning_tokens``). Empty when the backend
    reports none — the adaptive gateway usually omits them."""
    out: dict[str, object] = {}
    for key, value in usage.items():
        if isinstance(value, dict):
            for sub, sv in value.items():
                if "thinking" in sub or "reasoning" in sub:
                    out[f"{key}.{sub}"] = sv
        elif "thinking" in key or "reasoning" in key:
            out[key] = value
    return out


def _log_parsed_result(
    logger: logging.Logger,
    skill: str,
    rc: int,
    stdout: str,
    label: str | None = None,
    output_cap: int = 0,
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
    thinking_usage = _thinking_usage_fields(usage)
    if thinking_usage:
        logger.info(
            "claude skill thinking label=%s skill=%s %s",
            tag,
            skill,
            " ".join(f"{k}={v}" for k, v in thinking_usage.items()),
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
    if rc == 0 and not result.strip() and output_cap > 0:
        try:
            out_tok = int(usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            out_tok = 0
        if out_tok >= int(output_cap * 0.95):
            logger.warning(
                "claude skill empty result at output cap label=%s skill=%s "
                "output=%s cap=%s — promoting rc=0 to TRUNCATED_RC (%s)",
                tag,
                skill,
                out_tok,
                output_cap,
                TRUNCATED_RC,
            )
            return TRUNCATED_RC, ""
    return rc, result


def _kill_process_group(
    proc: subprocess.Popen[str],
    logger: logging.Logger,
    grace_seconds: float | None = None,
) -> None:
    """Terminate the whole session (claude + tool children), then escalate."""
    if proc.pid is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    grace = _TIMEOUT_GRACE_SECONDS if grace_seconds is None else max(0.05, float(grace_seconds))
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=min(grace, _TIMEOUT_GRACE_SECONDS))
    except subprocess.TimeoutExpired:
        logger.warning(
            "claude skill process group still alive after SIGKILL pgid=%s",
            pgid,
        )


def _flush_loggers(logger: logging.Logger) -> None:
    """Push heartbeat/progress lines to disk immediately (Celery can buffer)."""
    for log in (logger, logging.getLogger()):
        for handler in getattr(log, "handlers", []):
            try:
                handler.flush()
            except Exception:
                pass


def _heartbeat_seconds(logger: logging.Logger) -> int:
    """Seconds between `claude skill running` lines; ``0`` disables heartbeats."""
    raw = os.environ.get("HTMLVIDEO_CLAUDE_HEARTBEAT_SECONDS", "").strip()
    if raw == "0":
        return 0
    if not raw:
        return _HEARTBEAT_DEFAULT_SECONDS
    return _env_positive_int(
        "HTMLVIDEO_CLAUDE_HEARTBEAT_SECONDS", _HEARTBEAT_DEFAULT_SECONDS, logger
    )


def _wait_claude_proc(
    proc: subprocess.Popen[str],
    timeout: int,
    logger: logging.Logger,
    skill: str,
    label: str | None,
    watcher: _TruncationWatcher | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Wait for the Claude child while emitting heartbeat + stderr progress.

    ``watcher`` (optional) is polled every few seconds; a truncation verdict
    raises ``_TruncatedKill`` so the caller can kill the process group.
    ``cancel_event`` (optional) being set raises ``_CancelledKill``.

    Shared by every skill profile (intent / storyboard / visual / frame_html /
    repair / verify): ``communicate()`` buffers until exit, so long adaptive-
    thinking turns look hung in htmlvideo_worker.log. This polls, heartbeats
    every N seconds (``HTMLVIDEO_CLAUDE_HEARTBEAT_SECONDS``, default 15; ``0``
    disables), and re-logs matching stderr lines (tool/API) into the same logger.
    """
    tag = _log_label(label)
    heartbeat = _heartbeat_seconds(logger)
    started = time.monotonic()
    deadline = started + timeout
    last_beat = started
    last_watch = started
    stdout_chunks: list[str] = []

    def _read_stdout() -> None:
        try:
            if proc.stdout is None:
                return
            data = proc.stdout.read()
            if data:
                stdout_chunks.append(data)
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass

    def _read_stderr() -> None:
        if proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                line = raw.rstrip("\n")
                if not line or not _PROGRESS_RE.search(line):
                    continue
                logger.info(
                    "claude skill progress label=%s skill=%s %s",
                    tag,
                    skill,
                    line[:_STDERR_LINE_LIMIT],
                )
                _flush_loggers(logger)
        finally:
            try:
                proc.stderr.close()
            except Exception:
                pass

    t_out = threading.Thread(
        target=_read_stdout, name=f"claude-stdout-{tag}", daemon=True
    )
    t_err = threading.Thread(
        target=_read_stderr, name=f"claude-stderr-{tag}", daemon=True
    )
    t_out.start()
    t_err.start()
    killed_early = False
    try:
        while proc.poll() is None:
            now = time.monotonic()
            if now >= deadline:
                killed_early = True
                _kill_process_group(proc, logger, grace_seconds=0.5)
                raise subprocess.TimeoutExpired(
                    proc.args if proc.args is not None else ["claude"], timeout
                )
            if cancel_event is not None and cancel_event.is_set():
                killed_early = True
                _kill_process_group(proc, logger, grace_seconds=0.5)
                raise _CancelledKill()
            if heartbeat and (now - last_beat) >= heartbeat:
                logger.info(
                    "claude skill running label=%s skill=%s elapsed=%ss "
                    "timeout=%ss (waiting on model/tools)",
                    tag,
                    skill,
                    int(now - started),
                    timeout,
                )
                _flush_loggers(logger)
                last_beat = now
            if watcher is not None and (now - last_watch) >= _TRUNC_POLL_SECONDS:
                last_watch = now
                reason = watcher.poll()
                if reason:
                    killed_early = True
                    _kill_process_group(proc, logger, grace_seconds=0.5)
                    raise _TruncatedKill(reason)
            time.sleep(0.25)
    finally:
        if killed_early and proc.poll() is None:
            _kill_process_group(proc, logger, grace_seconds=0.5)
        join_timeout = 1.0 if killed_early else _TIMEOUT_GRACE_SECONDS
        t_out.join(timeout=join_timeout)
        t_err.join(timeout=join_timeout)
    return "".join(stdout_chunks)


def _apply_debug_request_env(env: dict[str, str]) -> None:
    """When HTMLVIDEO_CLAUDE_DEBUG_REQUEST is explicitly enabled (=1/true/yes/on),
    dump full Anthropic request/response bodies to log/api-bodies and
    claude-debug.log. Defaults OFF (2026-11-26 S1-5) to save disk IO.

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

    Always drops built-in git/PR workflow text and auto-memory prompts for this
    non-interactive pipeline, and strips any inherited ``ANTHROPIC_LOG`` from
    the worker daemon environment unless ``HTMLVIDEO_CLAUDE_DEBUG_REQUEST=1`` is
    enabled (or ``extra_env`` explicitly sets ``ANTHROPIC_LOG``) so normal runs
    do not dump multi-MB HTTP bodies into stdout and ``log/api-bodies``.
    """
    env = {
        **os.environ,
        "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }
    env.pop("ANTHROPIC_LOG", None)
    if "BUN_OPTIONS" in env and _INSPECT_PRELOAD in env["BUN_OPTIONS"]:
        cleaned = " ".join(
            tok for tok in env["BUN_OPTIONS"].split()
            if tok != f"--preload={_INSPECT_PRELOAD}"
        ).strip()
        if cleaned:
            env["BUN_OPTIONS"] = cleaned
        else:
            env.pop("BUN_OPTIONS", None)
    if extra_env:
        env.update(extra_env)
    _apply_debug_request_env(env)
    return env


def _run_profile(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None,
    skill: str,
    label: str | None = None,
    session_id: str | None = None,
    trunc_kill: bool = False,
    max_turns_override: int | None = None,
    timeout_override: int | None = None,
    cancel_event: threading.Event | None = None,
    thinking_default: str | None = None,
    tools_override: str | None = None,
) -> tuple[int, str]:
    profile = _resolve_profile(skill, logger, thinking_default)
    if max_turns_override and max_turns_override > 0:
        profile["max_turns"] = int(max_turns_override)
    if timeout_override and timeout_override > 0:
        profile["timeout"] = int(timeout_override)
    if tools_override is not None:
        profile["tools"] = tools_override
    if trunc_kill and not session_id:
        session_id = str(uuid.uuid4())
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
        "max_turns=%s model=%s timeout=%s session=%s trunc_kill=%s",
        tag,
        project_dir,
        skill,
        env.get("CLAUDE_CODE_EFFORT_LEVEL", profile["effort"]),
        env["MAX_THINKING_TOKENS"],
        max_turns,
        model or "-",
        timeout,
        session_id or "-",
        bool(trunc_kill),
    )
    cmd = _profile_cmd(
        prompt,
        max_turns,
        bool(profile["deny_agent"]),
        model,
        profile.get("deny_tools"),
        session_id=session_id,
        tools=profile.get("tools"),
    )
    watcher = None
    if trunc_kill and session_id:
        try:
            config_dir = env.get("CLAUDE_CONFIG_DIR") or os.path.join(
                os.path.expanduser("~"), ".claude")
            # Zero-tool text-out calls cannot recover across turns when unsigned
            # thinking is dropped, so default to streak=1 unless overridden.
            # A profile may pin its own default via ``trunc_streak``.
            default_streak = profile.get("trunc_streak") or (
                1 if profile.get("tools") == "" else _DEFAULT_TRUNC_KILL_STREAK
            )
            trunc_streak = _env_positive_int(
                "HTMLVIDEO_CLAUDE_TRUNC_KILL_STREAK",
                default_streak,
                logger,
            )
            watcher = _TruncationWatcher(
                session_id, project_dir, config_dir,
                _output_token_limit(env), logger, max_streak=trunc_streak)
        except Exception as exc:
            logger.debug("truncation watcher unavailable: %s", exc)
            watcher = None
    # Never log the prompt body (docs §7: start/invoke omit prompt text).
    # Env log is a whitelist — full os.environ dumps secrets (tokens) and
    # pollutes truncation assertions (e.g. HEADLESS matching HEAD).
    cmd_for_log = list(cmd)
    if len(cmd_for_log) >= 2 and cmd_for_log[-2] == "--":
        cmd_for_log[-1] = "<prompt>"
    env_for_log = {k: env[k] for k in _ENV_LOG_KEYS if k in env}
    logger.info(
        "claude skill invoke label=%s skill=%s cmd=%s env=%s",
        tag,
        skill,
        cmd_for_log,
        env_for_log,
    )
    proc = subprocess.Popen(
        cmd,
        cwd=project_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # tee progress into logger (was inherited/silent in worker log)
        text=True,
        start_new_session=True,
    )
    try:
        stdout = _wait_claude_proc(
            proc, timeout, logger, skill, label, watcher=watcher,
            cancel_event=cancel_event)
    except _CancelledKill:
        logger.warning(
            "claude skill cancelled label=%s skill=%s — killing process group "
            "(rc=%s)", tag, skill, CANCELLED_RC)
        _kill_process_group(proc, logger)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=_TIMEOUT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return CANCELLED_RC, ""
    except _TruncatedKill as trunc:
        logger.warning(
            "claude skill truncated label=%s skill=%s session=%s reason=%s "
            "— killing process group (rc=%s)",
            tag,
            skill,
            session_id,
            trunc.reason,
            TRUNCATED_RC,
        )
        _kill_process_group(proc, logger)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=_TIMEOUT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return TRUNCATED_RC, ""
    except subprocess.TimeoutExpired:
        logger.error(
            "claude skill timeout label=%s skill=%s timeout=%s",
            tag,
            skill,
            timeout,
        )
        _kill_process_group(proc, logger)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=_TIMEOUT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # Timeout: rc 124 and result text "" — partial stdout is discarded
        # (nothing parseable survives the kill).
        return 124, ""
    return _log_parsed_result(
        logger,
        skill,
        proc.returncode or 0,
        stdout or "",
        label=label,
        output_cap=_output_token_limit(env),
    )


def run_claude_result(
    project_dir: str,
    prompt: str,
    logger: logging.Logger,
    extra_env: dict[str, str] | None = None,
    skill: str | None = None,
    label: str | None = None,
    session_id: str | None = None,
    trunc_kill: bool = False,
    max_turns: int | None = None,
    timeout: int | None = None,
    cancel_event: threading.Event | None = None,
    thinking_default: str | None = None,
    tools: str | None = None,
) -> tuple[int, str]:
    """run_claude + the parsed terminal 'result' text.

    ``session_id`` pins ``--session-id``; ``trunc_kill`` tails that session's
    jsonl and kills a hard-truncated (thinking-only, output-cap) turn,
    returning ``(TRUNCATED_RC, "")``. ``max_turns`` / ``timeout`` override the
    profile values for this one call (salvage passes). Setting
    ``cancel_event`` kills the session and returns ``(CANCELLED_RC, "")``.
    ``thinking_default`` swaps the profile's thinking default for this call
    (the per-skill ``_THINKING_TOKENS`` env still wins).

    Frame workers gate on their final reply (`done OK <frame_id>`), which the
    CLI surfaces in the JSON stdout's `result` field — callers that need that
    text (driver collect/retry logic) use this; everyone else keeps run_claude.

    ``label`` is an optional caller tag (e.g. frame id) echoed in every skill
    log line so concurrent workers stay distinguishable.

    The result text is ``""`` whenever it cannot be produced: JSON parse
    failure or a wall-clock timeout (which yields ``(124, "")``). Callers
    must gate on the exit code for those cases.
    """
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    if skill not in _PROFILES:
        raise ValueError(f"unknown claude skill profile: {skill}")
    return _run_profile(
        project_dir, prompt, logger, extra_env, skill, label,
        session_id=session_id, trunc_kill=trunc_kill,
        max_turns_override=max_turns, timeout_override=timeout,
        cancel_event=cancel_event, thinking_default=thinking_default,
        tools_override=tools,
    )


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
    cap, and wall-clock timeout; it is required (``None`` or an unknown name
    raises ``ValueError``).
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
