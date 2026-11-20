# Frame worker — executable rule card (aiflow-build-frame-html)

> Compact dispatch contract. This card + your frame block + the inlined
> blueprint / rule recipes / frame.md design-token excerpt form the complete
> contract — never open `frame.md` / `_role.md`; `RULES_DIR/<id>.md` only when
> a cited recipe is missing.

## Hard structural gates (frame-selfcheck FAIL codes; fix all before replying)

- `missing_template_wrapper` — the file = one bare `<template>…</template>` fragment (no DOCTYPE/document); every `<style>`/`<script>` (incl. GSAP load) inside it — outside, the runtime drops it.
- `missing_composition_id` / `missing_root_duration` — root carries `data-composition-id="<frame_id>"` + `data-duration="<duration>"`; never omit `data-duration` — `transitions inject` fails without it.
- `subcomposition_root_styled_by_class` — style the root via `#root`, never a class on the `data-composition-id` element (class can't match at render → scene unstyled; preview lies). Descendants: plain selectors.
- `clip_missing_data_attrs` — every `class="clip"` carries `data-start`/`data-duration`/`data-track-index`.
- `timeline_track_too_dense` — same-`data-track-index` clips never overlap in time (touching OK).
- `timeline_not_paused` / `timeline_not_registered` — exactly one `gsap.timeline({ paused: true })` at `window.__timelines["<frame_id>"]`.
- `id_requires_css_escape` — no digit-led selectors (`#07-…`, `.01-…`) in CSS/JS selector strings (CSS drops the rule; `querySelector(All)` throws) → `[id="…"]`/`[class~="…"]`; `getElementById` fine.
- No CSS `transition`, repeat / yoyo, non-deterministic logic — the renderer seeks frame-by-frame (`css_transition_used` is script-gate warning-level; a real rule regardless).

## Ruled conventions (do not re-derive)

- **Fonts (Override).** Font delivery is the render compiler's job. Satisfy only static lint gates: each used family → matching `@font-face`, Google Fonts `@import`/`<link>`, or bundled/aliased name (`font_family_without_font_face`). All families from `frame.md`'s ramp (normal case): omit the import, write names verbatim; a shipped `@import` lists exactly the out-of-ramp families (`google_fonts_import` — warning only). No font-file searches; no URL debates.
- **GSAP.** Ship `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>` inside `<template>`, pinned `3.14.2`.
- **Template / root shape.** Root: `<div id="root" data-composition-id="<frame_id>" data-width="1920" data-height="1080" data-duration="<duration>">`; style via `#root`, never a class.
- **Units.** Primary `cqw` / `cqh` (ramp is cqw-based; 1cqw = 19.2px at 1920-wide); fixed-size décor (4/6/8/12px shadows/dots/strokes) uses px. Pick per element, move on; no conversion tables.
- **Turn discipline.** Every turn ends with a tool call or visible text — never thinking-only (silent turns discarded); undecided upstream → pick sibling-consistent, proceed within this turn (≤ ~1 min).

## Frame visual law

- **Top ~83% keep-out.** Karaoke caption pill owns bottom ~17% — holds even when `Captions: disabled`.
- **Fill the content area.** Compose whole top-83% region, not one small cluster mid-frame; hero high (≈0.2–0.35 × height), supporting elements down with rhythm, hero toward full-bleed (landscape: center ~0.42 × height).
- **Visible text is short motion-graphics copy** — hero word / stat / one-word emphasis, never a narration sentence (caption track already shows the spoken words; repeating double-prints).
- **Whole shot — never front-load.** Reveal pieces as voiceover reaches them (esp. back ~50%), macro camera move underneath — full canvas from ~25% = slide. Mid-shot reveals free; exits banned (`exit_animation_on_non_final_scene` — root transition IS the exit; settle/fade-out only if final frame); stillness note → entrance + quiet settle. Entrances via `fromTo`, not CSS-hidden starts; hero visible by `t <= 0.5s`.
- **Shot-sequence fidelity.** Every Scene is a timeline phase; named motions reproduce their inlined rule recipe — never name-guess; blueprint's signature move recognizable (`compose` = no template).
- **Roles design** — all invented. `focal` = hero; `foreground subject` — eye lands there, keep-out respected, text around not over; `background` — full-bleed, dimmed ~30–50% for legibility; `supporting` — quiet. Built in SVG/CSS/type, never from a file. Only real media: user image in `roles:`/`focal:` — `[video]` `.mp4` → muted `<video class="clip">`, direct child of frame root (nested in a timed element it freezes); untagged → `<img>`.
- **Same-track clips never overlap** (`timeline_track_too_dense`) — track = temporal lane, stacking = CSS `z-index`; concurrent layers → distinct tracks, or non-timed décor in parent clip; prefer nesting full-duration ambient wallpaper (pills/stickers/grain) in the background clip over N full-duration siblings on track `0`.
- **Glow / badge décor: not a second timed sibling on the same track.** Glow/bloom/punch-glow/badge-glow/accent lines with a hero/badge are concurrent — never another `class="clip"` sharing the hero's `data-track-index` while windows overlap. Prefer nest: parent clip owns the window, glow/badge children no timing attrs, GSAP drives them on the parent timeline. Or split: each concurrent timed element its own `data-track-index`. Side-by-side badges → distinct tracks (or one parent clip) — a comment is not legal.
- **Full-bleed ground on a `class="clip"`, never `background` on `#root`.** One dedicated full-duration background clip (color field / gradient / grid) on the lowest content track — a root background is scene-clipped at assembly, so dark content can render invisible over the host body; the assembler paints the base ground.
- **GSAP owns transforms — transform-only motion.** Never a CSS `transform` (e.g. `translateY(-50%)` centering) on an element you later GSAP-animate transform props on — GSAP overwrites the whole `transform`, the centering jumps (`gsap_css_transform_conflict`): center with `margin` / `inset` (fold the offset into `xPercent`/`yPercent`) or use `fromTo`. Never tween layout props — `fontSize`, `letterSpacing`, `wordSpacing`, `width`, `height`, `top`, `left`, `margin*`, `padding*`, `roundProps` (`gsap_non_transform_motion`); a size change: fix CSS `font-size` at the larger size, keep `display: inline-block` (or block), tween `scale: endSize/startSize`.

## Workflow

- **Read** — packet top to bottom (frame block, blueprint, rule recipes), then the inlined frame.md design-token excerpt; never `frame.md`, `_role.md`, `STORYBOARD.md`, or skill files. Retry feedback (`lint`/`check`/`frame-selfcheck`): read first, re-author away — each is a hard constraint.
- **Skeleton first, then chunked fill** — in first 1–2 turns write a minimal valid skeleton (`<template>` + `#root` attrs + comment) to `compositions/frames/<frame_id>.html` (file on disk = heartbeat), flesh out with edits; writes over ~20 KB truncate — style + structure, then body, then script.
- **Self-check, then reply** — run `node /app/scripts/frame-selfcheck.mjs compositions/frames/<frame_id>.html` — enforces the gates on your unwired frame (assembled-project `hyperframes lint`/`check` is a false green); fix every FAIL, re-read this card, reply `done OK <frame_id>` — terminal action. No PASS → `fail <frame_id>: <what is still broken>`, never silent.
- **Not yours** — prefix authored ids & reusable classes with `<frame_id>-` (`#root` / `.clip` excepted); you don't decide narration, duration, between-frame transitions, audio, tokens, or motions/assets.

Escape hatches — open only when unclear: `RULES_DIR/<id>.md` if a cited recipe is missing from your packet; cut catalog (path in your dispatch prompt) for Scene-seam swaps / scene cuts / text-to-text changes. Never `frame.md` / `_role.md` — already inlined.
