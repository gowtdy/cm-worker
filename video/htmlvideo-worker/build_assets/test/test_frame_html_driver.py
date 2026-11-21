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
        patcher = mock.patch.dict(os.environ, {
            "HTMLVIDEO_FRAME_HTML_DRIVER": "1",
            "HTMLVIDEO_FRAME_HTML_SKELETON": "1",
        }, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_dispatch(self, results, assemble_results=((0, ""),)):
        """results: dict frame_id -> list of (rc, text) per attempt.
        assemble_results: build_index_html return values in call order."""
        calls = {k: list(v) for k, v in results.items()}
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None):
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
        def counting(project_dir, prompt, logger, extra_env=None, skill=None, label=None):
            seen.append(prompt)
            return orig(project_dir, prompt, logger, extra_env=extra_env, skill=skill, label=label)
        with mock.patch.object(fh, "run_claude_result", side_effect=counting):
            fh.build_frame_html(d, None)
        self.assertEqual(sum("02-answer" in p for p in seen), 2)

    def test_two_rounds_then_raise(self):
        d = make_project(skeleton=True)
        self._patch_dispatch({"01-hook": [(0, "done OK 01-hook")],
                              "02-answer": [(0, ""), (0, "")]})
        with self.assertRaises(RuntimeError) as ctx:
            fh.build_frame_html(d, None)
        self.assertIn("02-answer: prior attempt", str(ctx.exception))

    def test_timeout_signal_embedded_in_retry_note(self):
        d = make_project(skeleton=True)
        seen = []
        def fake(project_dir, prompt, logger, extra_env=None, skill=None, label=None):
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
