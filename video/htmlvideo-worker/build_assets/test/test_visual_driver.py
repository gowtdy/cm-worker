# -*- coding: utf-8 -*-
"""Unit tests for the frame-visual design-contract (Phase A) prompt."""
from __future__ import annotations

import logging
import unittest
from unittest import mock

from html_utility import visual as vis

_TOKENS = (
    'colors:\n'
    '  cream: "#F5F5F0"\n'
    '  coral: "#E85D4E"\n'
    'typography:\n'
    '  body: { fontFamily: "Space Grotesk", cqw: 0.85, weight: 400 }\n'
    '  display: { fontFamily: "Bodoni Moda", cqw: 12.0 }\n'
    '  headline: { fontFamily: "Bodoni Moda", cqw: 5.0 }'
)

_CATALOG = frozenset({
    "kinetic-type-beats",
    "titlecard-reveal",
    "comparison-split",
    "dataviz-countup",
    "cta-morph-press",
    "grid-card-assemble",
})


class DesignPromptTest(unittest.TestCase):
    def test_inlines_tokens_and_forbids_reading_frame_md(self):
        p = vis._build_design_prompt(
            "---\nformat: landscape\n---", ["1", "2", "3"], _TOKENS, _CATALOG
        )
        self.assertNotIn("Read frame.md", p)
        self.assertIn('"#F5F5F0"', p)
        self.assertIn('"#E85D4E"', p)
        self.assertIn("1, 2, 3", p)
        self.assertIn("## Blueprint assignment", p)          # assignment still the model's
        self.assertIn("creative body", p)                    # palette/type no longer the model's

    def test_design_prompt_lists_catalog_and_bans_type_tokens(self):
        p = vis._build_design_prompt(
            "---\nformat: landscape\n---", ["1", "2"], _TOKENS, _CATALOG
        )
        self.assertIn("Valid blueprint ids", p)
        self.assertIn("- kinetic-type-beats", p)
        self.assertIn("- titlecard-reveal", p)
        self.assertIn("`compose`", p)
        self.assertIn("Banned examples:", p)
        self.assertIn("`display`", p)
        self.assertIn("`headline`", p)
        self.assertNotIn("- display\n", p)  # type token must not appear as a valid id

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
        # P0-1 (2026-11-25 6b06cfc0eeb0cbb3): bold heading variant normalized to H2
        bold_out = vis._validate_design_contract(
            "**Color roles.** Cream.\n\n**Blueprint assignment**\nF1 = kinetic-type-beats (Adapt)\n"
        )
        self.assertIn("\n## Blueprint assignment\nF1 = kinetic-type-beats (Adapt)", bold_out)
        self.assertNotIn("**Blueprint assignment**", bold_out)
        # Bare F1 = ... lines without heading also get normalized
        bare_out = vis._validate_design_contract(
            "**Motion grammar.** Snap.\n\nF1 = kinetic-type-beats (Adapt)\nF2 = compose\n"
        )
        self.assertIn("\n## Blueprint assignment\nF1 = kinetic-type-beats (Adapt)", bare_out)
        # H3 variant also normalized
        h3_out = vis._validate_design_contract(
            "prose\n\n### Blueprint assignment:\nF1 = titlecard-reveal (Adapt)\n"
        )
        self.assertIn("\n## Blueprint assignment\nF1 = titlecard-reveal (Adapt)", h3_out)
        with self.assertRaises(RuntimeError):
            vis._validate_design_contract("<creative, no assignment>")

    def test_catalog_prompt_block_includes_blueprint_intents(self):
        p = vis._catalog_prompt_block(frozenset({"titlecard-reveal", "comparison-split"}))
        self.assertIn("- titlecard-reveal — ", p)
        self.assertIn("- comparison-split — ", p)
        self.assertIn("- compose — ", p)

    def test_falls_back_to_reading_frame_md_without_tokens(self):
        p = vis._build_design_prompt(
            "---\nformat: landscape\n---", ["1", "2"], "", _CATALOG
        )
        self.assertIn("Read frame.md", p)


class BlueprintCatalogGateTest(unittest.TestCase):
    def test_catalog_includes_on_disk_shapes(self):
        cat = vis._blueprint_catalog()
        self.assertIn("titlecard-reveal", cat)
        self.assertIn("comparison-split", cat)
        self.assertNotIn("display", cat)
        self.assertNotIn("compose", cat)  # compose is synthetic, not a file

    def test_type_token_names_from_tokens(self):
        names = vis._type_token_names(_TOKENS)
        self.assertIn("cream", names)
        self.assertIn("display", names)
        self.assertIn("headline", names)
        self.assertIn("body", names)

    def test_assignment_misses_type_tokens(self):
        a = (
            "## Blueprint assignment\n"
            "F1 = display (Reproduce)\n"
            "F2 = titlecard-reveal (Adapt)\n"
            "F3 = split (Adapt)\n"
        )
        misses = vis._assignment_catalog_misses(a, ["1", "2", "3"], _CATALOG)
        self.assertEqual(misses, [("1", "display"), ("3", "split")])

    def test_assignment_accepts_compose_and_catalog(self):
        a = (
            "## Blueprint assignment\n"
            "F1 = compose (Adapt)\n"
            "F2 = kinetic-type-beats (Reproduce)\n"
        )
        self.assertEqual(
            vis._assignment_catalog_misses(a, ["1", "2"], _CATALOG), []
        )

    def test_splice_assignment_replaces_block(self):
        design = (
            "## Video direction\n\ngrammar\n\n"
            "## Blueprint assignment\n\n"
            "F1 = display (Reproduce)\n"
            "F2 = headline (Adapt)\n"
        )
        rewrite = (
            "## Blueprint assignment\n\n"
            "F1 = kinetic-type-beats (Reproduce)\n"
            "F2 = titlecard-reveal (Adapt)\n"
        )
        out = vis._splice_assignment(design, rewrite)
        self.assertIn("grammar", out)
        self.assertIn("F1 = kinetic-type-beats", out)
        self.assertNotIn("display", out)

    def test_assignment_rewrite_prompt_lists_bad_and_catalog(self):
        design = "## Video direction\nx\n\n## Blueprint assignment\nF1 = display (Adapt)\n"
        p = vis._build_assignment_rewrite_prompt(
            design, [("1", "display")], ["1", "2"], _CATALOG, ["display", "headline"]
        )
        self.assertIn("F1=display", p)
        self.assertIn("Valid blueprint ids", p)
        self.assertIn("- titlecard-reveal", p)
        self.assertIn("`display`", p)


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

    def test_worker_prompt_requires_rules_field(self):
        p = vis._build_worker_prompt("7", "block", "design")
        self.assertIn("- rules: <comma-separated rule ids", p)
        self.assertIn("`- rules:` is REQUIRED", p)
        self.assertIn("rejected and re-dispatched", p)
        self.assertIn("`discrete-text-sequence` + `context-sensitive-cursor`", p)
        self.assertIn("Valid rule ids: ", p)
        self.assertIn("discrete-text-sequence", p.split("Valid rule ids: ", 1)[1])
        self.assertNotIn("Candidate rule ids", p)

    def test_worker_prompt_candidate_rules_after_shared_prefix(self):
        p = vis._build_worker_prompt(
            "7", "block", "DESIGN", "BP", ["discrete-text-sequence", "svg-path-draw"])
        self.assertIn("Candidate rule ids (from this blueprint's rule mapping", p)
        self.assertIn("discrete-text-sequence, svg-path-draw", p)
        self.assertLess(p.index("[DESIGN CONTRACT]"), p.index("Candidate rule ids"))
        self.assertLess(p.index("Candidate rule ids"), p.index("[CONTEXT"))

    def test_worker_prompt_assignment_is_authoritative(self):
        p = vis._build_worker_prompt(
            "2", "block", "design", assigned_blueprint="grid-card-assemble (Adapt)")
        self.assertIn("BLUEPRINT AUTHORITY", p)
        self.assertIn("is FINAL", p)
        self.assertIn("superseded outline leftover: ignore it", p)
        self.assertIn("do NOT compare, weigh, or arbitrate blueprint ids", p)
        self.assertIn("copied verbatim from your assignment line", p)
        self.assertIn("Adapt/Reproduce and the Scenes", p)
        self.assertTrue(p.rstrip().endswith(
            "Your blueprint line is exactly: `- blueprint: grid-card-assemble (Adapt)`."))
        self.assertLess(p.index("BLUEPRINT AUTHORITY"), p.index("[DESIGN CONTRACT]"))
        self.assertNotIn("Your blueprint line is exactly",
                         vis._build_worker_prompt("2", "block", "design"))

    def test_assigned_blueprint_field_and_mode(self):
        a = "## Blueprint assignment\nF2 = grid-card-assemble (Adapt)\nF3 = compose (Adapt)\n"
        self.assertEqual(vis._frame_blueprint_mode(a, "2"), "Adapt")
        self.assertEqual(vis._frame_blueprint_mode(a, "9"), "")
        self.assertEqual(vis._assigned_blueprint_field("grid-card-assemble", "Adapt"),
                         "grid-card-assemble (Adapt)")
        self.assertEqual(vis._assigned_blueprint_field("compose", "Adapt"), "compose")
        self.assertEqual(vis._assigned_blueprint_field("", ""), "compose")

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

    def test_blueprint_body_warns_on_miss(self):
        log = logging.getLogger("test.visual.bp_miss")
        with mock.patch.object(log, "warning") as warn:
            self.assertEqual(vis._blueprint_body("no-such-blueprint", log), "")
            warn.assert_called_once()
            self.assertIn("no-such-blueprint", warn.call_args[0][1])

    def test_blueprint_body_reads_real_file(self):
        self.assertTrue(vis._blueprint_body("titlecard-reveal"))


_GOOD_REPLY = (
    "done OK 6\n"
    "- blueprint: grid-card-assemble (Adapt)\n"
    "- focal: the card grid\n"
    "- roles: grid = foreground · cream = background wallpaper\n"
    "- sfx: soft-pop\n"
    "- rules: center-outward-expansion, spring-pop-entrance\n"
    "Scene 1 (0.0–2.0s): cards rise (`center-outward-expansion`).\n"
    "Scene 2 (2.0–6.0s): grid locks.\n"
)


class StripConflictingBlueprintsTest(unittest.TestCase):
    _HEAD = "## Frame 2 — Scorecard\n\n- scene: two columns\n"
    _TAIL = "- duration: 8.2s\n- beat: comprehension\n"

    def test_conflicting_line_dropped(self):
        block = (self._HEAD + "- blueprint: titlecard-reveal — one restrained reveal, "
                 "then a still hold.\n" + self._TAIL)
        out, dropped = vis._strip_conflicting_blueprints(
            block, "grid-card-assemble", "Adapt")
        self.assertEqual(dropped, ["titlecard-reveal"])
        self.assertNotIn("titlecard-reveal", out)
        self.assertNotIn("blueprint", out)
        self.assertEqual(out, self._HEAD + self._TAIL)

    def test_same_id_kept_and_mode_aligned(self):
        block = self._HEAD + "- blueprint: grid-card-assemble (Reproduce)\n" + self._TAIL
        out, dropped = vis._strip_conflicting_blueprints(
            block, "grid-card-assemble", "Adapt")
        self.assertEqual(dropped, [])
        self.assertIn("- blueprint: grid-card-assemble (Adapt)\n", out)
        same = self._HEAD + "- blueprint: grid-card-assemble — tiles cascade\n" + self._TAIL
        self.assertEqual(
            vis._strip_conflicting_blueprints(same, "GRID-card-assemble", "Adapt"),
            (same, []))

    def test_no_blueprint_line_unchanged(self):
        block = self._HEAD + self._TAIL
        self.assertEqual(
            vis._strip_conflicting_blueprints(block, "grid-card-assemble"), (block, []))
        self.assertEqual(vis._strip_conflicting_blueprints("", "x"), ("", []))

    def test_multiple_conflicts_and_duplicates_cleared(self):
        block = (self._HEAD
                 + "- blueprint: titlecard-reveal — old\n"
                 + "  * **blueprint:** kinetic-type-beats\n"
                 + "- blueprint: Adapt\n"
                 + "- blueprint: grid-card-assemble — keep me\n"
                 + "- blueprint: grid-card-assemble (Reproduce)\n"
                 + "- blueprints: not-a-field\n"
                 + self._TAIL)
        out, dropped = vis._strip_conflicting_blueprints(block, "grid-card-assemble")
        self.assertEqual(
            dropped, ["titlecard-reveal", "kinetic-type-beats", "∅", "grid-card-assemble"])
        self.assertEqual(out.count("blueprint:"), 1)
        self.assertIn("- blueprint: grid-card-assemble — keep me", out)
        self.assertIn("- blueprints: not-a-field", out)

    def test_compose_assignment_drops_outline_candidate(self):
        out, dropped = vis._strip_conflicting_blueprints(
            self._HEAD + "- blueprint: titlecard-reveal\n", "")
        self.assertEqual(dropped, ["titlecard-reveal"])
        self.assertNotIn("blueprint", out)


def _blueprint_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if vis._BLUEPRINT_FIELD_RE.match(line)]


class EnforceStoryboardBlueprintsTest(unittest.TestCase):
    _ASSIGN = "F1 = grid-card-assemble (Adapt)\nF2 = compose\n"
    _ADD = "- focal: f\n- roles: r\nScene 1 (0–3): a\n"

    def _board(self, b1: str, b2: str) -> str:
        return "---\nformat: 1\n---\n\n## Blueprint assignment\n" + self._ASSIGN + "\n" + b1 + "\n" + b2

    def test_outline_conflict_and_worker_line_collapse_to_one(self):
        b1 = (_BLOCKS["1"] + "- blueprint: titlecard-reveal — old\n\n"
              "- blueprint: grid-card-assemble (Adapt)\n" + self._ADD)
        b2 = _BLOCKS["2"] + "- blueprint: compose\n\n- blueprint: compose\n" + self._ADD
        out, fixes = vis._enforce_storyboard_blueprints(self._board(b1, b2), self._ASSIGN)
        _, blocks, _ = vis._split_storyboard(out)
        self.assertEqual(_blueprint_lines(blocks["1"]), ["- blueprint: grid-card-assemble (Adapt)"])
        self.assertEqual(_blueprint_lines(blocks["2"]), ["- blueprint: compose"])
        self.assertEqual(fixes, {"1": ["titlecard-reveal"], "2": ["compose"]})
        self.assertIn(_BLOCKS["1"], out)
        self.assertIn("## Blueprint assignment\n" + self._ASSIGN, out)   # preamble untouched

    def test_worker_off_assignment_line_rewritten_in_place(self):
        b1 = _BLOCKS["1"] + "\n- blueprint: kinetic-type-beats (Reproduce)\n" + self._ADD
        out, fixes = vis._enforce_storyboard_blueprints(
            self._board(b1, _BLOCKS["2"] + "\n- blueprint: compose\n" + self._ADD), self._ASSIGN)
        self.assertEqual(fixes, {"1": ["kinetic-type-beats"]})
        self.assertIn("- beat: recognition + concern\n\n- blueprint: grid-card-assemble (Adapt)\n"
                      "- focal: f", out)

    def test_missing_line_injected_before_focal(self):
        b1 = _BLOCKS["1"] + "\n" + self._ADD
        out, fixes = vis._enforce_storyboard_blueprints(
            self._board(b1, _BLOCKS["2"]), self._ASSIGN)
        _, blocks, _ = vis._split_storyboard(out)
        self.assertIn("\n\n- blueprint: grid-card-assemble (Adapt)\n- focal: f", blocks["1"])
        self.assertEqual(_blueprint_lines(blocks["1"]), ["- blueprint: grid-card-assemble (Adapt)"])
        self.assertEqual(fixes["1"], ["∅"])
        # no anchor at all → appended at the end of the section
        self.assertTrue(blocks["2"].endswith("- beat: comprehension\n- blueprint: compose"))

    def test_canonical_board_is_a_noop(self):
        b1 = _BLOCKS["1"] + "\n- blueprint: grid-card-assemble (Adapt)\n" + self._ADD
        b2 = _BLOCKS["2"] + "\n- blueprint: compose\n" + self._ADD
        board = self._board(b1, b2)
        self.assertEqual(vis._enforce_storyboard_blueprints(board, self._ASSIGN), (board, {}))


class SceneParseTest(unittest.TestCase):
    def test_bullet_prefixed_scene_lines_accepted_and_normalized(self):
        reply = _GOOD_REPLY.replace("Scene 1", "- Scene 1").replace("Scene 2", "* Scene 2")
        out = vis._parse_worker_additions("6", reply)
        self.assertIsNotNone(out)
        self.assertIn("\nScene 1 (0.0–2.0s): cards rise (`center-outward-expansion`).", out)
        self.assertIn("\nScene 2 (2.0–6.0s): grid locks.", out)
        self.assertNotIn("- Scene", out)
        self.assertNotIn("done OK", out)

    def test_bold_and_indented_scene_lines_normalized(self):
        reply = (
            "  done OK 6\n"
            "  - focal: f\n"
            "  - roles: r\n"
            "  - rules: gsap-effects\n"
            "**Scene 1 (0.0–2.0s):** a\n"
            "  Scene 2 (2.0–6.0s): b\n"
        )
        out = vis._parse_worker_additions("6", reply)
        self.assertEqual(
            out, "- focal: f\n- roles: r\n- rules: gsap-effects\n"
                 "Scene 1 (0.0–2.0s): a\nScene 2 (2.0–6.0s): b")

    def test_diagnose_reasons(self):
        self.assertIsNone(vis._diagnose_worker_reply("6", _GOOD_REPLY))
        self.assertEqual(vis._diagnose_worker_reply("6", ""), "empty reply")
        no_scene = "\n".join(l for l in _GOOD_REPLY.split("\n") if "Scene" not in l)
        self.assertIn("Scene", vis._diagnose_worker_reply("6", no_scene))
        no_roles = _GOOD_REPLY.replace("- roles:", "roles:")
        self.assertIn("`- roles:`", vis._diagnose_worker_reply("6", no_roles))
        self.assertIn("missing `done OK 6`",
                      vis._diagnose_worker_reply("6", _GOOD_REPLY.replace("done OK 6", "")))
        self.assertIn("does not match frame 7", vis._diagnose_worker_reply("7", _GOOD_REPLY))

    def test_reply_without_rules_rejected(self):
        no_rules = "\n".join(l for l in _GOOD_REPLY.split("\n") if "- rules:" not in l)
        self.assertIn("`- rules:", vis._diagnose_worker_reply("6", no_rules))
        self.assertIsNone(vis._parse_worker_additions("6", no_rules))
        empty = _GOOD_REPLY.replace(
            "- rules: center-outward-expansion, spring-pop-entrance", "- rules:")
        self.assertIn("`- rules:", vis._diagnose_worker_reply("6", empty))
        self.assertIsNone(vis._parse_worker_additions("6", empty))

    def test_reply_with_only_unknown_rules_rejected(self):
        bad = _GOOD_REPLY.replace(
            "center-outward-expansion, spring-pop-entrance", "pop-stagger, hard-cut")
        reason = vis._diagnose_worker_reply("6", bad)
        self.assertIn("no known rule id", reason)
        self.assertIn("pop-stagger", reason)
        self.assertIsNone(vis._parse_worker_additions("6", bad))
        self.assertIn("no known rule id", vis._dispatch_note(0, bad, "6"))

    def test_rules_line_canonicalized_and_unknown_dropped(self):
        reply = _GOOD_REPLY.replace(
            "- rules: center-outward-expansion, spring-pop-entrance",
            "- rules: `spring-pop-entrance`, cut-the-curve spring-pop-entrance, gsap-effects")
        out = vis._parse_worker_additions("6", reply)
        self.assertIn("\n- rules: spring-pop-entrance, gsap-effects\n", out)
        self.assertNotIn("cut-the-curve", out)


class RuleCatalogTest(unittest.TestCase):
    def test_rule_catalog_matches_rules_dir(self):
        cat = vis._rule_catalog()
        self.assertIn("discrete-text-sequence", cat)
        self.assertIn("context-sensitive-cursor", cat)
        self.assertNotIn("typewriter-reveal", cat)   # a blueprint, not a rule

    def test_blueprint_rule_ids_from_full_rule_mapping(self):
        ids = vis._blueprint_rule_ids("typewriter-reveal")
        self.assertEqual(ids[:2], ["context-sensitive-cursor", "discrete-text-sequence"])
        self.assertIn("svg-path-draw", ids)
        self.assertNotIn("techniques", ids)          # not an on-disk rule
        self.assertTrue(set(ids) <= vis._rule_catalog())
        self.assertEqual(vis._blueprint_rule_ids("compose"), [])
        self.assertEqual(vis._blueprint_rule_ids("no-such-blueprint"), [])

    def test_every_blueprint_yields_candidates(self):
        for bp in vis._blueprint_catalog():
            self.assertTrue(vis._blueprint_rule_ids(bp), bp)

    def test_dispatch_note_carries_reason_and_tail(self):
        bad = _GOOD_REPLY.replace("Scene", "Shot")
        note = vis._dispatch_note(0, bad, "6")
        self.assertIn("missing `Scene N", note)
        self.assertIn("last words:", note)
        self.assertIn("grid locks.", note)
        self.assertIn("timeout", vis._dispatch_note(124, "", "6"))
        self.assertIn("rc=1", vis._dispatch_note(1, "", "6"))


class SceneWindowTest(unittest.TestCase):
    def test_6b01_f3_overlap_clamped(self):
        text = ("- focal: f\n"
                "Scene 1 (0.0–1.8s): a\n"
                "Scene 2 (1.6–6.8s): b\n"
                "Scene 3 (6.6–9.0s): c\n")
        out, fixes = vis._normalize_scene_windows(text, 9.0)
        self.assertIn("Scene 1 (0.0–1.8s): a", out)
        self.assertIn("Scene 2 (1.8–6.8s): b", out)
        self.assertIn("Scene 3 (6.8–9.0s): c", out)
        self.assertEqual(len(fixes), 2)
        self.assertTrue(out.startswith("- focal: f\n"))

    def test_first_start_pulled_to_zero_and_coverage_extended(self):
        text = "Scene 1 (0.3–2s): a\nScene 2 (2–4s): b\n"
        out, fixes = vis._normalize_scene_windows(text, 6.153)
        self.assertIn("Scene 1 (0.0–2.0s): a", out)
        self.assertIn("Scene 2 (2.0–6.153s): b", out)
        self.assertEqual(len(fixes), 2)

    def test_overrun_clamped_to_duration_but_good_coverage_untouched(self):
        out, fixes = vis._normalize_scene_windows(
            "Scene 1 (0.0–4.0s): a\nScene 2 (4.0–9.0s): b\n", 8.208)
        self.assertIn("Scene 2 (4.0–8.208s)", out)
        self.assertEqual(len(fixes), 1)
        text = "Scene 1 (0.0–4.0s): a\nScene 2 (4.0–7.6s): b\n"
        self.assertEqual(vis._normalize_scene_windows(text, 8.0), (text, []))

    def test_clean_windows_unchanged_and_no_duration_ok(self):
        text = "Scene 1 (0–2): a\nScene 2 (2–5): b\n"
        self.assertEqual(vis._normalize_scene_windows(text, None), (text, []))
        self.assertEqual(vis._normalize_scene_windows("no scenes", 5.0), ("no scenes", []))

    def test_inverting_overlap_left_for_gate(self):
        text = "Scene 1 (0.0–3.0s): a\nScene 2 (2.0–2.5s): b\n"
        out, fixes = vis._normalize_scene_windows(text, None)
        self.assertEqual(out, text)
        self.assertEqual(fixes, [])

    def test_gate_unparseable_window_canonicalized(self):
        out, fixes = vis._normalize_scene_windows(
            "Scene 1 (0.0 s – 2.0 s): a\nScene 2 (2.0–5.0s): b\n", 5.0)
        self.assertIn("Scene 1 (0.0–2.0s): a", out)
        self.assertIn("window format canonicalized", fixes)

    def test_frame_duration(self):
        self.assertEqual(vis._frame_duration("- duration: 6.153s\n"), 6.153)
        self.assertIsNone(vis._frame_duration("- scene: x"))


_BLOCKS = {
    "1": ("## Frame 1 — The hunch and the panic\n\n"
          "- scene: Cream canvas. Two kinetic words swap in place.\n"
          '- voiceover: "Most investors buy on a hunch. Neither ends well."\n'
          "- duration: 6.153s\n- transition_in: cut\n- beat: recognition + concern\n"),
    "2": ("## Frame 2 — Check one\n\n"
          "- scene: The checklist card; row one highlights.\n"
          '- voiceover: "First, understand it."\n'
          "- duration: 8.2s\n- transition_in: push-slide RIGHT\n- beat: comprehension\n"),
    "3": ("## Frame 3 — Check two\n\n"
          "- scene: Same checklist card; row two checks off.\n"
          '- voiceover: "Second, check the engine."\n'
          "- duration: 7.0s\n- transition_in: push-slide RIGHT\n- beat: momentum\n"),
}


class PromptShapeTest(unittest.TestCase):
    def test_no_slash_skill_in_driver_prompts(self):
        prompts = [
            vis._build_design_prompt("---\n---", ["1"], "", frozenset({"a"})),
            vis._build_worker_prompt("1", "block", "design", "bp"),
            vis._build_assignment_rewrite_prompt("d", [("1", "x")], ["1"], frozenset({"a"})),
        ]
        for p in prompts:
            self.assertNotIn("/aiflow-build-frame-visual", p)
            self.assertFalse(p.lstrip().startswith("/"))
        for p in prompts[:2]:
            self.assertIn("aiflow-build-frame-visual contract", p)

    def test_worker_prompt_shared_prefix_order_and_thinking_hint(self):
        p = vis._build_worker_prompt("3", "FRAME BLOCK", "DESIGN TEXT", "BP BODY")
        i_design = p.index("[DESIGN CONTRACT]")
        i_bp = p.index("[SELECTED BLUEPRINT")
        i_ctx = p.index("[CONTEXT")
        self.assertLess(i_design, i_bp)
        self.assertLess(i_bp, i_ctx)
        self.assertLess(p.index("DESIGN TEXT"), p.index("FRAME BLOCK"))
        self.assertIn("do NOT draft the full answer", p)
        # everything before the design contract is frame-agnostic (cacheable)
        self.assertEqual(
            p[:i_design],
            vis._build_worker_prompt("9", "other", "DESIGN TEXT", "")[:i_design])
        self.assertIn("`done OK 3`", p[i_ctx:])

    def test_design_prompt_has_frame_summaries_and_locked_transitions(self):
        order = ["1", "2", "3"]
        p = vis._build_design_prompt(
            "---\n---", order, "", frozenset({"a"}), vis._frame_summaries(_BLOCKS, order))
        self.assertIn("F1 — The hunch and the panic", p)
        self.assertIn("beat: recognition + concern", p)
        self.assertIn("6.153s", p)
        self.assertIn("transition_in: push-slide RIGHT", p)
        self.assertIn("scene: Cream canvas. Two kinetic words swap in place.", p)
        self.assertIn('vo: "Most investors buy on a hunch."', p)
        self.assertNotIn("Neither ends well", p)        # only the first sentence
        self.assertIn("**Locked transitions**", p)
        self.assertIn("**Shared stage**", p)
        self.assertIn("COMPLETE numbered list of short slot labels", p)
        self.assertIn("NEVER design, mention, or place an in-frame caption band", p)
        self.assertIn("light/warm candies", p)
        self.assertIn("F2→F3", p)
        self.assertIn("parser-anchor", p)

    def test_shared_stage_pairs(self):
        sums = vis._frame_summaries(_BLOCKS, ["1", "2", "3"])
        pairs = vis._shared_stage_pairs(sums)
        self.assertEqual([(a, b) for a, b, _ in pairs], [("2", "3")])
        overlap = [
            {"num": "1", "scene": "white checklist card rows highlight", "duration": "7s"},
            {"num": "2", "scene": "white checklist card rows strike", "duration": "7.2s"},
        ]
        self.assertEqual(len(vis._shared_stage_pairs(overlap)), 1)


class BlueprintTruncateTest(unittest.TestCase):
    def test_short_body_untouched(self):
        self.assertEqual(vis._truncate_blueprint("# t\n\n**intent**: x", 5000),
                         "# t\n\n**intent**: x")

    def test_long_body_keeps_signature_sections(self):
        body = (
            "# demo — Demo\n\n"
            "**intent**: the signature move.\n\n"
            "**roles served**\n\n" + "- role line\n" * 400 + "\n"
            "**duration**: 3–5s\n\n"
            "**shot structure**\n\n```\n" + "Scene line\n" * 600 + "```\n\n"
            "**motion vocabulary**: rise, settle\n\n"
            "**rule mapping**\n\n" + "- map\n" * 300
        )
        out = vis._truncate_blueprint(body, 3000)
        self.assertLessEqual(len(out), 3300)
        self.assertTrue(out.startswith("# demo"))
        for kept in ("**intent**", "**duration**", "**shot structure**", "**motion vocabulary**"):
            self.assertIn(kept, out)
        self.assertNotIn("**roles served**", out)
        self.assertNotIn("**rule mapping**", out)
        self.assertIn("(truncated", out)
        self.assertEqual(out.count("```") % 2, 0)

    def test_real_long_blueprint_capped(self):
        out = vis._blueprint_body("kinetic-type-beats")
        self.assertLessEqual(len(out), vis.DEFAULT_BLUEPRINT_MAX_CHARS + 300)
        self.assertIn("**shot structure**", out)
        self.assertIn("(truncated", out)


class DriverFanOutTest(unittest.TestCase):
    """Phase B end-to-end with Claude + gate mocked."""

    def setUp(self):
        import os as os_
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="visual-driver-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        # Existing fan-out fixtures omit ledger tables; pin LEDGER=0 here and
        # verify default-ON ledger in a dedicated test below.
        ledger_patch = mock.patch.dict(os_.environ, {vis.VISUAL_LEDGER_ENV: "0"})
        ledger_patch.start()
        self.addCleanup(ledger_patch.stop)
        self.board = "---\nformat: 1920x1080\n---\n\n" + "\n".join(
            _BLOCKS[k] for k in ("1", "2"))
        with open(os_.path.join(self.tmp, "STORYBOARD.md"), "w", encoding="utf-8") as f:
            f.write(self.board)
        with open(os_.path.join(self.tmp, "frame.md"), "w", encoding="utf-8") as f:
            f.write("---\ncolors:\n  cream: \"#F5F5F0\"\n---\n")
        with open(os_.path.join(self.tmp, "hyperframes.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        # stale fragment from an earlier run must not leak into the merge
        os_.makedirs(os_.path.join(self.tmp, ".hyperframes", "visual"))
        with open(os_.path.join(self.tmp, ".hyperframes", "visual", "F1.md"), "w") as f:
            f.write("STALE")

    def test_failed_frame_redispatched_without_waiting_for_others(self):
        import os
        import threading
        f2_retry_started = threading.Event()
        calls = []
        lock = threading.Lock()

        def fake_result(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            with lock:
                calls.append((label, prompt, kw))
            if label == "design":
                return 0, ("**Motion grammar.** calm\n\n## Blueprint assignment\n"
                           "F1 = titlecard-reveal (Adapt)\nF2 = compose (Adapt)\n")
            if label == "F1":
                # A whole-round barrier would never start F2's retry while F1 runs.
                self.assertTrue(f2_retry_started.wait(5), "F2 retry waited for F1")
                return 0, ("done OK 1\n- focal: f\n- roles: r\n- rules: discrete-text-sequence\n"
                           "Scene 1 (0.0–2.0s): a\nScene 2 (1.8–5.0s): b\n")
            if "[RE-DISPATCH]" in prompt:
                f2_retry_started.set()
                return 0, ("done OK 2\n- focal: g\n- roles: s\n- rules: gsap-effects\n"
                           "- Scene 1 (0.0–3.0s): c\n- Scene 2 (3.0–8.2s): d\n")
            return 0, "done OK 2\n- focal: g\n- roles: s\nShot 1: no scene lines"

        gate = mock.Mock(returncode=0, stdout="", stderr="")
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_result), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1", "HTMLVIDEO_PREFIX_WARMUP_SECONDS": "0"}):
            vis.build_frame_visual(self.tmp, log)

        design_kw = next(kw for label, _, kw in calls if label == "design")
        self.assertEqual(design_kw["skill"], "visual_design")
        self.assertEqual(design_kw["thinking_default"], str(vis.DEFAULT_DESIGN_THINKING_TOKENS))
        self.assertTrue(design_kw.get("trunc_kill"))
        retry_prompt = next(p for label, p, _ in calls
                            if label == "F2" and "[RE-DISPATCH]" in p)
        self.assertIn("missing `Scene N", retry_prompt)
        self.assertIn("no scene lines", retry_prompt)
        f1_prompt = next(p for label, p, _ in calls if label == "F1")
        self.assertIn("Candidate rule ids", f1_prompt)      # titlecard-reveal mapping
        f2_prompt = next(p for label, p, _ in calls if label == "F2")
        self.assertNotIn("Candidate rule ids", f2_prompt)   # compose

        vdir = os.path.join(self.tmp, ".hyperframes", "visual")
        with open(os.path.join(vdir, "F1.md"), encoding="utf-8") as f:
            f1 = f.read()
        self.assertIn("Scene 2 (2.0–6.153s): b", f1)   # overlap + coverage fixed
        self.assertNotIn("STALE", f1)
        with open(os.path.join(vdir, "F2.md"), encoding="utf-8") as f:
            self.assertIn("\nScene 1 (0.0–3.0s): c", f.read())
        self.assertTrue(os.path.isfile(os.path.join(vdir, "_design.md")))

        with open(os.path.join(self.tmp, "STORYBOARD.md"), encoding="utf-8") as f:
            merged = f.read()
        for k in ("1", "2"):
            self.assertIn(_BLOCKS[k].strip(), merged)   # narrative byte-exact
        self.assertIn("## Video direction", merged)
        self.assertNotIn("- Scene", merged)
        self.assertIn("- rules: discrete-text-sequence", merged)
        self.assertIn("- rules: gsap-effects", merged)

    def test_worker_context_drops_conflicting_outline_blueprints(self):
        import os
        b1 = _BLOCKS["1"] + "- blueprint: titlecard-reveal — one restrained reveal\n"
        b2 = _BLOCKS["2"] + "- blueprint: kinetic-type-beats — words swap\n"
        with open(os.path.join(self.tmp, "STORYBOARD.md"), "w", encoding="utf-8") as f:
            f.write("---\nformat: 1920x1080\n---\n\n" + b1 + "\n" + b2)
        prompts = {}

        def fake_result(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            if label == "design":
                return 0, ("## Blueprint assignment\nF1 = grid-card-assemble (Adapt)\n"
                           "F2 = kinetic-type-beats (Reproduce)\n")
            prompts[label] = prompt
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b")

        gate = mock.Mock(returncode=0, stdout="", stderr="")
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_result), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}), \
                self.assertLogs("test.visual.driver", level="INFO") as cm:
            vis.build_frame_visual(self.tmp, log)

        ctx1 = prompts["F1"].split("[CONTEXT", 1)[1]
        self.assertNotIn("titlecard-reveal", ctx1)
        self.assertIn("- beat: recognition + concern", ctx1)
        self.assertIn("`- blueprint: grid-card-assemble (Adapt)`", ctx1)
        ctx2 = prompts["F2"].split("[CONTEXT", 1)[1]
        self.assertIn("- blueprint: kinetic-type-beats — words swap", ctx2)   # same id kept
        self.assertIn("`- blueprint: kinetic-type-beats (Reproduce)`", ctx2)
        self.assertTrue(any("F1 CONTEXT: dropped outline blueprint(s) titlecard-reveal"
                            in m for m in cm.output))
        self.assertFalse(any("F2 CONTEXT" in m for m in cm.output))
        with open(os.path.join(self.tmp, "STORYBOARD.md"), encoding="utf-8") as f:
            merged = f.read()
        self.assertIn(_BLOCKS["1"].strip(), merged)   # narrative fields byte-exact
        # workers emitted no blueprint line → assignment injected, outline leftovers gone
        _, blocks, _ = vis._split_storyboard(merged)
        self.assertEqual(_blueprint_lines(blocks["1"]), ["- blueprint: grid-card-assemble (Adapt)"])
        self.assertEqual(_blueprint_lines(blocks["2"]),
                         ["- blueprint: kinetic-type-beats (Reproduce)"])
        self.assertNotIn("titlecard-reveal", blocks["1"])

    def test_merge_keeps_one_authoritative_blueprint_per_frame(self):
        import os
        b1 = _BLOCKS["1"] + "- blueprint: titlecard-reveal — one restrained reveal\n"
        b2 = (_BLOCKS["2"] + "- blueprint: kinetic-type-beats — words swap\n"
              "- blueprint: Adapt\n")
        with open(os.path.join(self.tmp, "STORYBOARD.md"), "w", encoding="utf-8") as f:
            f.write("---\nformat: 1920x1080\n---\n\n" + b1 + "\n" + b2)

        def fake_result(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            if label == "design":
                return 0, ("## Blueprint assignment\nF1 = grid-card-assemble (Adapt)\n"
                           "F2 = kinetic-type-beats (Reproduce)\n")
            bp = ("- blueprint: grid-card-assemble (Adapt)\n" if label == "F1"
                  else "- blueprint: titlecard-reveal (Adapt)\n")   # F2 worker went off-assignment
            return 0, (f"done OK {label[1:]}\n{bp}- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b")

        gate = mock.Mock(returncode=0, stdout="", stderr="")
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_result), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}), \
                self.assertLogs("test.visual.driver", level="INFO") as cm:
            vis.build_frame_visual(self.tmp, log)

        with open(os.path.join(self.tmp, "STORYBOARD.md"), encoding="utf-8") as f:
            merged = f.read()
        _, blocks, _ = vis._split_storyboard(merged)
        self.assertEqual(_blueprint_lines(blocks["1"]),
                         ["- blueprint: grid-card-assemble (Adapt)"])
        self.assertEqual(_blueprint_lines(blocks["2"]),
                         ["- blueprint: kinetic-type-beats (Reproduce)"])
        # the surviving line sits in the worker additions (before `- focal:`)
        self.assertLess(blocks["1"].index("- blueprint:"), blocks["1"].index("- focal: f"))
        self.assertGreater(blocks["1"].index("- blueprint:"), blocks["1"].index("- beat:"))
        self.assertNotIn("titlecard-reveal", merged.split("## Frame 1", 1)[1])
        self.assertIn(_BLOCKS["2"].strip(), merged)
        self.assertTrue(any("F1 merge: blueprint normalized" in m for m in cm.output))

    def test_repair_round_cannot_restore_stale_blueprint(self):
        import os

        def fake_result(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            if label == "design":
                return 0, ("## Blueprint assignment\nF1 = grid-card-assemble (Adapt)\n"
                           "F2 = compose\n")
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b")

        path = os.path.join(self.tmp, "STORYBOARD.md")

        def fake_repair(project_dir, prompt, logger, **kw):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            text = text.replace("## Frame 2", "- blueprint: titlecard-reveal (Adapt)\n\n## Frame 2")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return 0

        gates = iter([mock.Mock(returncode=1, stdout="bad", stderr=""),
                      mock.Mock(returncode=0, stdout="", stderr="")])
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_result), \
                mock.patch.object(vis, "run_claude", side_effect=fake_repair), \
                mock.patch.object(vis.subprocess, "run", side_effect=lambda *a, **k: next(gates)), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}), \
                self.assertLogs("test.visual.driver", level="WARNING") as cm:
            vis.build_frame_visual(self.tmp, log)

        with open(path, encoding="utf-8") as f:
            _, blocks, _ = vis._split_storyboard(f.read())
        self.assertEqual(_blueprint_lines(blocks["1"]),
                         ["- blueprint: grid-card-assemble (Adapt)"])
        self.assertEqual(_blueprint_lines(blocks["2"]), ["- blueprint: compose"])
        self.assertTrue(any("re-normalized F1:" in m and "titlecard-reveal" in m
                            for m in cm.output))

    def test_frame_without_rules_is_retried_then_fails(self):
        import os
        seen = []

        def fake_result(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            if label == "design":
                return 0, ("## Blueprint assignment\nF1 = compose (Adapt)\n"
                           "F2 = compose (Adapt)\n")
            seen.append((label, prompt))
            rules = "- rules: gsap-effects\n" if label == "F1" else ""
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n{rules}"
                       "Scene 1 (0–3): types on\nScene 2 (3–6.2): b")

        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_result), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}):
            with self.assertRaisesRegex(RuntimeError, r"\['2'\]"):
                vis.build_frame_visual(self.tmp, log)
        f2 = [p for label, p in seen if label == "F2"]
        self.assertEqual(len(f2), 1 + vis.MAX_RETRY_ROUNDS)
        self.assertIn("`- rules:", f2[-1].split("[RE-DISPATCH]", 1)[1])

    def test_sanitize_low_contrast_prose(self):
        out, n = vis._sanitize_low_contrast_prose(
            "violet text on a sky pill fill; yellow word on cream; coral word on the white pill"
        )
        self.assertEqual(n, 2)
        self.assertIn("ink text on a sky pill fill", out)
        self.assertIn("ink word on cream", out)
        self.assertIn("coral word on the white pill", out)

    def test_frame_failing_twice_raises(self):
        import os

        def fake_result(project_dir, prompt, logger, **kw):
            if kw.get("label") == "design":
                return 0, ("## Blueprint assignment\nF1 = compose (Adapt)\n"
                           "F2 = compose (Adapt)\n")
            if kw.get("label") == "F1":
                return 0, ("done OK 1\n- focal: f\n- roles: r\n- rules: gsap-effects\n"
                           "Scene 1 (0–3): a\nScene 2 (3–6.2): b")
            return 0, "nothing useful"

        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_result), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}):
            with self.assertRaisesRegex(RuntimeError, r"\['2'\]"):
                vis.build_frame_visual(self.tmp, log)

    def test_legacy_rollback_uses_legacy_profile(self):
        import os
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude", return_value=1) as rc, \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "0"}):
            with self.assertRaises(RuntimeError):
                vis.build_frame_visual(self.tmp, log)
        self.assertEqual(rc.call_args.kwargs["skill"], "visual_legacy")
        self.assertTrue(rc.call_args.args[1].startswith("/aiflow-build-frame-visual"))

    def test_design_bold_blueprint_heading_and_missing_assignment_repair(self):
        import os
        # 1) Bold **Blueprint assignment** (6b06 failure) succeeds with ZERO extra calls
        calls = []
        def fake_bold(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            calls.append(label)
            if label == "design":
                return 0, ("**Motion grammar.** snap\n\n**Blueprint assignment**\n"
                           "F1 = titlecard-reveal (Adapt)\nF2 = compose (Adapt)\n")
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b")
        gate = mock.Mock(returncode=0, stdout="", stderr="")
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_bold), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}):
            vis.build_frame_visual(self.tmp, log)
        self.assertNotIn("design-bp-fix", calls)
        with open(os.path.join(self.tmp, ".hyperframes", "visual", "_design.md"), encoding="utf-8") as f:
            self.assertIn("\n## Blueprint assignment\n", f.read())

        # 2) Completely missing assignment triggers targeted repair instead of hard crash
        with open(os.path.join(self.tmp, "STORYBOARD.md"), "w", encoding="utf-8") as f:
            f.write(self.board)
        calls2 = []
        def fake_missing(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            calls2.append((label, kw.get("skill")))
            if label == "design":
                return 0, "**Motion grammar.** snap only — forgot assignment"
            if label == "design-bp-fix":
                return 0, "## Blueprint assignment\nF1 = titlecard-reveal (Adapt)\nF2 = compose\n"
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_missing), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}):
            vis.build_frame_visual(self.tmp, log)
        self.assertIn(("design-bp-fix", "visual_design"), calls2)

    def test_ledger_is_default_off_and_opt_in_in_driver(self):
        import os
        os.environ.pop(vis.VISUAL_LEDGER_ENV, None)
        prompts_off = []
        def fake_off(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            if label == "design":
                return 0, ("## Blueprint assignment\nF1 = compose\nF2 = compose\n")
            prompts_off.append(prompt)
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b\n")
        gate = mock.Mock(returncode=0, stdout="", stderr="")
        log = logging.getLogger("test.visual.driver")
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_off), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1"}):
            vis.build_frame_visual(self.tmp, log)
        self.assertTrue(all("## Layout ledger" not in p for p in prompts_off))
        self.assertTrue(all("NEVER redraw a second copy of the numbered slot" in p for p in prompts_off))

        # Reset STORYBOARD and verify =1 opt-in enables ledger tables
        with open(os.path.join(self.tmp, "STORYBOARD.md"), "w", encoding="utf-8") as f:
            f.write(self.board)
        prompts_on = []
        def fake_on(project_dir, prompt, logger, **kw):
            label = kw.get("label")
            if label == "design":
                return 0, ("## Blueprint assignment\nF1 = compose\nF2 = compose\n")
            prompts_on.append(prompt)
            return 0, (f"done OK {label[1:]}\n- focal: f\n- roles: r\n"
                       "- rules: gsap-effects\nScene 1 (0–3): a\nScene 2 (3–6.2): b\n"
                       + _GATE_LEDGER)
        with mock.patch.object(vis, "run_claude_result", side_effect=fake_on), \
                mock.patch.object(vis.subprocess, "run", return_value=gate), \
                mock.patch.dict(os.environ, {vis.VISUAL_DRIVER_ENV: "1", vis.VISUAL_LEDGER_ENV: "1"}):
            vis.build_frame_visual(self.tmp, log)
        self.assertTrue(all("## Layout ledger" in p for p in prompts_on))


_GATE = "/app/scripts/storyboard-gate.mjs"


def _gate_frame(rules_line: str, scenes: bool = True) -> str:
    body = (
        "## Frame 1 — Hook\n\n"
        "- duration: 4.0s\n- transition_in: cut\n- status: outline\n"
        "- src: compositions/frames/frame-01.html\n"
    )
    if scenes:
        body += (
            "- focal: hero word\n- roles: word = foreground\n" + rules_line
            + "Scene 1 (0.0–2.0s): the word types on (`discrete-text-sequence`).\n"
            "Scene 2 (2.0–4.0s): holds.\n"
        )
    return body


_GATE_LEDGER = (
    "\n## Layout ledger\n\n"
    "| element | role | place | size | color | order |\n"
    "|---------|------|-------|------|-------|-------|\n"
    "| word | focal | center | 8cqw | cream | 1 |\n\n"
    "## Motion ledger\n\n"
    "| scene | element | from → to | ease | dur |\n"
    "|-------|---------|-----------|------|-----|\n"
    "| scene1 | word | opacity 0 → 1 | power2.inOut | 1s |\n"
)


@unittest.skipUnless(__import__("shutil").which("node"), "node not installed")
class StoryboardGateRulesTest(unittest.TestCase):
    def _run(self, frame: str, direction: bool = True, env: dict | None = None):
        import os
        import subprocess
        import tempfile
        d = tempfile.mkdtemp(prefix="sb-gate-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        text = "---\nformat: 1920x1080\n---\n\n"
        if direction:
            text += "## Video direction\n\ncalm\n\n"
        with open(os.path.join(d, "STORYBOARD.md"), "w", encoding="utf-8") as f:
            f.write(text + frame)
        full_env = None
        if env is not None:
            full_env = {**os.environ, **env}
        p = subprocess.run(["node", _GATE, "--videodir", d],
                           capture_output=True, text=True, timeout=60, env=full_env)
        return p.returncode, p.stdout + p.stderr

    def test_valid_rules_pass(self):
        rc, out = self._run(
            _gate_frame("- rules: discrete-text-sequence, context-sensitive-cursor\n") + _GATE_LEDGER
        )
        self.assertEqual(rc, 0, out)

    def test_missing_rules_fails_with_hint(self):
        rc, out = self._run(_gate_frame(""))
        self.assertEqual(rc, 1, out)
        self.assertIn("✗ Frame 1: missing non-empty `- rules:`", out)
        self.assertIn("Scene text already cites: discrete-text-sequence", out)

    def test_empty_rules_fails(self):
        rc, out = self._run(_gate_frame("- rules:\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("missing non-empty `- rules:`", out)

    def test_unknown_rule_id_fails(self):
        rc, out = self._run(_gate_frame("- rules: discrete-text-sequence, cut-the-curve\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("unknown rule id(s): cut-the-curve", out)

    def test_pre_design_outline_not_flagged_for_rules(self):
        _rc, out = self._run(_gate_frame("", scenes=False), direction=False)
        self.assertIn("pre-design outline", out)
        self.assertNotIn("rules:", out)

    def test_ledger_on_with_ledger_passes(self):
        rc, out = self._run(
            _gate_frame("- rules: discrete-text-sequence\n") + _GATE_LEDGER,
            env={"HTMLVIDEO_FRAME_VISUAL_LEDGER": "1"})
        self.assertEqual(rc, 0, out)

    def test_ledger_on_missing_ledger_fails(self):
        rc, out = self._run(
            _gate_frame("- rules: discrete-text-sequence\n"),
            env={"HTMLVIDEO_FRAME_VISUAL_LEDGER": "1"})
        self.assertEqual(rc, 1, out)
        self.assertIn("`## Layout ledger`", out)

    def test_ledger_off_by_default_does_not_require(self):
        rc, out = self._run(_gate_frame("- rules: discrete-text-sequence\n"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Layout ledger", out)

    def test_audio_sync_durations_rescales_motion_ledger(self):
        import json
        import os
        import subprocess
        import tempfile
        d = tempfile.mkdtemp(prefix="sb-sync-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        sb_path = os.path.join(d, "STORYBOARD.md")
        meta_path = os.path.join(d, "audio_meta.json")
        with open(sb_path, "w", encoding="utf-8") as f:
            f.write(_gate_frame("- rules: discrete-text-sequence\n") + _GATE_LEDGER)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"voices": [{"frame": 1, "duration_s": 8.0}]}, f)
        p = subprocess.run(
            ["node", "/app/scripts/audio.mjs", "sync-durations", "--videodir", d],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(sb_path, encoding="utf-8") as f:
            synced = f.read()
        self.assertIn("Scene 1 (0.0–4.0s)", synced)
        self.assertIn("Scene 2 (4.0–8s)", synced)
        self.assertIn("| scene1 | word | opacity 0 → 1 | power2.inOut | 2.0s |", synced)


_LEDGER_COLORS = frozenset({
    "cream", "ink", "outline", "white", "coral", "lime", "lavender",
    "sky", "violet", "yellow", "peach", "mint", "shadow",
})

_GOOD_LEDGER = (
    "## Layout ledger\n\n"
    "| element | role | place | size | color | order |\n"
    "|---------|------|-------|------|-------|-------|\n"
    "| word | focal | center x=50% y=42% | 8.5cqw | violet | 2 |\n"
    "| bg | background | full-bleed | — | cream | 0 |\n\n"
    "## Motion ledger\n\n"
    "| scene | element | from → to | ease | dur |\n"
    "|-------|---------|-----------|------|-----|\n"
    "| scene1 | word | opacity 0 → 1 | power2.inOut | 1.2s |\n"
)


class LedgerValidationTest(unittest.TestCase):
    def _good(self):
        return _GOOD_REPLY + "\n" + _GOOD_LEDGER

    def test_ledger_not_required_by_default(self):
        # Backward compat: without require_ledger the old reply still passes.
        self.assertIsNone(vis._diagnose_worker_reply("6", _GOOD_REPLY))

    def test_ledger_requested_requires_both_tables(self):
        self.assertIsNone(vis._diagnose_worker_reply(
            "6", self._good(), require_ledger=True, color_catalog=_LEDGER_COLORS))
        no_layout = self._good().replace("## Layout ledger", "## Layout table")
        self.assertIn("`## Layout ledger`", vis._diagnose_worker_reply(
            "6", no_layout, require_ledger=True, color_catalog=_LEDGER_COLORS))
        no_motion = self._good().split("## Motion ledger")[0]
        self.assertIn("`## Motion ledger`", vis._diagnose_worker_reply(
            "6", no_motion, require_ledger=True, color_catalog=_LEDGER_COLORS))

    def test_empty_ledger_tables_rejected(self):
        empty = _GOOD_REPLY + "\n## Layout ledger\n\n## Motion ledger\n"
        self.assertIn("data row", vis._diagnose_worker_reply(
            "6", empty, require_ledger=True, color_catalog=_LEDGER_COLORS))

    def test_unknown_ledger_color_rejected(self):
        bad = self._good().replace("| violet |", "| red-magic |")
        self.assertIn("palette role", vis._diagnose_worker_reply(
            "6", bad, require_ledger=True, color_catalog=_LEDGER_COLORS))

    def test_unknown_ledger_ease_rejected(self):
        bad = self._good().replace("power2.inOut", "wiggle.fast")
        self.assertIn("ease", vis._diagnose_worker_reply(
            "6", bad, require_ledger=True, color_catalog=_LEDGER_COLORS))

    def test_color_token_names_excludes_typography(self):
        self.assertEqual(set(vis._color_token_names(_TOKENS)), {"cream", "coral"})


class LedgerPromptTest(unittest.TestCase):
    def test_worker_prompt_emits_ledger_contract_when_requested(self):
        p = vis._build_worker_prompt(
            "7", "block", "design", require_ledger=True,
            color_catalog=frozenset({"cream", "ink", "violet"}))
        self.assertIn("## Layout ledger", p)
        self.assertIn("## Motion ledger", p)
        self.assertIn("Valid color roles: ", p)
        self.assertIn("cream", p)
        self.assertIn("from → to", p)

    def test_worker_prompt_omits_ledger_by_default(self):
        p = vis._build_worker_prompt("7", "block", "design")
        self.assertNotIn("## Layout ledger", p)
        self.assertNotIn("## Motion ledger", p)


class SharedStageAndHedgeOptimizationTest(unittest.TestCase):
    def test_shared_stage_pairs_ignores_canvas_only_same_phrase(self):
        summaries = [
            {"num": "1", "scene": "Flat sun-bleached cream canvas, one pill card.", "duration": "9s"},
            {"num": "2", "scene": "Same cream stage. Three empty outline pill cards slide into a row.", "duration": "8s"},
            {"num": "3", "scene": "Same three-gate stage. The first card fills with checked rows.", "duration": "8s"},
        ]
        pairs = vis._shared_stage_pairs(summaries)
        pair_ids = [(a, b) for a, b, _ in pairs]
        self.assertNotIn(("1", "2"), pair_ids)
        self.assertIn(("2", "3"), pair_ids)

    def test_design_prompt_and_stage_note_ban_recap_sweep_and_empty_closer(self):
        prompt = vis._build_design_prompt("preamble", ["1", "2", "3"], tokens=_TOKENS)
        self.assertIn("≤ 250 words total", prompt)
        self.assertIn("default directly to `top-stepper-center-card`", prompt)
        self.assertIn("ALWAYS use `[dim, dim, ...]`", prompt)
        self.assertIn("NEVER design an in-frame recap sweep", prompt)

        spec = (
            "## Shared stage spec\n"
            "layout: top-stepper-center-card\n"
            "title: The three gates\n"
            "slots: 01 Fundamentals | 02 Valuation | 03 Exit Plan\n"
            "F7: [done:lime, done:lime, done:lime]\n"
        )
        note = vis._frame_shared_stage_note(spec, "7")
        self.assertIn("recap sweep", note)
        self.assertIn("never fade `#stage-card` out to empty canvas", note)
        self.assertIn("do NOT mark any `.hf-stage-slot` as `done`", note)
        self.assertIn("NEVER dim or reset them", note)

        frame_prompt = vis._build_worker_prompt(
            "4", "block", "design", assigned_blueprint="kinetic-type-beats (Adapt)",
            rule_catalog=frozenset(["spring-pop-entrance"]),
        )
        self.assertIn("NEVER fabricate quantitative figures", frame_prompt)
        self.assertIn("Bind at most 1–2 micro-interactions", frame_prompt)

    def test_frame_html_hedge_defaults_and_hard_noops(self):
        from html_utility import frame_html as fh
        self.assertEqual(fh.DEFAULT_HEDGE_EFFORT, "low")
        self.assertEqual(fh.DEFAULT_MAX_HEDGES, 6)
        self.assertEqual(fh.HEDGE_TIMEOUT, 180)
        prompt = fh._worker_prompt(
            "01-hook", skeleton_mode=False, dispatch_text="# Dispatch: 01-hook\n", text_out=True
        )
        self.assertIn("NO manual `gsap.ticker` / `requestAnimationFrame`", prompt)
        self.assertIn("bounded per-effect `onUpdate` proxies", prompt)
        self.assertNotIn("SLIM_PRIMARY_ENV", dir(fh))
        self.assertNotIn("## Selected blueprint", prompt)
        self.assertIn("NO inline `<svg><feTurbulence>`", prompt)
        self.assertIn("NEVER set `white-space: nowrap` on multi-word sentences", prompt)
        self.assertIn("never ghost side-by-side", prompt)
        self.assertIn('<svg preserveAspectRatio="none">', prompt)

        # P1-4: _minimal_dispatch_text keeps compact snippets by default, strips full & blueprint
        snip_dispatch = (
            "Shared prefix\n# Dispatch: 01-hook\n\n"
            "## Selected blueprint\nLong blueprint prose\n\n"
            "## Rule index\n- rules/a.md\n\n"
            "## Rule recipes (snippets)\n```js\ntl.fromTo('.x', {y:10}, {y:0});\n```\n"
        )
        kept = fh._minimal_dispatch_text(snip_dispatch, keep_snippets=True)
        self.assertNotIn("Long blueprint prose", kept)
        self.assertIn("tl.fromTo('.x'", kept)
        stripped = fh._minimal_dispatch_text(snip_dispatch, keep_snippets=False)
        self.assertNotIn("tl.fromTo('.x'", stripped)
        self.assertIn("Omitted in minimal-motion fallback", stripped)

        full_dispatch = (
            "Shared prefix\n# Dispatch: 01-hook\n\n"
            "## Rule recipes (full)\nHuge full recipe text\n"
        )
        kept_full = fh._minimal_dispatch_text(full_dispatch, keep_snippets=True)
        self.assertNotIn("Huge full recipe text", kept_full)
        self.assertIn("Omitted in minimal-motion fallback", kept_full)


if __name__ == "__main__":
    unittest.main()