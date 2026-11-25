# -*- coding: utf-8 -*-
"""Unit tests for html_utility.verify (scan precheck + digest guard messaging)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from html_utility import verify as v
from html_utility import verify_overlap_autofix as ao


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


def _visual_scan(with_payload: bool = False) -> str:
    """Mechanical PASS teed above a pixels FAIL. Payload is a trap for auto-overlap."""
    body = (
        "══ verify-all [verify] ══\n"
        "── mechanical gate (verify-report) ──\n"
        "GATE: PASS ✓ (lint+check+structural+transitions)\n"
        "── actionable summary ──\n"
        "  No action. See known-failures.mjs\n"
        "── visual gate (snapshot + pixels) ──\n"
        "frame-03.png                         FAIL: collapse: narrow blob in corner\n"
        "frame-05.png                         FAIL: empty frame (0.01% content)\n"
        "SUMMARY: 2 snapshot(s), 2 FAIL finding(s)\n"
        "\nverify-all: MECHANICAL PASS / VISUAL FAIL (pixels)\n"
        "VISUAL_GATE: FAIL\n"
    )
    if with_payload:
        body += _payload_line(_ROWS_SAMPLE) + "\n"
    return body


def _mechanical_scan() -> str:
    return (
        "── actionable summary ──\n"
        "  1) Known failure #4 candidate — content_overlap\n\n"
        "GATE: FAIL ✗ (…, 2 error finding(s))\n"
        "verify-all: mechanical gate FAIL (report exit 1) — visual gate not opened.\n"
    )


class ScanFailureKindTest(unittest.TestCase):
    def test_gate_fail_without_visual_marker_is_mechanical(self):
        out = _mechanical_scan()
        self.assertNotIn("VISUAL_GATE:", out)
        self.assertEqual(v.classify_scan_failure(out), "mechanical")
        self.assertEqual(v.classify_scan_failure(_scan([])), "mechanical")

    def test_visual_gate_fail_is_visual_even_after_gate_pass(self):
        out = _visual_scan()
        self.assertEqual(v.classify_scan_failure(out), "visual")
        self.assertFalse(v.scan_looks_unactionable(out))

    def test_visual_gate_pass_is_not_a_failure_kind(self):
        self.assertIsNone(
            v.classify_scan_failure("GATE: PASS ✓\nVISUAL_GATE: PASS\n")
        )


class FixPlaybookTest(unittest.TestCase):
    def test_playbook_delegates_to_catalog_single_source(self):
        pb = v._FIX_PLAYBOOK
        self.assertIn("known-failures.mjs", pb)
        # Per-failure rules moved out of the playbook into the catalog; the
        # playbook must not keep a second copy (that drift was the bug).
        self.assertNotIn("designed_overlay", pb)
        self.assertNotIn("real_collision", pb)
        self.assertNotIn("ambiguous", pb)
        self.assertNotIn("data-layout-allow-overlap", pb)

    def test_partial_miss_marker_is_unactionable(self):
        out = (
            "  ✗ PARSER/INFRA: check JSON declares errorCount that parsed findings "
            "do not match — declared=8 parsed=1\n"
            "── actionable summary ──\n"
            "  1) Known failure #4 — content_overlap triage — designed_overlay×1\n"
            "GATE: FAIL ✗ (…, 1 error finding(s))\n"
        )
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
        p = mock.patch.object(v, "_run_prepare", return_value=(0, "verify-all: prepare done ✓\n"))
        self.prep = p.start(); self.addCleanup(p.stop)

    def test_unactionable_scan_raises_without_claude(self):
        scan = (
            "── actionable summary ──\n"
            "  #2 tell only. No action.\n\n"
            "GATE: FAIL ✗ (…, 0 error finding(s))\n"
        )
        with mock.patch.object(v, "_run_verify", return_value=(1, scan)), \
             mock.patch.object(v, "_run_fix_worker") as claude:
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
        with mock.patch.object(v, "_run_verify", side_effect=[
                 (1, scan),
                 (0, "GATE: PASS ✓\n"),
             ]), \
             mock.patch.object(v, "_run_fix_worker", return_value=0) as claude, \
             mock.patch.object(v, "_composition_html_digest", side_effect=["a", "b"]), \
             mock.patch.object(
                 v, "_validate_verify_outputs",
                 return_value=os.path.join(self.d, "snapshots", "contact-sheet.jpg"),
             ):
            # need a fake contact sheet path existence? validate is mocked
            out = v.verify_frame(self.d, self.logger)
        claude.assert_called_once()
        self.assertIn("contact-sheet", out)


_FRAME_05 = """<template>
<div id="root" data-composition-id="05-price" data-duration="5">
  <div class="clip f5-bg" data-start="0" data-duration="5">
    <div class="price-num">&#8212;</div>
    <div class="price-tag">Price You Pay</div>
    <div class="f5-stamp">FAIR</div>
    <div class="cap-sub">A great company</div>
    <img src="x.png" class="seal" />
  </div>
  <script>document.querySelector("div.price-num > span:nth-of-type(1)")</script>
</div>
</template>
"""

_FRAME_08 = """<template>
<div id="root" data-composition-id="08-stock">
  <div class="clip f8-bg" data-start="0">
    <div class="f8-ticker">
      <span>BUY TEST</span><span class="f8-dot">·</span>
      <span>SELL RULES</span><span class="f8-dot">·</span>
      <span>EXIT RULES</span>
    </div>
    <div class="f8-ticker f8-ticker-b" data-layout-allow-overlap="true"><span>X</span></div>
  </div>
</div>
</template>
"""


def _payload_line(rows: list[dict], **extra) -> str:
    body = {"version": 1, "designed_overlay": rows, "real_collision": [],
            "ambiguous": [], "other_check_errors": 0}
    body.update(extra)
    return ao.FINDINGS_JSON_PREFIX + json.dumps(body)


def _scan(rows: list[dict], actionable: str = "", **extra) -> str:
    return (
        "══ verify-all [verify] ══\n"
        "── structural gate ──\n"
        "  ✓ frame-selfcheck: 7/7 PASS\n"
        "── check ──\n"
        "  ✗ [Layout] content_overlap in compositions/frames/05-price.html — "
        "div.price-num inside div.price-tag\n"
        "  ⚠ [Contrast] contrast_aa_failure in compositions/frames/01.html — x\n"
        "── frame midpoints ──\n  AT: 1,2,3\n\n"
        "── actionable summary ──\n"
        + (actionable or "  1) Known failure #4 — content_overlap triage — designed_overlay×1\n")
        + "\nGATE: FAIL ✗ (lint ok=true, check exit=1, 1 error finding(s))\n"
        + _payload_line(rows, **extra) + "\n"
        "\nverify-all: report exit 1 — fix the findings above, then rerun the verify phase (prepare already ran).\n"
    )


def _safe(row: dict, token: str = "x", side: str = "selector") -> dict:
    return {**row, "reason": f'decorative token "{token}"', "autofix_ok": True,
            "mark_side": side}


def _weak(row: dict, token: str) -> dict:
    return {**row, "reason": f'weak decorative token "{token}"', "autofix_ok": False,
            "mark_side": None}


# Rows as findingsPayload emits them for the 6b02 shapes: strong tokens are
# auto-fixable on their own side; weak ones (tag / dot) stay with the model.
_ROWS_SAMPLE = [
    _safe({"file": "compositions/frames/05-price.html", "selector": "div.price-num",
           "container": "div.f5-stamp"}, "stamp", side="container"),
    _safe({"file": "compositions/frames/08-stock.html",
           "selector": "div.f8-ticker > span:nth-of-type(3)",
           "container": "span.f8-row-text"}, "ticker"),
    _weak({"file": "compositions/frames/05-price.html", "selector": "div.price-num",
           "container": "div.price-tag"}, "tag"),
    _weak({"file": "compositions/frames/08-stock.html", "selector": "span.f8-dot",
           "container": "span.f8-row-text"}, "dot"),
]

_VERIFY_CHECK_PARSE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "lib", "verify-check-parse.mjs",
)


def _js_findings_payload(errors: list[dict]) -> dict:
    """Run the real JS triage (``findingsPayload``) on CLI-shaped errors."""
    script = (
        f"import {{ findingsPayload }} from {json.dumps(_VERIFY_CHECK_PARSE)};"
        "const errs = JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify(findingsPayload(errs)));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(errors)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(proc.stdout)


class _ProjectMixin:
    def _make_project(self) -> str:
        d = tempfile.mkdtemp(prefix="verify-ao-")
        frames = os.path.join(d, "compositions", "frames")
        os.makedirs(frames)
        for name in ("hyperframes.json", "index.html", "STORYBOARD.md"):
            with open(os.path.join(d, name), "w") as f:
                f.write("x\n")
        for name, body in (("05-price.html", _FRAME_05), ("08-stock.html", _FRAME_08)):
            with open(os.path.join(frames, name), "w", encoding="utf-8") as f:
                f.write(body)
        return d

    def _read(self, d: str, name: str) -> str:
        with open(os.path.join(d, "compositions", "frames", name), encoding="utf-8") as f:
            return f.read()


class SelectorParseTest(unittest.TestCase):
    def test_compound_forms(self):
        c = ao.parse_selector("div.price-num")
        self.assertEqual((c[0].tag, c[0].classes), ("div", ["price-num"]))
        c = ao.parse_selector("#\\30 7-hero")
        self.assertEqual(c[0].id, "07-hero")
        c = ao.parse_selector('div[data-layout-name="card"]')
        self.assertEqual(c[0].attrs, {"data-layout-name": "card"})
        c = ao.parse_selector('[data-layout-name="hero card"]')
        self.assertEqual(c[0].attrs, {"data-layout-name": "hero card"})
        c = ao.parse_selector("div.f8-ticker > span:nth-of-type(5)")
        self.assertEqual(len(c), 2)
        self.assertEqual(c[1].nth_of_type, 5)
        self.assertFalse(c[1].anchorable)

    def test_unsupported_forms(self):
        for sel in ("", "div p", "a, b", "div:hover", "div > "):
            self.assertIsNone(ao.parse_selector(sel), sel)


class OverlapAutofixUnitTest(unittest.TestCase, _ProjectMixin):
    def setUp(self):
        self.logger = logging.getLogger("test.verify.ao")

    def test_marks_sample_targets(self):
        d = self._make_project()
        res = ao.autofix_designed_overlays(d, _scan(_ROWS_SAMPLE), self.logger)
        self.assertTrue(res.changed)
        self.assertEqual(res.handled, 2)
        self.assertEqual(len(res.unresolved), 2)  # weak tag / dot rows
        f5 = self._read(d, "05-price.html")
        self.assertIn('<div class="f5-stamp" data-layout-allow-overlap="true">', f5)
        self.assertIn('<div class="price-num">', f5)
        self.assertIn('<div class="price-tag">', f5)
        self.assertIn('<div class="cap-sub">', f5)
        self.assertIn('querySelector("div.price-num > span:nth-of-type(1)")', f5)
        f8 = self._read(d, "08-stock.html")
        self.assertIn('<div class="f8-ticker" data-layout-allow-overlap="true">', f8)
        # f8-dot is a weak row, but sits under the flagged .f8-ticker anyway
        self.assertIn('<span class="f8-dot">·</span>', f8)
        self.assertEqual(f8.count("data-layout-allow-overlap"), 2)
        self.assertEqual(res.nodes_marked, 2)

    def test_only_autofix_ok_rows_are_written(self):
        d = self._make_project()
        base = {"file": "compositions/frames/05-price.html", "selector": "div.price-num",
                "container": "div.price-tag", "mark_side": "selector"}
        rows = [
            {**base, "reason": "fixHint says intentional"},
            {**base, "reason": 'decorative token "x"', "autofix_ok": False},
            {**base, "reason": 'decorative token "x"', "autofix_ok": "true"},
            # Legacy rows without autofix_ok: a "safe-looking" reason no longer qualifies
            {**base, "reason": "dataAttributes mark it intentional"},
            {**base, "reason": 'decorative token "tag"'},
        ]
        before = self._read(d, "05-price.html")
        with self.assertLogs("test.verify.ao", level="INFO") as logs:
            res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(len(res.unresolved), 5)
        self.assertEqual(before, self._read(d, "05-price.html"))
        self.assertTrue(any("left for the model" in m for m in logs.output))

    def test_missing_mark_side_is_skipped(self):
        d = self._make_project()
        base = {"file": "compositions/frames/05-price.html", "selector": "div.price-num",
                "container": "div.f5-stamp", "reason": 'decorative token "stamp"',
                "autofix_ok": True}
        before = self._read(d, "05-price.html")
        with self.assertLogs("test.verify.ao", level="INFO") as logs:
            res = ao.autofix_designed_overlays(
                d, _scan([dict(base), {**base, "mark_side": "both"}]), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(len(res.unresolved), 2)
        self.assertEqual(before, self._read(d, "05-price.html"))
        self.assertTrue(any("mark_side=None" in m for m in logs.output))

    def test_mark_side_container_marks_only_container(self):
        d = self._make_project()
        row = _safe({"file": "compositions/frames/05-price.html",
                     "selector": "div.price-num", "container": "div.f5-stamp"},
                    "stamp", side="container")
        res = ao.autofix_designed_overlays(d, _scan([row]), self.logger)
        self.assertEqual(res.nodes_marked, 1)
        f5 = self._read(d, "05-price.html")
        self.assertIn('<div class="f5-stamp" data-layout-allow-overlap="true">', f5)
        self.assertIn('<div class="price-num">', f5)
        self.assertEqual(f5.count("data-layout-allow-overlap"), 1)

    def test_mark_side_not_found_does_not_fall_back(self):
        d = self._make_project()
        rows = [
            _safe({"file": "compositions/frames/05-price.html",
                   "selector": "#runtime-only", "container": "div.f5-stamp"}, "stamp"),
            _safe({"file": "compositions/frames/05-price.html",
                   "selector": "div.f5-stamp", "container": "div.nope"},
                  "stamp", side="container"),
            _safe({"file": "compositions/frames/05-price.html",
                   "selector": "div.f5-stamp", "container": ""}, side="container"),
        ]
        before = self._read(d, "05-price.html")
        res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(len(res.unresolved), 3)
        self.assertIn("mark_side=selector #runtime-only", res.unresolved[0])
        self.assertIn("mark_side=container has no selector", res.unresolved[2])
        self.assertEqual(before, self._read(d, "05-price.html"))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_triage_never_autofixes_caption_or_weak_tags(self):
        """End to end through the real JS triage: caption band and weak tokens
        must not be written; the strong ticker row still is."""
        d = self._make_project()
        f5, f8 = "compositions/frames/05-price.html", "compositions/frames/08-stock.html"
        errors = [
            {"code": "content_overlap", "file": f5, "selector": "div.cap-sub",
             "container": "#caption-word-32-0", "msg": ""},
            {"code": "content_overlap", "file": f5, "selector": "#caption-word-32-0",
             "container": "div.cap-sub", "msg": ""},
            {"code": "content_overlap", "file": f5, "selector": "div.f5-cap-pill",
             "container": "#caption-word-3-1", "msg": ""},
            {"code": "content_overlap", "file": f5, "selector": "div.price-num",
             "container": "div.price-tag", "msg": ""},
            {"code": "content_overlap", "file": f8, "selector": "span.f8-dot",
             "container": "div.f8-ticker", "msg": ""},
        ]
        payload = _js_findings_payload(errors)
        self.assertEqual(len(payload["real_collision"]), 3)
        line = ao.FINDINGS_JSON_PREFIX + json.dumps(payload)
        before = self._read(d, "05-price.html"), self._read(d, "08-stock.html")
        res = ao.autofix_designed_overlays(d, f"GATE: FAIL ✗\n{line}\n", self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(before, (self._read(d, "05-price.html"), self._read(d, "08-stock.html")))
        self.assertNotIn('class="cap-sub" data-layout-allow-overlap', self._read(d, "05-price.html"))
        ticker = _js_findings_payload([{
            "code": "content_overlap", "file": f8,
            "selector": "div.f8-ticker > span:nth-of-type(3)",
            "container": "span.f8-row-text", "msg": ""}])
        line = ao.FINDINGS_JSON_PREFIX + json.dumps(ticker)
        res = ao.autofix_designed_overlays(d, f"GATE: FAIL ✗\n{line}\n", self.logger)
        self.assertEqual(res.nodes_marked, 1)
        self.assertIn('<div class="f8-ticker" data-layout-allow-overlap="true">',
                      self._read(d, "08-stock.html"))

    def test_ambiguous_and_real_rows_never_written(self):
        d = self._make_project()
        row = _safe({"file": "compositions/frames/05-price.html",
                     "selector": "div.cap-sub", "container": ""})
        before = self._read(d, "05-price.html")
        res = ao.autofix_designed_overlays(
            d, _scan([], ambiguous=[row], real_collision=[row]), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(before, self._read(d, "05-price.html"))

    def test_payload_version_mismatch_skips(self):
        d = self._make_project()
        before = self._read(d, "05-price.html")
        with self.assertLogs("test.verify.ao", level="WARNING") as logs:
            res = ao.autofix_designed_overlays(d, _scan(_ROWS_SAMPLE, version=2), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(before, self._read(d, "05-price.html"))
        self.assertTrue(any("version" in m for m in logs.output))

    def _write_frame(self, d: str, name: str, body: str, newline: str = "\n") -> str:
        path = os.path.join(d, "compositions", "frames", name)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(body)
        return path

    def test_anchor_deeper_than_parent_is_unresolved(self):
        d = self._make_project()
        self._write_frame(d, "09-deep.html", (
            '<template><div id="root" data-composition-id="09">\n'
            '<div class="wrap"><div>a</div><div><span>x</span></div></div>\n'
            '<div class="row"><span>y</span><span>z</span></div>\n'
            '</div></template>\n'))
        rows = [
            _safe({"file": "compositions/frames/09-deep.html",
                   "selector": "div.wrap > div:nth-of-type(2) > span:nth-of-type(1)",
                   "container": ""}),
        ]
        with self.assertLogs("test.verify.ao", level="INFO") as logs:
            res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(len(res.unresolved), 1)
        self.assertIn("more than one level above", res.unresolved[0])
        self.assertTrue(any("unresolved" in m for m in logs.output))
        # One hook-less child segment → the direct parent is marked
        rows = [_safe({"file": "compositions/frames/09-deep.html",
                       "selector": "div.row > span:nth-of-type(2)", "container": ""})]
        res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertEqual(res.nodes_marked, 1)
        self.assertIn('<div class="row" data-layout-allow-overlap="true">',
                      self._read(d, "09-deep.html"))
        # Parent without a matching child segment is not a target
        rows = [_safe({"file": "compositions/frames/09-deep.html",
                       "selector": "div.row > span:nth-of-type(7)", "container": ""})]
        res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertFalse(res.changed)

    def test_too_many_matches_left_for_model(self):
        d = self._make_project()
        spans = "".join(f'<span class="sticker">{i}</span>' for i in range(4))
        self._write_frame(d, "10-many.html", (
            '<template><div id="root" data-composition-id="10">'
            f'<div class="a">{spans}</div>'
            f'<div class="b" data-layout-allow-overlap="true">{spans}</div>'
            '</div></template>'))
        rows = [_safe({"file": "compositions/frames/10-many.html",
                       "selector": "span.sticker", "container": ""})]
        before = self._read(d, "10-many.html")
        with self.assertLogs("test.verify.ao", level="WARNING") as logs:
            res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(before, self._read(d, "10-many.html"))
        self.assertTrue(any("too broad" in m for m in logs.output))
        # Nodes already covered by a flagged ancestor do not count
        self._write_frame(d, "10-many.html", (
            '<template><div id="root" data-composition-id="10">'
            '<div class="a"><span class="sticker">0</span></div>'
            f'<div class="b" data-layout-allow-overlap="true">{spans}</div>'
            '</div></template>'))
        res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertEqual(res.nodes_marked, 1)

    def test_broad_row_covered_by_same_batch_parent(self):
        d = self._make_project()
        seals = "".join('<span>w</span><span class="f8-seal">·</span>' for _ in range(5))
        self._write_frame(d, "08-stock.html", (
            '<template><div id="root" data-composition-id="08">'
            f'<div class="f8-ticker">{seals}</div></div></template>'))
        dot = _safe({"file": "compositions/frames/08-stock.html",
                     "selector": "span.f8-seal", "container": ""}, "seal")
        ticker = _safe({"file": "compositions/frames/08-stock.html",
                        "selector": "div.f8-ticker > span:nth-of-type(3)", "container": ""})
        res = ao.autofix_designed_overlays(d, _scan([dot, ticker]), self.logger)
        self.assertEqual(res.unresolved, [])
        self.assertEqual(res.nodes_marked, 1)
        f8 = self._read(d, "08-stock.html")
        self.assertIn('<div class="f8-ticker" data-layout-allow-overlap="true">', f8)
        self.assertEqual(f8.count("data-layout-allow-overlap"), 1)

    def test_crlf_and_mode_preserved(self):
        d = self._make_project()
        path = self._write_frame(
            d, "05-price.html", _FRAME_05.replace("\n", "\r\n"), newline="")
        os.chmod(path, 0o644)
        ao.autofix_designed_overlays(d, _scan(_ROWS_SAMPLE[:1]), self.logger)
        with open(path, "rb") as f:
            raw = f.read()
        self.assertIn(b'<div class="f5-stamp" data-layout-allow-overlap="true">', raw)
        self.assertEqual(raw.count(b"\r\n"), _FRAME_05.count("\n"))
        self.assertNotIn(b"\r\r", raw)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        frames = os.listdir(os.path.dirname(path))
        self.assertFalse([n for n in frames if n.endswith(".tmp")])

    def test_idempotent(self):
        d = self._make_project()
        ao.autofix_designed_overlays(d, _scan(_ROWS_SAMPLE), self.logger)
        snap = self._read(d, "05-price.html"), self._read(d, "08-stock.html")
        res = ao.autofix_designed_overlays(d, _scan(_ROWS_SAMPLE), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(snap, (self._read(d, "05-price.html"), self._read(d, "08-stock.html")))

    def test_self_closing(self):
        d = self._make_project()
        rows = [_safe({"file": "compositions/frames/05-price.html", "selector": "img.seal",
                       "container": "div.price-num"}, "seal")]
        res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertEqual(res.handled, 1)
        f5 = self._read(d, "05-price.html")
        self.assertIn('<img src="x.png" class="seal" data-layout-allow-overlap="true" />', f5)
        self.assertIn('<div class="price-num">', f5)

    def test_refuses_root_clip_bare_and_outside_paths(self):
        d = self._make_project()
        rows = [_safe(r) for r in (
            {"file": "compositions/frames/05-price.html", "selector": "#root",
             "container": ""},
            {"file": "compositions/frames/05-price.html",
             "selector": 'div[data-start="0"]', "container": ""},
            {"file": "compositions/frames/05-price.html",
             "selector": "div > div:nth-of-type(2)", "container": ""},
            {"file": "compositions/frames/05-price.html", "selector": "div.nope",
             "container": ""},
            {"file": "../outside.html", "selector": "div.x", "container": ""},
            {"file": "index.html", "selector": "div.x", "container": ""},
        )]
        before = self._read(d, "05-price.html")
        res = ao.autofix_designed_overlays(d, _scan(rows), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(len(res.unresolved), 6)
        self.assertEqual(before, self._read(d, "05-price.html"))

    def test_no_payload_is_noop(self):
        d = self._make_project()
        res = ao.autofix_designed_overlays(d, "GATE: FAIL ✗\n", self.logger)
        self.assertFalse(res.changed)
        self.assertIsNone(ao.parse_findings_payload("VERIFY_FINDINGS_JSON {bad"))

    def test_caption_keepout_deletes_strip(self):
        d = self._make_project()
        row = {"file": "compositions/frames/05-price.html",
               "selector": "div.cap-sub", "container": "#caption-word-32-0"}
        res = ao.autofix_caption_keepout(d, _scan([], real_collision=[row]), self.logger)
        self.assertTrue(res.changed)
        self.assertEqual(res.handled, 1)
        f5 = self._read(d, "05-price.html")
        self.assertNotIn("cap-sub", f5)
        # The overlapping frame caption strip is gone; siblings survive.
        self.assertIn('<div class="price-tag">', f5)
        self.assertIn('<div class="f5-stamp">', f5)

    def test_caption_keepout_ignores_non_caption_row(self):
        d = self._make_project()
        row = {"file": "compositions/frames/05-price.html",
               "selector": "div.price-num", "container": "div.price-tag"}
        before = self._read(d, "05-price.html")
        res = ao.autofix_caption_keepout(d, _scan([], real_collision=[row]), self.logger)
        self.assertFalse(res.changed)
        self.assertEqual(before, self._read(d, "05-price.html"))


class VerifyFrameAutoOverlapTest(unittest.TestCase, _ProjectMixin):
    def setUp(self):
        self.d = self._make_project()
        self.logger = logging.getLogger("test.verify.flow")
        self.sheet = os.path.join(self.d, "snapshots", "contact-sheet.jpg")
        p = mock.patch.object(v, "_run_prepare", return_value=(0, "verify-all: prepare done ✓\n"))
        self.prep = p.start(); self.addCleanup(p.stop)

    def _run(self, scans, env=None, claude_rc=0):
        env = env or {}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(v, "_run_verify", side_effect=scans) as scan, \
             mock.patch.object(v, "_run_fix_worker", return_value=claude_rc) as claude, \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            if "HTMLVIDEO_VERIFY_AUTO_OVERLAP" not in env:
                os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            out = v.verify_frame(self.d, self.logger)
        return out, scan, claude

    def test_designed_overlay_only_skips_claude(self):
        out, scan, claude = self._run([(1, _scan(_ROWS_SAMPLE)), (0, "GATE: PASS ✓\n")])
        self.assertEqual(out, self.sheet)
        claude.assert_not_called()
        self.assertEqual(scan.call_count, 2)
        self.assertIn('data-layout-allow-overlap="true"', self._read(self.d, "05-price.html"))

    def test_clean_first_scan_is_untouched(self):
        before = self._read(self.d, "05-price.html")
        out, scan, claude = self._run([(0, "GATE: PASS ✓\n")])
        self.assertEqual(scan.call_count, 1)
        claude.assert_not_called()
        self.assertEqual(before, self._read(self.d, "05-price.html"))

    def test_auto_overlap_disabled_uses_model(self):
        def fake_claude(project_dir, prompt, logger, skill):
            path = os.path.join(project_dir, "compositions", "frames", "08-stock.html")
            with open(path, "a") as f:
                f.write("<!-- edited -->\n")
            return 0

        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_AUTO_OVERLAP": "0"}), \
             mock.patch.object(v, "_run_verify",
                               side_effect=[(1, _scan(_ROWS_SAMPLE)), (0, "GATE: PASS ✓\n")]), \
             mock.patch.object(v, "_run_fix_worker", side_effect=fake_claude) as claude, \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            v.verify_frame(self.d, self.logger)
        claude.assert_called_once()
        self.assertNotIn("data-layout-allow-overlap=\"true\"",
                         self._read(self.d, "05-price.html"))

    def test_remaining_real_collision_goes_to_model_with_rescan_output(self):
        remaining = _scan(
            [], actionable="  1) Known failure #4 — content_overlap triage — "
            "real_collision×1 (x): reposition\n",
            real_collision=[{"file": "compositions/frames/05-price.html",
                             "selector": "h1.title", "container": "p.body"}],
        )
        prompts = []

        def fake_claude(project_dir, prompt, logger, skill):
            prompts.append(prompt)
            with open(os.path.join(project_dir, "compositions", "frames",
                                   "05-price.html"), "a") as f:
                f.write("<!-- moved -->\n")
            return 0

        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, _scan(_ROWS_SAMPLE)), (1, remaining), (0, "GATE: PASS ✓\n")]), \
             mock.patch.object(v, "_run_fix_worker", side_effect=fake_claude), \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            v.verify_frame(self.d, self.logger)
        self.assertEqual(len(prompts), 1)
        self.assertIn("real_collision×1", prompts[0])
        self.assertNotIn("designed_overlay×1", prompts[0])

    def test_autofix_crash_returns_original_scan(self):
        scan = _scan(_ROWS_SAMPLE)
        with mock.patch.object(v, "autofix_designed_overlays",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(v, "_run_verify") as rescan, \
             self.assertLogs("test.verify.flow", level="ERROR") as logs:
            out = v._auto_fix_overlaps(self.d, 1, scan, 1, self.logger)
        self.assertEqual(out, (1, scan))
        rescan.assert_not_called()
        self.assertTrue(any("crashed" in m for m in logs.output))

    def _edit_claude(self, project_dir, prompt, logger, skill):
        with open(os.path.join(project_dir, "compositions", "frames",
                               "05-price.html"), "a") as f:
            f.write("<!-- moved -->\n")
        return 0

    def test_final_rescan_gets_autofix_pass(self):
        remaining = _scan([], real_collision=[{
            "file": "compositions/frames/05-price.html",
            "selector": "h1.title", "container": "p.body"}])
        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_FIX_ROUNDS": "1"}), \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, remaining), (1, _scan(_ROWS_SAMPLE)), (0, "GATE: PASS ✓\n")]) as scan, \
             mock.patch.object(v, "_run_fix_worker", side_effect=self._edit_claude) as claude, \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            out = v.verify_frame(self.d, self.logger)
        self.assertEqual(out, self.sheet)
        self.assertEqual(claude.call_count, 1)
        self.assertEqual(scan.call_count, 3)
        self.assertIn('<div class="f5-stamp" data-layout-allow-overlap="true">',
                      self._read(self.d, "05-price.html"))

    def test_final_rescan_pass_is_accepted(self):
        remaining = _scan([], real_collision=[{
            "file": "compositions/frames/05-price.html",
            "selector": "h1.title", "container": "p.body"}])
        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_FIX_ROUNDS": "1"}), \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, remaining), (0, "GATE: PASS ✓\n")]), \
             mock.patch.object(v, "_run_fix_worker", side_effect=self._edit_claude), \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            self.assertEqual(v.verify_frame(self.d, self.logger), self.sheet)

    def test_final_rescan_still_failing_raises(self):
        remaining = _scan([], real_collision=[{
            "file": "compositions/frames/05-price.html",
            "selector": "h1.title", "container": "p.body"}])
        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_FIX_ROUNDS": "1"}), \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, remaining), (1, remaining)]), \
             mock.patch.object(v, "_run_fix_worker", side_effect=self._edit_claude):
            with self.assertRaises(RuntimeError) as ctx:
                v.verify_frame(self.d, self.logger)
        self.assertIn("after 1 fix rounds", str(ctx.exception))

    def test_remaining_unactionable_after_autofix_raises_without_model(self):
        leftover = (
            "── actionable summary ──\n  #2 tell only. No action.\n\n"
            "GATE: FAIL ✗ (…, 0 error finding(s))\n"
        )
        with mock.patch.object(v, "_run_verify",
                               side_effect=[(1, _scan(_ROWS_SAMPLE)), (1, leftover)]), \
             mock.patch.object(v, "_run_fix_worker") as claude:
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            with self.assertRaises(RuntimeError) as ctx:
                v.verify_frame(self.d, self.logger)
        claude.assert_not_called()
        self.assertIn("0 actionable findings", str(ctx.exception))

    def test_prepare_runs_once_across_rounds(self):
        remaining = _scan([], real_collision=[{
            "file": "compositions/frames/05-price.html",
            "selector": "h1.title", "container": "p.body"}])
        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_FIX_ROUNDS": "1"}), \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, remaining), (1, _scan(_ROWS_SAMPLE)), (0, "GATE: PASS ✓\n")]) as scan, \
             mock.patch.object(v, "_run_fix_worker", side_effect=self._edit_claude), \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            v.verify_frame(self.d, self.logger)
        self.assertEqual(self.prep.call_count, 1)
        self.assertEqual(scan.call_count, 3)

    def test_prepare_failure_raises_without_verify_or_model(self):
        self.prep.return_value = (2, "✗ inject failed: boom")
        with mock.patch.object(v, "_run_verify") as verify, \
             mock.patch.object(v, "_run_fix_worker") as claude:
            with self.assertRaises(RuntimeError) as ctx:
                v.verify_frame(self.d, self.logger)
        self.assertIn("prepare failure", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))
        verify.assert_not_called()
        claude.assert_not_called()

    def test_visual_gate_fail_uses_visual_prompt_skips_overlap(self):
        prompts = []

        def fake_claude(project_dir, prompt, logger, skill):
            prompts.append(prompt)
            with open(os.path.join(project_dir, "compositions", "frames",
                                   "05-price.html"), "a") as f:
                f.write("<!-- pixels -->\n")
            return 0

        passed = "verify-all: MECHANICAL+VISUAL PASS\nVISUAL_GATE: PASS\n"
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(v, "autofix_designed_overlays") as overlap, \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, _visual_scan(with_payload=True)),
                 (0, passed),
             ]), \
             mock.patch.object(v, "_run_fix_worker", side_effect=fake_claude) as claude, \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            out = v.verify_frame(self.d, self.logger)
        self.assertEqual(out, self.sheet)
        overlap.assert_not_called()
        claude.assert_called_once()
        self.assertEqual(len(prompts), 1)
        prompt = prompts[0]
        self.assertIn("pixels", prompt.lower())
        self.assertIn("visual", prompt.lower())
        self.assertIn("collapse", prompt.lower())
        self.assertIn("empty", prompt.lower())
        self.assertIn("VISUAL_GATE: FAIL", prompt)
        self.assertIn("frame-03.png", prompt)
        self.assertNotIn("known-failures.mjs", prompt)
        self.assertNotIn("── actionable summary ──", prompt)

    def test_visual_zero_edit_raises_without_overlap(self):
        with mock.patch.object(v, "autofix_designed_overlays") as overlap, \
             mock.patch.object(v, "_run_verify", return_value=(1, _visual_scan())), \
             mock.patch.object(v, "_run_fix_worker", return_value=0):
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            with self.assertRaises(RuntimeError) as ctx:
                v.verify_frame(self.d, self.logger)
        overlap.assert_not_called()
        self.assertIn("made no changes", str(ctx.exception))

    def test_mechanical_numbered_action_uses_old_prompt(self):
        prompts = []

        def fake_claude(project_dir, prompt, logger, skill):
            prompts.append(prompt)
            with open(os.path.join(project_dir, "compositions", "frames",
                                   "05-price.html"), "a") as f:
                f.write("<!-- mechanical -->\n")
            return 0

        passed = "verify-all: MECHANICAL+VISUAL PASS\nVISUAL_GATE: PASS\n"
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(v, "_run_verify", side_effect=[
                 (1, _mechanical_scan()),
                 (0, passed),
             ]), \
             mock.patch.object(v, "_run_fix_worker", side_effect=fake_claude), \
             mock.patch.object(v, "_validate_verify_outputs", return_value=self.sheet):
            os.environ.pop("HTMLVIDEO_VERIFY_AUTO_OVERLAP", None)
            self.assertEqual(v.verify_frame(self.d, self.logger), self.sheet)
        self.assertEqual(len(prompts), 1)
        self.assertIn("known-failures.mjs", prompts[0])
        self.assertIn("── actionable summary ──", prompts[0])
        self.assertIn("GATE: FAIL", prompts[0])
        self.assertNotIn("VISUAL_GATE:", prompts[0])

    def test_both_gates_pass_does_not_call_model(self):
        passed = "GATE: PASS ✓\nVISUAL_GATE: PASS\nverify-all: MECHANICAL+VISUAL PASS\n"
        before = self._read(self.d, "05-price.html")
        out, scan, claude = self._run([(0, passed)])
        self.assertEqual(out, self.sheet)
        self.assertEqual(scan.call_count, 1)
        claude.assert_not_called()
        self.assertEqual(before, self._read(self.d, "05-price.html"))


class VerifyFramePrepareTest(unittest.TestCase):
    def test_run_verify_phase_cmd_and_cwd(self):
        d = tempfile.mkdtemp(prefix="verify-phase-")
        self.addCleanup(shutil.rmtree, d, True)
        logger = logging.getLogger("test.verify.phase")
        proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="x", stderr="")
        with mock.patch.object(v.subprocess, "run", return_value=proc) as run:
            rc, _out = v._run_verify(d, logger)
        self.assertEqual(rc, 1)
        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            ["node", v.VERIFY_ALL_SCRIPT, "--videodir", d, "--phase", "verify"],
        )
        self.assertEqual(kwargs["cwd"], d)
        self.assertEqual(kwargs["timeout"], 900)

        with mock.patch.object(v.subprocess, "run", return_value=proc) as run:
            v._run_prepare(d, logger)
        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            ["node", v.VERIFY_ALL_SCRIPT, "--videodir", d, "--phase", "prepare"],
        )
        self.assertEqual(kwargs["cwd"], d)
        self.assertEqual(kwargs["timeout"], 300)

    def test_run_verify_phase_timeout_returns_2(self):
        d = tempfile.mkdtemp(prefix="verify-phase-")
        self.addCleanup(shutil.rmtree, d, True)
        logger = logging.getLogger("test.verify.phase")
        with mock.patch.object(
            v.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="node", timeout=900),
        ):
            rc, out = v._run_verify(d, logger)
        self.assertEqual(rc, 2)
        self.assertIn("--phase verify timed out", out)


class FixPromptTest(unittest.TestCase):
    def test_prompt_trims_waterfall_and_payload(self):
        os.environ.pop("HTMLVIDEO_VERIFY_FIX_FULL_SCAN", None)
        p = v._build_fix_prompt(_scan(_ROWS_SAMPLE), "/abs/proj")
        self.assertIn("── actionable summary ──", p)
        self.assertIn("GATE: FAIL", p)
        self.assertIn("✗ [Layout] content_overlap", p)
        self.assertNotIn("frame-selfcheck: 7/7 PASS", p)
        self.assertNotIn("[Contrast]", p)
        self.assertNotIn("AT: 1,2,3", p)
        self.assertNotIn(ao.FINDINGS_JSON_PREFIX.strip(), p)
        self.assertIn("Project root (absolute): /abs/proj", p)

    def test_full_scan_rollback(self):
        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_FIX_FULL_SCAN": "1"}):
            p = v._build_fix_prompt(_scan(_ROWS_SAMPLE))
        self.assertIn("frame-selfcheck: 7/7 PASS", p)
        self.assertNotIn(ao.FINDINGS_JSON_PREFIX.strip(), p)

    def test_missing_summary_falls_back_to_full(self):
        os.environ.pop("HTMLVIDEO_VERIFY_FIX_FULL_SCAN", None)
        p = v._build_fix_prompt("weird output without header\nGATE: FAIL ✗\n")
        self.assertIn("weird output without header", p)

    def test_reply_contract(self):
        p = v._build_fix_prompt(_scan(_ROWS_SAMPLE))
        self.assertIn("fixed <n>", p)
        self.assertIn("fixed 0", p)
        self.assertNotIn("list — one line each", p)
        self.assertNotIn("do NOT read the whole file first", p)
        self.assertIn("Read each target file (absolute path) at most ONCE, then Edit", p)
        self.assertIn("Do NOT\n  Grep / Glob", p)

    def test_trim_keeps_error_detail_lines(self):
        os.environ.pop("HTMLVIDEO_VERIFY_FIX_FULL_SCAN", None)
        scan = (
            "── transitions verify ──\n"
            "  ✗ exit 1 — transitions verify failed\n"
            "      boundary 03→04: no injected block found\n"
            "      boundary 05→06: duration 0.6 != 0.5\n"
            "\n"
            "── check ──\n"
            "  ✗ [Layout] content_overlap in compositions/frames/05.html — x\n"
            "    Fix: give each block its own zone\n"
            "  ⚠ [Contrast] contrast_aa_failure — y\n"
            "      contrast detail must not leak\n"
            "  ✓ fine\n"
            "── actionable summary ──\n  1) transitions verify failures\n"
            "\nGATE: FAIL ✗ (…, 2 error finding(s))\n"
        )
        p = v._build_fix_prompt(scan)
        self.assertIn("boundary 03→04: no injected block found", p)
        self.assertIn("boundary 05→06: duration 0.6 != 0.5", p)
        self.assertIn("Fix: give each block its own zone", p)
        self.assertNotIn("[Contrast]", p)
        self.assertNotIn("contrast detail must not leak", p)
        self.assertNotIn("✓ fine", p)

    def test_visual_prompt_is_pixels_not_mechanical_playbook(self):
        p = v._build_visual_fix_prompt(_visual_scan(), "/abs/proj")
        self.assertIn("pixels", p.lower())
        self.assertIn("visual", p.lower())
        self.assertIn("collapse means layout collapse", p)
        self.assertIn("empty means missing content", p)
        self.assertIn("frame-03.png", p)
        self.assertIn("frame-05.png", p)
        self.assertIn("compositions/frames", p)
        self.assertIn("fixed <n>", p)
        self.assertIn("VISUAL_GATE: FAIL", p)
        self.assertIn("Project root (absolute): /abs/proj", p)
        self.assertIn("Do NOT run verify-all / lint / check / snapshot", p)
        self.assertNotIn("known-failures.mjs", p)
        self.assertNotIn("── actionable summary ──", p)
        self.assertNotIn("GATE: PASS", p)

    def test_split_scan_filters_sibling_files_from_summary(self):
        scan = (
            "── error findings ──\n"
            "    ✗ gsap_exit_missing_hard_kill  compositions/frames/01-buy.html — exit on #ticker\n"
            "    ✗ gsap_exit_missing_hard_kill  compositions/frames/07-rules.html — exit on .f7-cap\n"
            "\n"
            "── actionable summary ──\n"
            "  • google_fonts_import (non-blocking) — ignore.\n"
            "  1) other lint errors — Fix the named file (compositions/frames/01-buy.html: exit on #ticker; compositions/frames/07-rules.html: exit on .f7-cap). Do not act on google_fonts_import.\n"
            "\nGATE: FAIL ✗ (2 error finding(s))\n"
        )
        frags, _ = v._split_scan_by_file(scan)
        self.assertEqual(set(frags.keys()), {
            "compositions/frames/01-buy.html",
            "compositions/frames/07-rules.html",
        })
        self.assertIn("01-buy.html", frags["compositions/frames/01-buy.html"])
        self.assertNotIn("07-rules.html", frags["compositions/frames/01-buy.html"])
        self.assertIn("07-rules.html", frags["compositions/frames/07-rules.html"])
        self.assertNotIn("01-buy.html", frags["compositions/frames/07-rules.html"])
        p1 = v._build_fix_prompt_from_fragment(
            frags["compositions/frames/01-buy.html"], "/abs/proj", "scan findings",
            target_file="compositions/frames/01-buy.html",
        )
        self.assertIn("Scope: fix ONLY `compositions/frames/01-buy.html`", p1)

    def test_single_file_small_html_is_inlined_and_toggleable(self):
        d = tempfile.mkdtemp(prefix="verify-inline-")
        self.addCleanup(shutil.rmtree, d, True)
        frames = os.path.join(d, "compositions", "frames")
        os.makedirs(frames)
        with open(os.path.join(frames, "01-hook.html"), "w", encoding="utf-8") as f:
            f.write("<template><div class=\"word\">BUY</div></template>\n")
        scan = (
            "── check ──\n"
            "  ✗ [Layout] content_overlap in compositions/frames/01-hook.html — div.word overlaps div.sub\n"
            "── actionable summary ──\n"
            "  1) content_overlap — real_collision×1 (compositions/frames/01-hook.html: div.word overlaps div.sub)\n"
            "GATE: FAIL ✗ (1 error finding(s))\n"
        )
        os.environ.pop("HTMLVIDEO_VERIFY_INLINE_FILE", None)
        p = v._build_fix_prompt(scan, d)
        self.assertIn("── current file (", p)
        self.assertIn("1\t<template><div class=\"word\">BUY</div></template>", p)
        with mock.patch.dict(os.environ, {"HTMLVIDEO_VERIFY_INLINE_FILE": "0"}):
            p_off = v._build_fix_prompt(scan, d)
        self.assertNotIn("── current file (", p_off)

    def test_truncated_worker_with_landed_edits_proceeds_to_rescan(self):
        d = tempfile.mkdtemp(prefix="verify-trunc-")
        self.addCleanup(shutil.rmtree, d, True)
        frames = os.path.join(d, "compositions", "frames")
        os.makedirs(frames)
        target = os.path.join(frames, "01-hook.html")
        with open(target, "w", encoding="utf-8") as f:
            f.write("<template>before</template>\n")
        logger = logging.getLogger("test.verify.trunc")

        def edit_then_truncate(project_dir, prompt, log, label):
            with open(target, "w", encoding="utf-8") as f:
                f.write("<template>after</template>\n")
            return v.TRUNCATED_RC

        scan = (
            "── check ──\n"
            "  ✗ [Layout] content_overlap in compositions/frames/01-hook.html — x\n"
            "── actionable summary ──\n"
            "  1) content_overlap — reposition\n"
            "GATE: FAIL ✗ (1 error finding(s))\n"
        )
        with mock.patch.object(v, "_run_fix_worker", side_effect=edit_then_truncate), \
             mock.patch.object(v, "_run_verify", return_value=(0, "GATE: PASS ✓\n")) as rescan, \
             self.assertLogs("test.verify.trunc", level="WARNING") as logs:
            rc, _ = v._apply_model_fix(d, scan, 1, logger)
        self.assertEqual(rc, 0)
        rescan.assert_called_once()
        self.assertTrue(any("after writing edits" in m for m in logs.output))

        # Without edits, TRUNCATED_RC still raises.
        with mock.patch.object(v, "_run_fix_worker", return_value=v.TRUNCATED_RC):
            with self.assertRaises(RuntimeError) as ctx:
                v._apply_model_fix(d, scan, 1, logger)
        self.assertIn("truncated", str(ctx.exception))

    def test_run_verify_phase_logs_findings_on_failure(self):
        d = tempfile.mkdtemp(prefix="verify-log-")
        self.addCleanup(shutil.rmtree, d, True)
        logger = logging.getLogger("test.verify.log")
        proc = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout=(
                "── check ──\n"
                "  ✗ [Layout] content_overlap in compositions/frames/01.html — div.word overlaps div.sub\n"
                "── actionable summary ──\n"
                "  1) content_overlap — reposition\n"
                "GATE: FAIL ✗ (1 error finding(s))\n"
            ),
            stderr="",
        )
        with mock.patch.object(v.subprocess, "run", return_value=proc), \
             self.assertLogs("test.verify.log", level="INFO") as logs:
            rc, _ = v._run_verify(d, logger)
        self.assertEqual(rc, 1)
        joined = "\n".join(logs.output)
        self.assertIn("verify-all verify findings:", joined)
        self.assertIn("div.word overlaps div.sub", joined)

    def test_verify_pixels_catches_empty_main_stage_masked_by_caption_band(self):
        import importlib.util
        from PIL import Image, ImageDraw
        spec = importlib.util.spec_from_file_location("verify_pixels", "/app/scripts/verify-pixels.py")
        vp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vp)

        d = tempfile.mkdtemp(prefix="vp-stage-")
        self.addCleanup(shutil.rmtree, d, True)
        img_path = os.path.join(d, "frame-01.png")
        # Cream 1920x1080 canvas with ZERO pixels in top 83%, dark caption pill in bottom 17%
        img = Image.new("RGB", (1920, 1080), (245, 245, 240))
        draw = ImageDraw.Draw(img)
        draw.rectangle([560, 930, 1360, 1010], fill=(26, 26, 26))
        img.save(img_path)
        res = vp.analyze(img_path)
        self.assertGreaterEqual(res["content_pct"], 0.2)
        self.assertEqual(res["stage_content_pct"], 0.0)
        self.assertTrue(
            any(sev == "FAIL" and "empty main stage" in msg for sev, msg in res["findings"]),
            res["findings"],
        )


if __name__ == "__main__":
    unittest.main()
