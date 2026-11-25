# Frame worker — executable rule card (aiflow-build-frame-html)

> Compact dispatch contract: this card + frame block + inlined blueprint +
> frame.md token excerpt. Recipes default to `## Rule recipes (snippets)` in
> the dispatch; follow that note (full `RULES_DIR` Read only if a detail is
> missing). Index-only packets: batch-Read every Rule index path once.
> Never open `frame.md` / `_role.md`. Priority: `## Resolved conflicts` rulings >
> Scene list > Video direction > blueprint > frame.md prose (token values always exact).

## Hard structural gates (frame-selfcheck FAIL codes; the driver checks them)

- `missing_template_wrapper` — the file = one bare `<template>…</template>` fragment (no DOCTYPE/document); every `<style>`/`<script>` (incl. GSAP load) inside it — outside, the runtime drops it.
- `missing_composition_id` / `missing_root_duration` — root carries `data-composition-id="<frame_id>"` + `data-duration="<duration>"`; never omit `data-duration` — `transitions inject` fails without it.
- `subcomposition_root_styled_by_class` — style the root via `#root`, never a class on the `data-composition-id` element (class can't match at render → scene unstyled; preview lies). Descendants: plain selectors.
- `clip_missing_data_attrs` — every `class="clip"` carries `data-start`/`data-duration`/`data-track-index`.
- `timeline_track_too_dense` — same-`data-track-index` clips never overlap in time (touching OK).
- `timeline_not_paused` / `timeline_not_registered` — exactly one `gsap.timeline({ paused: true })` at `window.__timelines["<frame_id>"]`.
- `id_requires_css_escape` — no digit-led selectors (`#07-…`, `.01-…`) in CSS/JS selector strings (CSS drops the rule; `querySelector(All)` throws) → `[id="…"]`/`[class~="…"]`; `getElementById` fine.
- No CSS `transition`, repeat / yoyo, non-deterministic logic — the renderer seeks frame-by-frame (`css_transition_used` is script-gate warning-level; a real rule regardless).

## Ruled conventions (do not re-derive)

- **Fonts (Override).** Write only `frame.md` family names + generic fallback (`sans-serif`/`serif`). Never write `@import`, `<link>`, or `@font-face` — driver runs `font-imports.mjs`. Lint checks `font_family_without_font_face`; `google_fonts_import` is warning only.
- **GSAP.** Ship `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>` inside `<template>`, pinned `3.14.2`.
- **Root shape.** `<div id="root" data-composition-id="<frame_id>" data-width="1920" data-height="1080" data-duration="<duration>">`; style via `#root`.
- **Units.** Primary `cqw`/`cqh` (1cqw = 19.2px at 1920w); fixed décor (4/6/8/12px shadows/strokes) uses px. No pixel conversion tables.
- **Turn & thinking discipline.** Every turn ends with a tool call or visible text; keep thinking to brief notes — never draft CSS/HTML/JS in thinking.

## Frame visual law

- **Top ~83% keep-out & NO in-frame caption band.** Root `captions.html` owns bottom ~17% (`y > 83cqh`) even when `Captions: disabled`. NEVER render `.caption-band`, bottom subtitle bars, or footer rules inside a frame.
- **Scale & legibility floor.** Hero occupies `35–55%` of canvas (center ~`40–42cqh`, never a `<10%` lone box on empty ground). Pill/body labels `>= 1.05cqw` (load-bearing copy `>= 1.4cqw`, never `<0.85cqw`).
- **Flex/Grid inside cards & splits (mandatory).** Lay out card interiors, slot racks, and side-by-side card+chart splits with `display: flex` or `display: grid`, `padding`, `gap`, and `box-sizing: border-box` (`min-width: 0`) — NEVER stack rows/titles/columns inside cards with raw `position: absolute` `%` offsets.
- **Contrast & Shared stage.** Text on white/cream surfaces MUST use `ink`; light candies (`yellow`/`lime`/`mint`/`peach`/`lavender`/`sky`) are pill FILLS only, NEVER text color on light surfaces. For a **Shared stage**, keep exact card width/grid and render ALL numbered slot labels identically across frames (dim inactive slots, never blank/`—`).
- **Clean morphs & SVG axes.** Morph/swap transitions fade outgoing cards to `autoAlpha: 0` (never an `opacity: 0.3` ghost). SVG line charts put X-axis at bottom (`y = H - pad`); higher values have smaller `y`.
- **Short motion-graphics copy** — hero word / stat / 1–3 word label, never a narration sentence.
- **Whole shot — never front-load.** Reveal pieces on voiceover cues (esp. back ~50%). Exits banned on non-final frames (`exit_animation_on_non_final_scene` — root transition IS the exit; fade-out only if final frame). Entrances via `fromTo`, hero visible by `t <= 0.5s`.
- **Shot-sequence fidelity & Roles.** Every Scene = one timeline phase; named motions follow `RULES_DIR` recipes; keep blueprint signature move (`compose` = no template). `focal` = hero; `foreground subject` — text around not over; `background` — full-bleed; `supporting` — quiet. Built in SVG/CSS/type; only real media is user asset in `roles:`/`focal:` (`[video]` `.mp4` → muted `<video class="clip">` direct child of `#root`; image → `<img>`).
- **Same-track clips never overlap** (`timeline_track_too_dense`) — track = temporal lane, stacking = `z-index`; nest ambient décor/glow/badges inside one parent clip (children without timing attrs) or use distinct `data-track-index` lanes.
- **Full-bleed ground on `class="clip"`, never `background` on `#root`.** One full-duration background clip on track `0`.
- **GSAP owns transforms.** Never put CSS `transform` on a GSAP-animated element (`gsap_css_transform_conflict`); center via `xPercent`/`yPercent`. Never tween layout props — `fontSize`, `letterSpacing`, `wordSpacing`, `width`, `height`, `top`, `left`, `margin*`, `padding*`, `roundProps` (`gsap_non_transform_motion`); for size changes, set larger CSS `font-size` and tween `scale`.

## Workflow

- **Read** — the inlined dispatch; in your FIRST tool turn Read every `RULES_DIR/<id>.md` in the Rule index as one parallel batch, never re-read (with `## Rule recipes` inlined: no batch read, a full recipe only if a detail is missing). Never `frame.md`, `_role.md`, `STORYBOARD.md`, skill files. Retry findings (`frame-selfcheck`/lint): read first, re-author away.
- **FILL the pre-built skeleton** — `compositions/frames/<frame_id>.html` already exists (`skeleton-v1`); never a new skeleton; first edit by turn 2. Final file < ~15 KB: after Reading the skeleton, **one Write (or one large Edit) of the whole file** — no style → body → script split. Hard cap: a single Write input ≤ ~20 KB (InputValidationError).
- **Reply** — **only** `done OK <frame_id>` (or `fail <frame_id>: <reason>`); no summary / checklist / explanation. Do NOT run frame-selfcheck / Bash — the driver runs it and re-dispatches with FAIL findings.
- **Not yours** — prefix authored ids & reusable classes with `<frame_id>-` (`#root` / `.clip` excepted); you don't decide narration, duration, between-frame transitions, audio, tokens, or motions/assets.

Cut catalog (path in your dispatch prompt): open only for Scene-seam swaps / scene cuts / text-to-text changes.

## GSAP idioms (copy)

```js
// CSS: #root{width:100cqw;height:100cqh} .X{position:absolute;left:50%;top:40%} — no CSS transform
tl.set(".X", { xPercent: -50, yPercent: -50 }, 0); // centering lives in GSAP
tl.fromTo(".X", { autoAlpha: 0, y: 40 }, { autoAlpha: 1, y: 0, duration: 0.6 }, 0.1);
// fromTo paints "from" at build (immediateRender); a 2nd fromTo on same prop → immediateRender: false
```
