# -*- coding: utf-8 -*-
"""Unit tests for storyboard prompt input reduction (frame.md register-only inline)."""
from __future__ import annotations

import os
import tempfile
import unittest

from html_utility import storyboard_contract as sc
from html_utility import storyboard as sb

FRAME_MD = """---
version: alpha
name: Capsule — Frame (video / frame layer)
description: >
  Video-first companion to Capsule's design.md. Sun-bleached cream canvas.
unit: the frame — 1920×1080 primary
principle: atoms are sacred
colors:
  cream: "#F5F5F0"
  coral: "#E85D4E"
typography:
  body: { fontFamily: "Space Grotesk", cqw: 0.85 }
components:
  pill:
    backgroundColor: "any candy"
---
## Overview
"""

BRIEF_MD = """---
message: "A test message"
audience: "testers"
aspect: 1920x1080
length: 60s
style_preset: capsule
---
## Intent
test
"""


class SkeletonTest(unittest.TestCase):
    def test_skeleton_frontmatter_from_brief(self):
        fm = sc.build_skeleton_frontmatter(BRIEF_MD)
        self.assertIn("format: 1920x1080", fm)
        self.assertIn("duration: 60s", fm)
        self.assertIn('message: "A test message"', fm)
        self.assertIn('audience: "testers"', fm)
        self.assertIn("mode: autonomous", fm)
        self.assertIn("arc: <FILL>", fm)
        self.assertIn("music: <FILL>", fm)

    def test_skeleton_pulls_arc_from_brief(self):
        brief = BRIEF_MD.replace(
            "style_preset: capsule",
            'style_preset: capsule\narc: "how-to-process (Hook → Thesis → Steps → CTA)"',
        )
        fm = sc.build_skeleton_frontmatter(brief)
        self.assertIn('arc: "how-to-process (Hook → Thesis → Steps → CTA)"', fm)
        self.assertNotIn("arc: <FILL>", fm)


class FrameRegisterTest(unittest.TestCase):
    def test_register_drops_design_tokens(self):
        reg = sc._frame_register(FRAME_MD)
        self.assertIn("Capsule", reg)                 # name kept
        self.assertIn("cream canvas", reg)            # description (register) kept
        self.assertNotIn("#F5F5F0", reg)              # hex dropped
        self.assertNotIn("typography:", reg)          # design tokens dropped
        self.assertNotIn("components:", reg)

    def test_no_frontmatter_falls_back_to_head(self):
        self.assertEqual(sc._frame_register("# Overview\n\nbody"), "# Overview\n\nbody")
        self.assertEqual(sc._frame_register(""), "")


class BuildPromptTest(unittest.TestCase):
    def test_prompt_inlines_register_not_full_frame(self):
        d = tempfile.mkdtemp(prefix="sb-")
        with open(os.path.join(d, "BRIEF.md"), "w") as f:
            f.write(BRIEF_MD)
        with open(os.path.join(d, "frame.md"), "w") as f:
            f.write(FRAME_MD)
        p = sc.build_storyboard_prompt(d, contract_card="CARD")
        self.assertIn("Capsule", p)                                  # register present
        self.assertIn("frame.md register", p)                        # labelled register
        self.assertNotIn("#F5F5F0", p)                               # hex dropped
        self.assertNotIn('fontFamily: "Space Grotesk", cqw: 0.85', p)  # typography dropped
        self.assertIn("CARD", p)                                     # card still inlined

    def test_prompt_inlines_pre_seeded_frontmatter(self):
        d = tempfile.mkdtemp(prefix="sb-")
        with open(os.path.join(d, "BRIEF.md"), "w") as f:
            f.write(BRIEF_MD)
        with open(os.path.join(d, "frame.md"), "w") as f:
            f.write(FRAME_MD)
        p = sc.build_storyboard_prompt(d, contract_card="CARD")
        self.assertIn("## Pre-seeded STORYBOARD frontmatter", p)
        self.assertIn("format: 1920x1080", p)
        self.assertIn("mode: autonomous", p)
        self.assertIn("do NOT Read STORYBOARD.md", p)         # no file Read needed


class SrcNormalizeTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(sb._slugify("Not investing, guessing"), "not-investing-guessing")
        self.assertEqual(sb._slugify("  Two-Part Checklist "), "two-part-checklist")

    def test_normalize_frame_srcs(self):
        d = tempfile.mkdtemp(prefix="sb-src-")
        text = (
            "## Frame 1 — Not investing, guessing\n"
            "- scene: x\n"
            "- src: compositions/frames/whatever.html\n"
            "\n"
            "## Frame 2 — Two-part checklist\n"
            "- scene: y\n"
            "- src: compositions/frames/02-garbage.html\n"
        )
        with open(os.path.join(d, "STORYBOARD.md"), "w") as f:
            f.write(text)
        sb._normalize_frame_srcs(d)
        with open(os.path.join(d, "STORYBOARD.md")) as f:
            out = f.read()
        self.assertIn("- src: compositions/frames/01-not-investing-guessing.html", out)
        self.assertIn("- src: compositions/frames/02-two-part-checklist.html", out)


if __name__ == "__main__":
    unittest.main()