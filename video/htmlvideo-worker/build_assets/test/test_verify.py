# -*- coding: utf-8 -*-
"""Unit tests for html_utility.verify (scan precheck + digest guard messaging)."""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest import mock

from html_utility import verify as v


class ScanUnactionableTest(unittest.TestCase):
    def test_fail_zero_errors_no_numbered_action(self):
        out = """
── actionable summary ──
  #2 tell only — digit-led ids exist but no digit-led selectors. No action.

GATE: FAIL ✗ (lint ok=True, check exit=1, digit-led selectors=0, missing frames=0, transitions=ok, 0 error finding(s))
"""
        self.assertTrue(v.scan_looks_unactionable(out))

    def test_fail_with_numbered_action_is_actionable(self):
        out = """
── actionable summary ──
  1) Known failure #4 candidate — content_overlap: add data-layout-allow-overlap

GATE: FAIL ✗ (…, 8 error finding(s))
"""
        self.assertFalse(v.scan_looks_unactionable(out))

    def test_pass_is_not_unactionable(self):
        self.assertFalse(
            v.scan_looks_unactionable("GATE: PASS ✓ (lint+check+structural+transitions)")
        )

    def test_parser_infra_marker(self):
        out = "  ✗ PARSER/INFRA: check exited non-zero but 0 error findings\nGATE: FAIL ✗"
        self.assertTrue(v.scan_looks_unactionable(out))


class VerifyFramePrecheckTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="verify-pre-")
        os.makedirs(os.path.join(self.d, "compositions", "frames"), exist_ok=True)
        for name in ("hyperframes.json", "index.html", "STORYBOARD.md"):
            with open(os.path.join(self.d, name), "w") as f:
                f.write("x\n")
        with open(
            os.path.join(self.d, "compositions", "frames", "01-hook.html"), "w"
        ) as f:
            f.write("<template></template>")
        self.logger = logging.getLogger("test.verify")

    def test_unactionable_scan_raises_without_claude(self):
        scan = (
            "── actionable summary ──\n"
            "  #2 tell only. No action.\n\n"
            "GATE: FAIL ✗ (…, 0 error finding(s))\n"
        )
        with mock.patch.object(v, "_run_verify_all", return_value=(1, scan)), \
             mock.patch.object(v, "run_claude") as claude:
            with self.assertRaises(RuntimeError) as ctx:
                v.verify_frame(self.d, self.logger)
        claude.assert_not_called()
        self.assertIn("0 actionable findings", str(ctx.exception))
        self.assertIn("refusing to invoke the model", str(ctx.exception))

    def test_actionable_scan_invokes_claude(self):
        scan = (
            "── actionable summary ──\n"
            "  1) Known failure #4 candidate — content_overlap\n\n"
            "GATE: FAIL ✗ (…, 2 error finding(s))\n"
        )
        with mock.patch.object(v, "_run_verify_all", side_effect=[
                 (1, scan),
                 (0, "GATE: PASS ✓\n"),
             ]), \
             mock.patch.object(v, "run_claude", return_value=0) as claude, \
             mock.patch.object(v, "_composition_html_digest", side_effect=["a", "b"]), \
             mock.patch.object(
                 v, "_validate_verify_outputs",
                 return_value=os.path.join(self.d, "snapshots", "contact-sheet.jpg"),
             ):
            # need a fake contact sheet path existence? validate is mocked
            out = v.verify_frame(self.d, self.logger)
        claude.assert_called_once()
        self.assertIn("contact-sheet", out)


if __name__ == "__main__":
    unittest.main()
