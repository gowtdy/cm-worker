# -*- coding: utf-8 -*-
"""Unit tests for the frame-visual design-contract (Phase A) prompt."""
from __future__ import annotations

import unittest

from html_utility import visual as vis

_TOKENS = (
    'colors:\n'
    '  cream: "#F5F5F0"\n'
    '  coral: "#E85D4E"\n'
    'typography:\n'
    '  body: { fontFamily: "Space Grotesk", cqw: 0.85, weight: 400 }'
)


class DesignPromptTest(unittest.TestCase):
    def test_inlines_tokens_and_forbids_reading_frame_md(self):
        p = vis._build_design_prompt("---\nformat: landscape\n---", ["1", "2", "3"], _TOKENS)
        self.assertNotIn("Read frame.md", p)
        self.assertIn('"#F5F5F0"', p)
        self.assertIn('"#E85D4E"', p)
        self.assertIn("1, 2, 3", p)
        self.assertIn("## Blueprint assignment", p)          # assignment still the model's
        self.assertIn("creative body", p)                    # palette/type no longer the model's

    def test_scripted_direction_head_carries_palette_type(self):
        head = vis._scripted_direction_head(_TOKENS)
        self.assertIn("## Video direction", head)
        self.assertIn("cream `#F5F5F0`", head)      # readable prose, not YAML
        self.assertIn("do not restate", head)
        self.assertNotIn("colors:", head)            # raw YAML dropped

    def test_tokens_to_prose_not_yaml(self):
        prose = vis._tokens_to_prose(_TOKENS)
        self.assertIn("cream `#F5F5F0`", prose)
        self.assertIn("Type Space Grotesk", prose)
        self.assertNotIn("colors:", prose)
        self.assertNotIn("  cream:", prose)

    def test_repair_prompt_inlines_findings(self):
        p = vis._visual_repair_prompt("F3 coverage < 90%")
        self.assertIn("F3 coverage < 90%", p)
        self.assertIn("Do NOT change narrative fields", p)

    def test_validate_design_contract(self):
        self.assertEqual(
            vis._validate_design_contract("<creative>\n\n## Blueprint assignment\nF1 = a"),
            "<creative>\n\n## Blueprint assignment\nF1 = a",
        )
        with self.assertRaises(RuntimeError):
            vis._validate_design_contract("<creative, no assignment>")

    def test_falls_back_to_reading_frame_md_without_tokens(self):
        p = vis._build_design_prompt("---\nformat: landscape\n---", ["1", "2"], "")
        self.assertIn("Read frame.md", p)


class FrameMdTokensTest(unittest.TestCase):
    def test_extracts_colors_and_typography_from_frontmatter(self):
        fm = (
            "---\n"
            "version: alpha\n"
            "colors:\n"
            '  cream: "#F5F5F0"\n'
            '  coral: "#E85D4E"\n'
            "typography:\n"
            '  body: { fontFamily: "Space Grotesk", cqw: 0.85 }\n'
            "spacing:\n"
            '  pad: "5cqw"\n'
            "---\n"
            "## Overview\n"
        )
        out = vis._frame_md_tokens(fm)
        self.assertIn('cream: "#F5F5F0"', out)
        self.assertIn('coral: "#E85D4E"', out)
        self.assertIn("typography:", out)
        self.assertNotIn("spacing:", out)

    def test_no_frontmatter_returns_empty(self):
        self.assertEqual(vis._frame_md_tokens("# no frontmatter\n\n## Colors\n"), "")


class WorkerPromptTest(unittest.TestCase):
    def test_blueprint_body_inlined(self):
        p = vis._build_worker_prompt("7", "block", "design", "BLUEPRINT BODY")
        self.assertIn("[SELECTED BLUEPRINT", p)
        self.assertIn("BLUEPRINT BODY", p)

    def test_blueprint_body_omitted_when_empty(self):
        p = vis._build_worker_prompt("7", "block", "design")
        self.assertNotIn("[SELECTED BLUEPRINT", p)

    def test_worker_prompt_locks_to_inlined_inputs(self):
        p = vis._build_worker_prompt("7", "block", "design")
        self.assertIn("read the SKILL reference files", p)
        self.assertIn("do NOT search the filesystem", p)
        self.assertIn("do NOT run any script or gate", p)
        self.assertIn("/rules/", p)

    def test_split_design(self):
        d = vis._split_design(
            "## Video direction\ngrammar\n## Blueprint assignment\nF1 = a (Adapt)")
        self.assertIn("grammar", d[0])
        self.assertNotIn("Blueprint assignment", d[0])
        self.assertIn("F1 = a", d[1])

    def test_frame_blueprint_and_line(self):
        a = ("## Blueprint assignment\n\n"
             "F1 = kinetic-type-beats (Adapt)\n"
             "F7 = titlecard-reveal (Adapt)\n")
        self.assertEqual(vis._frame_blueprint(a, "7"), "titlecard-reveal")
        self.assertEqual(vis._frame_assignment_line(a, "7"), "F7 = titlecard-reveal (Adapt)")
        self.assertEqual(vis._frame_blueprint(a, "999"), "")

    def test_blueprint_body_empty_for_compose(self):
        self.assertEqual(vis._blueprint_body("compose"), "")
        self.assertEqual(vis._blueprint_body(""), "")
        self.assertEqual(vis._blueprint_body("no-such-blueprint"), "")

    def test_blueprint_body_reads_real_file(self):
        self.assertTrue(vis._blueprint_body("titlecard-reveal"))


if __name__ == "__main__":
    unittest.main()