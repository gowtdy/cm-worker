# -*- coding: utf-8 -*-
"""stat/skill-timing.py: message.id dedup + truncation / request metrics."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_PATH = os.path.join(os.path.dirname(__file__), "..", "stat", "skill-timing.py")
_spec = importlib.util.spec_from_file_location("skill_timing", _PATH)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)


def _ts(sec):
    return f"2026-11-22T00:00:{sec:02d}.000Z"


def _asst(sec, mid, blocks, out=10, stop=None):
    return {"type": "assistant", "timestamp": _ts(sec), "message": {
        "role": "assistant", "id": mid, "stop_reason": stop,
        "usage": {"input_tokens": 5, "output_tokens": out,
                  "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 1},
        "content": blocks}}


def _user(sec, content, meta=False):
    d = {"type": "user", "timestamp": _ts(sec),
         "message": {"role": "user", "content": content}}
    if meta:
        d["isMeta"] = True
    return d


def _tool(name, tid):
    return {"type": "tool_use", "name": name, "id": tid, "input": {}}


def _result(tid):
    return {"type": "tool_result", "tool_use_id": tid, "content": "ok"}


SESSION = [
    _user(0, [{"type": "text", "text": "You are one per-frame HTML worker … done OK 01-hook"}]),
    # request m1: thinking + 3 parallel Reads written as 4 lines
    _asst(5, "m1", [{"type": "thinking", "thinking": "plan"}], out=900),
    _asst(5, "m1", [_tool("Read", "r1")], out=900),
    _asst(5, "m1", [_tool("Read", "r2")], out=900),
    _asst(5, "m1", [_tool("Read", "r3")], out=900),
    _user(6, [_result("r1"), _result("r2"), _result("r3")]),
    # request m2: thinking only at the cap → truncated, then the meta nudge
    _asst(40, "m2", [{"type": "thinking", "thinking": "x" * 50}], out=48000),
    _user(41, [{"type": "text", "text": "[Your previous response had no visible output. Please continue."}], meta=True),
    # request m3: first Write
    _asst(50, "m3", [{"type": "thinking", "thinking": "go"}], out=2000),
    _asst(52, "m3", [_tool("Write", "w1")], out=2000),
    _user(53, [_result("w1")]),
    _asst(55, "m4", [{"type": "text", "text": "done OK 01-hook"}], out=20),
]


class SkillTimingTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for e in SESSION:
                f.write(json.dumps(e) + "\n")
        self.addCleanup(os.remove, self.path)

    def test_dedup_rounds_and_usage(self):
        s = st.analyze(self.path)
        self.assertEqual(s["label"], "aiflow-build-frame-html:01-hook")
        self.assertEqual(s["api_requests"], 4)
        self.assertEqual(s["tool_turns"], 2)  # m1 (3 Reads in one request) + m3
        self.assertEqual(s["tools"], 4)
        self.assertEqual(s["tokens"]["output"], 900 + 48000 + 2000 + 20)
        self.assertEqual(s["tokens"]["input"], 4 * 5)
        self.assertEqual(s["tokens"]["cache_read"], 4 * 100)
        self.assertEqual(s["usage_msgs"], 4)

    def test_first_write_and_request_metrics(self):
        s = st.analyze(self.path)
        self.assertEqual(s["first_write_tool"], "Write")
        self.assertEqual(s["first_write_tool_turn"], 2)
        self.assertEqual(s["first_write_req"], 3)
        self.assertEqual(s["truncations"], 1)  # cap hit + nudge = same request
        self.assertAlmostEqual(s["max_req_s"], 34.0)  # 6s tool_result → 40s m2

    def test_trunc_cap_and_nudge_only(self):
        self.assertEqual(st.analyze(self.path, trunc_cap=60000)["truncations"], 1)
        no_nudge = [e for e in SESSION if not e.get("isMeta")]
        with open(self.path, "w") as f:
            for e in no_nudge:
                f.write(json.dumps(e) + "\n")
        self.assertEqual(st.analyze(self.path, trunc_cap=60000)["truncations"], 0)
        self.assertEqual(st.analyze(self.path)["truncations"], 1)

    def test_csv_columns_align(self):
        st.root = os.path.dirname(self.path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st.list_report([st.analyze(self.path)], csv=True)
        header, row = buf.getvalue().strip().splitlines()
        self.assertEqual(len(header.split(",")), len(row.split(",")))
        cols = dict(zip(header.split(","), row.split(",")))
        self.assertEqual(cols["api_requests"], "4")
        self.assertEqual(cols["truncations"], "1")
        self.assertEqual(cols["first_write_req"], "3")
        self.assertEqual(cols["rounds"], "2")

    def test_group_and_detail_render(self):
        st.root = os.path.dirname(self.path)
        s = st.analyze(self.path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st.group_report([s])
            st.detail_report(s)
        out = buf.getvalue()
        self.assertIn("trunc", out)
        self.assertIn("截断 1 次", out)


if __name__ == "__main__":
    unittest.main()
