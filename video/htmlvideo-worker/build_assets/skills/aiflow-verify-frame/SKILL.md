---
name: aiflow-verify-frame
description: "Verify the assembled video: inject transitions, run hyperframes lint/check, snapshot frame midpoints, glance at the contact sheet. Use when index.html and compositions/frames/*.html already exist. Not storyboard, visual design, HTML build, assemble, or final render."
---

# AIFlow verify frame

Verify the assembled video. This skill does **not** own storyboard, visual design, HTML frame builds, assemble `index.html`, or final render. HTML build is `/aiflow-build-frame-html`.

You are the orchestrator. Work in the HyperFrames project root.

---

## Prerequisites

**Must already exist:** `hyperframes.json`, `index.html`, `STORYBOARD.md`, and `compositions/frames/*.html` (from `/aiflow-build-frame-html` + assemble).

If `hyperframes.json`, `STORYBOARD.md` or the frames are missing → stop and report the blocker. Do not invent a storyboard, do not re-init.

If `index.html` is the un-assembled stub → that is **Known failure #1**: apply its one-line recovery, then continue. Do not investigate the stub.

**Out of scope:** `hyperframes init`, brief writing, storyboard narrative/script planning, writing shot sequences / `## Video direction`, writing HTML frames, `frame-packets.mjs`, `audio.mjs`, `hyperframes preview` / `render`. `assemble-index.mjs` runs in the pipeline, not here — the one exception is the recovery in Known failure #1.

---

## Verify

Goal: Verify the assembled video — inject transitions, pass lint/check, snapshot frame midpoints, glance at the contact sheet.

**Inject guard — a project may already be injected, and runs must be idempotent:**

1. Always start with `node /app/scripts/transitions.mjs verify --videodir ${videodir}` (read-only, ~1s);
2. exit 0 → transitions already stamped — **skip `inject`**, go to the full report;
3. output contains `no injected block found` → run `inject` once, then `verify` again (must exit 0);
4. boundary failures (block exists but mismatches) → don't guess; run the full report — its gate and actionable summary decide. Re-`inject` is safe (idempotent since 2026-11-14: re-derives id references and tracks from the current index, removes the stale block first, and frame durations only converge toward the STORYBOARD contract, never drift).

**One-command loop (preferred):** `node /app/scripts/verify-all.mjs --videodir ${videodir}` runs the whole mechanical phase — font-imports (step 0, non-fatal) → inject guard → full report → (on gate pass) midpoints + snapshot + verify-pixels — and returns one verdict. Use it first; fall back to the individual commands below only when its exit code already says which single step to rerun.

**First command is `verify-all.mjs` — no pre-exploration.** This skill's content is already loaded into context, and `verify-all.mjs` prints every finding, the GATE verdict, and the `verify-pixels` result in one shot. Do not `find`/`cat` the skill files, do not `ls` the frames or project first, do not build a task list (it is blocked by the profile anyway).

```bash
node /app/scripts/font-imports.mjs --videodir ${videodir} --scope all   # step 0; also runs inside verify-all (non-fatal)
node /app/scripts/verify-all.mjs --videodir ${videodir}   # exit 0 = gate+snapshot done; 1 = findings, fix & rerun; 2 = blocker
node /app/scripts/verify-report.mjs --videodir ${videodir} --preflight   # ~3s: prerequisites, stub check, frames vs STORYBOARD
node /app/scripts/transitions.mjs verify --videodir ${videodir}          # inject guard above
node /app/scripts/transitions.mjs inject --videodir ${videodir}          # only per guard step 3
node /app/scripts/verify-report.mjs --videodir ${videodir}               # full one-shot diagnosis + gates
AT="$(node /app/scripts/frame-midpoints.mjs --videodir ${videodir} | tail -1)"
npx hyperframes snapshot ${videodir} --at "${AT}"
python3 /app/scripts/verify-pixels.py --videodir ${videodir}
```

`verify-report.mjs` is the single diagnosis round: prerequisites, stub/assembly state, per-frame structural gate (`frame-selfcheck.mjs` verdicts per frame), transitions-verify echo, `lint --json` findings (errors + the two tell warnings per file), `check` findings grouped and annotated, frame midpoints with the ready-to-use `AT=` line. **Exit 0 = gates pass (lint + check + structural gate + transitions) → go straight to snapshot. Exit 1 = findings to fix → playbook below, batched fixes, rerun ONLY the report. Exit 2 = infrastructure error (missing files / CLI crash) → surface the stderr and stop.** The same codes apply to `--preflight`: 0 = proceed, 1 = actionable (stub → Known failure #1 recovery; frames missing vs STORYBOARD), 2 = project blocker. Prefer its findings over any new investigation: spend at most 2 extra investigation turns before editing.

`snapshot` stitches the captured frames into a contact sheet: a single `snapshots/contact-sheet.jpg` when ≤9 frames were captured, otherwise paginated `snapshots/contact-sheet-1..N.jpg` sheets (9 per page) — both forms pass verification, do not rename or merge them. `verify-pixels.py` then runs the deterministic collapse/empty regression over the per-frame PNGs (corner-hug content-bbox check, empty-frame check, ink presence, content quadrant map): **exit 0 → move on; a FAIL → look at that named snapshot only** — never re-derive verdicts with ad-hoc image scripts. If this session's model has vision, also glance at the sheet(s); if it does not, the verify-pixels report IS the glance — don't linger either way.

If a command fails (`transitions inject` / `transitions verify` / `lint` / `check` / `frame-midpoints` / `snapshot`), consult the **Known failures playbook** below first: most failures here have a mechanical fix whose smoking gun is already visible in the output you just ran. Apply the cheapest safe edit to `compositions/frames/NN-*.html`, then rerun ONLY the failed command. A failure not listed in the playbook still gets the same treatment: cheapest safe edit + rerun, under the playbook's discipline rules.

**Gate:** `lint` + `check` + structural gate + transitions all pass (the full `verify-report.mjs` run exits 0) and the snapshots were inspected before render. Stop when the gate passes — do not render.

**Known non-issue — frame `data-duration` differs between a frame file and its `index.html` wrapper.** The transition injector extends the OUTGOING frame's wrapper (and its tail clips) by the transition overlap, by design. A mismatch is NOT a bug; `transitions verify` is the gate. Do not grep or hexdump `data-duration` across frames to "confirm" it — that drift is owned by `transitions.mjs`, not verify.

---

## Known failures — mechanical playbook

**Discipline (applies to every failure in this skill, listed here or not):**

1. **Signals first.** The tell is usually in the output you just ran, before any new investigation: the `verify-report.mjs` findings (warning counts ARE signals — `id_requires_css_escape` is the tell for failure #2, while the report's structural-gate lines are the proof), `check` error names (`console_error`, `content_overlap`, …), and a failing command's own error hint.
2. **One-shot mechanical fixes.** Enumerate every occurrence in ONE grep across all frames — use absolute paths, and cover all three forms `#[0-9]`, `"[#]0[0-9]`, `\.[0-9]` — then fix them all in ONE pass. Multiple edits to the same file in one message are fine as long as the edited ranges do not overlap.
3. **No rabbit-holes.** Do NOT build /tmp probe projects; do NOT bisect hypotheses (cq units, GSAP versions, script placement inside/outside root, `position: relative` vs `absolute`); do NOT read the CLI's source code to understand a failure. These are the highest-cost failure modes observed and are banned here.
4. **Do not chase warnings.** Warnings that are known non-blockers (`google_fonts_import`, and `id_requires_css_escape` once you have confirmed the file has no digit-led *selectors*) are not grounds for edits.
5. **Batch independent tool calls.** In one message, issue every Read/grep/Bash that does NOT depend on another call's output — reading five frames is five parallel Reads in one round, not five rounds. Only a call whose input comes from a previous result waits for that result.
6. **Absolute paths, no cwd trust.** Every Bash command uses absolute paths; the shell cwd can reset between calls in this environment. Need a different directory? `cd /abs/path && …` inside that same compound command, nothing persists.
7. **Trust the merged report — do not re-run its internals.** `verify-report.mjs` already runs the per-frame structural gate (`frame-selfcheck`), `lint --json`, `check`, and `transitions verify` inside ONE command. Re-issuing `npx hyperframes lint` / `check` / `transitions verify` yourself duplicates its work and spawns extra rounds. Run the report, act on its findings, rerun ONLY the report (or `verify-all.mjs`).
8. **Never read `/app/scripts/*.mjs` source.** `transitions.mjs`, `verify-report.mjs`, `frame-midpoints.mjs`, `frame-selfcheck.mjs` are deterministic gates; their behaviour is documented above and in their exit codes. Reading them to "understand" a failure is a rabbit-hole (discipline #3) — the smoking gun is already in the output you just ran.

**1. `transitions inject` fails: `✗ no frame clips found in index.html`**

The index is still the init stub — the pipeline's assemble step never ran. Recovery is deterministic, idempotent, <10s:
`node /app/scripts/assemble-index.mjs --videodir ${videodir}` (absolute path per discipline #6) — then rerun inject. Do none of the following: reading the stub, reading `transitions.mjs` source, `stat` timestamps, reporting a blocker, hand-editing the index.

**2. Frames collapse / `console_error` + `SyntaxError` / a burst of layout errors**

Digit-led id/class selectors (`#07-the-harder-half-p1`, `.01-two-questions-float`, `"#06-…"` inside GSAP selector strings): CSS silently drops the entire rule, `querySelector(All)` throws. The tell: `id_requires_css_escape` warnings — but an id *attribute* that merely starts with a digit is harmless; the proof is the report's structural-gate lines naming actual digit-led **selectors**. Fix (mechanical, all frames listed there, one pass): CSS/JS selector strings → attribute form `[id="07-the-harder-half-p1"]` / `[class~="01-two-questions-float"]`; leave `getElementById` untouched (valid for digit ids) — the tell warnings remain afterwards and are fine. Since 2026-11-14 `/app/scripts/frame-selfcheck.mjs` FAILs this pattern at build time — seeing it at verify means a legacy project or a hand-edited frame; fix, note it in the summary, and move on.

**3. `font_family_without_font_face` errors (didot / bodoni moda / space grotesk)**

`verify-all` step 0 already ran `font-imports.mjs` (idempotent, non-fatal). Hand-fix only residuals it left. A dead fallback such as `Didot` in a non-primary slot of a `font-family` stack → delete that name. A ramp family that is still missing → paste this marked block (both marker lines — the next font-imports run rewrites it in place) at the top of the first `<style>` inside `<template>` (after `@charset` if present):

```css
/* aiflow:font-imports v1 */
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda&family=Space+Grotesk&display=swap');
/* aiflow:font-imports end */
```

Nothing more: do not add `@font-face`, do not search local fonts, do not fetch URL fonts. `google_fonts_import` stays a warning.

**4. `content_overlap` on a designed overlay (e.g. the stamp over card "?" in frame 06)**

Mark the overlapped element with `data-layout-allow-overlap` instead of repositioning. Repositioning breaks the intended design.

**5. `text_box_overflow` of ~1–4px on `#caption-word-*` / `.caption-line` — known false positive, do not chase**

The caption pill uses a deliberately snug `line-height` (set once in `scripts/captions.mjs`) and has no `overflow:hidden`, so a heavy display glyph's ink spills a few px into the pill's own padding — nothing is actually clipped. Do NOT inflate the caption `line-height` (it balloons the pill, which is worse). Act only when the finding names a **frame** element (`#el-NN-*`), not a caption word.

---

## Quick Reference

| Read | When |
| ---- | ---- |
| [`../hyperframes-cli/SKILL.md`](../hyperframes-cli/SKILL.md) | `lint` / `check` / `snapshot` CLI |
| [`../hyperframes-core/references/production-loop.md`](../hyperframes-core/references/production-loop.md) | Verify stage in the production loop |