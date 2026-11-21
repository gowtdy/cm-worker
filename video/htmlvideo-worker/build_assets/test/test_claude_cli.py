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
# Tuple: effort, thinking, max_turns, timeout, deny_agent, max_output
_PROFILES = {
    "intent": ("high", "32000", "100", 1800, True, "32000"),
    "storyboard": ("high", "64000", "8", 1800, True, "16000"),
    "visual": ("high", "64000", "100", 1800, True, "32000"),
    "frame_html": ("high", "64000", "60", 1500, True, "48000"),
    "repair": ("high", "40000", "40", 600, True, "16000"),
    "verify": ("medium", "32000", "40", 1800, True, "16000"),
}
_AGENT_DENY = ["Agent", "Task"]
_FORBIDDEN = [
    "--effort",
    "--bare",
    "--no-session-persistence",
    "--max-budget-usd",
    "--max-thinking-tokens",
    "--tools",
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
        proc.communicate.return_value = (stdout, None)
        proc.poll.return_value = rc
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
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "high")
        self.run_mock.assert_not_called()

    def test_each_skill_profile(self) -> None:
        os.environ["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        for skill, (effort, thinking, turns, timeout, deny_agent, max_output) in _PROFILES.items():
            with self.subTest(skill=skill):
                self.popen_mock.reset_mock()
                code, logs, cmd, kwargs = self._invoke(skill=skill)
                self.assertEqual(code, 0)
                self.assertEqual(kwargs["cwd"], CWD)
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertTrue(kwargs["text"])
                self.assertTrue(kwargs["start_new_session"])
                self.assertIsNone(kwargs["stderr"])
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
                proc.communicate.assert_called_once_with(timeout=timeout)
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
        # serial stages: intent/visual at 32000; storyboard/verify tightened to 16000
        for skill in ("intent", "visual"):
            self.assertEqual(_resolve_profile(skill, logger)["max_output"], "32000")
        for skill in ("storyboard", "verify"):
            self.assertEqual(_resolve_profile(skill, logger)["max_output"], "16000")

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

    def test_debug_request_off_by_default(self) -> None:
        _, _, _, kwargs = self._invoke(skill="intent")
        env = kwargs["env"]
        self.assertNotEqual(env.get("ANTHROPIC_LOG"), "debug")
        self.assertNotIn("--preload=", env.get("BUN_OPTIONS", ""))

    def test_env_overrides_and_model_flag(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_EFFORT"] = "low"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_THINKING_TOKENS"] = "1000"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_MAX_TURNS"] = "7"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_TIMEOUT"] = "30"
        os.environ["HTMLVIDEO_CLAUDE_VISUAL_MODEL"] = "deepseek-v4-pro"
        _, logs, cmd, kwargs = self._invoke(skill="visual")
        self.assertEqual(
            self.popen_mock.return_value.communicate.call_args.kwargs["timeout"],
            30,
        )
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "7")
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
        self.assertEqual(
            self.popen_mock.return_value.communicate.call_args.kwargs["timeout"],
            1800,
        )
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "64000")
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
        self.assertEqual(
            self.popen_mock.return_value.communicate.call_args.kwargs["timeout"],
            1800,
        )
        joined = "\n".join(logs.output)
        self.assertIn("HTMLVIDEO_CLAUDE_VERIFY_MAX_TURNS=0 <= 0", joined)
        self.assertIn("HTMLVIDEO_CLAUDE_VERIFY_TIMEOUT=-3 <= 0", joined)

    def test_invalid_effort_falls_back(self) -> None:
        os.environ["HTMLVIDEO_CLAUDE_INTENT_EFFORT"] = "hgih"
        _, logs, _, kwargs = self._invoke(skill="intent")
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "high")
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
        self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "high")
        # B architecture: no stage dispatches subagents, frame_html included.
        self.assertIn("Agent", cmd)
        self.assertIn("Task", cmd)
        self.assertIn("WebFetch", cmd)

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
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "16000"
        _, logs, _, kwargs = self._invoke(skill="visual")
        self.assertEqual(kwargs["env"]["MAX_THINKING_TOKENS"], "15999")
        self.assertIn("clamped to 15999", "\n".join(logs.output))

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
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=["claude"], timeout=1800),
            ("", None),
        ]
        proc.poll.side_effect = [None, None, -9]
        self.popen_mock.return_value = proc
        with mock.patch(
            "html_utility.claude_cli.os.getpgid", return_value=7777
        ) as getpgid, mock.patch(
            "html_utility.claude_cli.os.killpg"
        ) as killpg, mock.patch(
            "html_utility.claude_cli.time.sleep"
        ), mock.patch(
            "html_utility.claude_cli.time.monotonic",
            side_effect=[0.0, 0.0, 10.0],
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
            popen_mock.return_value.communicate.return_value = (
                _result_json(result="done OK 01-hook"), "")
            popen_mock.return_value.returncode = 0
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
        self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], "high")


if __name__ == "__main__":
    unittest.main()
