# Frame worker — executable rule card (aiflow-build-frame-html)

> Compact dispatch contract: this card + frame.md token excerpt + Design
> contract + Assigned storyboard block + `## Rule recipes (snippets)`. Follow
> inlined snippets directly (Read `RULES_DIR` only when tools are enabled and
> a detail is missing). Never open `frame.md` / `_role.md`. Priority:
> `## Resolved conflicts` rulings > Scene list > Video direction > frame.md
> prose (token values always exact).

## Hard structural gates (frame-selfcheck FAIL codes; the driver checks them)

- `missing_template_wrapper` — the file = one bare `<template>…</template>` fragment (no DOCTYPE/document); every `<style>`/`<script>` (incl. GSAP load) inside it — outside, the runtime drops it.
- `missing_composition_id` / `missing_root_duration` — root carries `data-composition-id="<frame_id>"` + `data-duration="<duration>"`; never omit `data-duration` — `transitions inject` fails without it.
- `subcomposition_root_styled_by_class` — style the root via `#root`, never a class on the `data-composition-id` element (class can't match at render → scene unstyled; preview lies). Descendants: plain selectors.
- `clip_missing_data_attrs` — every `class="clip"` carries `data-start`/`data-duration`/`data-track-index`.
- `timeline_track_too_dense` — same-`data-track-index` clips never overlap in time (touching OK).
- `timeline_not_paused` / `timeline_not_registered` — exactly one `gsap.timeline({ paused: true })` at `window.__timelines["<frame_id>"]`.
- `id_requires_css_escape` — no digit-led selectors (`#07-…`, `.01-…`) in CSS/JS selector strings (CSS drops the rule; `querySelector(All)` throws) → `[id="…"]`/`[class~="…"]`; `getElementById` fine.
- No CSS `transition`, infinite `repeat: -1`, or non-deterministic logic — the renderer seeks frame-by-frame (`css_transition_used` is script-gate warning-level; finite bounded pulses fit inside `data-duration`).

## Ruled conventions (do not re-derive)

- **Fonts (Override).** Write only `frame.md` family names + generic fallback (`sans-serif`/`serif`). Never write `@import`, `<link>`, or `@font-face` — driver runs `font-imports.mjs`. Lint checks `font_family_without_font_face`; `google_fonts_import` is warning only.
- **GSAP.** Ship `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>` inside `<template>`, pinned `3.14.2`.
- **Root shape.** `<div id="root" data-composition-id="<frame_id>" data-width="1920" data-height="1080" data-duration="<duration>">`; style via `#root`.
- **Units.** Primary `cqw`/`cqh` (1cqw = 19.2px at 1920w); fixed décor (4/6/8/12px shadows/strokes) uses px. No pixel conversion tables.
- **Uppercase hero words.** For all-caps 1–2 word storyboard tokens (e.g. `"BUY?"`, `"DECIDE"`), use `Space Grotesk` bold uppercase (honoring any "no uppercase Bodoni" rule) — decide once, never debate font case.
- **Turn & thinking discipline.** Keep thinking brief — never draft CSS/HTML/JS in thinking.

## Frame visual law

- **Top ~83% keep-out & NO in-frame caption band.** Root `captions.html` owns bottom ~17% (`y > 83cqh`) even when `Captions: disabled`. NEVER render `.caption-band`, bottom subtitle bars, or footer rules.
- **Scale & legibility floor.** Hero occupies `35–55%` of canvas (center ~`40–42cqh`, never a `<10%` lone box). Pill/body labels `>= 1.05cqw` (load-bearing `>= 1.4cqw`, never `<0.85cqw`).
- **Flex/Grid inside cards & splits (mandatory).** Use `display: flex`/`grid`, `padding`, `gap`, `box-sizing: border-box` (`min-width: 0`) inside cards — NEVER stack rows/titles/columns with raw `position: absolute` `%` offsets. When Scene 2 replaces Scene 1 inside `#stage-card`, put both in the SAME slot (`grid-area: 1 / 1` or shared center anchor) so hidden `autoAlpha: 0` Scene 1 children never leave blank upper space.
- **Caret & delayed inline badges.** Place `__hf.type` caret `<span>` AFTER the text `<span>` (never before). Position a later-Scene badge/check beside centered text (e.g. CTA pill check) absolutely or in a shared slot so an `autoAlpha: 0` flex child never shifts the headline off-center.
- **Contrast & Shared stage.** Text on white/cream MUST use `ink`; light candies (`yellow`/`lime`/`mint`/`peach`/`lavender`/`sky`) are pill FILLS only, never text on light surfaces. For **Shared stage**, keep exact card width/grid and render ALL numbered slot labels identically (dim inactive slots, never blank/`—`).
- **Clean morphs & SVG axes.** Fade outgoing morph cards to `autoAlpha: 0` (no `opacity: 0.3` ghost); when a centered stamp/seal lands over `#stage-card`, fade underlying headline/sub-line to `autoAlpha: 0`. SVG line charts put X-axis at bottom (`y = H - pad`).
- **Short motion-graphics copy** — hero word / stat / 1–3 word label, never a narration sentence.
- **Whole shot — never front-load.** Reveal on voiceover cues (esp. back ~50%). Exits banned on non-final frames (`exit_animation_on_non_final_scene` — root transition IS the exit; fade-out only if final frame). Entrances via `fromTo`, hero visible by `t <= 0.5s`.
- **Shot-sequence fidelity & Roles.** Every Scene = one timeline phase; named motions follow inlined recipes; follow `Adapt:` and Scene cues directly. `focal` = hero; `foreground subject` — text around not over; `background` — full-bleed; `supporting` — quiet. Only real media is user asset in `roles:`/`focal:` (`[video]` `.mp4` → muted `<video class="clip">` direct child of `#root`; image → `<img>`).
- **Same-track clips never overlap** (`timeline_track_too_dense`) — track = temporal lane, stacking = `z-index`; nest ambient décor/glow/badges inside one parent clip (children without timing attrs) or use distinct `data-track-index` lanes.
- **Full-bleed ground on `class="clip"`, never `background` on `#root`.** One full-duration background clip on track `0`.
- **GSAP owns transforms.** Never put CSS `transform` on a GSAP-animated element (`gsap_css_transform_conflict`); center via `xPercent`/`yPercent`. Static CSS `transform: rotate(…)` on non-animated décor (and untouched `.hf-stage-lockup` skeleton CSS) is allowed and never triggers lint — do not audit it. Never tween layout props (`fontSize`/`letterSpacing`/`wordSpacing`/`width`/`height`/`top`/`left`/`margin*`/`padding*`/`roundProps` — `gsap_non_transform_motion`); tween `scale` instead.
- **Tight wrapper for decorative rings.** A ring/halo wrapping a capsule (e.g. `.capsule-ring` with `inset: -0.7cqw`) MUST sit inside a `display: inline-flex` / `width: fit-content` wrapper — NEVER a `width: 100%` container.

## Workflow

- **Single-turn direct output.** All context (skeleton, frame block, design contract, `## Rule recipes (snippets)`) is already inlined in your prompt. Do NOT call `Read`/`Write`/`Bash` tools, and never open `frame.md`, `_role.md`, `STORYBOARD.md`, or skill files.
- **Output ONLY the completed `<template>…</template>` fragment** (filling the pre-built `skeleton-v1` scaffold) — no prose, no explanation, no `done OK` footer. Retry findings (`frame-selfcheck`/lint): fix every reported issue directly in the `<template>…</template>` output.
- **Not yours** — prefix authored ids & reusable classes with `<frame_id>-` (`#root` / `.clip` excepted); you don't decide narration, duration, between-frame transitions, audio, tokens, or motions/assets.

## GSAP idioms (copy)

```js
// CSS: #root{width:100cqw;height:100cqh} .X{position:absolute;left:50%;top:40%} — no CSS transform
tl.set(".X", { xPercent: -50, yPercent: -50 }, 0); // ONLY for position:absolute;left:50%;top:50% — NEVER on flex/grid children or position:relative
tl.fromTo(".X", { autoAlpha: 0, y: 40 }, { autoAlpha: 1, y: 0, duration: 0.6 }, 0.1);
// fromTo paints "from" at build (immediateRender); a 2nd fromTo on same prop → immediateRender: false
```
