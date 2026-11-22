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

- **Fonts (Override).** Write only `frame.md` family names plus a generic fallback (`sans-serif` / `serif`). Never write `@import`, `<link>`, or `@font-face` — the driver runs `font-imports.mjs`. Lint still checks `font_family_without_font_face`; `google_fonts_import` is a warning only.
- **GSAP.** Ship `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>` inside `<template>`, pinned `3.14.2`.
- **Template / root shape.** Root: `<div id="root" data-composition-id="<frame_id>" data-width="1920" data-height="1080" data-duration="<duration>">`; style via `#root`, never a class.
- **Units.** Primary `cqw` / `cqh` (ramp is cqw-based; 1cqw = 19.2px at 1920-wide); fixed-size décor (4/6/8/12px shadows/dots/strokes) uses px. Pick per element, move on; no conversion tables.
- **Turn discipline.** Every turn ends with a tool call or visible text — never thinking-only (silent turns discarded); undecided upstream → pick sibling-consistent, proceed within this turn (≤ ~1 min).

## Frame visual law

- **Top ~83% keep-out.** Karaoke caption pill owns bottom ~17% — holds even when `Captions: disabled`.
- **Fill the content area.** Compose whole top-83% region, not one small cluster mid-frame; hero high (≈0.2–0.35 × height), supporting elements down with rhythm, hero toward full-bleed (landscape: center ~0.42 × height).
- **Visible text is short motion-graphics copy** — hero word / stat / one-word emphasis, never a narration sentence (caption track already shows the spoken words; repeating double-prints).
- **Whole shot — never front-load.** Reveal pieces as voiceover reaches them (esp. back ~50%), macro camera move underneath — full canvas from ~25% = slide. Mid-shot reveals free; exits banned (`exit_animation_on_non_final_scene` — root transition IS the exit; settle/fade-out only if final frame); stillness note → entrance + quiet settle. Entrances via `fromTo`, not CSS-hidden starts; hero visible by `t <= 0.5s`.
- **Shot-sequence fidelity.** Every Scene is a timeline phase; named motions reproduce their `RULES_DIR` recipe — never name-guess; blueprint's signature move recognizable (`compose` = no template).
- **Roles design** — all invented. `focal` = hero; `foreground subject` — eye lands there, keep-out respected, text around not over; `background` — full-bleed, dimmed ~30–50% for legibility; `supporting` — quiet. Built in SVG/CSS/type, never from a file. Only real media: user image in `roles:`/`focal:` — `[video]` `.mp4` → muted `<video class="clip">`, direct child of frame root (nested in a timed element it freezes); untagged → `<img>`.
- **Same-track clips never overlap** (`timeline_track_too_dense`) — track = temporal lane, stacking = CSS `z-index`; concurrent layers → distinct tracks, or non-timed décor in parent clip; prefer nesting full-duration ambient wallpaper (pills/stickers/grain) in the background clip over N full-duration siblings on track `0`.
- **Glow / badge décor: not a second timed sibling on the same track.** Glow/bloom/punch-glow/badge-glow/accent lines with a hero/badge are concurrent — never another `class="clip"` sharing the hero's `data-track-index` while windows overlap. Prefer nest: parent clip owns the window, glow/badge children no timing attrs, GSAP drives them on the parent timeline. Or split: each concurrent timed element its own `data-track-index`. Side-by-side badges → distinct tracks (or one parent clip) — a comment is not legal.
- **Full-bleed ground on a `class="clip"`, never `background` on `#root`.** One dedicated full-duration background clip (color field / gradient / grid) on the lowest content track — a root background is scene-clipped at assembly, so dark content can render invisible over the host body; the assembler paints the base ground.
- **GSAP owns transforms — transform-only motion.** Never a CSS `transform` (e.g. `translateY(-50%)` centering) on an element you later GSAP-animate transform props on — GSAP overwrites the whole `transform`, the centering jumps (`gsap_css_transform_conflict`): center with `margin` / `inset` (fold the offset into `xPercent`/`yPercent`) or use `fromTo`. Never tween layout props — `fontSize`, `letterSpacing`, `wordSpacing`, `width`, `height`, `top`, `left`, `margin*`, `padding*`, `roundProps` (`gsap_non_transform_motion`); a size change: fix CSS `font-size` at the larger size, keep `display: inline-block` (or block), tween `scale: endSize/startSize`.

## Workflow

- **Read** — the inlined dispatch; in your FIRST tool turn Read every `RULES_DIR/<id>.md` in the Rule index as one parallel batch, never re-read (with `## Rule recipes` inlined: no batch read, a full recipe only if a detail is missing). Never `frame.md`, `_role.md`, `STORYBOARD.md`, skill files. Retry findings (`frame-selfcheck`/lint): read first, re-author away.
- **FILL the pre-built skeleton** — `compositions/frames/<frame_id>.html` already exists (`skeleton-v1`); do not write a new skeleton. First edit by turn 2; chunked edits ≤ ~20 KB — style, then body, then script.
- **Reply** — `done OK <frame_id>` (terminal) or `fail <frame_id>: <reason>`. Do NOT run frame-selfcheck / Bash — the driver runs it and re-dispatches with FAIL findings.
- **Not yours** — prefix authored ids & reusable classes with `<frame_id>-` (`#root` / `.clip` excepted); you don't decide narration, duration, between-frame transitions, audio, tokens, or motions/assets.

Cut catalog (path in your dispatch prompt): open only for Scene-seam swaps / scene cuts / text-to-text changes.
