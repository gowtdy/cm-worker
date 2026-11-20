---
name: aiflow-build-frame-visual
description: "Enrich STORYBOARD.md with time-coded shot sequences and a ## Video direction block. Use when outline STORYBOARD.md and frame.md already exist. Not HTML frames, storyboard narrative, audio, assemble index, or final render."
---

# AIFlow build frame visual

Frame visual design only: add time-coded shot sequences and video-wide direction to `STORYBOARD.md`. This skill does **not** write HTML, dispatch workers, own narrative planning, audio, assemble `index.html`, or final render. HTML composition is `/aiflow-build-frame-html`.

You are the orchestrator. Work in the HyperFrames project root. Do not put design or motion rules here beyond what this skill cites; those live in `references/` and `../hyperframes-animation/`.

> **Driver fast path (design phase / per-frame worker):** if the invoking prompt
> identifies you as a *single role* — the **design phase** («You are the GLOBAL design
> phase only») or a **per-frame worker** («You are ONE per-frame worker», inputs
> inlined) — then you are **not the orchestrator** and this skill's orchestrator flow
> (re-run guard, reference reads, full-board Write, gate) does **not** apply. Do NOT
> read the reference files, do NOT search the filesystem, do NOT write `STORYBOARD.md`,
> do NOT run `storyboard-gate.mjs` / `caption-pacing.mjs`. Output exactly the requested
> text (the contract's creative body, or your frame's `blueprint`/`focal`/`roles`/`sfx`/
> `Scene` lines) as your final reply — the driver merges and gates.

---

## Prerequisites

**Must already exist:** `hyperframes.json`, `frame.md`, and an outline-stage `STORYBOARD.md` (from `/aiflow-build-storyboard`: narrative fields filled, `status: outline`).

If `STORYBOARD.md` or `frame.md` is missing → stop and report the blocker. Do not invent a storyboard, do not init a project, do not re-interrogate.

**Re-run guard (run FIRST, before reading or writing anything):** `node /app/scripts/storyboard-gate.mjs --videodir ${videodir}`
- ✗ `F1 BLOCKER: … already animated` → the design was already consumed by `/aiflow-build-frame-html`. STOP and report — never regenerate (a rewrite would silently diverge the storyboard from the built frames on disk);
- ✓ `design already complete` → a previous run finished the design — verify-only idempotent skip, do not rewrite;
- otherwise (outline board, findings) → proceed to design.

**Out of scope:** `hyperframes init`, brief writing, storyboard narrative/script planning, writing HTML, dispatching frame workers, `audio.mjs`, assemble `index.html`, transitions inject, `hyperframes lint` / `check` / `preview` / `render`.

---

## Frame Visual Design

Goal: Add the visual direction, layout intent, and motion choices to each storyboard frame.

**Skip the sketch pass.** This skill runs without collaborative layout review — do not wireframe frames, do not mark `built`, do not wait for sketch confirmation. Write the visual design below directly onto the outline frames (autonomous mode).

Compose the **entire** update — all frames' shot sequences plus `## Video direction` — in one pass, then land it with **ONE `Write` of the complete new `STORYBOARD.md`**. Never per-frame `Edit` appends: a past run re-transcribed its finished draft through 15 sequential Edits ≈ 2.3m of pure round-trip overhead, while one Write ≈ 0.1m. Reserve `Edit` for a single spot-fix AFTER the write, and it must carry an exact `old_string`. Do not create another storyboard. Use `frame.md` as source of truth for color, type, layout feel, and style.

**Preserved fields are byte-exact.** A full-file rewrite re-types everything, so drift is a real risk: `scene`, `voiceover`, `duration`, `transition_in`, `status`, `src`, `type`, `beat`, and every other narrative bullet must be reproduced **character-for-character** from the Read you made. The gate script checks `status`/`transition_in`/`src`/`duration`; you check the rest against the gathered copy before writing.

Gather every input in **≤3 batched rounds**: one message can carry many parallel Reads — take the project files (`STORYBOARD.md`, `frame.md`, `SCRIPT.md`, `BRIEF.md`, `hyperframes.json`) as one batch, the four reference/index files as a second. No read-one-then-ponder loops; re-read only when the design genuinely needs a detail.

Read `references/visual-design.md`, `../hyperframes-animation/blueprints-index.md`, `references/motion-language.md`, and `../hyperframes-animation/rules-index.md`. Use `visual-design.md` for the method (the time-coded shot sequence, the inline Layout vocabulary, and the invented-visual treatment), plus the required `## Video direction` block. Use `../hyperframes-animation/blueprints-index.md` to pick each frame's shot shape. Use `motion-language.md` (the motion vocabulary + the motion doctrine) and `../hyperframes-animation/rules-index.md` (valid rule names) for motion — do not invent motion names.

For every frame, write a **time-coded shot sequence** into `STORYBOARD.md` per `visual-design.md`'s method: pick the frame's blueprint (or compose), instantiate it with THIS frame's **invented** content, and pace each Scene's reveal to the voiceover so the frame develops across its full duration instead of front-loading then freezing. Because visuals are faceless/invented, `focal`/`roles` name the **invented visual elements** (a hero word, a diagram node, a data-viz series) — you are designing them, not selecting captured assets. State layout and motion **inline** per Scene (vocabularies in `visual-design.md` and `motion-language.md`). Add one video-wide `## Video direction` block.

If `caption_groups.json` exists, run `node /app/scripts/caption-pacing.mjs --videodir ${videodir}` ONCE and pace each Scene's reveal to its per-frame VO table. Do NOT parse it with `jq` (not installed in this environment) or hand-written inline scripts — the observed failure chain is command-not-found → heredoc syntax error → third attempt.

Do not change story, script, `transition_in`, `status`, or the source text. Do not write HTML. There is **no asset-staging step** — visuals are built by `/aiflow-build-frame-html` workers. If the user supplied a real `public/<basename>` image, reference it by path in the relevant frame's `focal`/`roles`; otherwise nothing to stage.

**Gate:** after the single Write, run `node /app/scripts/storyboard-gate.mjs --videodir ${videodir}` — deterministic (~1s): per-frame Scene count/window monotonicity/coverage ≥90% of duration (no front-loading), `focal`/`roles`, `## Video direction`, `status: outline`, valid `transition_in`/`src` line. Expected findings, NOT defects: `src not on disk` ⚠ on every frame — the `.html` is the product of the NEXT stage (`/aiflow-build-frame-html`), so never "fix" it by creating HTML here (only a missing/malformed `- src:` line is a real ✗) — and unknown backtick motion names ⚠ (review). On exit 0 run the model-only pass on the just-written file; the gate already did the structural sweep, so the full-file Read is NOT mandatory — gate + targeted grep on `STORYBOARD.md` (verbatim VO quotes, scene-to-cue alignment, preserved-field byte-exactness) is the observed best pattern, equally rigorous and cheaper. Fix only verified gaps with targeted Edits, rerun the script once, then stop — do not build HTML.

---

## Quick Reference

| Read | When |
| ---- | ---- |
| [`references/visual-design.md`](references/visual-design.md) | Shot sequence + Layout vocabulary |
| [`references/motion-language.md`](references/motion-language.md) | Motion vocabulary + doctrine |
| [`../hyperframes-animation/blueprints-index.md`](../hyperframes-animation/blueprints-index.md) | Pick shot shape |
| [`../hyperframes-animation/rules-index.md`](../hyperframes-animation/rules-index.md) + [`../hyperframes-animation/rules/`](../hyperframes-animation/rules/) | Valid motion names |
| `/app/scripts/caption-pacing.mjs --videodir ${videodir}` | Per-frame VO pacing table (run once — no jq, no inline scripts) |
| `/app/scripts/storyboard-gate.mjs --videodir ${videodir}` | F1 re-run guard + post-write structural gate (run before AND after; `src not on disk` ⚠ is expected at this stage) |
