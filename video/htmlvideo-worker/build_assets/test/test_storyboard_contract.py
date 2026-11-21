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
        self.assertIn('First tool call MUST be Write("STORYBOARD.md")', p)
        self.assertIn("Do NOT Write SCRIPT.md", p)
        self.assertNotIn("both Write tool", p)


class SrcNormalizeTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(sb._slugify("Not investing, guessing"), "not-investing-guessing")
        self.assertEqual(sb._slugify("  Two-Part Checklist "), "two-part-checklist")

    def test_slugify_non_ascii_uses_stable_hash(self):
        # Chinese-only titles previously collapsed to "" → ``01-.html``.
        # Now a stable zh+8hex so frames stay unique (not all ``NN-frame``).
        self.assertEqual(sb._slugify("买入最难的是卖"), "zhed4d2ab4")
        self.assertEqual(sb._slugify("???"), "zh2d86c2a6")
        self.assertEqual(sb._slugify(""), "zhda39a3ee")
        # Same title → same slug; different titles → different slugs.
        self.assertEqual(sb._slugify("买入最难的是卖"), sb._slugify("买入最难的是卖"))
        self.assertNotEqual(sb._slugify("买入最难的是卖"), sb._slugify("另一标题"))

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

    def test_normalize_chinese_title_avoids_trailing_hyphen_id(self):
        d = tempfile.mkdtemp(prefix="sb-zh-")
        text = (
            "## Frame 1 — 买入最难的是卖\n"
            "- scene: x\n"
            "- src: compositions/frames/01-.html\n"
        )
        with open(os.path.join(d, "STORYBOARD.md"), "w") as f:
            f.write(text)
        sb._normalize_frame_srcs(d)
        with open(os.path.join(d, "STORYBOARD.md")) as f:
            out = f.read()
        self.assertIn("- src: compositions/frames/01-zhed4d2ab4.html", out)
        self.assertNotIn("01-.html", out)
        self.assertNotIn("01-frame.html", out)


class ScriptDeriveTest(unittest.TestCase):
    def test_generate_script_from_voiceovers(self):
        d = tempfile.mkdtemp(prefix="sb-script-")
        brief_zh = BRIEF_MD.replace(
            "style_preset: capsule",
            "style_preset: capsule\nlanguage: zh",
        )
        with open(os.path.join(d, "BRIEF.md"), "w") as f:
            f.write(brief_zh)
        storyboard = (
            "---\nmessage: x\n---\n\n"
            "## Frame 1 — Hook title\n"
            "- scene: s1\n"
            '- voiceover: "First the snowball — then the hill."\n'
            "- duration: 4s\n"
            "\n"
            "## Frame 2 — Silent beat\n"
            "- scene: s2\n"
            '- voiceover: ""\n'
            "- duration: 3s\n"
            "\n"
            "## Frame 3 — Close\n"
            "- scene: s3\n"
            '- voiceover: "Ship it today."\n'
            "- duration: 5.5s\n"
        )
        with open(os.path.join(d, "STORYBOARD.md"), "w") as f:
            f.write(storyboard)
        path = sb.generate_script_from_storyboard(d)
        self.assertTrue(path and os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("(Frame 1)", text)
        self.assertIn("(Frame 3)", text)
        self.assertNotIn("(Frame 2)", text)  # empty voiceover omitted
        self.assertIn("    First the snowball — then the hill.", text)
        self.assertIn("    Ship it today.", text)
        self.assertIn("**Time:** 0.0 – 4.0s", text)
        self.assertIn("**Time:** 4.0 – 9.5s", text)  # cumulative across spoken only
        self.assertIn("中文普通话", text)

    def test_silent_board_removes_script(self):
        d = tempfile.mkdtemp(prefix="sb-silent-")
        with open(os.path.join(d, "STORYBOARD.md"), "w") as f:
            f.write(
                "## Frame 1 — Quiet\n"
                "- scene: s\n"
                '- voiceover: ""\n'
                "- duration: 4s\n"
            )
        script = os.path.join(d, "SCRIPT.md")
        with open(script, "w") as f:
            f.write("stale\n")
        self.assertIsNone(sb.generate_script_from_storyboard(d))
        self.assertFalse(os.path.isfile(script))

    def test_finalize_gates_spoken_board(self):
        d = tempfile.mkdtemp(prefix="sb-final-")
        with open(os.path.join(d, "BRIEF.md"), "w") as f:
            f.write(BRIEF_MD)
        with open(os.path.join(d, "STORYBOARD.md"), "w") as f:
            f.write(
                "## Frame 1 — Hook\n"
                "- scene: s\n"
                '- voiceover: "Hello there."\n'
                "- duration: 3s\n"
                "- src: compositions/frames/wrong.html\n"
            )
        out = sb.finalize_storyboard_outputs(d)
        self.assertTrue(out.endswith("STORYBOARD.md"))
        with open(os.path.join(d, "STORYBOARD.md"), encoding="utf-8") as f:
            self.assertIn("01-hook.html", f.read())
        with open(os.path.join(d, "SCRIPT.md"), encoding="utf-8") as f:
            self.assertIn("    Hello there.", f.read())


class ProfileDenyTest(unittest.TestCase):
    def test_storyboard_denies_explore_tools(self):
        from html_utility.claude_cli import _PROFILES

        deny = set(_PROFILES["storyboard"].get("deny_tools") or [])
        for name in ("Bash", "Read", "Glob", "Grep", "Edit"):
            self.assertIn(name, deny)
        self.assertNotIn("Write", deny)
        self.assertEqual(_PROFILES["storyboard"]["max_turns"], 8)

    def test_visual_denies_explore_and_write(self):
        from html_utility.claude_cli import _PROFILES

        deny = set(_PROFILES["visual"].get("deny_tools") or [])
        for name in ("Bash", "Glob", "Grep", "Write", "Edit"):
            self.assertIn(name, deny)
        self.assertNotIn("Read", deny)  # optional rules escape hatch

    def test_verify_denies_explore_keeps_edit(self):
        from html_utility.claude_cli import _PROFILES

        deny = set(_PROFILES["verify"].get("deny_tools") or [])
        for name in ("Bash", "Glob", "Grep"):
            self.assertIn(name, deny)
        for name in ("Read", "Edit", "Write"):
            self.assertNotIn(name, deny)
        self.assertEqual(_PROFILES["verify"]["max_turns"], 40)


if __name__ == "__main__":
    unittest.main()