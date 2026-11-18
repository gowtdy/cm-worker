# Frame worker — aiflow-build-frame-html delta

> Filler contract for the aiflow frame worker. The packet builder prepends
the shared contract (`../hyperframes-core/references/frame-worker-core.md`)
to form the full `_role.md`; the dispatch `.dispatch.md` carries the compact
rule card. This delta covers what is aiflow-specific: invented visuals, the
`done OK` reply, root `data-duration`, same-track density, the frame file that
already exists on disk.

## INPUT — aiflow fields

The shared contract's INPUT applies in full. Your packet carries everything selected upstream — the frame's exact `## Frame N` block (`## Assigned storyboard block`), the inlined blueprint body (`## Selected blueprint: <id>`), a rule INDEX (`## Rule index`: each cited motion's path under `RULES_DIR/`) — open a recipe file only while authoring its phase; no recipe bodies are inlined — **never open `STORYBOARD.md` or the skill documents**. The one sanctioned exception is the cut catalog: your dispatch context carries its file path — open **only** that file, and only when a Scene seam is a within-scene swap / scene-to-scene cut / text-to-text line change (on the shared contract's terms). The aiflow-specific fields:

  **Your dispatch document is the single required input**
(`.hyperframes/frame-packets/<frame_id>.dispatch.md`): it inlines the rule
card, your storyboard block, the blueprint, a rule INDEX (recipes are opened
on demand from `RULES_DIR` while you author each phase), and the frame.md
design-token excerpt. The full `_role.md` and `frame.md` are escape hatches —
open them only when a dispatch rule is unclear.

- `focal:` — which **invented** element is the hero.
- `roles:` — each invented element's role: `foreground subject` / `background` full-bleed / `supporting`. Because the explainer is **faceless, these are elements you design** (a hero word, a diagram node, a chart series, a coined-term card), not captured assets. The **only** real media is a user-supplied image, when present: `public/<basename> — description` (a **`[video]`** tag marks a `.mp4` clip the user provided).

## Retry — addition

A re-dispatch in this workflow may also carry `frame-selfcheck` output — the per-frame script gate from § Self-check below. Same treatment as the shared `lint` / `check` findings: read it first and re-author so none of those findings recur.

## OUTPUT — `done OK` protocol (Override)

**Override** of the shared contract's "do not report back": after your self-check gate passes and your eyeball re-read is clean, **reply `done OK <frame_id>`** — that reply is your terminal action (the orchestrator trusts the reply plus the artifact on disk; it does **not** re-verify or edit your frame). If you cannot reach PASS after fixing, reply `fail <frame_id>: <what is still broken>` instead of leaving a silent half-state.  **The frame file already exists.** In this workflow the packet builder
pre-writes a valid skeleton (`<!-- skeleton-v1 -->`, passes frame-selfcheck)
to `compositions/frames/<frame_id>.html` before you start — fill it with
chunked Edits (style → body → script); you may add/remove nodes or rewrite
the file wholly (last write wins). A file on disk is therefore your working
document, never proof of completion — your `done OK` reply is. The artifact itself follows the shared contract exactly (one bare `<template>…</template>` at `compositions/frames/<frame_id>.html`). (Sketches do not occur in this pipeline — the visual skill skips the sketch pass — so the shared contract's sketch section never applies; a file already on disk at dispatch is the skeleton — plus your own edits if this is a retry.)

## You do NOT decide — addition

- **Which visuals exist** — you design the invented visuals the Scene lines describe (typography / abstract graphics / diagrams / data-viz); the only real media is the user-supplied `public/<basename>` named in `roles:` / `focal:`. Never reference any other file or URL. (The rest of the shared contract's list stands unchanged.)

## Ruled conventions — settled, do not re-derive

Pre-ruled to stop deliberation stalls (observed: a worker burned two full 32k-token turns debating fonts and produced nothing). **Do not weigh, compare, or re-derive any of these — accept the ruling and build.** Where a ruling contradicts the shared contract (or a bullet in this file), **the ruling wins**; contradictions are marked **Override**.

- **Fonts (Override of the shared contract's "fonts ship as .woff2 files" bullet).** Font delivery is **the render compiler's job, not yours** — at render time it resolves every `font-family` you declare and injects data-URI `@font-face` rules from the runtime's **embedded font bundle** (this pipeline's build image embeds every font its design presets use) and fetches anything else from Google Fonts with a local cache. Your file only needs to satisfy the **static lint gates**:
  - `font_family_without_font_face` — every used family needs a matching `@font-face` **or** a Google Fonts `@import` / `<link>` in this file **or** a bundled/aliased name;
  - `google_fonts_import` — **warning only**.
  Concretely: **when every family you use is one `frame.md` declares — the normal case, since its whole ramp comes from the design presets, which the build image embeds — omit the `@import` entirely** and just write the family names verbatim (the lint passes for bundled names and you avoid the `google_fonts_import` warning). Ship an `@import` **only** when the frame names a family from **outside** `frame.md`'s ramp (e.g. a brand override); list exactly those families, illustration:
  ```css
  @import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,400..800&family=Space+Grotesk:wght@400..700&display=swap');
  ```
  Do **not** search for local font files (`assets/fonts/` may not exist — one `ls` is final) and do **not** debate any URL — the omission/import decision above is mechanical, not judgmental. `frame.md`'s ramp assigns families to slots (reading ramp vs display ramp) — follow it verbatim. The determinism "no network fetches" constraint governs **your JS runtime logic**; the compiler owns font fetches. This is closed.
- **GSAP.** Always ship `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>` **inside the `<template>`**, version pinned at `3.14.2`. Do not grep siblings to compare versions. Register exactly one `gsap.timeline({ paused: true })` at `window.__timelines["<frame_id>"]`.
- **Template / root shape.** Bare `<template>…</template>`; every data attribute on the root: `<div id="root" data-composition-id="<frame_id>" data-width="1920" data-height="1080" data-duration="<block duration verbatim>">`. Style via the `#root` selector, never a class.
- **Units.** Primary unit `cqw` / `cqh` — `frame.md`'s entire type ramp is cqw-based (at 1920-wide containers, 1cqw = 19.2px). Fixed-size décor (4/6/8/12px shadows, dots, strokes) uses px. Both pass lint; pick per element and move on — never build conversion tables.
- **Turn discipline.** Every turn ends with a tool call or visible text; never end a turn with thinking only (a silent turn is discarded and re-run from scratch). If something is not decided upstream, pick the sibling-consistent choice and proceed **within this turn**; do not deliberate for more than ~a minute.
- **Skeleton fill (Override).** The skeleton already exists on disk — your FIRST edit must land within your first 1–2 turns, flesh it out with chunked Edits after that. Never burn a turn planning a whole frame before the first edit; the file is your heartbeat, keep it valid at every step.
- **Chunk large writes.** A Write input over ~20 KB gets truncated into an InputValidationError (observed). Author a full frame as: first write style + structure, then append the body, then the script block.

## Frame constraints (aiflow deltas)

The shared contract's constraints apply in full; these are the aiflow-specific ones on top:

- **Design each element by its `roles`** (the `focal` is the hero): a `foreground subject` is the thing the eye lands on — respect the 83% keep-out, lay text around it, not over it; a `background` is full-bleed and dimmed ~30–50% so foreground content stays legible; `supporting` elements (labels, secondary shapes, ambient layers) stay quiet. These are **invented** — you build them in SVG / CSS / type, not from a file. **If the user supplied a real image** named in `roles:` / `focal:`, place it — a `[video]` candidate (`.mp4`) renders as a **muted** `<video class="clip">` (`data-start` / `data-duration` / `data-track-index` per the shared clip contract), a **direct child of the frame root** — never nested in another timed element, or the renderer freezes it; an untagged image → `<img>`.
- **Same-track clips must not overlap in time** (`timeline_track_too_dense`). `data-track-index` is a temporal lane, not paint order — stacking is CSS `z-index`. Concurrent layers (background + floating pills + hero) need **distinct tracks**, **or** nest non-timed décor inside **one** parent `class="clip"` (children: no `data-start` / `data-duration` / `data-track-index`). Prefer nesting full-duration ambient wallpaper (pills / stickers / grain extras) inside the background clip rather than N sibling full-duration clips on track `0`.
- **Glow / badge décor is not a second timed sibling on the same track.** Soft glow, bloom, punch-glow, badge-glow, and accent lines that sit *with* a hero/badge are concurrent layers. Model them one of two ways — never as another `class="clip"` sharing the hero's `data-track-index` while windows overlap:

  - **Prefer nest:** one parent `class="clip"` owns the time window; glow/badge children have **no** `data-start` / `data-duration` / `data-track-index` (matches `ambient-glow-bloom`). Drive their motion with GSAP on the parent timeline.
  - **Or split tracks:** each concurrent timed element gets its own `data-track-index`; stacking is CSS `z-index`, not track number.
  - Side-by-side badges that are both on screen at once (e.g. left + right) **must** use distinct tracks (or one parent clip wrapping both). A comment like `<!-- Track 3: glow + accent -->` does **not** make same-track overlap legal.

  **Wrong** — glow/accent as timed siblings on the same track (assemble fails with `timeline_track_too_dense`):

  ```html
  <div id="badge" class="clip" data-start="5.5" data-duration="1.5" data-track-index="3">✓ PASS</div>
  <div id="badge-glow" class="clip" data-start="5.5" data-duration="1.0" data-track-index="3"></div>

  <div id="punch-glow" class="clip" data-start="2.5" data-duration="1.0" data-track-index="3"></div>
  <div id="accent" class="clip" data-start="3.0" data-duration="1.0" data-track-index="3"></div>
  <!-- 3.0–3.5 overlaps even if you meant "Track 3 group" -->

  <div id="badge-l" class="clip" data-start="4.2" data-duration="1.8" data-track-index="5">Good co.</div>
  <div id="badge-r" class="clip" data-start="2.8" data-duration="3.2" data-track-index="5">Worth it</div>
  ```

  **Right A — nest décor (preferred for glow):**

  ```html
  <div id="badge-wrap" class="clip" data-start="5.5" data-duration="1.5" data-track-index="3">
    <div id="badge-glow"></div>
    <div id="badge">✓ PASS</div>
  </div>
  ```

  **Right B — distinct tracks:**

  ```html
  <div id="badge" class="clip" data-start="5.5" data-duration="1.5" data-track-index="3">✓ PASS</div>
  <div id="badge-glow" class="clip" data-start="5.5" data-duration="1.0" data-track-index="4"></div>

  <div id="badge-l" class="clip" data-start="4.2" data-duration="1.8" data-track-index="5">Good co.</div>
  <div id="badge-r" class="clip" data-start="2.8" data-duration="3.2" data-track-index="6">Worth it</div>
  ```
- **Root must carry `data-duration` (aiflow / transitions inject).** HyperFrames-core allows omitting root `data-duration` when a GSAP timeline is present; **this workflow does not**. The frame root (`id="root"`) must declare `data-duration="<packet duration>"` (the block's `duration` verbatim) alongside `data-composition-id="<frame_id>"`. Downstream `transitions.mjs inject` extends that attr (and tail clips) on outgoing frames — a root without `data-duration` fails inject.

## Workflow (shared steps apply — aiflow changes)

1. **Read** — the shared step applies; read from your **packet**, not the skill files: frame block under `## Assigned storyboard block`, blueprint body under `## Selected blueprint: <id>` (absent when `compose`), each cited motion's recipe path — open the recipe at `RULES_DIR/<id>.md` (the dispatch's Rule index lists them) while you author that phase. The structural law is restated in the shared contract above — do **not** open `hyperframes-core/SKILL.md` or the skill catalog.
2. **Design** — the shared step applies; it is this workflow's job to **invent** the visual elements the Scene lines describe (the diagram, the hero word, the metaphor) — a visual idea that reinforces the beat, not a literal restyle of the words.
3. **Author** — the shared step applies, with one change: the root also carries **`data-duration="<duration>"`** (packet `duration` verbatim — see Frame constraints above).  (the root already carries `data-duration` — the skeleton stamped it; keep it).
4. **Self-check, then finish** — shared step applies, plus the script gate and delta additions below; **replying `done OK <frame_id>`** (or `fail …`) is your terminal action.

## Self-check (script gate + delta additions)

Run the script gate first: `node /app/scripts/frame-selfcheck.mjs compositions/frames/<frame_id>.html` — it enforces the deterministic structural subset (template wrapper/bounds, root attrs, clip attrs, same-track overlap, paused-registered timeline) with `hyperframes lint`'s rule codes, and fails fast where the assembled-project gate would be a false green. You **can't** meaningfully run `hyperframes lint` / `check` here: they operate on the **assembled project** (the `index.html` graph / bundle), and your frame isn't wired in yet — so they report on *other* files, not yours. (**Override** of the shared contract's runner: in this workflow the orchestrator does not assemble or lint — those gates run downstream after assembly, and a failed frame comes back as a re-dispatch with the finding, same as the shared Retry path.) Re-read your file against the **shared checklist first** (the shared contract's self-check above — every item there applies), then the aiflow additions below; the codes in parens are `hyperframes lint`'s and what a re-dispatch may cite back:
  The skeleton guaranteed `missing_template_wrapper` / root attrs /
paused-registered timeline before you started — do not regress them.

- `missing_root_duration` — extends the shared first checklist item: the frame root must carry **all three** of `id="root"`, `data-composition-id="<frame_id>"`, and `data-duration="<duration>"` (the block's `duration` verbatim). Example: `<div id="root" data-composition-id="01-hook" data-width="1920" data-height="1080" data-duration="5">`. The shared contract allows omitting root `data-duration` when a GSAP timeline is present; **this workflow does not** — see Frame constraints (a root without it fails `transitions inject` on outgoing frames).
- **Full-bleed extension** — the shared full-bleed item applies, extended: the ground is **one** dedicated full-duration `class="clip"` background element on the lowest content track; other full-duration ambience (floating pills, stickers, extra grain) goes **inside** that clip or on **higher** tracks — never as sibling clips sharing the same track.
- `timeline_track_too_dense` — clips on the same `data-track-index` must not overlap in time (adjacent touch OK). Track ≠ z-index; stacking is CSS `z-index`. Concurrent layers → distinct tracks, **or** nest non-timed décor inside one parent `class="clip"` (children without timing attrs). Never put N full-duration siblings on track `0`. Especially: never put `*-glow` / accent / paired badges as overlapping timed siblings on one track — nest or split tracks (see Frame constraints).
- `gsap_non_transform_motion` — never tween layout/reflow props: `fontSize`, `letterSpacing`, `wordSpacing`, `width`, `height`, `top`, `left`, `margin*`, `padding*`, or `roundProps` (they snap to device pixels under seek-by-frame capture and stutter on ease-out tails). Type-size change (display→headline, `12cqw`→`5cqw`): fix CSS `font-size` at the **larger** size, keep `display: inline-block` (or block), tween `scale: endSize/startSize` (e.g. `5/12`). Never `tl.to(..., { fontSize: "5cqw" })`. Size-change beats map to `counting-dynamic-scale`'s START_SIZE/END_SIZE → scale recipe when named.