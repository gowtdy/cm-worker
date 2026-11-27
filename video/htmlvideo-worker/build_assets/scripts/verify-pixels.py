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
import re
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
STAGE_TOP_FRAC = 0.83    # top 83% main stage (excludes bottom 17% caption band)
SPARSE_STAGE_WARN = 0.01 # <1.0% content in top 83% = tiny/under-scaled hero warning
SPARSE_RATIO = 0.15      # content bbox below this = suspicious
CORNER_BAND = 0.25       # bbox hugging within this fraction of an edge
INK_MAX = 90             # max(r,g,b) below this = ink

# S1-4: Shared Stage `#stage-card` interior probe boxes (fraction of canvas W/H),
# inset by STAGE_CARD_INSET to avoid the 3px border and drop shadow.
STAGE_CARD_BOXES = {
    "top-stepper-center-card": (0.18, 0.82, 0.19, 0.65),
    "left-rail-right-card": (0.34, 0.94, 0.14, 0.78),
    "center-scorecard": (0.20, 0.80, 0.38, 0.78),
}
STAGE_CARD_INSET = 0.02
STAGE_CARD_BG_COLORS = ((255, 255, 255), (245, 245, 240))
STAGE_CARD_DIST = 25.0
STAGE_CARD_EMPTY_RATIO = 0.0025
STAGE_CARD_COLLAPSE_BAND = 0.22
STAGE_CARD_COLLAPSE_SHARE = 0.75
STAGE_CARD_COLLAPSE_OPP_MAX = 0.01

_SCENE_TAG_RE = re.compile(r"<div\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'data-composition-src="([^"]*compositions/frames/[^"]+)"')
_START_ATTR_RE = re.compile(r'data-start="([\d.]+)"')
_DUR_ATTR_RE = re.compile(r'data-duration="([\d.]+)"')
_STAGE_LAYOUT_RE = re.compile(r'data-stage-layout="([^"]+)"')
_SNAP_TIME_RE = re.compile(r"-at-([\d.]+)s\.png$")


def _stage_card_probe_enabled():
    raw = os.environ.get("HTMLVIDEO_VERIFY_STAGE_CARD_PROBE", "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _load_stage_card_intervals(videodir):
    """Return list of (start, end, stage_layout) for frames containing `#stage-card`."""
    index_path = os.path.join(videodir, "index.html")
    if not os.path.isfile(index_path):
        return []
    try:
        with open(index_path, encoding="utf-8") as fh:
            index_html = fh.read()
    except OSError:
        return []
    intervals = []
    for m in _SCENE_TAG_RE.finditer(index_html):
        tag = m.group(0)
        src_m = _SRC_ATTR_RE.search(tag)
        if not src_m:
            continue
        start_m = _START_ATTR_RE.search(tag)
        dur_m = _DUR_ATTR_RE.search(tag)
        if not start_m or not dur_m:
            continue
        try:
            start = float(start_m.group(1))
            dur = float(dur_m.group(1))
        except ValueError:
            continue
        rel = src_m.group(1).lstrip("./")
        frame_path = os.path.join(videodir, rel)
        if not os.path.isfile(frame_path):
            continue
        try:
            with open(frame_path, encoding="utf-8") as fh:
                fhtml = fh.read()
        except OSError:
            continue
        if 'id="stage-card"' not in fhtml and "id='stage-card'" not in fhtml:
            continue
        lay_m = _STAGE_LAYOUT_RE.search(fhtml)
        layout = lay_m.group(1).strip() if lay_m else "top-stepper-center-card"
        if layout not in STAGE_CARD_BOXES:
            layout = "top-stepper-center-card"
        intervals.append((start, start + dur, layout))
    return intervals


def _stage_layout_for_snapshot(snap_path, intervals):
    if not intervals:
        return None
    m = _SNAP_TIME_RE.search(os.path.basename(snap_path))
    if not m:
        return None
    try:
        t = float(m.group(1))
    except ValueError:
        return None
    for start, end, layout in intervals:
        if start - 0.05 <= t <= end + 0.05:
            return layout
    return None


def _detect_stage_card_y_bounds(img, bx0, bx1, default_y0, default_y1):
    """Find actual top/bottom dark border rows of `#stage-card` on full-res image."""
    W, H = img.size
    px_full = img.load()
    span = bx1 - bx0
    xs = [int(W * (bx0 + 0.06 * span + 0.88 * span * i / 39)) for i in range(40)]
    y_lo = max(0, int(H * max(0.08, default_y0 - 0.04)))
    y_hi = min(H, int(H * min(0.84, max(default_y1, 0.78) + 0.04)))
    dark_rows = [
        y for y in range(y_lo, y_hi)
        if sum(1 for x in xs if max(px_full[x, y]) < 80) >= 32
    ]
    if len(dark_rows) >= 2 and (dark_rows[-1] - dark_rows[0]) > H * 0.25:
        return dark_rows[0] / H, dark_rows[-1] / H
    return default_y0, default_y1


def _probe_stage_card_interior(img, small_px, sw, sh, layout):
    """Probe `#stage-card` interior for empty card or edge-collapsed content."""
    box = STAGE_CARD_BOXES.get(layout)
    if not box:
        return [], None
    bx0, bx1, default_y0, default_y1 = box
    by0, by1 = _detect_stage_card_y_bounds(img, bx0, bx1, default_y0, default_y1)
    x0 = max(0, int(sw * (bx0 + STAGE_CARD_INSET)))
    x1 = min(sw, int(sw * (bx1 - STAGE_CARD_INSET)))
    y0 = max(0, int(sh * (by0 + STAGE_CARD_INSET)))
    y1 = min(sh, int(sh * (by1 - STAGE_CARD_INSET)))
    cw = x1 - x0
    ch = y1 - y0
    if cw <= 4 or ch <= 4:
        return [], None

    active = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = small_px[x, y]
            if all(rgb_dist(c, bg) > STAGE_CARD_DIST for bg in STAGE_CARD_BG_COLORS):
                active.append((x - x0, y - y0))

    total = cw * ch
    active_ratio = len(active) / total
    findings = []
    meta = {
        "layout": layout,
        "interior_active_pct": round(active_ratio * 100, 3),
    }
    if active_ratio < STAGE_CARD_EMPTY_RATIO:
        findings.append((
            "FAIL",
            f"empty #stage-card interior ({active_ratio * 100:.2f}% active pixels)",
        ))
        return findings, meta

    left_cut = max(1, int(cw * STAGE_CARD_COLLAPSE_BAND))
    right_start = cw // 2
    left_share = sum(1 for (x, _) in active if x < left_cut) / len(active)
    right_area = max(1, (cw - right_start) * ch)
    right_density = sum(1 for (x, _) in active if x >= right_start) / right_area

    top_cut = max(1, int(ch * STAGE_CARD_COLLAPSE_BAND))
    bot_start = ch // 2
    top_share = sum(1 for (_, y) in active if y < top_cut) / len(active)
    bot_area = max(1, cw * (ch - bot_start))
    bot_density = sum(1 for (_, y) in active if y >= bot_start) / bot_area

    meta["left22_share_pct"] = round(left_share * 100, 1)
    meta["right50_density_pct"] = round(right_density * 100, 3)
    meta["top22_share_pct"] = round(top_share * 100, 1)
    meta["bottom50_density_pct"] = round(bot_density * 100, 3)

    if left_share > STAGE_CARD_COLLAPSE_SHARE and right_density < STAGE_CARD_COLLAPSE_OPP_MAX:
        findings.append((
            "FAIL",
            f"collapsed content inside #stage-card (left 22% holds {left_share * 100:.1f}%, right 50% has {right_density * 100:.2f}%)",
        ))
    elif top_share > STAGE_CARD_COLLAPSE_SHARE and bot_density < STAGE_CARD_COLLAPSE_OPP_MAX:
        findings.append((
            "FAIL",
            f"collapsed content inside #stage-card (top 22% holds {top_share * 100:.1f}%, bottom 50% has {bot_density * 100:.2f}%)",
        ))
    return findings, meta

def rgb_dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

def analyze(path, stage_layout=None):
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
    stage_h = max(1, int(sh * STAGE_TOP_FRAC))
    stage_content = [(x, y, c) for (x, y, c) in content if y < stage_h]
    stage_ratio = len(stage_content) / (sw * stage_h)
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
    elif stage_ratio < EMPTY_RATIO:
        findings.append((
            "FAIL",
            f"empty main stage above caption band ({stage_ratio * 100:.2f}% content in top 83%)",
        ))
    else:
        probe = stage_content if stage_content else content
        xs = [p[0] for p in probe]
        ys = [p[1] for p in probe]
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
        if stage_ratio < SPARSE_STAGE_WARN:
            findings.append(("WARN", f"under-scaled main stage ({stage_ratio * 100:.2f}% content in top 83%)"))
    if ink < 0.00001 and ratio >= EMPTY_RATIO:
        findings.append(("WARN", "no text-contrast pixels detected"))
    stage_card_meta = None
    if stage_layout and _stage_card_probe_enabled():
        sc_findings, stage_card_meta = _probe_stage_card_interior(img, px, sw, sh, stage_layout)
        findings.extend(sc_findings)
    out = {
        "file": os.path.basename(path),
        "dominant": tuple(dominant),
        "content_pct": round(ratio * 100, 2),
        "stage_content_pct": round(stage_ratio * 100, 2),
        "quads_tl_tr_bl_br_pct": quads_pct,
        "ink_pct": round(ink * 100, 3),
        "findings": findings,
    }
    if stage_card_meta is not None:
        out["stage_card"] = stage_card_meta
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videodir", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    snaps = sorted(glob.glob(os.path.join(args.videodir, "snapshots", "frame-*.png")))
    if not snaps:
        print("[verify-pixels] no snapshots/frame-*.png found — run snapshot first", file=sys.stderr)
        sys.exit(2)
    stage_intervals = _load_stage_card_intervals(args.videodir) if _stage_card_probe_enabled() else []
    results, fails = [], 0
    for s in snaps:
        try:
            layout = _stage_layout_for_snapshot(s, stage_intervals)
            r = analyze(s, stage_layout=layout)
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