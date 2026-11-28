# -*- coding: utf-8 -*-
"""Deterministic fan-out driver: collect, retry, marking, repair."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from html_utility import frame_html as fh

STORYBOARD = """## Frame 1 — Hook
- status: outline
- src: compositions/frames/01-hook.html
- duration: 4s

Scene 1 (0.0–4.0s): pill.
## Frame 2 — Answer
- status: outline
- src: compositions/frames/02-answer.html
- duration: 5s

Scene 1 (0.0–5.0s): grid.
## Video direction
"""

def make_project(skeleton=False, with_dispatch=True):
    d = tempfile.mkdtemp(prefix="fhdriver-")
    os.makedirs(os.path.join(d, ".hyperframes", "frame-packets"), exist_ok=True)
    os.makedirs(os.path.join(d, "compositions", "frames"), exist_ok=True)
    with open(os.path.join(d, "STORYBOARD.md"), "w") as f: f.write(STORYBOARD)
    with open(os.path.join(d, "frame.md"), "w") as f: f.write("x")
    with open(os.path.join(d, "hyperframes.json"), "w") as f: f.write("{}")
    for fid in ("01-hook", "02-answer"):
        open(os.path.join(d, ".hyperframes", "frame-packets", fid + ".md"), "w").close()
        open(os.path.join(d, ".hyperframes", "frame-packets", "_role.md"), "w").close()
        if with_dispatch:
            open(os.path.join(d, ".hyperframes", "frame-packets", fid + ".dispatch.md"), "w").close()
        if skeleton:
            with open(os.path.join(d, "compositions", "frames", fid + ".html"), "w") as f:
                f.write("<!-- skeleton-v1 --><template></template>")
    return d

class DriverTest(unittest.TestCase):
    def setUp(self):
        # Production defaults: TWO_PHASE OFF, HEDGE ON, TEXT_OUT ON. Unit fixtures
        # below exercise the tool-mode protocol (`done OK <fid>`) unless a test
        # explicitly turns TEXT_OUT / TWO_PHASE / HEDGE on.
        patcher = mock.patch.dict(os.environ, {
            "HTMLVIDEO_FRAME_HTML_DRIVER": "1",
            "HTMLVIDEO_FRAME_HTML_SKELETON": "1",
            fh.TEXT_OUT_ENV: "0",
            fh.TWO_PHASE_ENV: "0",
            fh.HEDGE_ENV: "0",
        }, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The fixture skeleton is not a real frame; the gate itself is covered
        # by frame-selfcheck's own tests.
        sc = mock.patch.object(
            fh, "_run_frame_selfcheck",
            side_effect=lambda d, fid: (
                os.path.isfile(fh._frame_path(d, fid)), "[mock selfcheck]"))
        sc.start(); self.addCleanup(sc.stop)

    def _patch_dispatch(self, results, assemble_results=((0, ""),)):
        """results: dict frame_id -> list of (rc, text) per attempt.
        assemble_results: build_index_html return values in call order."""
        calls = {k: list(v) for k, v in results.items()}
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            # Match on the frame-specific path tokens every worker prompt
            # carries (dispatch doc + frame file) — a bare fid substring also
            # matches another frame's id inside a re-dispatch note tail.
            fid = next(rid for rid in calls
                       if f"{rid}.dispatch.md" in prompt or f"{rid}.html" in prompt)
            return calls[fid].pop(0)
        p1 = mock.patch.object(fh, "run_claude_result", side_effect=fake)
        p2 = mock.patch.object(fh, "build_index_html", side_effect=list(assemble_results))
        p1.start(); self.addCleanup(p1.stop)
        p2.start(); self.addCleanup(p2.stop)

    def test_all_ok_marks_and_gates(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({k: [(0, f"done OK {k}")] for k in ("01-hook", "02-answer")})
        fh.build_frame_html(d, None)
        with open(os.path.join(d, "STORYBOARD.md")) as f:
            text = f.read()
        self.assertIn("- status: animated", text)

    def test_retry_then_converge(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({
            "01-hook": [(0, "done OK 01-hook")],
            "02-answer": [(0, "…"), (0, "done OK 02-answer")],
        })
        fh.build_frame_html(d, None)

    def test_done_ok_for_other_frame_rejected(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({
            "01-hook": [(0, "done OK 01-hook")],
            "02-answer": [(0, "done OK 01-hook"), (0, "done OK 02-answer")],
        })
        seen = []
        orig = fh.run_claude_result
        def counting(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            seen.append(prompt)
            return orig(project_dir, prompt, logger, extra_env=extra_env, skill=skill, label=label, **kw)
        with mock.patch.object(fh, "run_claude_result", side_effect=counting):
            fh.build_frame_html(d, None)
        self.assertEqual(sum("02-answer" in p for p in seen), 2)

    def test_two_rounds_then_raise(self):
        os.environ["HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS"] = "2"
        d = make_project(skeleton=True)
        self._patch_dispatch({"01-hook": [(0, "done OK 01-hook")],
                              "02-answer": [(0, ""), (0, "")]})
        with self.assertRaises(RuntimeError) as ctx:
            fh.build_frame_html(d, None)
        self.assertIn("02-answer: prior attempt", str(ctx.exception))

    def test_timeout_signal_embedded_in_retry_note(self):
        d = make_project(skeleton=True)
        seen = []
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = "01-hook" if "01-hook" in prompt else "02-answer"
            seen.append((fid, prompt))
            if fid == "01-hook" and len([s for s in seen if s[0] == fid]) == 1:
                return 124, ""
            return 0, f"done OK {fid}"
        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")):
            fh.build_frame_html(d, None)
        retry_prompt = [p for fid, p in seen if fid == "01-hook"][-1]
        self.assertIn("wall-clock timeout", retry_prompt)

    def test_assemble_failure_runs_repair_once(self):
        d = make_project(skeleton=True)
        self._patch_dispatch(
            {k: [(0, f"done OK {k}")] for k in ("01-hook", "02-answer")},
            assemble_results=((1, "bad index"), (0, "")),
        )
        with mock.patch.object(fh, "run_claude", return_value=0) as repair:
            fh.build_frame_html(d, None)
        self.assertEqual(repair.call_count, 1)
        self.assertEqual(repair.call_args.kwargs.get("skill"), "repair")

    def test_skeleton_mismatch_raises_preflight(self):
        d = make_project(skeleton=False)
        with self.assertRaises(RuntimeError):
            fh.build_frame_html(d, None)

    def test_worker_prompt_locks_scope_to_single_frame(self):
        p = fh._worker_prompt(
            "01-hook", skeleton_mode=True, dispatch_text="# Dispatch stub\n")
        self.assertIn(
            "Scope is exactly one file: compositions/frames/01-hook.html", p
        )
        self.assertIn("Never read, open, or write any other", p)

    def test_worker_prompt_dispatch_self_sufficient(self):
        p = fh._worker_prompt(
            "01-hook", skeleton_mode=True, dispatch_text="# Dispatch stub\n")
        self.assertNotIn(".hyperframes/frame-packets/_role.md", p)
        self.assertIn("RULES_DIR", p)
        self.assertIn("never open frame.md or _role.md", p)

    def test_worker_prompt_inlines_dispatch_and_forbids_reread(self):
        body = "# Dispatch: 01-hook\n\n## Assigned storyboard block\nHELLO-DISPATCH\n"
        p = fh._worker_prompt(
            "01-hook", skeleton_mode=True, dispatch_text=body)
        self.assertIn("## Dispatch (authoritative — already complete)", p)
        self.assertIn("HELLO-DISPATCH", p)
        self.assertIn("Do NOT Read", p)
        self.assertIn("01-hook.dispatch.md", p)  # named only as "do not read"
        # Reading from disk when project_dir is set
        d = make_project(skeleton=True)
        with open(
            os.path.join(
                d, ".hyperframes", "frame-packets", "01-hook.dispatch.md"),
            "w",
        ) as f:
            f.write("# Dispatch from disk\nDISK-TOKEN\n")
        p2 = fh._worker_prompt(
            "01-hook", skeleton_mode=True, project_dir=d)
        self.assertIn("DISK-TOKEN", p2)

    def test_worker_prompt_transcribe_not_redesign(self):
        # P0 (2026-11-23): the worker must transcribe the inlined spec, not
        # re-derive it — the three observed thinking wastes are named outright.
        p = fh._worker_prompt(
            "01-hook", skeleton_mode=True, dispatch_text="# Dispatch stub\n")
        self.assertIn("TRANSCRIBING that spec into HTML", p)
        self.assertIn("not redesigning it", p)
        self.assertIn("do NOT re-list the Scene beats", p)
        self.assertIn("do NOT recompute cqw/cqh", p)
        self.assertIn("do NOT re-decide colors, type sizes, or layout", p)

    def test_worker_prompt_transcribes_ledger_when_present(self):
        # 2026-11-27 (P0-1): ledger transcription instruction is injected only
        # when the dispatch actually carries a Layout/Motion ledger, avoiding
        # contradictions with the flex/grid guardrail when ledger is OFF.
        p_off = fh._worker_prompt(
            "01-hook", skeleton_mode=True, dispatch_text="# Dispatch stub\n")
        self.assertNotIn("transcribe their concrete values verbatim", p_off)
        p_on = fh._worker_prompt(
            "01-hook", skeleton_mode=True,
            dispatch_text="# Dispatch stub\n\n## Layout ledger\n| e | r | p | s | c | o |\n")
        self.assertIn("`## Layout ledger`", p_on)
        self.assertIn("`## Motion ledger`", p_on)
        self.assertIn("transcribe their concrete values verbatim", p_on)

    def test_worker_done_ok_accepts_hyphen_suffix_ids(self):
        # 2026-11-21: empty-slug frames were ``01-``; trailing ``\b`` rejected
        # a correct ``done OK 01-`` and forced a false retry round.
        self.assertTrue(fh._worker_done_ok("01-", "done OK 01-"))
        self.assertTrue(fh._worker_done_ok("01-", "done OK 01-\n"))
        self.assertTrue(fh._worker_done_ok("04-", "…\ndone OK 04-"))
        self.assertTrue(fh._worker_done_ok("01-hook", "done OK 01-hook"))
        self.assertFalse(fh._worker_done_ok("01-", "done OK 01-hook"))
        self.assertFalse(fh._worker_done_ok("02-", "done OK 01-"))
        self.assertFalse(fh._worker_done_ok("01-", ""))

    def test_hyphen_suffix_frame_id_marks_animated(self):
        d = make_project(skeleton=True)
        # Rename packets/frames to trailing-hyphen ids (today's failure shape).
        pkt = os.path.join(d, ".hyperframes", "frame-packets")
        frames = os.path.join(d, "compositions", "frames")
        mapping = {"01-hook": "01-", "02-answer": "02-"}
        for old, new in mapping.items():
            os.rename(os.path.join(pkt, old + ".md"), os.path.join(pkt, new + ".md"))
            os.rename(
                os.path.join(pkt, old + ".dispatch.md"),
                os.path.join(pkt, new + ".dispatch.md"),
            )
            os.rename(
                os.path.join(frames, old + ".html"),
                os.path.join(frames, new + ".html"),
            )
        board = (
            "## Frame 1 — Hook\n"
            "- status: outline\n"
            "- src: compositions/frames/01-.html\n"
            "- duration: 4s\n\n"
            "## Frame 2 — Answer\n"
            "- status: outline\n"
            "- src: compositions/frames/02-.html\n"
            "- duration: 5s\n\n"
            "## Video direction\n"
        )
        with open(os.path.join(d, "STORYBOARD.md"), "w") as f:
            f.write(board)
        self._patch_dispatch({k: [(0, f"done OK {k}")] for k in ("01-", "02-")})
        fh.build_frame_html(d, None)
        with open(os.path.join(d, "STORYBOARD.md")) as f:
            text = f.read()
        self.assertEqual(text.count("- status: animated"), 2)


class PipelineTest(unittest.TestCase):
    def _run(self, fake):
        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")):
            fh.build_frame_html(self.d, None)

    def setUp(self):
        DriverTest.setUp(self)
        self.d = make_project(skeleton=True)
        self.calls = []

    @staticmethod
    def _fid(prompt):
        return "01-hook" if "01-hook.html" in prompt else "02-answer"

    def test_retry_does_not_wait_for_slow_sibling(self):
        import threading
        retried = threading.Event()
        order = []

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            n = sum(1 for f in order if f == fid) + 1
            order.append(fid)
            if fid == "02-answer":
                retried.wait(timeout=5)
                order.append("02-answer:returned")
                return 0, "done OK 02-answer"
            if n == 1:
                return 0, "no reply"
            retried.set()
            return 0, "done OK 01-hook"

        self._run(fake)
        self.assertTrue(retried.is_set())
        self.assertLess(order.index("01-hook", order.index("01-hook") + 1),
                        order.index("02-answer:returned"))

    def test_truncated_retry_raises_output_cap_and_passes_session(self):
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            self.calls.append((fid, dict(extra_env or {}), kw, prompt))
            if fid == "01-hook" and sum(c[0] == fid for c in self.calls) == 1:
                return fh.TRUNCATED_RC, ""
            return 0, f"done OK {fid}"

        self._run(fake)
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertEqual(len(hook), 2)
        self.assertTrue(hook[0][2].get("trunc_kill"))
        self.assertTrue(hook[0][2].get("session_id"))
        self.assertNotEqual(hook[0][2]["session_id"], hook[1][2]["session_id"])
        self.assertNotIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS", hook[0][1])
        self.assertEqual(hook[1][1]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "64000")
        self.assertIn("hard truncation", hook[1][3])
        self.assertNotIn("FINISH pass", hook[1][3])  # file unchanged → no salvage

    def test_trunc_kill_off_omits_session(self):
        os.environ["HTMLVIDEO_FRAME_HTML_TRUNC_KILL"] = "0"
        self.addCleanup(os.environ.pop, "HTMLVIDEO_FRAME_HTML_TRUNC_KILL", None)

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            self.calls.append(kw)
            return 0, f"done OK {self._fid(prompt)}"

        self._run(fake)
        self.assertTrue(all("trunc_kill" not in kw for kw in self.calls))

    def _salvage_fake(self, first_rc):
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            self.calls.append((fid, dict(extra_env or {}), kw, prompt))
            if fid == "01-hook" and sum(c[0] == fid for c in self.calls) == 1:
                with open(fh._frame_path(project_dir, fid), "a") as f:
                    f.write("<!-- partial work -->")
                return first_rc, ""
            return 0, f"done OK {fid}"
        return fake

    def test_timeout_with_changed_file_runs_salvage_pass(self):
        self._run(self._salvage_fake(124))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertEqual(len(hook), 2)
        _, env, kw, prompt = hook[1]
        self.assertIn("FINISH pass", prompt)
        self.assertIn("<!-- partial work -->", prompt)  # current file inlined
        self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], fh.SALVAGE_MAX_OUTPUT)
        self.assertEqual(kw["max_turns"], fh.SALVAGE_MAX_TURNS)
        self.assertEqual(kw["timeout"], fh.SALVAGE_TIMEOUT)

    def test_truncated_with_changed_file_also_salvages(self):
        self._run(self._salvage_fake(fh.TRUNCATED_RC))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertIn("FINISH pass", hook[1][3])

    def test_salvage_off_does_full_redispatch(self):
        os.environ["HTMLVIDEO_FRAME_HTML_SALVAGE"] = "0"
        self.addCleanup(os.environ.pop, "HTMLVIDEO_FRAME_HTML_SALVAGE", None)
        self._run(self._salvage_fake(124))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertNotIn("FINISH pass", hook[1][3])
        self.assertIn("[RE-DISPATCH]", hook[1][3])
        self.assertNotIn("max_turns", hook[1][2])

    def test_stage_deadline_stops_new_attempts(self):
        os.environ["HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE"] = "100"
        self.addCleanup(os.environ.pop, "HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE", None)
        import itertools
        clock = itertools.count(0, 1000)

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            self.calls.append(fid)
            return (0, "nope") if fid == "01-hook" else (0, f"done OK {fid}")

        with mock.patch.object(fh.time, "monotonic", side_effect=lambda: next(clock)), \
             self.assertRaises(RuntimeError) as ctx:
            self._run(fake)
        self.assertIn("stage deadline", str(ctx.exception))
        self.assertEqual(self.calls.count("01-hook"), 1)

    def test_wave_mode_rollback_still_works(self):
        os.environ["HTMLVIDEO_FRAME_HTML_WAVE_MODE"] = "1"
        self.addCleanup(os.environ.pop, "HTMLVIDEO_FRAME_HTML_WAVE_MODE", None)
        with mock.patch.object(fh, "_build_frame_html_driver_pipeline") as pipe:
            def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
                self.calls.append(kw)
                return 0, f"done OK {self._fid(prompt)}"
            self._run(fake)
        pipe.assert_not_called()
        self.assertEqual(len(self.calls), 2)


class HardeningP1P2Test(unittest.TestCase):
    """P1-4 stage summary, salvageable gate, P2-2..P2-5 switches."""

    def setUp(self):
        DriverTest.setUp(self)
        self.d = make_project(skeleton=True)
        self.calls = []
        for key in (fh.MAX_OUTPUT_ENV, fh.EFFORT_ENV, fh.HEDGE_AFTER_ENV,
                    fh.DEGRADE_ENV, fh.DEGRADE_WINDOW_ENV,
                    "HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS",
                    "HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE"):
            self.addCleanup(os.environ.pop, key, None)
            os.environ.pop(key, None)
        # Keep TWO_PHASE/HEDGE off unless a test turns them on (DriverTest.setUp
        # already pins them to 0).
        os.environ[fh.TWO_PHASE_ENV] = "0"
        os.environ[fh.HEDGE_ENV] = "0"

    _fid = staticmethod(PipelineTest._fid)

    def _run(self, fake, logger=None):
        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")):
            fh.build_frame_html(self.d, logger)

    def _recorder(self, script):
        """script: fid -> list of callables(project_dir) -> (rc, text)."""
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            self.calls.append((fid, dict(extra_env or {}), kw, prompt, label, project_dir))
            return script[fid].pop(0)(project_dir)
        return fake

    def test_stage_summary_line(self):
        import logging

        def touch(d, fid="02-answer"):
            with open(fh._frame_path(d, fid), "a") as f:
                f.write("<!-- partial -->")
        script = {
            "01-hook": [lambda d: (fh.TRUNCATED_RC, ""), lambda d: (0, "done OK 01-hook")],
            "02-answer": [lambda d: (touch(d), (124, ""))[1],
                          lambda d: (0, "done OK 02-answer")],
        }
        logger = logging.getLogger("test.fh.summary")
        with self.assertLogs(logger, level="INFO") as logs:
            self._run(self._recorder(script), logger)
        line = [m for m in logs.output if "stage summary" in m]
        self.assertEqual(len(line), 1, logs.output)
        for token in ("frames=2", "ok=2", "failed=0", "attempts=4",
                      "truncations=1", "salvages=1", "timeouts=1",
                      "critical_path_frame=", "critical_path_s=", "total_s="):
            self.assertIn(token, line[0])

    def test_summary_logged_on_failure_too(self):
        import logging
        os.environ["HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS"] = "1"
        script = {"01-hook": [lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (1, "")]}
        logger = logging.getLogger("test.fh.summary2")
        with self.assertLogs(logger, level="INFO") as logs, \
             self.assertRaises(RuntimeError):
            self._run(self._recorder(script), logger)
        line = [m for m in logs.output if "stage summary" in m][0]
        self.assertIn("ok=1 failed=1", line)

    def test_salvageable_rules(self):
        unfilled_body = ("[FAIL] skeleton_unfilled: … (evidence: no-tween) — … "
                         "(skeleton_unfilled)\nFAIL x (1 finding)")
        unfilled_root = ("[FAIL] skeleton_unfilled: … (evidence: root-empty, "
                         "no-tween) — … (skeleton_unfilled)")
        self.assertTrue(fh._salvageable(True, ""))
        self.assertTrue(fh._salvageable(False, unfilled_body))
        self.assertFalse(fh._salvageable(False, unfilled_root))
        self.assertFalse(fh._salvageable(
            False, unfilled_body + "\n[FAIL] no #root div found"))
        self.assertFalse(fh._salvageable(False, ""))

    def test_timeout_with_body_but_no_tween_salvages(self):
        def sc(d, fid):
            with open(fh._frame_path(d, fid)) as f:
                txt = f.read()
            if "tl.to(" in txt or fid == "01-hook":
                return True, "PASS"
            return False, ("[FAIL] skeleton_unfilled: x (evidence: no-tween) — "
                           "y (skeleton_unfilled)")

        def write_body(d):
            with open(fh._frame_path(d, "02-answer"), "a") as f:
                f.write("<div class='x'></div>")
            return 124, ""

        def finish(d):
            with open(fh._frame_path(d, "02-answer"), "a") as f:
                f.write("<script>tl.to('.x',{x:1})</script>")
            return 0, "done OK 02-answer"
        script = {"01-hook": [lambda d: (0, "done OK 01-hook")],
                  "02-answer": [write_body, finish]}
        with mock.patch.object(fh, "_run_frame_selfcheck", side_effect=sc):
            self._run(self._recorder(script))
        ans = [c for c in self.calls if c[0] == "02-answer"]
        self.assertIn("FINISH pass", ans[1][3])

    def test_max_output_and_effort_overrides(self):
        os.environ[fh.MAX_OUTPUT_ENV] = "80000"
        os.environ[fh.EFFORT_ENV] = "medium"
        script = {"01-hook": [lambda d: (fh.TRUNCATED_RC, ""), lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (0, "done OK 02-answer")]}
        self._run(self._recorder(script))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertEqual(hook[0][1]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "80000")
        self.assertEqual(hook[0][1]["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        # truncation retry never lowers an explicit larger cap
        self.assertEqual(hook[1][1]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "80000")

    def test_invalid_effort_ignored_and_default_env_unchanged(self):
        os.environ[fh.EFFORT_ENV] = "turbo"
        script = {k: [lambda d, k=k: (0, f"done OK {k}")] for k in ("01-hook", "02-answer")}
        self._run(self._recorder(script))
        for c in self.calls:
            self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", c[1])
            self.assertNotIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS", c[1])

    def test_two_phase_default_off_single_phase_64k(self):
        import logging
        os.environ.pop(fh.TWO_PHASE_ENV, None)
        script = {k: [lambda d, k=k: (0, f"done OK {k}")] for k in ("01-hook", "02-answer")}
        logger = logging.getLogger("test.fh.defaults")
        with self.assertLogs(logger, level="INFO") as logs:
            self._run(self._recorder(script), logger)
        # one single-phase call per frame, profile cap (no driver-level override)
        self.assertEqual(len(self.calls), 2)
        for c in self.calls:
            self.assertNotIn("PHASE", c[3])
            self.assertNotIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS", c[1])
        start = [m for m in logs.output if "driver start" in m][0]
        self.assertIn("two_phase=False", start)
        self.assertIn("worker_cap=48000", start)
        from html_utility.claude_cli import _resolve_profile
        self.assertEqual(_resolve_profile("frame_html", logger)["max_output"], "48000")

    def test_two_phase_opt_in_and_hedge_default_on(self):
        os.environ.pop(fh.TWO_PHASE_ENV, None)
        os.environ.pop(fh.HEDGE_ENV, None)
        self.assertFalse(fh._env_on(fh.TWO_PHASE_ENV, default=False))
        self.assertTrue(fh._env_on(fh.HEDGE_ENV, default=True))
        os.environ[fh.TWO_PHASE_ENV] = "1"
        os.environ[fh.HEDGE_ENV] = "0"
        self.assertTrue(fh._env_on(fh.TWO_PHASE_ENV, default=False))
        self.assertFalse(fh._env_on(fh.HEDGE_ENV, default=True))

    def test_max_output_override_shows_in_start_log(self):
        import logging
        os.environ[fh.MAX_OUTPUT_ENV] = "80000"
        script = {k: [lambda d, k=k: (0, f"done OK {k}")] for k in ("01-hook", "02-answer")}
        logger = logging.getLogger("test.fh.capoverride")
        with self.assertLogs(logger, level="INFO") as logs:
            self._run(self._recorder(script), logger)
        start = [m for m in logs.output if "driver start" in m][0]
        self.assertIn("worker_cap=80000", start)

    def test_two_phase_runs_a_then_b(self):
        os.environ[fh.TWO_PHASE_ENV] = "1"

        def phase_a(d):
            with open(fh._frame_path(d, "01-hook"), "a") as f:
                f.write("<style>.PHASE-A-STYLE{}</style>")
            return 0, "phase-a OK 01-hook"
        script = {"01-hook": [phase_a, lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (0, "phase-a OK 02-answer"),
                                lambda d: (0, "done OK 02-answer")]}
        self._run(self._recorder(script))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertEqual(len(hook), 2)
        self.assertIn("PHASE A of 2", hook[0][3])
        self.assertEqual(hook[0][1]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], fh.TWO_PHASE_A_MAX_OUTPUT)
        self.assertEqual(hook[0][2]["max_turns"], fh.TWO_PHASE_A_MAX_TURNS)
        self.assertTrue(hook[0][4].endswith("-A"))
        self.assertIn("PHASE B of 2", hook[1][3])
        self.assertIn(".PHASE-A-STYLE", hook[1][3])  # A output inlined into B
        self.assertNotIn("max_turns", hook[1][2])
        self.assertTrue(hook[1][4].endswith("-B"))

    def test_two_phase_a_failure_skips_b_and_retries_single_phase(self):
        os.environ[fh.TWO_PHASE_ENV] = "1"
        script = {"01-hook": [lambda d: (fh.TRUNCATED_RC, ""), lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (0, "phase-a OK 02-answer"),
                                lambda d: (0, "done OK 02-answer")]}
        self._run(self._recorder(script))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertEqual(len(hook), 2)
        self.assertIn("PHASE A of 2", hook[0][3])
        self.assertNotIn("PHASE", hook[1][3])
        self.assertIn("hard truncation", hook[1][3])

    def test_degrade_near_deadline(self):
        os.environ[fh.DEGRADE_WINDOW_ENV] = "100000"  # always "near" the deadline
        script = {"01-hook": [lambda d: (0, "nope"), lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (0, "done OK 02-answer")]}
        self._run(self._recorder(script))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertIn("[DEGRADED", hook[1][3])
        self.assertNotIn("[RE-DISPATCH]", hook[1][3])
        self.assertEqual(hook[1][1]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], fh.DEGRADE_MAX_OUTPUT)
        self.assertEqual(hook[1][2]["max_turns"], fh.DEGRADE_MAX_TURNS)
        self.assertGreaterEqual(hook[1][2]["timeout"], fh.DEGRADE_MIN_TIMEOUT)
        self.assertTrue(hook[1][4].endswith("-degraded"))

    def test_degrade_off_keeps_normal_retry(self):
        os.environ[fh.DEGRADE_WINDOW_ENV] = "100000"
        os.environ[fh.DEGRADE_ENV] = "0"
        script = {"01-hook": [lambda d: (0, "nope"), lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (0, "done OK 02-answer")]}
        self._run(self._recorder(script))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertNotIn("[DEGRADED", hook[1][3])
        self.assertIn("[RE-DISPATCH]", hook[1][3])

    def test_default_window_does_not_degrade_early_retry(self):
        script = {"01-hook": [lambda d: (0, "nope"), lambda d: (0, "done OK 01-hook")],
                  "02-answer": [lambda d: (0, "done OK 02-answer")]}
        self._run(self._recorder(script))
        hook = [c for c in self.calls if c[0] == "01-hook"]
        self.assertNotIn("[DEGRADED", hook[1][3])

    def test_hedge_wins_and_primary_is_cancelled(self):
        import logging
        import threading
        os.environ[fh.HEDGE_ENV] = "1"
        os.environ[fh.HEDGE_AFTER_ENV] = "1"
        primary_cancelled = threading.Event()

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            self.calls.append((fid, label, project_dir, kw))
            if fid == "01-hook":
                return 0, "done OK 01-hook"
            if label.endswith("-hedge"):
                self.assertIn(os.path.join(".hyperframes", "hedge"), project_dir)
                # P2: a hedge races a degraded minimal fallback, not the full build.
                self.assertIn("[HEDGE", prompt)
                self.assertEqual((extra_env or {}).get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"),
                                 fh.HEDGE_MAX_OUTPUT)
                self.assertEqual(kw.get("max_turns"), fh.HEDGE_MAX_TURNS)
                self.assertEqual(kw.get("timeout"), fh.HEDGE_TIMEOUT)
                with open(fh._frame_path(project_dir, fid), "w") as f:
                    f.write("<!-- HEDGE-CONTENT -->")
                return 0, "done OK 02-answer"
            # primary: silent (no Write) until cancelled
            self.assertTrue(kw["cancel_event"].wait(timeout=10))
            primary_cancelled.set()
            return fh.CANCELLED_RC, ""

        logger = logging.getLogger("test.fh.hedge")
        with mock.patch.object(fh, "HEDGE_POLL_SECONDS", 0.05), \
             self.assertLogs(logger, level="INFO") as logs:
            self._run(fake, logger)
        self.assertTrue(primary_cancelled.is_set())
        with open(fh._frame_path(self.d, "02-answer")) as f:
            self.assertIn("HEDGE-CONTENT", f.read())
        with open(os.path.join(self.d, "STORYBOARD.md")) as f:
            self.assertEqual(f.read().count("- status: animated"), 2)
        hedge_root = os.path.join(self.d, ".hyperframes", "hedge")
        self.assertEqual(os.listdir(hedge_root), [])  # workdir cleaned
        summary = [m for m in logs.output if "stage summary" in m][0]
        self.assertIn("hedges=1 hedge_wins=1", summary)

    def test_hedge_not_started_when_primary_writes(self):
        import time as _time
        os.environ[fh.HEDGE_ENV] = "1"
        os.environ[fh.HEDGE_AFTER_ENV] = "1"

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = self._fid(prompt)
            self.calls.append(label)
            if fid == "02-answer":
                with open(fh._frame_path(project_dir, fid), "a") as f:
                    f.write("<!-- heartbeat -->")
                _time.sleep(1.5)
            return 0, f"done OK {fid}"

        with mock.patch.object(fh, "HEDGE_POLL_SECONDS", 0.05):
            self._run(fake)
        self.assertFalse(any(l.endswith("-hedge") for l in self.calls), self.calls)


class PromptModesTest(unittest.TestCase):
    def test_default_mode_batch_reads_recipes(self):
        p = fh._worker_prompt("01-hook", True, dispatch_text="# D\n## Rule index\n")
        self.assertIn("In your FIRST tool turn, Read every", p)
        self.assertIn("## Resolved conflicts", p)

    def test_snippet_mode(self):
        p = fh._worker_prompt(
            "01-hook", True,
            dispatch_text=f"# D\n## Rule index\n\n{fh.RECIPES_SNIPPET_HEADING}\n")
        self.assertIn("do NOT batch-read", p)
        self.assertNotIn("In your FIRST tool turn, Read every", p)
        self.assertIn("RULES_DIR", p)
        self.assertIn("never open frame.md or _role.md", p)

    def test_full_mode(self):
        p = fh._worker_prompt(
            "01-hook", True, dispatch_text=f"# D\n{fh.RECIPES_FULL_HEADING}\n")
        self.assertIn("Do\nNOT Read any recipe".replace("\n", " "), p.replace("\n", " "))

    def test_backticked_heading_in_card_is_not_snippet_mode(self):
        card = ("## Rule card\n> Recipes default to `## Rule recipes (snippets)` "
                "in the dispatch; `## Rule recipes (full)` only when set.\n")
        p = fh._worker_prompt(
            "01-hook", True, dispatch_text=f"# D\n{card}## Rule index\n- a\n")
        self.assertIn("Rule recipes are NOT inlined", p)
        self.assertIn("In your FIRST tool turn, Read every", p)
        self.assertNotIn("do NOT batch-read", p)
        p = fh._worker_prompt(
            "01-hook", True,
            dispatch_text=f"# D\n{card}## Rule index\n\n{fh.RECIPES_SNIPPET_HEADING}\n\n### x\n")
        self.assertIn("do NOT batch-read", p)
        self.assertNotIn("Rule recipes are NOT inlined", p)

    def test_recipes_mode_line_start(self):
        self.assertIsNone(fh._recipes_mode("see `## Rule recipes (snippets)`\n"))
        self.assertIsNone(fh._recipes_mode("x ## Rule recipes (full)\n"))
        self.assertEqual(fh._recipes_mode("a\n## Rule recipes (snippets)\n"), "snippets")
        self.assertEqual(fh._recipes_mode("## Rule recipes (full)\nb"), "full")

    def test_single_write_and_terse_reply_contract(self):
        p = fh._worker_prompt("01-hook", True, dispatch_text="# D\n")
        flat = " ".join(p.split())
        self.assertIn("under ~15 KB", flat)
        self.assertIn("ONE Write", flat)
        self.assertIn("under ~20 KB (InputValidationError)", flat)
        self.assertIn("final reply is ONLY this one line: done OK 01-hook", flat)
        self.assertIn("No summary, checklist, or explanation", flat)

    def test_rich_skeleton_line(self):
        d = make_project(skeleton=True)
        with open(fh._frame_path(d, "01-hook"), "w") as f:
            f.write("<!-- skeleton-v1 (rich): fill -->\n<template></template>")
        with open(os.path.join(d, ".hyperframes", "frame-packets",
                               "01-hook.dispatch.md"), "w") as f:
            f.write("# D\n")
        self.assertIn('data-skeleton=\\"bg\\"'.replace("\\", ""),
                      fh._worker_prompt("01-hook", True, project_dir=d))
        self.assertNotIn("data-skeleton",
                         fh._worker_prompt("02-answer", True, dispatch_text="# D\n"))


class TextOutAndPrefixCacheTest(unittest.TestCase):
    """P0-2 shared prefix cache order + P0-3 1-turn text-out mode."""

    _TEMPLATE = (
        '<template>\n<div id="root" data-composition-id="{fid}" data-duration="4">'
        '<div class="clip bg" data-start="0" data-duration="4" data-track-index="0"></div>'
        '<div class="clip" data-start="0" data-duration="4" data-track-index="1"><h1>Hi</h1></div>'
        '</div>\n<style>.bg{position:absolute;inset:0}</style>\n'
        '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>\n'
        '<script>window.__timelines=window.__timelines||{};const tl=gsap.timeline({paused:true});'
        'window.__timelines["{fid}"]=tl;tl.to("h1",{opacity:1,duration:0.5},0);</script>\n</template>'
    )

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {
            "HTMLVIDEO_FRAME_HTML_DRIVER": "1",
            "HTMLVIDEO_FRAME_HTML_SKELETON": "1",
            fh.HEDGE_ENV: "0",
            fh.TWO_PHASE_ENV: "0",
        }, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(fh.TEXT_OUT_ENV, None)
        self.addCleanup(os.environ.pop, fh.TEXT_OUT_ENV, None)

    def test_worker_prompt_shared_prefix_identical_across_frames(self):
        shared = "## Project inputs\n- Project: /x\n\n## Role\nRULE CARD\n\n## Design contract\nGRAMMAR\n"
        d1 = shared + "\n# Dispatch: 01-hook\n\n## Rule recipes (snippets)\n### a\n"
        d2 = shared + "\n# Dispatch: 02-answer\n\n## Rule recipes (snippets)\n### b\n"
        for text_out in (False, True):
            p1 = fh._worker_prompt("01-hook", True, dispatch_text=d1, text_out=text_out)
            p2 = fh._worker_prompt("02-answer", True, dispatch_text=d2, text_out=text_out)
            cut1 = p1.index("# Dispatch: 01-hook")
            cut2 = p2.index("# Dispatch: 02-answer")
            self.assertEqual(cut1, cut2)
            self.assertEqual(p1[:cut1], p2[:cut2], f"prefix diverged for text_out={text_out}")
            self.assertNotIn("01-hook", p1[:cut1])

    def _tpl(self, fid):
        return self._TEMPLATE.replace("{fid}", fid)

    def test_extract_template_html(self):
        html = self._tpl("01-hook")
        self.assertEqual(fh._extract_template_html(f"```html\n{html}\n```"), html)
        self.assertEqual(fh._extract_template_html(f"Here:\n{html}\n"), html)
        self.assertIsNone(fh._extract_template_html("done OK 01-hook"))
        self.assertIsNone(fh._extract_template_html("<template>unclosed"))

    def test_text_out_default_on_writes_template_with_zero_tools(self):
        d = make_project(skeleton=True)
        for fid in ("01-hook", "02-answer"):
            with open(os.path.join(d, ".hyperframes", "frame-packets", f"{fid}.dispatch.md"), "w") as f:
                f.write(f"## Role\ncard\n\n# Dispatch: {fid}\n\n{fh.RECIPES_SNIPPET_HEADING}\n### x\n")
        seen = []

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = "01-hook" if "01-hook.html" in prompt else "02-answer"
            seen.append((fid, prompt, kw))
            return 0, f"```html\n{self._tpl(fid)}\n```"

        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")), \
             mock.patch.object(fh, "_run_frame_selfcheck", return_value=(True, "PASS")):
            fh.build_frame_html(d, None)

        self.assertEqual(len(seen), 2)
        for fid, prompt, kw in seen:
            self.assertEqual(kw.get("tools"), "")
            self.assertEqual(kw.get("max_turns"), fh.TEXT_OUT_MAX_TURNS)
            self.assertIn("## Pre-built skeleton", prompt)
            self.assertIn("<!-- skeleton-v1 -->", prompt)
            with open(fh._frame_path(d, fid)) as f:
                written = f.read()
            self.assertIn(f'data-composition-id="{fid}"', written)
            self.assertNotIn("```", written)

    def test_text_out_missing_template_retries_with_diagnostic(self):
        d = make_project(skeleton=True)
        for fid in ("01-hook", "02-answer"):
            with open(os.path.join(d, ".hyperframes", "frame-packets", f"{fid}.dispatch.md"), "w") as f:
                f.write(f"# Dispatch: {fid}\n\n{fh.RECIPES_SNIPPET_HEADING}\n")
        seen = []

        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = "01-hook" if "01-hook.html" in prompt else "02-answer"
            seen.append((fid, prompt))
            if fid == "02-answer" and sum(x[0] == fid for x in seen) == 1:
                return 0, "sorry no template"
            return 0, self._tpl(fid)

        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")), \
             mock.patch.object(fh, "_run_frame_selfcheck", return_value=(True, "PASS")):
            fh.build_frame_html(d, None)

        retries = [p for fid, p in seen if fid == "02-answer"]
        self.assertEqual(len(retries), 2)
        self.assertIn("without a complete <template>", retries[1])

    def test_hedge_workdir_does_not_copy_claude_md(self):
        d = make_project(skeleton=True)
        with open(os.path.join(d, "CLAUDE.md"), "w") as f:
            f.write("# Interactive CLAUDE.md\n")
        work = fh._prepare_hedge_dir(d, "01-hook", 1)
        self.addCleanup(__import__("shutil").rmtree, work, True)
        self.assertFalse(os.path.exists(os.path.join(work, "CLAUDE.md")))
        self.assertTrue(os.path.isfile(os.path.join(work, "frame.md")))

    def test_minimal_dispatch_preserves_shared_prefix_and_strips_blueprint_recipes(self):
        self.assertEqual(fh.TEXT_OUT_MAX_TURNS, 1)
        self.assertEqual(fh.DEFAULT_PREFIX_WARMUP_SECONDS, 5.0)
        shared = "## Project inputs\n- Project: /x\n\n## Role\nCARD\n\n## Design contract\nDIR\n"
        full = (
            shared
            + "\n# Dispatch: 05-sell\n\n## Assigned storyboard block\n\nScene 1 (0.0–4.0s): x\n"
            + "\n## Selected blueprint: panel-edit-live-sync (mode: Adapt)\n\nBIG-BP-BODY\n"
            + "\n## Rule index (recipes summarized below)\n\n- `a` → /rules/a.md\n"
            + "\n## Rule recipes (snippets)\n\n### a\n\nBIG-RECIPE-JS\n"
            + "\n## Resolved conflicts\n\n1. **ruling**\n"
        )
        slim = fh._minimal_dispatch_text(full)
        self.assertNotIn("BIG-BP-BODY", slim)
        self.assertNotIn("BIG-RECIPE-JS", slim)
        self.assertNotIn("## Selected blueprint", slim)
        self.assertNotIn("## Rule index", slim)
        self.assertIn("## Assigned storyboard block", slim)
        self.assertIn("## Rule recipes (snippets)", slim)
        self.assertIn("## Resolved conflicts", slim)
        p_full = fh._worker_prompt("05-sell", True, dispatch_text=full, text_out=True)
        p_slim = fh._worker_prompt("05-sell", True, dispatch_text=slim, text_out=True)
        cut = p_full.index("# Dispatch: 05-sell")
        self.assertEqual(p_full[:cut], p_slim[:cut], "shared KV cache prefix must stay identical")

    def test_hedge_fires_with_multiple_live_frames_and_keeps_project_cwd_in_text_out(self):
        import logging
        import threading
        d = make_project(skeleton=True)
        for fid in ("01-hook", "02-answer"):
            with open(os.path.join(d, ".hyperframes", "frame-packets", f"{fid}.dispatch.md"), "w") as f:
                f.write(
                    f"## Role\ncard\n\n# Dispatch: {fid}\n\n## Assigned storyboard block\nScene 1 (0–4s): x\n"
                    f"\n## Selected blueprint: k\nHEAVY-BLUEPRINT\n\n{fh.RECIPES_SNIPPET_HEADING}\n### r\nHEAVY-RECIPE\n"
                )
        os.environ[fh.HEDGE_ENV] = "1"
        os.environ[fh.HEDGE_AFTER_ENV] = "1"
        os.environ[fh.PREFIX_WARMUP_ENV] = "0"
        both_primaries_live = threading.Barrier(2, timeout=5)
        hedge_cwds = []
        hedge_prompts = []

        def fake(cwd, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = "01-hook" if "01-hook.html" in prompt else "02-answer"
            if label and label.endswith("-hedge"):
                hedge_cwds.append(cwd)
                hedge_prompts.append(prompt)
                return 0, self._tpl(fid)
            # Both primaries stall simultaneously (len(live) == 2): previously
            # maybe_hedge() waited for len(live) == 1 and deadlocked until timeout.
            both_primaries_live.wait()
            kw["cancel_event"].wait(timeout=10)
            return fh.CANCELLED_RC, ""

        logger = logging.getLogger("test.fh.multihedge")
        with mock.patch.object(fh, "HEDGE_POLL_SECONDS", 0.05), \
             mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")), \
             mock.patch.object(fh, "_run_frame_selfcheck", return_value=(True, "PASS")):
            fh.build_frame_html(d, logger)
        self.assertEqual(len(hedge_cwds), 2)
        for cwd in hedge_cwds:
            self.assertEqual(cwd, d, "text-out hedge must keep project_dir cwd for prefix cache")
        for hp in hedge_prompts:
            self.assertNotIn("HEAVY-BLUEPRINT", hp)
            self.assertNotIn("HEAVY-RECIPE", hp)

    def test_second_truncation_forces_slim_degraded_retry(self):
        d = make_project(skeleton=True)
        for fid in ("01-hook", "02-answer"):
            with open(os.path.join(d, ".hyperframes", "frame-packets", f"{fid}.dispatch.md"), "w") as f:
                f.write(
                    f"# Dispatch: {fid}\n\n## Selected blueprint: b\nHEAVY-BP\n\n{fh.RECIPES_SNIPPET_HEADING}\n### x\n"
                )
        calls = []

        def fake(cwd, prompt, logger, extra_env=None, skill=None, label=None, **kw):
            fid = "01-hook" if "01-hook.html" in prompt else "02-answer"
            if fid == "01-hook":
                return 0, self._tpl(fid)
            calls.append((label, prompt, dict(extra_env or {})))
            if len(calls) <= 2:
                return fh.TRUNCATED_RC, ""
            return 0, self._tpl(fid)

        with mock.patch.object(fh, "run_claude_result", side_effect=fake), \
             mock.patch.object(fh, "build_index_html", return_value=(0, "")), \
             mock.patch.object(fh, "_run_frame_selfcheck", return_value=(True, "PASS")):
            fh.build_frame_html(d, None)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][2].get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"), "64000")
        self.assertTrue(calls[2][0].endswith("-degraded"), calls[2][0])
        self.assertNotIn("HEAVY-BP", calls[2][1])

    def test_normalize_frame_html_rewrites_digit_selectors_and_strips_pure_scale(self):
        raw = (
            '<template>\n'
            '<div id="root" data-composition-id="07-sell" data-duration="7">\n'
            '  <div class="07-sell-spread" id="07-card"><div class="volume-bar"></div><div class="rot"></div></div>\n'
            '</div>\n'
            '<style>\n'
            '  /* .07-comment stays */\n'
            '  .07-sell-spread { color: #1E1E1E; width: 0.5cqw; }\n'
            '  #07-card { padding: 1cqw; }\n'
            '  .volume-bar { transform-origin: bottom; transform: scaleY(0); }\n'
            '  .rot { transform: translateX(-50%) scaleX(0); }\n'
            '</style>\n'
            '<script>\n'
            '  const el = document.getElementById("07-card");\n'
            '  tl.to(".07-sell-spread", { autoAlpha: 1 }, 0);\n'
            '  tl.set("#07-card", { y: 0 }, 0);\n'
            '  tl.set(".volume-bar", { scaleY: 0 }, 0);\n'
            '  tl.set(".rot", { scaleX: 0 }, 0);\n'
            '</script>\n'
            '</template>'
        )
        cleaned, notes = fh._normalize_frame_html(raw)
        self.assertIn("digit-led-selectors=4", notes)
        self.assertIn("css-scale-strip=1", notes)
        self.assertIn('[class~="07-sell-spread"] { color: #1E1E1E; width: 0.5cqw; }', cleaned)
        self.assertIn('[id="07-card"] { padding: 1cqw; }', cleaned)
        self.assertIn("/* .07-comment stays */", cleaned)
        self.assertIn('document.getElementById("07-card")', cleaned)
        self.assertIn('tl.to("[class~=\'07-sell-spread\']", { autoAlpha: 1 }, 0);', cleaned)
        self.assertIn('tl.set("[id=\'07-card\']", { y: 0 }, 0);', cleaned)
        self.assertNotIn("transform: scaleY(0)", cleaned)
        self.assertIn("transform-origin: bottom;", cleaned)
        # Compound translate+scale must NOT be stripped
        self.assertIn("transform: translateX(-50%) scaleX(0);", cleaned)
        # Idempotent on second pass
        cleaned2, notes2 = fh._normalize_frame_html(cleaned)
        self.assertEqual(cleaned2, cleaned)
        self.assertEqual(notes2, [])
