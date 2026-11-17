---
name: aiflow-build-frame-html
description: "Dispatch per-frame workers to write compositions/frames/*.html from a visually designed STORYBOARD.md. Use when STORYBOARD.md already has time-coded shot sequences and ## Video direction. Not visual design, storyboard narrative, audio, transitions inject, or final render."
---

# AIFlow build frame HTML

Build every storyboard frame as an HTML composition. This skill does **not** own visual design (shot sequences), narrative planning, audio, transitions inject, or final render. 

You are the orchestrator. Work in the HyperFrames project root. Do not put design or motion rules here beyond what workers need; those live in `sub-agents/frame-worker.md`, `references/cut-catalog.md`, `../hyperframes-animation/rules-index.md` and `../hyperframes-animation/rules/`.

---

## Prerequisites

**Must already exist:**

- `hyperframes.json`, `frame.md`, and a `STORYBOARD.md` that has already passed: every frame has a time-coded shot sequence with invented `focal`/`roles`, and a video-wide `## Video direction` block exists.
- `.hyperframes/frame-packets/` — already built upstream (pipeline runs `frame-packets.mjs` before this skill). One bounded packet per frame (the frame's exact storyboard block + the blueprint body + every cited rule recipe, inlined) and `_role.md` (`../hyperframes-core/references/frame-worker-core.md` + this skill's `sub-agents/frame-worker.md`, concatenated verbatim — the complete worker role).

If `STORYBOARD.md` or `frame.md` is missing, or visual design is incomplete (no `## Video direction`, missing shot sequences) → stop and report the blocker. Do **not** invent or fill in visual design yourself.

If `.hyperframes/frame-packets/` or `_role.md` is missing, or a storyboard frame has no matching packet → stop and report the blocker. Do **not** run `frame-packets.mjs` in this skill.

**Out of scope:** `hyperframes init`, brief writing, storyboard narrative/script planning, writing shot sequences / `## Video direction`, `frame-packets.mjs`, `audio.mjs`, transitions inject, `hyperframes lint` / `check` / `preview` / `render`.

---

## Build Frames

Goal: Build every storyboard frame as an HTML composition and assemble the playable video.

Before dispatch, read `references/subagent-dispatch.md`. Consume the pre-built packets under `.hyperframes/frame-packets/` — do not rebuild them. Dispatch one sub-agent per frame — **every worker as `subagent_type: frame-html-worker`**, the project-local agent definition (`.claude/agents/frame-html-worker.md`, effort pinned to `high`; the built-in catch-all agent bypasses the per-worker effort cap and must never be used) — in parallel if possible; otherwise run workers in waves. If `.claude/agents/frame-html-worker.md` is missing, create it first from `references/frame-html-worker.md`. Each worker gets exactly one frame: its prompt carries `_role.md` and that frame's packet — paste both in full, or hand the two file paths for the worker to read first (equivalent; the worker starts from exactly those two documents either way) — plus a dispatch context with `PROJECT_DIR`, `frame_id`, the absolute path to `references/cut-catalog.md` (this skill's copy — the one skill file a worker may open, when a Scene seam cites a within-scene cut), canvas size, and caption status + keep-out band if captions are enabled. Convention rulings (fonts, GSAP pin, template/root shape, units — `_role.md` § Ruled conventions) are **closed**: do not restate or re-argue them in the dispatch prompt; state only genuine per-project facts.

Workers read only their packet and `frame.md`; they never open `STORYBOARD.md` or the skill documents (the packet inlines what was selected upstream) — the one sanctioned exception is the cut catalog named in their dispatch context. Each worker writes only `compositions/frames/NN-*.html`. Workers must never edit `STORYBOARD.md`.

**Full-bleed backgrounds ride on a `class="clip"` layer, never the `#root`.** A frame's ground (color field / gradient / grid) is **one** full-duration background clip on the lowest content track — a `background` set on the `#root` / `data-composition-id` element is clip-gated to the frame's window and is not a dependable ground, so dark content can land on the black host `body` and render invisible. Other full-duration ambience nests inside that clip or uses higher tracks (never N sibling clips overlapping on the same `data-track-index`). The video's base ground is painted by the assembler from `frame.md`'s `canvas` color onto the index `#root`. (Full rule + self-check: `../hyperframes-core/references/frame-worker-core.md`.)

**Workers self-verify — trust their gate.** Each worker ends with a one-line verdict after passing its own structural gate (`done OK <frame_id>`) or failing it (`fail <frame_id>: <reason>`). Do **not** re-run per-frame checks or edit frame files on a worker's behalf — `done OK` plus the artifact on disk is the completion signal. As `done OK` frames land, mark them `animated` in `STORYBOARD.md` without re-verifying; a `fail` reply or a missing artifact marks that frame broken.

**Collect failures, then re-dispatch the whole batch in parallel.** After all workers have returned (or activity dies down), do **one** sweep — list `compositions/frames/*.html` and diff against every frame in `STORYBOARD.md` — and gather **every** broken frame (missing artifact or `fail` reply) into a single list. Re-dispatch the **entire list in one parallel wave** (same prompt, plus the prior failure reason so it is not repeated). Repeat this collect-then-batch-re-dispatch at most **two more rounds**; if any frame is still broken after two retry rounds, stop and report the blocker with the worker's last failure reason — do not loop, and do not hand-fix frames yourself to paper over a broken worker.

If the assemble command fails, surface stderr and stop — don't pile on recovery commands. Fix it yourself: the cheapest safe edit to `compositions/frames/NN-*.html`, then rerun assemble.

**Gate:** every frame is marked `animated`.

---

## Quick Reference

| Read | When |
| ---- | ---- |
| [`references/cut-catalog.md`](references/cut-catalog.md) | Within-frame seams (worker) |
| [`../hyperframes-animation/rules-index.md`](../hyperframes-animation/rules-index.md) + [`../hyperframes-animation/rules/`](../hyperframes-animation/rules/) | Motion recipes for workers |
| [`sub-agents/frame-worker.md`](sub-agents/frame-worker.md) | Per-frame worker role (delta — the shared core contract is prepended into `_role.md`) |
| `node /app/scripts/frame-selfcheck.mjs <frame>.html` | Worker self-gate (structural lint subset) |
| [`references/subagent-dispatch.md`](references/subagent-dispatch.md) | Safe sub-agent dispatch |
| [`references/frame-html-worker.md`](references/frame-html-worker.md) | Worker agent definition template (effort-pinned; copy to `.claude/agents/` if missing) |
