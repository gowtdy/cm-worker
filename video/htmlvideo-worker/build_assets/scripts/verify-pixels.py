#!/usr/bin/env python3
"""
verify-pixels.py — deterministic snapshot regression probes for /aiflow-verify-frame.

Replaces open-ended "glance at the contact sheet" with measurable checks when the
verifying model has no vision (the 2026-11-12 verify session burned ~10 minutes
hand-writing ad-hoc PIL probes after its model could not see images). Pinned to the
failure class that actually bit: frames collapsing to a static top-left cluster
after CSS selector rules were silently dropped.

Checks per frame-*.png snapshot (deterministic, no model, no API key):
  empty frame      — <0.2% pixels differ from the dominant background colour
  collapse         — content bbox <15% of canvas AND hugs a corner
  sparse centered  — WARN: content bbox <15% but not corner-hugging
  no ink           — WARN: no near-black pixels (possible missing text)
Plus a content quadrant map and dominant colour per snapshot, so a text-only
model can reason about the sheets from the report alone.

Usage:
  python3 /app/scripts/verify-pixels.py --videodir /abs/path [--json]

Exit: 0 = no FAIL findings | 1 = at least one FAIL | 2 = no snapshots/IO error.
"""

import argparse
import glob
import json as _json
import os
import sys
from collections import Counter

try:
    from PIL import Image
except ImportError as e:  # pragma: no cover
    print(f"[verify-pixels] PIL not installed: {e}", file=sys.stderr)
    sys.exit(2)

try:
    RESAMPLE = Image.Resampling.NEAREST
except AttributeError:  # PIL < 9
    RESAMPLE = Image.NEAREST

CONTENT_DIST = 45.0      # RGB euclidean distance from dominant colour
EMPTY_RATIO = 0.002      # content below this = empty frame
SPARSE_RATIO = 0.15      # content bbox below this = suspicious
CORNER_BAND = 0.25       # bbox hugging within this fraction of an edge
INK_MAX = 90             # max(r,g,b) below this = ink

def rgb_dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

def analyze(path):
    img = Image.open(path).convert("RGB")
    W, H = img.size
    # downscale (NEAREST keeps the original palette) for fast stats
    scale = max(1, W // 480)
    small = img.resize((max(1, W // scale), max(1, H // scale)), RESAMPLE)
    sw, sh = small.size
    px = small.load()
    pixels = [(x, y, px[x, y]) for y in range(sh) for x in range(sw)]
    dominant = Counter(p[2] for p in pixels).most_common(1)[0][0]
    content = [(x, y, c) for (x, y, c) in pixels if rgb_dist(c, dominant) > CONTENT_DIST]
    ratio = len(content) / (sw * sh)
    quads = [0, 0, 0, 0]
    for (x, y, _) in content:
        q = (0 if x < sw / 2 else 1) + (0 if y < sh / 2 else 2)
        quads[q] += 1
    total_c = max(1, sum(quads))
    quads_pct = [round(100.0 * q / total_c, 1) for q in quads]
    # text-contrast probe adapts to the dominant colour: dark text on light
    # backgrounds, light text on dark backgrounds (a dark frame has no "ink").
    dom_lum = 0.299 * dominant[0] + 0.587 * dominant[1] + 0.114 * dominant[2]
    if dom_lum > 128:
        ink = sum(1 for (_, _, c) in pixels if max(c) < INK_MAX) / (sw * sh)
    else:
        ink = sum(1 for (_, _, c) in pixels if min(c) > 200) / (sw * sh)
    findings = []
    if ratio < EMPTY_RATIO:
        findings.append(("FAIL", f"empty frame ({ratio * 100:.2f}% content)"))
    else:
        xs = [p[0] for p in content]
        ys = [p[1] for p in content]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        xspan = (x1 - x0 + 1) / sw
        yspan = (y1 - y0 + 1) / sh
        bbox_ratio = xspan * yspan
        corner = (x0 < CORNER_BAND * sw or x1 > (1 - CORNER_BAND) * sw) and (
            y0 < CORNER_BAND * sh or y1 > (1 - CORNER_BAND) * sh
        )
        # FAIL only the historical collapse shape: a NARROW blob hugging a corner
        # (dropped CSS rules parked elements static at the top-left). A full-width
        # band is a layout/animation state, not a collapse — WARN with its shape so
        # a glance can decide.
        if bbox_ratio < SPARSE_RATIO and corner and xspan < 0.5 and yspan < 0.35:
            findings.append(("FAIL", f"collapse: narrow blob in corner bbox={bbox_ratio * 100:.1f}%"))
        elif bbox_ratio < SPARSE_RATIO:
            shape = "full-width band" if xspan > 0.9 else ("tall band" if yspan > 0.5 else "small cluster")
            findings.append(("WARN", f"sparse content bbox={bbox_ratio * 100:.1f}% ({shape})"))
    if ink < 0.00001 and ratio >= EMPTY_RATIO:
        findings.append(("WARN", "no text-contrast pixels detected"))
    return {
        "file": os.path.basename(path),
        "dominant": tuple(dominant),
        "content_pct": round(ratio * 100, 2),
        "quads_tl_tr_bl_br_pct": quads_pct,
        "ink_pct": round(ink * 100, 3),
        "findings": findings,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videodir", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    snaps = sorted(glob.glob(os.path.join(args.videodir, "snapshots", "frame-*.png")))
    if not snaps:
        print("[verify-pixels] no snapshots/frame-*.png found — run snapshot first", file=sys.stderr)
        sys.exit(2)
    results, fails = [], 0
    for s in snaps:
        try:
            r = analyze(s)
        except Exception as e:
            print(f"[verify-pixels] cannot analyze {s}: {e}", file=sys.stderr)
            sys.exit(2)
        for sev, _ in r["findings"]:
            if sev == "FAIL":
                fails += 1
        results.append(r)
    if args.json:
        print(_json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            verdict = ", ".join(f"{sev}: {msg}" for sev, msg in r["findings"]) or "PASS"
            q = r["quads_tl_tr_bl_br_pct"]
            print(
                f"{r['file']:32s} {verdict:55s} content={r['content_pct']:5.2f}%  "
                f"TL/TR/BL/BR={q[0]}/{q[1]}/{q[2]}/{q[3]}  ink={r['ink_pct']}%  dominant={r['dominant']}"
            )
        print(f"SUMMARY: {len(results)} snapshot(s), {fails} FAIL finding(s)")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()