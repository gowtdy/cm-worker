---
name: aiflow-build-frame-html
description: "Contract for the deterministic frame fan-out driver: per-frame workers fill pre-built compositions/frames/*.html skeletons from a merged .dispatch.md. Use when STORYBOARD.md has time-coded shot sequences and ## Video direction. Not visual design, narrative, audio, transitions inject, or render."
---

# AIFlow build frame HTML

This skill is the **executable contract** for the build-frame-html stage. The
Python driver (`html_utility/frame_html.py`) implements it deterministically;
the per-frame `claude` workers implement the filler contract below. No
orchestrator LLM exists in this stage. Design and motion rules live in
`sub-agents/frame-worker-card.md`, the full `_role.md`, `references/cut-catalog.md`,
and `../hyperframes-animation/`.

## Prerequisites

- `hyperframes.json`, `frame.md`, `STORYBOARD.md` (shot sequences + `## Video direction`) exist.
- `frame-packets` ran with dispatch ver 2: for every frame —
  `.hyperframes/frame-packets/<frame_id>.dispatch.md` (merged input: rule card +
  storyboard block + blueprint + rule index + frame.md excerpt) and
  `.hyperframes/frame-packets/<frame_id>.md` (legacy packet, rollback only).
- M3 (skeleton on): `compositions/frames/<frame_id>.html` pre-exists, marked
  `<!-- skeleton-v1 -->`, passing `frame-selfcheck.mjs`.
- Missing either → the driver raises; **do not build packets inline here**.

## Driver spec (frame_html.py — the executable)

1. Enumerate frames from `.hyperframes/frame-packets/*.md` (skip `_role.md`,
   `*.dispatch.md`).
2. Fan out one `claude -p` worker per frame, concurrency
   `HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY` (default 16); per-worker env:
   cap/turns/timeout from the `frame_html` claude profile (64000/60/1500).
3. A frame is **done** iff rc==0 ∧ result contains `done OK <frame_id>`
   (skeleton off additionally requires the file on disk). Artifact existence
   alone is NOT a completion signal while skeletons pre-exist.
4. Collect every failure, re-dispatch the whole failing batch in parallel with
   the failure reason appended, at most 1 round. Then raise — never hand-fix.
5. Mark landed frames `animated` in STORYBOARD (regex, deterministic).
6. Assemble via `assemble-index.mjs`; on failure run ONE `repair`-profile
   session (cheapest safe structural edit to frame files only), re-assemble
   once, else raise with stderr.

## Worker contract (sub-agents/frame-worker.md + frame-worker-card.md)

Read `.dispatch.md` fully (the only required input) → fill the
`compositions/frames/<frame_id>.html` file —— skeleton already on disk:
chunked edits style → body → script, each ≤ ~20 KB, first edit in 1–2 turns →
`node /app/scripts/frame-selfcheck.mjs` → fix all FAILs → reply
`done OK <frame_id>` (or `fail <frame_id>: <reason>`). Escape hatches:
`_role.md` (full contract), `frame.md`, cut catalog, `RULES_DIR/*.md` — open
only when a dispatch rule is unclear.

## Gates

Every frame `done OK` ∧ `status: animated` ∧ `compositions/frames/*.html`
non-empty → `assemble-index.mjs` → (verify stage owns the rest).
