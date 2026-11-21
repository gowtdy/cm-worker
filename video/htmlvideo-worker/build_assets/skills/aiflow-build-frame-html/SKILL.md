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
   cap/turns/timeout from the `frame_html` claude profile (48000/60/1500);
   Bash/Glob/Grep denied (driver owns the gate).
3. After each worker return the driver runs
   `node frame-selfcheck.mjs compositions/frames/<frame_id>.html`. A frame is
   **done** iff rc==0 ∧ `done OK <frame_id>` ∧ selfcheck PASS ∧ file on disk.
   Artifact existence / skeleton selfcheck alone is NOT completion.
4. Failures (missing done OK, non-zero rc, or selfcheck FAIL) are re-dispatched
   in parallel with findings appended — bounded by
   `HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS` (default 3). Then raise — never unbounded.
5. Mark landed frames `animated` in STORYBOARD (regex, deterministic).
6. Assemble via `assemble-index.mjs`; on failure run ONE `repair`-profile
   session (cheapest safe structural edit to frame files only), re-assemble
   once, else raise with stderr.

## Worker contract (sub-agents/frame-worker.md + frame-worker-card.md)

Driver inlines `.dispatch.md` into the worker prompt (do not Read it again) →
fill `compositions/frames/<frame_id>.html` (skeleton on disk): chunked
Edit/Write style → body → script, each ≤ ~20 KB, first edit in 1–2 turns →
reply `done OK <frame_id>` (or `fail <frame_id>: <reason>`). Do **not** run
`frame-selfcheck` or Bash — the driver runs the gate and re-dispatches with
FAIL findings when needed. Escape hatches: cut catalog / `RULES_DIR/*.md`
only when a cited recipe is missing from the inlined dispatch; never open
`frame.md` / `_role.md`.

## Gates

Every frame `done OK` ∧ `status: animated` ∧ `compositions/frames/*.html`
non-empty → `assemble-index.mjs` → (verify stage owns the rest).
