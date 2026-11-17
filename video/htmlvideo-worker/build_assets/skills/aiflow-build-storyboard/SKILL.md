---
name: aiflow-build-storyboard
description: "Read BRIEF.md (message + intent) plus project visible-text/frame.md, and write STORYBOARD.md plus SCRIPT.md when narration is needed. Use when the project already has BRIEF.md and needs frame-by-frame narrative planning only — not design, audio, or render."
---

# AIFlow build storyboard

Turn the project's brief and source text into an approved frame-by-frame teaching plan. This skill owns **narrative planning only** — not project init, design presets, audio, HTML frames, or render.

You are a story creator. Work in the HyperFrames project root (the directory that already has `BRIEF.md` / `hyperframes.json`). Do not run `hyperframes init`, `build-frame.mjs`, `audio.mjs`, or `hyperframes render`.

> **Programmatic fast path:** when the invoking prompt inlines `references/contract-card.md` plus the project inputs (BRIEF.md / frame.md / visible-text.txt / user_script.txt / VO_MODE), the card IS the contract for this run — skip the Prerequisites and Quick Reference reads entirely, and go straight to composing and writing `STORYBOARD.md` + `SCRIPT.md` in one response. Read the references only when the card was not provided.

---

## Prerequisites

**`BRIEF.md` must already exist** with a non-empty `message` in frontmatter. Read it and ask nothing — the brief is settled. Its `flow` × `storyboard` derive the interaction mode (`../hyperframes-core/references/brief-contract.md` § 1). Shape and field semantics: `../hyperframes-core/references/brief-format.md`.

If `BRIEF.md` is missing or `message` is empty → stop and report the blocker. Do not invent a brief, do not init a project, do not re-interrogate.

From `BRIEF.md` take at least: `message` (thesis), `aspect`, `length`, `language`, `angle`, `audience`, `tone`, `flow`, `storyboard`, plus body sections `## Intent` / `## Customizations` / `## Notes` / `## Assets` when present.

Also read project inputs per `references/story-design.md`: `capture/extracted/visible-text.txt` (information source), `frame.md` (voice/register soft guide), and when present `user_script.txt` + `VO_MODE`.

---

## Storyboard and Script

Goal: Turn the text into an approved frame-by-frame teaching plan.

Read `../hyperframes-creative/references/story-spine.md` (hook language, value-before-evidence, storyboard-as-proposal), `references/story-design.md`, `../hyperframes-animation/blueprints-index.md`, `../hyperframes-core/references/storyboard-format.md`, and `../hyperframes-core/references/script-format.md`. Use them to write `STORYBOARD.md` and, when narration is needed, `SCRIPT.md`.

Use `story-design.md` for the explainer structure (concept / how-to / listicle / story), hook strategy, clarity techniques, emotional beats, the type-enum mapping, and `VO_MODE`. The video's sequence comes from **narrative design, not the input text's paragraph order** — reorder, merge, omit, compress. As a **soft guide**, consult the role→blueprint menu in `../hyperframes-animation/blueprints-index.md`: for each beat, write the voiceover in the shape its candidate blueprint implies and tag that candidate `blueprint:` id when one fits. Teaching truth still decides which beats exist — never force a beat to fit a blueprint, and never invent a beat just because a proven shape is available. Faceless visuals are invented downstream, so frames do **not** carry an asset inventory: leave `asset_candidates` empty unless the user supplied a real `public/<basename>` image. Use the exact required fields from the storyboard and script references.

Write outline-stage frames only: `status: outline`, required narrative fields filled. Do **not** write `## Video direction` or time-coded Scene shot sequences — those belong to `/aiflow-build-frame-visual`.

Write `STORYBOARD.md` and `SCRIPT.md` **in one response**: issue the two Write tool calls together in the same turn (SCRIPT.md whenever any frame has a non-empty `voiceover`). Do not split the two files across turns, do not end the turn between the two Writes, and do not re-read either file after writing — their state is already in context.

Storyboard frontmatter: set `format` from BRIEF `aspect`, copy `message` from BRIEF, set `audience` / `arc` / `mode` (derived) / `music` per the storyboard and story-design references.

After drafting, run the review loop's plan pass — `../hyperframes-core/references/review-loop.md` § 1: open the board (don't ask whether to), present the plan as a proposal, and ask the two questions — approve or change, and **sketches first** (recommended) or skip. Feedback loops through chat or the board's comments file until approved. This is a **checkpoint gate** (brief contract § 1): in autonomous mode there is no board and nothing to ask — post the same summary as a heads-up and proceed.

**Gate:** `STORYBOARD.md` exists, every frame has the required narrative fields, `SCRIPT.md` exists when narration is needed (any frame with a non-empty `voiceover`; both files written in the same response), and the user approved the frame-by-frame plan (autonomous: the summary was posted as a heads-up).

---

## Quick Reference

| Read | When |
| ---- | ---- |
| [`../hyperframes-core/references/brief-format.md`](../hyperframes-core/references/brief-format.md) | `BRIEF.md` shape and lifecycle. |
| [`../hyperframes-core/references/brief-contract.md`](../hyperframes-core/references/brief-contract.md) | Gate types, mode from `flow`×`storyboard`. |
| [`../hyperframes-creative/references/story-spine.md`](../hyperframes-creative/references/story-spine.md) | Hook language, value-before-evidence, proposal shape. |
| [`references/story-design.md`](references/story-design.md) | Explainer story plan (reads visible-text / frame.md). |
| [`../hyperframes-animation/blueprints-index.md`](../hyperframes-animation/blueprints-index.md) | Soft role→blueprint menu. |
| [`../hyperframes-core/references/storyboard-format.md`](../hyperframes-core/references/storyboard-format.md) | Write `STORYBOARD.md`. |
| [`../hyperframes-core/references/script-format.md`](../hyperframes-core/references/script-format.md) | Write `SCRIPT.md` when narration is needed. |
| [`../hyperframes-core/references/review-loop.md`](../hyperframes-core/references/review-loop.md) | Plan-pass checkpoint / autonomous heads-up. |
