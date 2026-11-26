# -*- coding: utf-8 -*-
"""Profile and usage-log behavior of run_claude."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import unittest
from unittest import mock

from html_utility.claude_cli import _INSPECT_PRELOAD, run_claude

PROMPT = "PROMPT_SECRET_DO_NOT_LOG"
CWD = "/tmp/htmlvideo-proj"

# Mirrors html_utility.claude_cli._PROFILES (post-clamp thinking where needed).
# Tuple: effort, thinking, max_turns, timeout, deny_agent, max_output, tools
_PROFILES = {
    "intent": ("medium", "12000", "4", 1800, True, "12000", ""),
    "storyboard": ("high", "16000", "8", 1800, True, "16000", "Write"),
    "visual_design": ("medium", "24000", "1", 1800, True, "24000", ""),
    "visual": ("medium", "20000", "1", 1800, True, "20000", ""),
    "visual_legacy": ("high", "64000", "100", 1800, True, "32000", None),
    "frame_html": ("medium", "48000", "30", 1500, True, "48000", "Read,Write,Edit"),
    "repair": ("high", "40000", "40", 600, True, "16000", None),
    "verify": ("medium", "32000", "40", 1800, True, "32000", "Read,Write,Edit"),
}
_AGENT_DENY = ["Agent", "Task"]
_FORBIDDEN = [
    "--effort",
    "--bare",
    "--no-session-persistence",
    "--max-budget-usd",
    "--max-thinking-tokens",
    "--system-prompt",
]
_CLEAR_ENV = (
    "MAX_THINKING_TOKENS",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_SUBAGENT_EFFORT",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_MAX_THINKING_TOKENS",
    "ANTHROPIC_LOG",
    "BUN_OPTIONS",
    "HTMLVIDEO_CLAUDE_HEARTBEAT_SECONDS",
)


def _result_json(**overrides) -> str:
    payload = {
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "duration_ms": 1500,
        "result": "visible result",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 22,
            "cache_read_input_tokens": 33,
            "cache_creation_input_tokens": 44,
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


class RunClaudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in list(os.environ):
            if key.startswith("HTMLVIDEO_CLAUDE_") or key in _CLEAR_ENV:
                del os.environ[key]
        self.which = mock.patch(
            "html_utility.claude_cli.shutil.which",
            return_value="/usr/bin/claude",
        ).start()
        self.run_mock = mock.patch(
            "html_utility.claude_cli.subprocess.run"
        ).start()
        self.popen_mock = mock.patch(
            "html_utility.claude_cli.subprocess.Popen"
        ).start()
        self.logger = logging.getLogger("test.claude_cli")
        self.addCleanup(mock.patch.stopall)

    def tearDown(self) -> None:
        self._env.stop()

    def _completed_popen(self, stdout: str = "", rc: int = 0) -> mock.Mock:
        proc = mock.Mock()
        proc.pid = 4242
        proc.returncode = rc
        proc.args = ["claude"]
        proc.poll.return_value = rc  # already exited → wait loop returns immediately
        out = mock.Mock()
        out.read.return_value = stdout
        err = mock.Mock()
        err.__iter__ = mock.Mock(return_value=iter([]))
        proc.stdout = out
        proc.stderr = err
        self.popen_mock.return_value = proc
        return proc

    def _invoke(
        self,
        skill: str = "intent",
        extra_env: dict[str, str] | None = None,
        stdout: str | None = None,
        rc: int = 0,
        prompt: str = PROMPT,
    ):
        if stdout is None:
            stdout = _result_json()
        self._completed_popen(stdout=stdout, rc=rc)
        with self.assertLogs("test.claude_cli", level="INFO") as logs:
            code = run_claude(
                CWD, prompt, self.logger, extra_env=extra_env, skill=skill
            )
        args, kwargs = self.popen_mock.call_args
        return code, logs, args[0], kwargs

    def test_missing_cli_raises(self) -> None:
        self.which.return_value = None
        with self.assertRaises(RuntimeError):
            run_claude(CWD, PROMPT, self.logger, skill="intent")
        self.run_mock.assert_not_called()
        self.popen_mock.assert_not_called()

    def test_unknown_skill_raises(self) -> None:
        with self.assertRaises(ValueError):
            run_claude(CWD, PROMPT, self.logger, skill="not-a-skill")
        self.run_mock.assert_not_called()
        self.popen_mock.assert_not_called()

    def test_skill_none_raises(self) -> None:
        with self.assertRaises(ValueError):
            run_claude(CWD, PROMPT, self.logger)
        self.run_mock.assert_not_called()
        self.popen_mock.assert_not_called()

    def test_profile_env_off_is_ignored(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_PROFILE"] = "off"
        code, _, cmd, kwargs = self._invoke(skill="intent")
        self.assertEqual(code, 0)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        self.run_mock.assert_not_called()

    def test_each_skill_profile(self) -> None:
        os.environ["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        for skill, (effort, thinking, turns, timeout, deny_agent, max_output, tools) in _PROFILES.items():
            with self.subTest(skill=skill):
                self.popen_mock.reset_mock()
                code, logs, cmd, kwargs = self._invoke(skill=skill)
                self.assertEqual(code, 0)
                self.assertEqual(kwargs["cwd"], CWD)
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertTrue(kwargs["text"])
                self.assertTrue(kwargs["start_new_session"])
                self.assertEqual(kwargs["stderr"], subprocess.PIPE)
                self.assertEqual(cmd[0], "claude")
                self.assertIn("--dangerously-skip-permissions", cmd)
                self.assertEqual(
                    cmd[cmd.index("--permission-mode") + 1], "bypassPermissions"
                )
                self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
                self.assertEqual(cmd[cmd.index("--max-turns") + 1], turns)
                self.assertIn("--exclude-dynamic-system-prompt-sections", cmd)
                self.assertNotIn("--model", cmd)
                for flag in _FORBIDDEN:
                    self.assertNotIn(flag, cmd)
                if tools is not None:
                    self.assertIn("--tools", cmd)
                    self.assertEqual(cmd[cmd.index("--tools") + 1], tools)
                else:
                    self.assertNotIn("--tools", cmd)
                from html_utility.claude_cli import _BASE_DENY, _PROFILES as REAL

                deny = list(_BASE_DENY)
                if deny_agent:
                    deny.extend(_AGENT_DENY)
                deny.extend(REAL[skill].get("deny_tools") or [])
                deny_at = cmd.index("--disallowedTools")
                self.assertEqual(cmd[deny_at + 1 : deny_at + 1 + len(deny)], deny)
                self.assertEqual(cmd[deny_at + 1 + len(deny)], "--")
                self.assertEqual(cmd[-2:], ["--", PROMPT])
                self.assertEqual(cmd.count("--disallowedTools"), 1)
                env = kwargs["env"]
                self.assertIsNot(env, os.environ)
                self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], effort)
                self.assertEqual(env["MAX_THINKING_TOKENS"], thinking)
                self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], max_output)
                self.assertEqual(env["CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"], "1")
                self.assertNotIn("CLAUDE_CODE_MAX_THINKING_TOKENS", env)
                self.assertEqual(os.environ["CLAUDE_CODE_EFFORT_LEVEL"], "max")
                proc = self.popen_mock.return_value
                proc.poll.assert_called()
                joined = "\n".join(logs.output)
                self.assertIn(f"skill={skill}", joined)
                self.assertIn(f"effort={effort}", joined)
                self.assertIn(f"thinking={thinking}", joined)
                self.assertIn(f"max_turns={turns}", joined)
                self.assertIn("model=-", joined)
                self.assertIn(f"timeout={timeout}", joined)
                self.assertNotIn(PROMPT, joined)
                self.assertIn("label=-", joined)
                self.assertIn(
                    "claude skill done label=- skill=%s rc=0 subtype=success "
                    "is_error=False num_turns=2 duration_ms=1500 "
                    "input=11 output=22 cache_read=33 cache_create=44" % skill,
                    joined,
                )
                self.assertIn(
                    "claude skill result label=- skill=%s result=visible result"
                    % skill,
                    joined,
                )

    def test_profile_max_output_env(self):
        from html_utility.claude_cli import _resolve_profile
        logger = logging.getLogger("t")
        # frame_html default cap reaches the child env
        prof = _resolve_profile("frame_html", logger)
        self.assertEqual(prof["max_output"], "48000")
        # per-stage override beats the default
        with mock.patch.dict(os.environ, {"HTMLVIDEO_CLAUDE_FRAME_HTML_MAX_OUTPUT_TOKENS": "128000"}):
            prof = _resolve_profile("frame_html", logger)
            self.assertEqual(prof["max_output"], "128000")
        self.assertEqual(_resolve_profile("intent", logger)["max_output"], "12000")
        self.assertEqual(_resolve_profile("visual_design", logger)["max_output"], "24000")
        self.assertEqual(_resolve_profile("visual", logger)["max_output"], "20000")
        self.assertEqual(_resolve_profile("storyboard", logger)["max_output"], "16000")
        # 2026-11-26 (6b0795df): verify cap aligned with its 32000 thinking budget
        self.assertEqual(_resolve_profile("verify", logger)["max_output"], "32000")

    def test_visual_thinking_default_per_call_and_env_rollback(self):
        from html_utility.claude_cli import _PROFILES as REAL, _resolve_profile
        logger = logging.getLogger("t")
        self.assertEqual(_resolve_profile("visual", logger)["thinking"], "20000")
        self.assertEqual(
            _resolve_profile("visual", logger, "32000")["thinking"], "32000")
        self.assertIn("Skill", REAL["visual"]["deny_tools"])
        self.assertIn("Read", REAL["visual"]["deny_tools"])
        self.assertIn("Skill", REAL["intent"]["deny_tools"])
        self.assertEqual(
            REAL["frame_html"]["deny_tools"], ["Bash", "Glob", "Grep", "Skill"])
        self.assertNotIn("deny_tools", REAL["visual_legacy"])  # legacy edits STORYBOARD
        with mock.patch.dict(
                os.environ, {"HTMLVIDEO_CLAUDE_VISUAL_THINKING_TOKENS": "64000"}):
            self.assertEqual(_resolve_profile("visual", logger)["thinking"], "64000")
            self.assertEqual(
                _resolve_profile("visual", logger, "32000")["thinking"], "64000")

    def test_run_claude_result_thinking_default_reaches_env(self):
        from html_utility.claude_cli import run_claude_result
        self._completed_popen(stdout=_result_json())
        with self.assertLogs("test.claude_cli", level="INFO"):
            run_claude_result(CWD, PROMPT, self.logger, skill="visual",
                              thinking_default="32000")
        env = self.popen_mock.call_args.kwargs["env"]
        self.assertEqual(env["MAX_THINKING_TOKENS"], "32000")

    def test_thinking_usage_logged_when_present(self):
        usage = {"input_tokens": 1, "output_tokens": 2,
                 "output_tokens_details": {"reasoning_tokens": 7}}
        _, logs, _, _ = self._invoke(skill="visual", stdout=_result_json(usage=usage))
        self.assertIn(
            "claude skill thinking label=- skill=visual "
            "output_tokens_details.reasoning_tokens=7", "\n".join(logs.output))
        _, logs, _, _ = self._invoke(skill="visual")
        self.assertNotIn("claude skill thinking", "\n".join(logs.output))

    def test_debug_request_env_sets_anthropic_log_and_bun_preload(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_DEBUG_REQUEST"] = "1"
        _, _, _, kwargs = self._invoke(skill="intent")
        env = kwargs["env"]
        self.assertEqual(env["ANTHROPIC_LOG"], "debug")
        self.assertEqual(
            env["BUN_OPTIONS"], f"--preload={_INSPECT_PRELOAD}"
        )
        self.assertTrue(os.path.isfile(_INSPECT_PRELOAD))

    def test_debug_request_via_extra_env_appends_existing_bun_options(self) -> None:
        _, _, _, kwargs = self._invoke(
            skill="intent",
            extra_env={
                "HTMLVIDEO_CLAUDE_DEBUG_REQUEST": "yes",
                "BUN_OPTIONS": "--smol",
            },
        )
        env = kwargs["env"]
        self.assertEqual(env["ANTHROPIC_LOG"], "debug")
        self.assertEqual(
            env["BUN_OPTIONS"], f"--smol --preload={_INSPECT_PRELOAD}"
        )

    def test_debug_request_on_by_default_and_zero_disables(self) -> None:
        # Default ON: unset HTMLVIDEO_CLAUDE_DEBUG_REQUEST enables ANTHROPIC_LOG + preload
        _, _, _, kwargs_on = self._invoke(skill="intent")
        env_on = kwargs_on["env"]
        self.assertEqual(env_on["ANTHROPIC_LOG"], "debug")
        self.assertEqual(env_on["BUN_OPTIONS"], f"--preload={_INSPECT_PRELOAD}")
        # Explicit =0 disables and strips inherited ANTHROPIC_LOG + preload
        self.popen_mock.reset_mock()
        os.environ["ANTHROPIC_LOG"] = "debug"
        os.environ["BUN_OPTIONS"] = f"--smol --preload={_INSPECT_PRELOAD}"
        os.environ["HTMLVIDEO_CLAUDE_DEBUG_REQUEST"] = "0"
        _, _, _, kwargs_off = self._invoke(skill="intent")
        env_off = kwargs_off["env"]
        self.assertNotIn("ANTHROPIC_LOG", env_off)
        self.assertEqual(env_off.get("BUN_OPTIONS"), "--smol")

    def test_empty_result_at_output_cap_promotes_to_truncated_rc(self) -> None:
        from html_utility.claude_cli import TRUNCATED_RC, run_claude_result
        self._completed_popen(
            stdout=_result_json(
                result="",
                num_turns=1,
                usage={"input_tokens": 100, "output_tokens": 48000},
            ),
            rc=0,
        )
        with self.assertLogs("test.claude_cli", level="INFO") as logs:
            rc, text = run_claude_result(CWD, PROMPT, self.logger, skill="frame_html")
        self.assertEqual((rc, text), (TRUNCATED_RC, ""))
        self.assertIn("promoting rc=0 to TRUNCATED_RC", "\n".join(logs.output))

    def test_zero_tool_profile_defaults_trunc_streak_to_one(self) -> None:
        from html_utility import claude_cli
        self._completed_popen(stdout=_result_json())
        captured = {}

        class SpyWatcher(claude_cli._TruncationWatcher):
            def __init__(self, *a, **kw):
                captured["max_streak"] = kw.get("max_streak")
                super().__init__(*a, **kw)

        with mock.patch.object(claude_cli, "_TruncationWatcher", SpyWatcher), \
             self.assertLogs("test.claude_cli", level="INFO"):
            claude_cli.run_claude_result(
                CWD, PROMPT, self.logger, skill="visual_design", trunc_kill=True
            )
            self.assertEqual(captured["max_streak"], 1)
            self.popen_mock.reset_mock()
            self._completed_popen(stdout=_result_json())
            claude_cli.run_claude_result(
                CWD, PROMPT, self.logger, skill="verify", trunc_kill=True
            )
            # verify pins trunc_streak=1 (no amnesiac auto-resume re-think)
            self.assertEqual(captured["max_streak"], 1)
            self.popen_mock.reset_mock()
            self._completed_popen(stdout=_result_json())
            claude_cli.run_claude_result(
                CWD, PROMPT, self.logger, skill="repair", trunc_kill=True
            )
            self.assertEqual(captured["max_streak"], 2)

    def test_intent_self_contained_prompt_disables_slash_and_hooks(self) -> None:
        from html_utility import intent as intent_mod
        p = intent_mod._build_identify_intent_prompt("Buy quality companies.")
        self.assertFalse(p.lstrip().startswith("/"))
        self.assertNotIn("/aiflow-identify-intent", p)
        _, _, cmd, _ = self._invoke(skill="intent")
        self.assertIn("--disable-slash-commands", cmd)
        self.assertIn("--settings", cmd)
        self.assertEqual(
            cmd[cmd.index("--settings") + 1],
            '{"disableAllHooks":true,"enabledPlugins":{"superpowers@claude-plugins-official":false}}',
        )

    def test_env_overrides_and_model_flag(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_EFFORT"] = "low"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_THINKING_TOKENS"] = "1000"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_MAX_TURNS"] = "7"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_TIMEOUT"] = "30"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_MODEL"] = "deepseek-v4-pro"
        _, logs, cmd, kwargs = self._invoke(skill="visual")
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "7")
        self.assertIn("timeout=30", "\n".join(logs.output))
        self.assertEqual(
            cmd[cmd.index("--model") : cmd.index("--")],
            ["--model", "deepseek-v4-pro"],
        )
        self.assertEqual(cmd[-2:], ["--", PROMPT])
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "low")
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "1000")
        joined = "\n".join(logs.output)
        self.assertIn("model=deepseek-v4-pro", joined)
        self.assertNotIn(PROMPT, joined)

    def test_invalid_numeric_override_uses_profile_default(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_STORYBOARD_MAX_TURNS"] = "nope"
        os.environ["HTMLVIDEO_CLAUDE_STORYBOARD_TIMEOUT"] = "soon"
        os.environ["HTMLVIDEO_CLAUDE_STORYBOARD_THINKING_TOKENS"] = "abc"
        _, logs, cmd, kwargs = self._invoke(skill="storyboard")
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "8")
        self.assertIn("timeout=1800", "\n".join(logs.output))
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "16000")
        joined = "\n".join(logs.output)
        self.assertIn("invalid HTMLVIDEO_CLAUDE_STORYBOARD_MAX_TURNS", joined)
        self.assertIn("invalid HTMLVIDEO_CLAUDE_STORYBOARD_TIMEOUT", joined)
        self.assertIn("invalid MAX_THINKING_TOKENS", joined)
        self.assertNotIn("clamped", joined)

    def test_non_positive_turns_and_timeout_fall_back(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_VERIFY_MAX_TURNS"] = "0"
        os.environ["HTMLVIDEO_CLAUDE_VERIFY_TIMEOUT"] = "-3"
        _, logs, cmd, _ = self._invoke(skill="verify")
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "40")
        joined = "\n".join(logs.output)
        self.assertIn("timeout=1800", joined)
        self.assertIn("HTMLVIDEO_CLAUDE_VERIFY_MAX_TURNS=0 <= 0", joined)
        self.assertIn("HTMLVIDEO_CLAUDE_VERIFY_TIMEOUT=-3 <= 0", joined)

    def test_invalid_effort_falls_back(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_INTENT_EFFORT"] = "hgih"
        _, logs, _, kwargs = self._invoke(skill="intent")
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        self.assertIn(
            "invalid HTMLVIDEO_CLAUDE_INTENT_EFFORT='hgih'",
            "\n".join(logs.output),
        )

    def test_extra_env_wins_over_profile(self) -> None:
        os.environ["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        _, _, _, kwargs = self._invoke(
            skill="intent",
            extra_env={
                "CLAUDE_CODE_EFFORT_LEVEL": "high",
                "MAX_THINKING_TOKENS": "1000",
                "CLAUDE_CODE_SUBAGENT_EFFORT": "high",
            },
        )
        env = kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], "high")
        self.assertEqual(env["MAX_THINKING_TOKENS"], "1000")
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_EFFORT"], "high")
        self.assertEqual(os.environ["CLAUDE_CODE_EFFORT_LEVEL"], "max")

    def test_frame_html_keeps_subagent_env_and_denies_agent_tools(self) -> None:
        _, _, cmd, kwargs = self._invoke(
            skill="frame_html",
            extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
        )
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_SUBAGENT_EFFORT"], "high")
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        # B architecture: no stage dispatches subagents, frame_html included.
        self.assertIn("Agent", cmd)
        self.assertIn("Task", cmd)
        self.assertIn("WebFetch", cmd)

    def test_tools_override_passes_empty_tools_flag(self) -> None:
        from html_utility.claude_cli import run_claude_result
        self._completed_popen(stdout=_result_json())
        with self.assertLogs("test.claude_cli", level="INFO"):
            run_claude_result(CWD, PROMPT, self.logger, skill="frame_html", tools="")
        cmd = self.popen_mock.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    def test_init_strips_project_claude_md_by_default(self) -> None:
        import tempfile
        from html_utility import init_htmlvideo as ih
        d = tempfile.mkdtemp(prefix="init-strip-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        claude_md = os.path.join(d, "CLAUDE.md")
        def fake_run(cmd, **kw):
            with open(claude_md, "w") as f:
                f.write("# Interactive guide\n")
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(ih.subprocess, "run", side_effect=fake_run):
            ret, _ = ih.init_htmlframe(self.logger, d, resolution="landscape")
        self.assertEqual(ret, 0)
        self.assertFalse(os.path.exists(claude_md))
        # Rollback switch preserves CLAUDE.md
        with mock.patch.object(ih.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {"HTMLVIDEO_STRIP_PROJECT_CLAUDE_MD": "0"}):
            ret, _ = ih.init_htmlframe(self.logger, d, resolution="landscape")
        self.assertEqual(ret, 0)
        self.assertTrue(os.path.isfile(claude_md))

    def test_thinking_at_output_cap_clamps_to_safe_cap(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_INTENT_THINKING_TOKENS"] = "128000"
        _, logs, _, kwargs = self._invoke(skill="intent")
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "127000")
        self.assertIn(
            "MAX_THINKING_TOKENS 128000 >= 128000; clamped to 127000",
            "\n".join(logs.output),
        )

    def test_thinking_just_under_cap_is_kept(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_INTENT_THINKING_TOKENS"] = "127999"
        _, logs, _, kwargs = self._invoke(skill="intent")
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "127999")
        self.assertNotIn("clamped", "\n".join(logs.output))

    def test_thinking_zero_override_disables_without_clamp_warning(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_INTENT_THINKING_TOKENS"] = "0"
        _, logs, _, kwargs = self._invoke(skill="intent")
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "0")
        self.assertNotIn("clamped", "\n".join(logs.output))

    def test_smaller_output_limit_clamps_thinking_below_it(self) -> None:
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "8000"
        _, logs, _, kwargs = self._invoke(skill="visual")
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "7999")
        self.assertIn("clamped to 7999", "\n".join(logs.output))

    def test_extra_env_thinking_over_cap_is_clamped(self) -> None:
        _, logs, _, kwargs = self._invoke(
            skill="intent", extra_env={"MAX_THINKING_TOKENS": "200000"}
        )
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "127000")
        self.assertIn("clamped to 127000", "\n".join(logs.output))

    def test_timeout_kills_process_group_and_returns_124(self) -> None:
        proc = mock.Mock()
        proc.pid = 7777
        proc.returncode = None
        proc.args = ["claude"]
        out = mock.Mock()
        out.read.return_value = ""
        err = mock.Mock()
        err.__iter__ = mock.Mock(return_value=iter([]))
        proc.stdout = out
        proc.stderr = err
        self.popen_mock.return_value = proc
        dead = {"v": False}

        def killpg_side_effect(pgid, sig):
            dead["v"] = True

        proc.poll.side_effect = lambda: -9 if dead["v"] else None
        # started=0, deadline=1800; loop sees now=1800 → TimeoutExpired
        mono = mock.Mock(side_effect=[0.0, 0.0, 1800.0, 1800.0, 1805.1])
        with mock.patch(
            "html_utility.claude_cli.os.getpgid", return_value=7777
        ) as getpgid, mock.patch(
            "html_utility.claude_cli.os.killpg", side_effect=killpg_side_effect
        ) as killpg, mock.patch(
            "html_utility.claude_cli.time.sleep"
        ), mock.patch(
            "html_utility.claude_cli.time.monotonic", mono
        ):
            with self.assertLogs("test.claude_cli", level="INFO") as logs:
                code = run_claude(CWD, PROMPT, self.logger, skill="intent")
        self.assertEqual(code, 124)
        self.assertIn(
            "claude skill timeout label=- skill=intent timeout=1800",
            "\n".join(logs.output),
        )
        getpgid.assert_called_with(7777)
        self.assertEqual(
            [c.args for c in killpg.call_args_list],
            [(7777, signal.SIGTERM), (7777, signal.SIGKILL)],
        )
        self.assertTrue(self.popen_mock.call_args.kwargs["start_new_session"])

    def test_invalid_json_returns_cli_rc_and_logs_tail(self) -> None:
        stdout = "HEAD" + ("x" * 3000)
        code, logs, _, _ = self._invoke(skill="intent", stdout=stdout, rc=7)
        self.assertEqual(code, 7)
        joined = "\n".join(logs.output)
        self.assertNotIn("HEAD", joined)
        self.assertIn("x" * 2000, joined)
        self.assertNotIn("claude skill done", joined)

    def test_non_object_json_is_not_treated_as_usage(self) -> None:
        code, logs, _, _ = self._invoke(skill="verify", stdout="[1, 2]", rc=5)
        self.assertEqual(code, 5)
        joined = "\n".join(logs.output)
        self.assertIn("[1, 2]", joined)
        self.assertNotIn("claude skill done", joined)

    def test_is_error_on_zero_exit_returns_1(self) -> None:
        code, logs, _, _ = self._invoke(
            skill="storyboard",
            stdout=_result_json(is_error=True, subtype="error_during_execution"),
            rc=0,
        )
        self.assertEqual(code, 1)
        self.assertIn("is_error=True", "\n".join(logs.output))
        self.assertIn("rc=0", "\n".join(logs.output))

    def test_nonzero_exit_is_kept_when_is_error(self) -> None:
        code, _, _, _ = self._invoke(
            skill="verify",
            stdout=_result_json(is_error=True),
            rc=3,
        )
        self.assertEqual(code, 3)

    def test_error_max_turns_warns_and_keeps_exit_code(self) -> None:
        code, logs, _, _ = self._invoke(
            skill="frame_html",
            stdout=_result_json(subtype="error_max_turns", is_error=False),
            rc=1,
        )
        self.assertEqual(code, 1)
        self.assertIn(
            "claude skill max turns label=- skill=frame_html "
            "subtype=error_max_turns",
            "\n".join(logs.output),
        )

    def test_error_max_turns_with_zero_exit_and_is_error_returns_1(self) -> None:
        code, logs, _, _ = self._invoke(
            skill="verify",
            stdout=_result_json(subtype="error_max_turns", is_error=True),
            rc=0,
        )
        self.assertEqual(code, 1)
        self.assertIn("subtype=error_max_turns", "\n".join(logs.output))

    def test_result_text_is_truncated_to_2000(self) -> None:
        code, logs, _, _ = self._invoke(
            skill="intent",
            stdout=_result_json(result="R" * 2500),
        )
        self.assertEqual(code, 0)
        joined = "\n".join(logs.output)
        self.assertIn("R" * 2000, joined)
        self.assertNotIn("R" * 2001, joined)

    def test_missing_usage_fields_log_as_none(self) -> None:
        code, logs, _, _ = self._invoke(
            skill="intent",
            stdout=json.dumps(
                {"subtype": "success", "is_error": False, "result": "only"}
            ),
        )
        self.assertEqual(code, 0)
        self.assertIn(
            "input=None output=None cache_read=None cache_create=None",
            "\n".join(logs.output),
        )
        self.assertIn("result=only", "\n".join(logs.output))

    def test_run_claude_result_returns_parsed_result(self):
        from html_utility.claude_cli import run_claude_result

        logger = logging.getLogger("t")
        with mock.patch("subprocess.Popen") as popen_mock:
            proc = mock.Mock()
            proc.pid = 1
            proc.returncode = 0
            proc.args = ["claude"]
            proc.poll.return_value = 0
            out = mock.Mock()
            out.read.return_value = _result_json(result="done OK 01-hook")
            err = mock.Mock()
            err.__iter__ = mock.Mock(return_value=iter([]))
            proc.stdout = out
            proc.stderr = err
            popen_mock.return_value = proc
            rc, text = run_claude_result(CWD, PROMPT, logger, skill="frame_html")
            self.assertEqual(rc, 0)
            self.assertIn("done OK 01-hook", text)

    def test_label_appears_in_skill_logs(self) -> None:
        from html_utility.claude_cli import run_claude_result

        self._completed_popen(stdout=_result_json(result="done OK 01-hook"))
        with self.assertLogs("test.claude_cli", level="INFO") as logs:
            rc, text = run_claude_result(
                CWD,
                PROMPT,
                self.logger,
                skill="frame_html",
                label="01-hook",
            )
        self.assertEqual(rc, 0)
        self.assertIn("done OK 01-hook", text)
        joined = "\n".join(logs.output)
        self.assertIn("label=01-hook", joined)
        self.assertIn(
            "claude skill start label=01-hook cwd=%s skill=frame_html" % CWD,
            joined,
        )
        self.assertIn(
            "claude skill done label=01-hook skill=frame_html",
            joined,
        )
        self.assertIn(
            "claude skill result label=01-hook skill=frame_html",
            joined,
        )

    def test_extra_env_max_output_override_wins_in_child_env(self) -> None:
        _, _, _, kwargs = self._invoke(
            skill="frame_html",
            extra_env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000"},
        )
        env = kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "128000")
        self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], "medium")

    def test_heartbeat_logs_while_running(self) -> None:
        proc = mock.Mock()
        proc.pid = 99
        proc.returncode = None
        proc.args = ["claude"]
        # still running for a few polls, then exit
        proc.poll.side_effect = [None, None, None, 0]
        out = mock.Mock()
        out.read.return_value = _result_json()
        err = mock.Mock()
        err.__iter__ = mock.Mock(return_value=iter([]))
        proc.stdout = out
        proc.stderr = err
        self.popen_mock.return_value = proc
        os.environ["HTMLVIDEO_CLAUDE_HEARTBEAT_SECONDS"] = "10"
        # started=0; after 10s heartbeat; then exit on next poll
        mono = mock.Mock(side_effect=[0.0, 0.0, 10.0, 10.5, 11.0, 11.0])
        with mock.patch("html_utility.claude_cli.time.sleep"), mock.patch(
            "html_utility.claude_cli.time.monotonic", mono
        ):
            with self.assertLogs("test.claude_cli", level="INFO") as logs:
                code = run_claude(CWD, PROMPT, self.logger, skill="verify")
        self.assertEqual(code, 0)
        self.assertIn(
            "claude skill running label=- skill=verify elapsed=10s",
            "\n".join(logs.output),
        )

    def test_stderr_progress_lines_are_logged(self) -> None:
        proc = mock.Mock()
        proc.pid = 99
        proc.returncode = 0
        proc.args = ["claude"]
        proc.poll.return_value = 0
        out = mock.Mock()
        out.read.return_value = _result_json()
        err = mock.Mock()
        err.__iter__ = mock.Mock(
            return_value=iter(
                [
                    "[INFO] [Stall] tool_dispatch_start tool=Edit\n",
                    "noise line without markers\n",
                    '[claude-code:unrecognized_model] {"model":"x"}\n',
                ]
            )
        )
        proc.stdout = out
        proc.stderr = err
        self.popen_mock.return_value = proc
        with self.assertLogs("test.claude_cli", level="INFO") as logs:
            code = run_claude(CWD, PROMPT, self.logger, skill="verify")
        self.assertEqual(code, 0)
        joined = "\n".join(logs.output)
        self.assertIn("claude skill progress", joined)
        self.assertIn("tool_dispatch_start", joined)
        self.assertIn("unrecognized_model", joined)
        self.assertNotIn("noise line without markers", joined)

    def test_session_id_flag_and_overrides(self) -> None:
        from html_utility.claude_cli import run_claude_result
        self._completed_popen(stdout=_result_json())
        with self.assertLogs("test.claude_cli", level="INFO"):
            run_claude_result(
                CWD, PROMPT, self.logger, skill="frame_html",
                session_id="11111111-2222-3333-4444-555555555555",
                max_turns=10, timeout=300)
        cmd = self.popen_mock.call_args.args[0]
        i = cmd.index("--session-id")
        self.assertEqual(cmd[i + 1], "11111111-2222-3333-4444-555555555555")
        self.assertLess(i, cmd.index("--"))
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "10")

    def test_truncation_watcher_kills_and_returns_125(self) -> None:
        from html_utility import claude_cli
        proc = mock.Mock()
        proc.pid = 5151
        proc.returncode = None
        proc.args = ["claude"]
        out = mock.Mock()
        out.read.return_value = ""
        err = mock.Mock()
        err.__iter__ = mock.Mock(return_value=iter([]))
        proc.stdout = out
        proc.stderr = err
        self.popen_mock.return_value = proc
        dead = {"v": False}
        proc.poll.side_effect = lambda: -15 if dead["v"] else None
        ticks = iter(float(i) * 3 for i in range(1000))
        with mock.patch.object(
            claude_cli._TruncationWatcher, "poll", return_value="thinking-only"
        ), mock.patch(
            "html_utility.claude_cli.os.getpgid", return_value=5151
        ), mock.patch(
            "html_utility.claude_cli.os.killpg",
            side_effect=lambda pgid, sig: dead.__setitem__("v", True),
        ) as killpg, mock.patch(
            "html_utility.claude_cli.time.sleep"
        ), mock.patch(
            "html_utility.claude_cli.time.monotonic", side_effect=lambda: next(ticks)
        ):
            with self.assertLogs("test.claude_cli", level="INFO") as logs:
                rc, text = claude_cli.run_claude_result(
                    CWD, PROMPT, self.logger, skill="frame_html",
                    trunc_kill=True)
        self.assertEqual((rc, text), (claude_cli.TRUNCATED_RC, ""))
        self.assertEqual(killpg.call_args_list[0].args, (5151, signal.SIGTERM))
        self.assertIn("claude skill truncated", "\n".join(logs.output))
        self.assertIn("--session-id", self.popen_mock.call_args.args[0])

    def test_cancel_event_kills_and_returns_130(self) -> None:
        import threading
        from html_utility import claude_cli
        proc = mock.Mock()
        proc.pid = 6161
        proc.returncode = None
        proc.args = ["claude"]
        out = mock.Mock()
        out.read.return_value = ""
        err = mock.Mock()
        err.__iter__ = mock.Mock(return_value=iter([]))
        proc.stdout = out
        proc.stderr = err
        self.popen_mock.return_value = proc
        dead = {"v": False}
        proc.poll.side_effect = lambda: -15 if dead["v"] else None
        cancel = threading.Event()
        cancel.set()
        with mock.patch(
            "html_utility.claude_cli.os.getpgid", return_value=6161
        ), mock.patch(
            "html_utility.claude_cli.os.killpg",
            side_effect=lambda pgid, sig: dead.__setitem__("v", True),
        ) as killpg, mock.patch("html_utility.claude_cli.time.sleep"):
            with self.assertLogs("test.claude_cli", level="INFO") as logs:
                rc, text = claude_cli.run_claude_result(
                    CWD, PROMPT, self.logger, skill="frame_html",
                    cancel_event=cancel)
        self.assertEqual((rc, text), (claude_cli.CANCELLED_RC, ""))
        self.assertEqual(killpg.call_args_list[0].args, (6161, signal.SIGTERM))
        self.assertIn("claude skill cancelled", "\n".join(logs.output))


    def test_unused_tools_and_hooks_disabled_for_self_contained_skills(self) -> None:
        _, _, cmd, _ = self._invoke(skill="verify")
        self.assertIn("--disable-slash-commands", cmd)
        self.assertIn("--settings", cmd)
        self.assertEqual(
            cmd[cmd.index("--settings") + 1],
            '{"disableAllHooks":true,"enabledPlugins":{"superpowers@claude-plugins-official":false}}',
        )
        for tool in ("Workflow", "ScheduleWakeup", "SendMessage", "CronCreate", "EnterWorktree"):
            self.assertIn(tool, cmd)
        # visual_legacy keeps Skill/hooks since it runs via slash command.
        self.popen_mock.reset_mock()
        _, _, legacy_cmd, _ = self._invoke(skill="visual_legacy")
        self.assertNotIn("--disable-slash-commands", legacy_cmd)
        self.assertNotIn("--settings", legacy_cmd)


class TruncationWatcherTests(unittest.TestCase):
    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def _watcher(self, entries, cap=48000, max_streak=2):
        import tempfile
        from html_utility.claude_cli import _TruncationWatcher
        cfg = tempfile.mkdtemp(prefix="cfg-")
        proj = "/tmp/some/project"
        slug = "-tmp-some-project"
        os.makedirs(os.path.join(cfg, "projects", slug))
        path = os.path.join(cfg, "projects", slug, f"{self.SID}.jsonl")
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        w = _TruncationWatcher(
            self.SID, proj, cfg, cap, logging.getLogger("t"), max_streak=max_streak
        )
        return w, path

    @staticmethod
    def _asst(mid, types, out, stop="end_turn"):
        return {"type": "assistant", "message": {
            "id": mid, "stop_reason": stop, "usage": {"output_tokens": out},
            "content": [{"type": t} for t in types]}}

    def test_meta_nudge_detected_at_streak_1(self):
        w, _ = self._watcher([
            self._asst("m1", ["thinking"], 48000),
            {"type": "user", "isMeta": True, "message": {"role": "user", "content":
             "[Your previous response had no visible output. Please continue and produce a user-visible response.]"}},
        ], max_streak=1)
        self.assertIn("no-visible-output", w.poll() or "")

    def test_thinking_only_at_cap_detected_on_next_user_line_at_streak_1(self):
        w, _ = self._watcher([
            self._asst("m1", ["thinking"], 48000),
            {"type": "user", "message": {"role": "user", "content": "next"}},
        ], max_streak=1)
        self.assertIn("output cap", w.poll() or "")

    def test_single_max_tokens_recovery_is_allowed_and_not_killed(self):
        # 2026-11-24 6b05ba2b5d081581: m1 hits max_tokens thinking-only, Claude
        # Code auto-resumes without a user JSONL line, m2 emits Edit, m3 emits
        # fixed 1. Watcher must allow 1 auto-resume and never kill on recovery.
        w, path = self._watcher([
            self._asst("m0", ["thinking", "tool_use"], 259, stop="tool_use"),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "html"}]}},
            self._asst("m1", ["thinking"], 16000, stop="max_tokens"),
        ], cap=16000)
        self.assertIsNone(w.poll())
        with open(path, "a") as f:
            f.write(json.dumps(self._asst("m2", ["thinking"], 3781, stop="tool_use")) + "\n")
            f.write(json.dumps(self._asst("m2", ["tool_use"], 3781, stop="tool_use")) + "\n")
            f.write(json.dumps(self._asst("m3", ["thinking", "text"], 39, stop="end_turn")) + "\n")
        self.assertIsNone(w.poll())

    def test_two_consecutive_max_tokens_kills_immediately(self):
        w, path = self._watcher([
            self._asst("m1", ["thinking"], 16000, stop="max_tokens"),
        ], cap=16000)
        self.assertIsNone(w.poll())
        with open(path, "a") as f:
            f.write(json.dumps(self._asst("m2", ["thinking"], 16000, stop="max_tokens")) + "\n")
        # Kills immediately on m2 without waiting for a 3rd message.
        self.assertIn("output cap", w.poll() or "")

    def test_qianfan_end_turn_at_cap_counts_immediately(self):
        # Qianfan gateway reports stop_reason="end_turn" even when output_tokens
        # hits max_tokens with zero visible blocks; streak=1 must catch it on m1
        # without waiting for a follow-up user message.
        w, _ = self._watcher([
            self._asst("m1", ["thinking"], 28000, stop="end_turn"),
        ], cap=28000, max_streak=1)
        self.assertIn("output cap", w.poll() or "")

    def test_flushed_chunk_with_truncation_and_recovery_never_retroactively_kills(self):
        # Even at max_streak=1, consuming the full chunk sees m2's tool_use
        # recovery and must not retroactively kill the finished session.
        w, _ = self._watcher([
            self._asst("m1", ["thinking"], 16000, stop="max_tokens"),
            self._asst("m2", ["thinking"], 3781, stop="tool_use"),
            self._asst("m2", ["tool_use"], 3781, stop="tool_use"),
            self._asst("m3", ["text"], 39, stop="end_turn"),
        ], cap=16000, max_streak=1)
        self.assertIsNone(w.poll())

    def test_cap_with_tool_use_is_not_truncation(self):
        w, _ = self._watcher([
            self._asst("m1", ["thinking"], 48000, stop="tool_use"),
            self._asst("m1", ["text"], 48000, stop="tool_use"),
            self._asst("m1", ["tool_use"], 48000, stop="tool_use"),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
            self._asst("m2", ["thinking"], 900),
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [
                {"type": "text", "text": "Base directory for this skill: /x"}]}},
        ])
        self.assertIsNone(w.poll())

    def test_incremental_tail_and_partial_lines(self):
        w, path = self._watcher([self._asst("m1", ["thinking"], 100)], max_streak=1)
        self.assertIsNone(w.poll())
        line = json.dumps({"type": "user", "isMeta": True, "message": {
            "content": "[Your previous response had no visible output. x]"}})
        with open(path, "a") as f:
            f.write(line[:20])
        self.assertIsNone(w.poll())
        with open(path, "a") as f:
            f.write(line[20:] + "\n")
        self.assertIsNotNone(w.poll())

    def test_missing_file_and_garbage_are_silent(self):
        from html_utility.claude_cli import _TruncationWatcher
        w = _TruncationWatcher(self.SID, "/nope", "/nonexistent-cfg", 100,
                               logging.getLogger("t"))
        self.assertIsNone(w.poll())
        w2, path = self._watcher([])
        with open(path, "a") as f:
            f.write("not json\n{broken\n")
        self.assertIsNone(w2.poll())
        self.assertFalse(w2.disabled)


if __name__ == "__main__":
    unittest.main()
