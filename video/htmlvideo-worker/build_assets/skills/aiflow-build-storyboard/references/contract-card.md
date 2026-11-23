# Storyboard contract card — autonomous outline stage (fast path)

Provenance: extracted from `../hyperframes-core/references/storyboard-format.md`,
`../hyperframes-core/references/script-format.md`, `references/story-design.md`,
`../hyperframes-animation/blueprints-index.md` (menu only),
`../hyperframes-core/references/brief-contract.md` § 1,
`../hyperframes-core/references/review-loop.md` § 1,
`../hyperframes-creative/references/story-spine.md`.
When the invoking prompt inlines this card, it IS the contract for the run — the full
reference files exist for human use; **do not re-read them**.

## Run shape

- `flow: automation` × `storyboard: no` → `mode: autonomous`. Persist `mode: autonomous` in STORYBOARD.md frontmatter.
- `hyperframes.json` / `meta.json` are NOT needed: all brief values arrive in the inlined BRIEF.md.

## Core doctrine

1. An article is an information dump; a video is a guided act of understanding. Never follow the source's paragraph order — reorder, merge, omit, compress. The single most common failure is paraphrasing the article in order.
2. Value before evidence (`story-spine`): the thesis (brief `message`) lands by **beat 2**; everything after is its evidence.
3. The hook speaks the viewer's language (outcome language) — never the source's section headings; numbers only when they carry stakes.
4. Teaching truth decides which beats exist. Blueprints are a soft menu — tagging one never bends a beat; omit `blueprint` when none fits.

## STORYBOARD.md — shape

### Frontmatter

| Key        | Meaning                                                        | Example                                              |
| ---------- | -------------------------------------------------------------- | ---------------------------------------------------- |
| `format`   | canvas size, from brief `aspect`                               | `1920x1080`                                          |
| `duration` | brief `length` expectation (advisory, not a gate)              | `60s`                                                |
| `message`  | one-line thesis, copied from brief                             | `"Analyzing fundamentals tells you when to buy…"`    |
| `arc`      | chosen structure (+ beat summary in parentheses)               | `how-to-process (Hook → Thesis → Buy tests → CTA)`   |
| `audience` | from brief                                                      | `Beginner to intermediate investors`                 |
| `mode`     | `autonomous`                                                    | `autonomous`                                         |
| `music`    | BGM mood (see Music below)                                     | `confident minimal tech underscore`                  |

### Frame template (exact required shape)

```md
## Frame N — Short name

- scene: one clear visual idea
- voiceover: "spoken guide text, or empty for a silent frame"
- duration: rough seconds (e.g. 6s)
- transition_in: crossfade
- status: outline
- src: compositions/frames/NN-short-name.html
- type: feature_showcase
- persuasion: Progressive disclosure
- beat: comprehension
- blueprint: kinetic-type-beats — candidate shape for the visual stage; omit when none fits (a tag, not a commitment)

narrativeRole: What this frame does in the viewer's understanding (its job, traced to the message).
keyMessage: The one idea the viewer should remember (one sentence).
```

- Every frame at this stage: `status: outline` exactly — never `built` / `animated` (later stages).
- No `## Video direction`, no time-coded `Scene N (a–b s)` shot blocks — those belong to the visual stage.
- `src` is a placeholder path; the file need not exist yet.
- Frame 1 `transition_in: cut` (there is no previous frame).

### type enum (shared with the visual layer) — explainer repurposing

| Explainer role you want          | `type`             |
| -------------------------------- | ------------------ |
| Hook / curiosity gap             | `hook`             |
| Pain / problem / why-care        | `pain_point`       |
| Name the core concept            | `product_intro`    |
| Mechanism / step / item          | `feature_showcase` |
| Implication / payoff / "so what" | `benefit_highlight`|
| Evidence / example / data point  | `social_proof`     |
| Thesis / takeaway / principle    | `branding`         |
| Call to think / try / act        | `cta`              |

Body = a run of `feature_showcase` (+ `benefit_highlight` / `social_proof` interleaves). At least one `feature_showcase` or `product_intro` must exist.

## Explainer structure (pick ONE; compound only if named explicitly)

| Structure           | Use when                              | Body shape                                                       |
| ------------------- | ------------------------------------- | ---------------------------------------------------------------- |
| `concept-explainer` | one idea/term the audience half-knows | name concept → reveal mechanism layer by layer → land implication |
| `how-to-process`    | a procedure with a clear start→finish | 3–6 ordered steps on one consistent stage, one move each         |
| `listicle`          | parallel, co-equal items              | hook → N roughly co-equal items → wrap; rule-of-three strongest  |
| `story-explainer`   | case study / history / cautionary tale| setup → tension → turn → resolution → lesson                     |

- 3–6 body frames, each with ONE clear job; no "more detail" filler frames.
- Set `arc` to the chosen structure (or `<outer> with <inner>` for a compound).
- Register matching (`frame.md` tunes voice, not structure): warm/handmade → plain considered · bold/poster → punchy declarative · friendly/polished → direct lighter · literary/tech → thoughtful precise.

## Hook (pick ONE strategy, first 3–5s)

Shocking statistic · Rhetorical question · Counterintuitive claim · Pain validation · Visceral metaphor · Concept announcement · Direct address · Imagine/scenario · Stakes/consequence.
Never open with a generic definition.

## persuasion (a NAMED technique, not "explain the idea")

- **Make-concrete:** Analogy/metaphor · Concretization · Worked example with real numbers · Anchoring on a familiar referent
- **Reveal-in-order:** Progressive disclosure · Build-up · Signposting
- **Contrast:** Before/after · Common-belief vs reality · Comparison of two options · Counterexample
- **Structure:** Rule of three · Numbered enumeration · Question→answer pairing · Frame-then-fill
- **Evidence:** Statistical proof · Demonstration · Causal chain (A→B→C)
- **Memory & landing:** Callback · Distillation · Coined term · Generalization

Combine when several are active ("Analogy + progressive disclosure"). When none fits, name a new one inline and explain it.

## beat (specific — one word or short compound)

- open the gap: curiosity · puzzlement · surprise · tension · concern · recognition
- orient: clarity · anticipation · focus
- build: comprehension · "aha" · confidence · momentum · conviction · mastery
- land: satisfaction · resolve · inevitability · "now I get it"

Prefer compounds ("Comprehension + delight"). Never generic "positive"/"interested".

## Blueprint menu (candidate tags — soft, one line each)

- `kinetic-type-beats` (Hook/Problem/Product_Intro/Benefits/CTA/Brand_Outro — the workhorse) — words carry the shot: in-place token swaps, beat-by-beat statements, spring-pop payoff
- `typewriter-reveal` (Hook/Brand_Outro) — a line types/edits like a human, then a brand payoff
- `spatial-pan-stations` (Hook/Problem/Product_Intro) — labeled stations on one canvas traversed by one camera
- `camera-journey` (Benefits/Key_Feature) — cinematic multi-leg camera travel tells cause→effect
- `zoom-out-workspace-reveal` (Hook/Benefits) — tight macro detail, one decelerating zoom-out to the whole
- `constellation-hub` (Hook/Social_Proof/CTA) — icon nodes ring a center and resolve on the core
- `grid-card-assemble` (Key_Feature/Benefits/Social_Proof) — tiles/logos self-assemble in a staggered cascade
- `logo-assemble-lockup` (Product_Intro/CTA/Brand_Outro) — a mark builds itself from parts into a lockup
- `cursor-ui-demo` (Product_Intro/Key_Feature/Hook/Benefits) — a visible cursor drives a UI through states
- `device-surface-showcase` (Key_Feature/Product_Intro) — device held hero, screens cycle a real flow
- `prompt-type-submit-generate` (Hook/Product_Intro/Key_Feature/CTA) — "watch me ask, watch it answer"
- `agent-progress-theater` (Key_Feature) — loader/status theater into a checklist that checks off
- `panel-edit-live-sync` (Key_Feature) — an inspector edits, a bound target updates in the same beat
- `transcript-scroll-artifact-reveal` (Key_Feature) — traverse a long generated document, pivot to the deliverable
- `dataviz-countup` (Hook/Problem/Product_Intro/Key_Feature/Social_Proof) — numbers/charts are the hero
- `titlecard-reveal` (Benefits/Social_Proof/CTA/Product_Intro) — one restrained reveal + still hold (breather beat)
- `comparison-split` (Key_Feature) — two paired items, mirrored book-open tilts
- `overwhelm-surround` (Problem) — accumulation closes in from all sides; "you're buried"
- `ticker-takeover` (Hook/Brand_Outro) — options cycle, then the hero crashes in and owns the frame
- `fixed-anchor-cycle` (Hook/Benefits/Brand_Outro) — one pinned element, surroundings cycle through states
- `video-text-pivot` (Product_Intro/Key_Feature) — video slides aside to a hero stat, kinetic text types in
- `cta-morph-press` (CTA/Hook) — a mark condenses into a CTA, a cursor lands the click

Write each `voiceover` in the shape its tagged blueprint implies (reveals paced to the line, not dumped at t=0). Omit the id and write plainly when none fits.

## Transitions (registry — only these names)

`cut | crossfade | blur-crossfade | push-slide LEFT | push-slide RIGHT | push-slide UP | push-slide DOWN | zoom-through | squeeze`

Pick 2–3 types for the whole video and repeat them. Ordered steps → consistent `push-slide`; layer reveal/atmosphere → `crossfade` / `blur-crossfade`; topic switch or new list item → `cut`.

## Continuity

- Multi-frame sequences share a **consistent stage** (restate the same stage idea in each frame's `scene`).
- Share one **consistent transition** across the run.
- A genuine element transform (a diagram node becomes a chart bar) stays inside **ONE frame** (`entrance → transform → settle`); note the intent in `scene`/prose — don't split it across a seam.

## Faceless — no asset inventory

No `asset_candidates` lines. The only real asset is a user-supplied image already at `public/<basename>` — then add `asset_candidates: public/<basename> — <what it is, ≤25 words>`. Never invent paths or reference `capture/`.

## Voiceover lines (SCRIPT.md is derived by the harness)

Do **not** Write `SCRIPT.md`. After this run the harness copies each non-empty
frame `voiceover` into `SCRIPT.md` (header + `## Line N — <title> (Frame N)` +
indented spoken text). Silent frames (`voiceover` empty) are omitted from
SCRIPT.md — their `narrativeRole` + `persuasion` carry the explanation.

Write each `voiceover` as the final spoken line:

- 1–2 sentences, usually 6–20 words; concrete and human — teach, don't read the article aloud.
- **Phrase-segment into discrete cues** ("First the snowball — then the hill — then the speed") so each on-screen piece can reveal when the voiceover names it; never one run-on breath.
- `VO_MODE: restructure` (inlined by caller when present): user_script.txt is source material — rewrite/reorder to fit. `verbatim`: don't change words, only segment at sentence/clause boundaries.

## Music

- `music: <mood phrase>` — BGM mood for the audio step.
- `music: none` — BGM off (narration still runs).
- `music: none` + no SCRIPT.md — the canonical fully-silent marker (no narration, no BGM, no SFX).
- No SCRIPT.md at all = narration-free video (BGM-only or silent per the music field).

## Output protocol (this run)

1. Compose the plan: structure → hook → frame list with all required fields (quality `voiceover` lines included).
2. **First tool call MUST be** `Write("STORYBOARD.md")`. Do not Bash/Read/Glob project or skill files — every input is already inlined. Do not Write `SCRIPT.md`.
3. Write the file in the SAME response as your reasoning — no draft or summary text before the Write. After the Write succeeds, reply with exactly one confirmation line (`STORYBOARD.md written — <N> frames, arc <arc>`) and stop. Do NOT echo a frame table or re-summarize `scene`/`voiceover`/`on screen`/`why` — the file is the deliverable, and the harness derives `SCRIPT.md` and the plan summary itself. No board, no approval questions, no waiting.

## Self-check before writing

- Sequence is narrative-driven, not paragraph-order-driven (core doctrine 1, 2).
- One named hook strategy opens; thesis lands by frame 2.
- Every frame carries: `scene`, `voiceover`, `duration`, `transition_in` (registry name), `status: outline`, `src` placeholder, `type`, `persuasion` (named technique), `beat` (specific) + `narrativeRole`/`keyMessage` prose; `blueprint` tag only where a shape genuinely fits.
- Body runs 3–6 cumulative frames, one job each.
- Transitions repeat 2–3 registry names; frame 1 is `cut`.
- No `## Video direction`, no Scene shot blocks, no `asset_candidates`, no `built`/`animated` statuses.