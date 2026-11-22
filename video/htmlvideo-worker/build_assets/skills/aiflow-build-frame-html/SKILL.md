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
   Bash/Glob/Grep denied (driver owns the gate). Per-frame pipeline
   (`as_completed`, no wave barrier): each frame has its own attempt counter
   and its retry is submitted the moment it fails; no new attempt starts after
   `HTMLVIDEO_FRAME_HTML_STAGE_DEADLINE` (default 2400s).
   `HTMLVIDEO_FRAME_HTML_WAVE_MODE=1` restores the old wave loop.
3. After each worker return the driver runs
   `node frame-selfcheck.mjs compositions/frames/<frame_id>.html`. A frame is
   **done** iff rc==0 ∧ `done OK <frame_id>` ∧ selfcheck PASS ∧ file on disk.
   Artifact existence / skeleton selfcheck alone is NOT completion.
4. Failures (missing done OK, non-zero rc, or selfcheck FAIL) are re-dispatched
   with findings appended — bounded by
   `HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS` (default 3). Then raise — never unbounded.
   - Truncation early-kill: each worker runs with a fixed `--session-id`; the
     driver tails its session jsonl and kills a thinking-only output-cap turn
     (rc 125). The retry after rc 125 runs with a 64000 output cap.
     `HTMLVIDEO_FRAME_HTML_TRUNC_KILL=0` disables.
   - Salvage: rc 124/125 with a changed frame file that passes selfcheck gets a
     short finish pass (current file inlined; 16000 cap / 10 turns / 300s)
     instead of a fresh re-dispatch. `HTMLVIDEO_FRAME_HTML_SALVAGE=0` disables.
5. Mark landed frames `animated` in STORYBOARD as each frame completes
   (regex, deterministic, serialized by a lock).
6. Assemble via `assemble-index.mjs`; on failure run ONE `repair`-profile
   session (cheapest safe structural edit to frame files only), re-assemble
   once, else raise with stderr.

## Worker contract (sub-agents/frame-worker.md + frame-worker-card.md)

Driver inlines `.dispatch.md` into the worker prompt (do not Read it again).
The dispatch inlines the rule card, storyboard block, selected blueprint and
frame.md token excerpt — rule recipes are **not** inlined, only indexed: in
the first tool turn the worker Reads every `RULES_DIR/<id>.md` in the Rule
index as one parallel batch. Then FILL the pre-built skeleton at
`compositions/frames/<frame_id>.html` (never write a new skeleton): chunked
Edit/Write style → body → script, each ≤ ~20 KB, first edit by turn 2 →
reply `done OK <frame_id>` (or `fail <frame_id>: <reason>`). Do **not** run
`frame-selfcheck` or Bash — the driver runs the gate and re-dispatches with
FAIL findings when needed. Cut catalog only for within-scene cut seams; never
open `frame.md` / `_role.md`.

Conflict priority (higher wins): Scene list > `## Video direction` / design
contract > blueprint body > frame.md narrative prose (token values exact).
When a block carries several `- blueprint:` lines, the packet builder picks
the last `(Adapt|Reproduce)` one and names it under `## Selected blueprint`.

## Gates

Every frame `done OK` ∧ `status: animated` ∧ `compositions/frames/*.html`
non-empty → `assemble-index.mjs` → (verify stage owns the rest).
