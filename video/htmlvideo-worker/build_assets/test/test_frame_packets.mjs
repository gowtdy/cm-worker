import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert";
import * as core from "../skills/hyperframes-core/scripts/lib/frame-packets-core.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CARD = join(ROOT, "skills", "aiflow-build-frame-html",
  "sub-agents", "frame-worker-card.md");

export const SELFCHECK_CODES = [
  "missing_template_wrapper", "missing_composition_id",
  "subcomposition_root_styled_by_class", "clip_missing_data_attrs",
  "timeline_track_too_dense", "timeline_not_paused",
  "timeline_not_registered", "missing_root_duration",
  "id_requires_css_escape",
];

export const LINT_CODES = [
  "css_transition_used", "gsap_css_transform_conflict",
  "gsap_non_transform_motion", "exit_animation_on_non_final_scene",
  "font_family_without_font_face", "google_fonts_import",
];

export function testCard() {
  const card = readFileSync(CARD, "utf8");
  assert.ok(card.trim().length > 0, "card must exist");
  assert.ok(Buffer.byteLength(card) <= 8000,
    `card ${Buffer.byteLength(card)} bytes > 8000 budget`);
  for (const code of SELFCHECK_CODES) {
    assert.ok(card.includes(code), `card must mention FAIL-gate code ${code}`);
  }
  for (const code of LINT_CODES) {
    assert.ok(card.includes(code), `card must mention lint code ${code}`);
  }
  assert.ok(card.includes("_role.md"), "card must point at the full contract");
  console.log("card OK", Buffer.byteLength(card), "bytes");
}

const FIX_STORYBOARD = `---
format: landscape
---
## Frame 1 — Hook

- status: outline
- src: compositions/frames/01-hook.html
- duration: 4s
- blueprint: split-panel
- rules: punch-in

Scene 1 (0.0–4.0s): title pill in.

## Frame 2 — Answer

- status: outline
- src: compositions/frames/02-answer.html
- duration: 5s
- blueprint: compose

Scene 1 (0.0–5.0s): stat grid reveal.
`;
const FIX_FRAME_MD = `# Placeholder\n\n## Colors\n\n- ink: #111\n\n## Typography\n\n- display: 9cqw\n\n## Frame Treatments\n\nbig catalog section\n\n## Numerals & Claims (hard rule)\n\nNo invented stats.\n`;

// Mirrors a real frame.md: the hex/font VALUES live in YAML frontmatter, while
// the `## Colors` / `## Typography` narrative references them only symbolically
// ({colors.cream}). The worker is forbidden from opening frame.md (input
// reduction), so the dispatch must inline these values for exact fidelity.
const REAL_FRAME_MD = `---
version: alpha
colors:
  cream: "#F5F5F0"
  ink: "#1A1A1A"
  coral: "#E85D4E"
typography:
  body: { fontFamily: "Space Grotesk", cqw: 0.85, weight: 400 }
  display: { fontFamily: "Bodoni Moda", cqw: 12.0, weight: 800 }
---

## Colors

Tokens identical to the source. \`{colors.cream}\` is the ground; \`{colors.ink}\` is type.

## Typography

Two ramps: Space Grotesk body; Bodoni display.
`;

function fixtureProject() {
  const dir = mkdtempSync(join(tmpdir(), "fh-"));
  const anim = join(dir, "rules"); mkdirSync(anim, { recursive: true });
  writeFileSync(join(anim, "punch-in.md"), "recipe body");
  writeFileSync(join(dir, "STORYBOARD.md"), FIX_STORYBOARD);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  const frameMd = join(dir, "frame.md"); writeFileSync(frameMd, FIX_FRAME_MD);
  return { dir, card, frameMd, anim };
}

export function testDispatchDocs() {
  const { dir, card, frameMd } = fixtureProject();
  const storyboard = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(storyboard).map((f) =>
    ({ ...f, frameId: core.frameId(f) }));
  const outDir = join(dir, ".hyperframes", "frame-packets");
  const docs = core.buildDispatchDocs({
    projectDir: dir, frames, outDir, animationDir: dir,
    cardPath: card, frameMdPath: frameMd,
  });
  assert.strictEqual(docs.length, 2);
  for (const d of docs) {
    const text = readFileSync(d.path, "utf8");
    assert.ok(d.bytes <= 40000, `dispatch ${d.frameId} over budget`);
    assert.ok(text.includes("## Role"), "has role section");
    assert.ok(text.includes("role card text"), "card must be inlined into the doc");
    assert.ok(text.includes("## Assigned storyboard block"), "has block");
    assert.ok(text.includes("## Rule index"), "has rule index");
    assert.ok(text.includes("## frame.md design tokens"), "has excerpt");
    assert.ok(text.includes("#111") && text.includes("9cqw"), "excerpt has tokens");
    assert.ok(text.includes("No invented stats"), "hard-rule section included");
    assert.ok(!text.includes("big catalog section"), "treatment catalog excluded");
  }
  // positive path: frame 1 cites `rules: punch-in` → index + default snippet
  const hookText = readFileSync(
    docs.find((d) => d.frameId === "01-hook").path, "utf8");
  assert.ok(hookText.includes("punch-in"), "rule index must cite cited rules by path");
  assert.ok(hookText.includes("rules/punch-in.md"),
    "rule index line must carry the rules/<id>.md path");
  assert.ok(hookText.includes("## Rule recipes (snippets)"),
    "default INLINE_RECIPES=snippet inlines recipe snippets");
  assert.ok(hookText.includes("Mechanism: recipe body"),
    "snippet carries the short recipe body");
  // frame with no cited rules stays index-only (no recipes section)
  const answerText = readFileSync(
    docs.find((d) => d.frameId === "02-answer").path, "utf8");
  assert.ok(!answerText.includes("## Rule recipes"),
    "frames without cited rules get no recipes section");
  // explicit off still indexes only
  const offDocs = core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, "off-mode"), animationDir: dir,
    cardPath: card, frameMdPath: frameMd, inlineRecipes: "off",
  });
  const offHook = readFileSync(
    offDocs.find((d) => d.frameId === "01-hook").path, "utf8");
  assert.ok(!offHook.includes("## Rule recipes"), "off mode has no recipes section");
  assert.ok(!offHook.includes("Mechanism: recipe body"));
  // determinism
  const again = core.buildDispatchDocs({
    projectDir: dir, frames: core.splitFrames(readFileSync(join(dir, "STORYBOARD.md"), "utf8")).map((f) => ({ ...f, frameId: core.frameId(f) })),
    outDir: join(dir, "second"), animationDir: dir,
    cardPath: card, frameMdPath: frameMd,
  });
  for (let i = 0; i < docs.length; i++)
    assert.strictEqual(readFileSync(again[i].path, "utf8"),
      readFileSync(docs[i].path, "utf8"));
  console.log("dispatch docs OK", docs.map((d) => `${d.frameId}:${d.bytes}`).join(" "));
}

export function testDispatchInlinesFrontmatterTokens() {
  const dir = mkdtempSync(join(tmpdir(), "fh-tokens-"));
  writeFileSync(join(dir, "STORYBOARD.md"), FIX_STORYBOARD);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  const frameMd = join(dir, "frame.md"); writeFileSync(frameMd, REAL_FRAME_MD);
  const anim = join(dir, "rules"); mkdirSync(anim, { recursive: true });
  writeFileSync(join(anim, "punch-in.md"), "recipe body");
  const storyboard = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(storyboard).map((f) =>
    ({ ...f, frameId: core.frameId(f) }));
  const docs = core.buildDispatchDocs({
    projectDir: dir, frames,
    outDir: join(dir, ".hyperframes", "frame-packets"),
    animationDir: dir, cardPath: card, frameMdPath: frameMd,
  });
  const text = readFileSync(docs[0].path, "utf8");
  assert.ok(text.includes('"#F5F5F0"'),
    "dispatch must inline the exact cream hex from frontmatter colors:");
  assert.ok(text.includes('"#E85D4E"'),
    "dispatch must inline the exact coral hex from frontmatter colors:");
  assert.ok(text.includes('"#1A1A1A"'),
    "dispatch must inline the exact ink hex from frontmatter colors:");
  assert.ok(text.includes('"Bodoni Moda"'),
    "dispatch must inline the exact font family from frontmatter typography:");
  console.log("dispatch frontmatter tokens OK");
}

export function testDispatchInlinesLedger() {
  // scheme-1 (L-4): the visual stage appends the Layout/Motion ledger to the
  // frame block, and buildDispatchDocs inlines `frame.block` verbatim — so the
  // ledger must reach the dispatch under `## Assigned storyboard block` with no
  // extra inline code.
  const dir = mkdtempSync(join(tmpdir(), "fh-ledger-"));
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  writeFileSync(join(dir, "frame.md"), FIX_FRAME_MD);
  const anim = join(dir, "rules"); mkdirSync(anim, { recursive: true });
  writeFileSync(join(anim, "punch-in.md"), "recipe body");
  const sb = `## Frame 1 — Hook

- status: outline
- src: compositions/frames/01-hook.html
- duration: 4s
- blueprint: compose
- rules: punch-in

Scene 1 (0.0–4.0s): word settles.

## Layout ledger

| element | role | place | size | color | order |
|---------|------|-------|------|-------|-------|
| word | focal | center x=50% y=42% | 8.5cqw | cream | 1 |

## Motion ledger

| scene | element | from → to | ease | dur |
|-------|---------|-----------|------|-----|
| scene1 | word | opacity 0 → 1 | power2.inOut | 1s |
`;
  writeFileSync(join(dir, "STORYBOARD.md"), sb);
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const docs = core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, "out"), animationDir: anim,
    cardPath: card, frameMdPath: join(dir, "frame.md"), storyboard: sb,
  });
  const text = readFileSync(docs[0].path, "utf8");
  assert.ok(text.includes("## Layout ledger"), "dispatch must inline the Layout ledger");
  assert.ok(text.includes("## Motion ledger"), "dispatch must inline the Motion ledger");
  assert.ok(text.includes("center x=50% y=42%"), "ledger place values reach the dispatch");
  assert.ok(text.includes("power2.inOut"), "ledger ease values reach the dispatch");
  const blockStart = text.indexOf("## Assigned storyboard block");
  assert.ok(blockStart > 0 && text.indexOf("## Layout ledger") > blockStart,
    "ledger must land inside the Assigned storyboard block");
  console.log("dispatch ledger inline OK");
}

export function testDispatchIngestsDesignContract() {
  const storyboard = FIX_STORYBOARD;
  const mk = () => {
    const dir = mkdtempSync(join(tmpdir(), "fh-design-"));
    writeFileSync(join(dir, "STORYBOARD.md"), storyboard);
    const card = join(dir, "card.md"); writeFileSync(card, "role card text");
    writeFileSync(join(dir, "frame.md"), FIX_FRAME_MD);
    const anim = join(dir, "rules"); mkdirSync(anim, { recursive: true });
    writeFileSync(join(anim, "punch-in.md"), "recipe body");
    return { dir, card };
  };

  // with _design.md → inlined as "## Design contract (video-wide)"
  const a = mk();
  mkdirSync(join(a.dir, ".hyperframes", "visual"), { recursive: true });
  writeFileSync(join(a.dir, ".hyperframes", "visual", "_design.md"),
    "## Video direction\npalette candy; negative list: no gradient depth.\n## Blueprint assignment\nF1 = split-panel (Reproduce)\n");
  const build = (dir, card) => {
    const sb = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
    const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
    return core.buildDispatchDocs({
      projectDir: dir, frames, outDir: join(dir, ".hyperframes", "frame-packets"),
      animationDir: dir, cardPath: card, frameMdPath: join(dir, "frame.md"),
    });
  };
  const docsA = build(a.dir, a.card);
  const textA = readFileSync(docsA[0].path, "utf8");
  assert.ok(textA.includes("## Design contract (video-wide)"),
    "dispatch must include the design-contract section when _design.md exists");
  assert.ok(textA.includes("negative list: no gradient depth"),
    "design-contract content must be inlined verbatim");

  // without _design.md → no such section (skip gracefully)
  const b = mk();
  const docsB = build(b.dir, b.card);
  const textB = readFileSync(docsB[0].path, "utf8");
  assert.ok(!textB.includes("## Design contract (video-wide)"),
    "no design-contract section when _design.md is absent");

  // P0-2 + P1-3: shared prefix byte-identical across frames; Blueprint assignment
  // stripped from Design contract; Pre-Render Self-Audit excluded.
  const textA1 = readFileSync(docsA[0].path, "utf8");
  const textA2 = readFileSync(docsA[1].path, "utf8");
  const cut1 = textA1.indexOf("# Dispatch: 01-hook");
  const cut2 = textA2.indexOf("# Dispatch: 02-answer");
  assert.ok(cut1 > 0 && cut1 === cut2, "shared prefix must precede # Dispatch");
  assert.strictEqual(textA1.slice(0, cut1), textA2.slice(0, cut2),
    "cross-frame prefix (Role + frame.md excerpt + Design contract) must be byte-identical");
  assert.ok(textA1.slice(0, cut1).includes("## Design contract (video-wide)"));
  assert.ok(textA1.slice(0, cut1).includes("## frame.md design tokens"));
  assert.ok(!textA1.includes("## Blueprint assignment"),
    "other frames' Blueprint assignment must be trimmed from Design contract");
  // Duplicate Palette & type prose trimmed when frontmatter tokens are present
  const trimmed = core.trimDesignContractForDispatch(
    "## Video direction\n\n**Palette & type (verbatim from frame.md — authoritative, do not restate)**\nColors: cream `#F5F5F0`\n\n**Motion grammar.** Snap.\n\n## Blueprint assignment\nF1 = compose\n",
    true,
  );
  assert.ok(!trimmed.includes("Palette & type") && trimmed.includes("Motion grammar") && !trimmed.includes("Blueprint assignment"));
  // Tolerant blueprintAssignment parses bold **Blueprint assignment** too
  const boldMap = core.blueprintAssignment("**Blueprint assignment**\nF1 = titlecard-reveal (Adapt)\n");
  assert.strictEqual(boldMap.get(1)?.id, "titlecard-reveal");
  console.log("design contract ingestion + shared prefix OK");
}

export function testSkeletons() {
  const { dir, card, frameMd, anim } = fixtureProject();
  const storyboard = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(storyboard).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const out = core.buildSkeletons({ projectDir: dir, frames, canvas: { width: 1920, height: 1080 } });
  assert.strictEqual(out.length, 2);
  const selfcheck = join(ROOT, "scripts", "frame-selfcheck.mjs");
  for (const { frameId, path } of out) {
    const html = readFileSync(path, "utf8");
    assert.ok(html.includes("skeleton-v1"), "marker present");
    assert.ok(html.includes(`data-composition-id="${frameId}"`), "comp id");
    assert.ok(html.includes("data-width=\"1920\""), "canvas dims");
    assert.ok(html.includes("data-duration=\"4\"") || html.includes("data-duration=\"5\""), "duration stamped as plain number");
    assertOnlyUnfilled(selfcheck, path);
  }
  console.log("skeletons OK: structure clean, flagged skeleton_unfilled");
}

// A raw skeleton must FAIL only on skeleton_unfilled — every structural gate passes.
function assertOnlyUnfilled(selfcheck, path) {
  const run = spawnSync("node", [selfcheck, path], { encoding: "utf8" });
  const out = `${run.stdout}${run.stderr}`;
  assert.strictEqual(run.status, 1, `raw skeleton must FAIL:\n${out}`);
  const fails = out.split("\n").filter((l) => l.startsWith("[FAIL]"));
  assert.strictEqual(fails.length, 1, `only skeleton_unfilled may fail:\n${out}`);
  assert.ok(fails[0].includes("(skeleton_unfilled)"), out);
  return fails[0];
}

const FILLED_WITH_MARKER = (id) => `<!-- skeleton-v1: filled — ${id} -->
<template>
<div id="root" data-composition-id="${id}" data-duration="4">
  <div class="clip bg" data-start="0" data-duration="4" data-track-index="0"></div>
  <div class="clip" data-start="0" data-duration="4" data-track-index="1"><h1 class="t">Hi</h1></div>
</div>
<style>.t { font-size: 5cqw; }</style>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
window.__timelines["${id}"] = tl;
tl.from(".t", { y: 20, opacity: 0, duration: 0.6 }, 0);
</script>
</template>
`;

export function testSelfcheckSkeletonUnfilled() {
  const selfcheck = join(ROOT, "scripts", "frame-selfcheck.mjs");
  const dir = mkdtempSync(join(tmpdir(), "fh-sc-"));
  const run = (name, html) => {
    const p = join(dir, `${name}.html`);
    writeFileSync(p, html);
    const r = spawnSync("node", [selfcheck, p], { encoding: "utf8" });
    return { status: r.status, out: `${r.stdout}${r.stderr}` };
  };
  // finished frame that kept the marker comment → PASS
  const ok = run("01-done", FILLED_WITH_MARKER("01-done"));
  assert.strictEqual(ok.status, 0, ok.out);
  // same frame without the marker → PASS (check only arms on the marker)
  const noMark = run("02-done", FILLED_WITH_MARKER("02-done").replace(/<!--[^>]*-->\n/, ""));
  assert.strictEqual(noMark.status, 0, noMark.out);
  // body filled, script not yet → FAIL with no-tween evidence only
  const half = run("03-half", FILLED_WITH_MARKER("03-half").replace(/^tl\.from[^\n]*\n/m, ""));
  assert.strictEqual(half.status, 1, half.out);
  assert.ok(/evidence: no-tween\)/.test(half.out), half.out);
  // a classList.add() call is not a tween
  const cls = run("04-cls", FILLED_WITH_MARKER("04-cls").replace(/^tl\.from[^\n]*$/m, 'document.body.classList.add("x");'));
  assert.strictEqual(cls.status, 1, cls.out);
  // only scaffold (data-skeleton) children in root → root-empty
  const scaffold = run("05-sc", FILLED_WITH_MARKER("05-sc")
    .replace(/<div class="clip" data-start[^\n]*\n/, "")
    .replace('class="clip bg"', 'class="clip bg" data-skeleton="bg"'));
  assert.strictEqual(scaffold.status, 1, scaffold.out);
  assert.ok(scaffold.out.includes("root-empty"), scaffold.out);
  console.log("selfcheck skeleton_unfilled OK");
}

export function testRichSkeleton() {
  const { dir } = fixtureProject();
  const storyboard = readFileSync(join(dir, "STORYBOARD.md"), "utf8")
    .replace("Scene 1 (0.0–4.0s): title pill in.", "Scene 1 (0.0–1.5s): title pill in.\nScene 2 (1.5–6.0s): subtitle -- rises.");
  const frames = core.splitFrames(storyboard).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const out = core.buildSkeletons({ projectDir: dir, frames, canvas: { width: 1920, height: 1080 }, rich: true });
  const selfcheck = join(ROOT, "scripts", "frame-selfcheck.mjs");
  const html = readFileSync(out[0].path, "utf8");
  assert.ok(html.includes("skeleton-v1 (rich)"));
  assert.ok(html.includes('data-skeleton="bg" data-start="0" data-duration="4" data-track-index="0"'), "full-duration bg clip");
  assert.ok(html.includes('window.__timelines["01-hook"] = tl;'), "registration");
  assert.ok(html.includes("gsap.timeline({ paused: true })"));
  // Scene 2 overflows the 4s duration → labels rescaled by 4/6
  assert.ok(html.includes('tl.addLabel("scene1", 0);'));
  assert.ok(html.includes('tl.addLabel("scene2", 1);'), html);
  assert.ok(!/<!--[^>]*--[^>]*-->[\s\S]*<!--.*subtitle --/.test(html), "no nested comment terminators");
  assert.ok(html.includes("subtitle — rises"), "`--` in scene text is neutralized inside the comment");
  for (const { path } of out) assertOnlyUnfilled(selfcheck, path);
  // env default is ON → rich skeleton when `rich` is omitted
  const byDefault = core.buildSkeletons({ projectDir: dir, frames });
  assert.ok(readFileSync(byDefault[0].path, "utf8").includes("(rich)"),
    "HTMLVIDEO_FRAME_HTML_RICH_SKELETON defaults ON");
  // explicit off / env=0 → plain skeleton
  const plain = core.buildSkeletons({ projectDir: dir, frames, rich: false });
  assert.ok(!readFileSync(plain[0].path, "utf8").includes("(rich)"));
  const prev = process.env.HTMLVIDEO_FRAME_HTML_RICH_SKELETON;
  process.env.HTMLVIDEO_FRAME_HTML_RICH_SKELETON = "0";
  try {
    const viaEnv = core.buildSkeletons({ projectDir: dir, frames });
    assert.ok(!readFileSync(viaEnv[0].path, "utf8").includes("(rich)"));
  } finally {
    if (prev === undefined) delete process.env.HTMLVIDEO_FRAME_HTML_RICH_SKELETON;
    else process.env.HTMLVIDEO_FRAME_HTML_RICH_SKELETON = prev;
  }
  console.log("rich skeleton OK");
}

const CONFLICT_STORYBOARD = `---
format: landscape
---
## Video direction

**Negative list.** No 3D spin, orbit, or screw-tumble on diagram nodes. Background always cream.

## Blueprint assignment

F1 = grid-card-assemble (Adapt)
F2 = titlecard-reveal (Reproduce)

## Frame 1 — Exit

- status: outline
- src: compositions/frames/01-exit.html
- duration: 9.265s
- blueprint: comparison-split
- blueprint: grid-card-assemble (Adapt)
- rules: punch-in

Scene 1 (0.0–2.6s): Cream field; rows settle. No orbit, no drift.
Scene 2 (2.6–6.8s): cards flip in with a rotateY reveal.
Scene 3 (6.8–13.0s): hold on an ink field.

## Frame 2 — Clean

- status: outline
- src: compositions/frames/02-clean.html
- duration: 4s
- blueprint: titlecard-reveal (Reproduce)

Scene 1 (0.0–4.0s): headline rises on a cream field, never orbiting.
`;

function conflictProject() {
  const dir = mkdtempSync(join(tmpdir(), "fh-conf-"));
  writeFileSync(join(dir, "STORYBOARD.md"), CONFLICT_STORYBOARD);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  writeFileSync(join(dir, "frame.md"), REAL_FRAME_MD);
  mkdirSync(join(dir, "rules"), { recursive: true });
  writeFileSync(join(dir, "rules", "punch-in.md"), RECIPE_MD);
  mkdirSync(join(dir, "blueprints"), { recursive: true });
  writeFileSync(join(dir, "blueprints", "grid-card-assemble.md"), "# g\n\n**duration**: 3.0–10.5s (x)\n\nbody");
  writeFileSync(join(dir, "blueprints", "titlecard-reveal.md"), "# t\n\n**duration**: 3–5s (y)\n\nbody");
  const sb = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  return { dir, card, sb, frames };
}

export function testResolvedConflicts() {
  const { dir, card, sb, frames } = conflictProject();
  const build = (out, opts = {}) => core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, out), animationDir: dir,
    cardPath: card, frameMdPath: join(dir, "frame.md"), storyboard: sb, ...opts,
  });
  const docs = build("out");
  const kinds = docs[0].conflicts.map((c) => c.kind);
  for (const k of ["blueprint-fields", "scene-overflow", "negative-list-3d", "background-color"])
    assert.ok(kinds.includes(k), `expected ${k} in ${kinds}`);
  assert.ok(!kinds.includes("negative-list-spin"), "nothing spins in frame 1");
  assert.ok(!kinds.includes("negative-list-orbit"), "negated 'No orbit' must not count");
  assert.ok(!kinds.includes("blueprint-duration"), "9.265s is inside 3.0–10.5s");
  const t1 = readFileSync(docs[0].path, "utf8");
  const tail = t1.slice(t1.indexOf("## Resolved conflicts"));
  assert.ok(t1.indexOf("## Resolved conflicts") > t1.indexOf("## frame.md design tokens"), "section is last");
  assert.ok(tail.includes("Scene 3 4.846s–9.265s"), "rescaled Scene windows spelled out");
  assert.ok(tail.includes("drop every 3D intent"), "3D ruling");
  assert.ok(tail.includes("ground layer stays cream"), "background ruling");
  // frame 2 is clean (negated orbit, in-range duration)
  assert.deepStrictEqual(docs[1].conflicts, []);
  assert.ok(!readFileSync(docs[1].path, "utf8").includes("## Resolved conflicts"));
  // blueprint duration + assignment mismatch
  const c = core.detectConflicts({
    block: "- duration: 8s\nScene 1 (0.0–8.0s): x\n",
    sel: { id: "titlecard-reveal", raw: "titlecard-reveal", mode: null, all: [], conflict: false },
    assigned: { id: "grid-card-assemble" },
    blueprintText: "**duration**: 3–5s (y)",
  });
  assert.deepStrictEqual(c.map((x) => x.kind), ["blueprint-assignment", "blueprint-duration"]);
  assert.deepStrictEqual(core.blueprintDurationRange("**duration**: ~4–12s (Hook)"), { lo: 4, hi: 12 });
  assert.deepStrictEqual(core.blueprintDurationRange("**duration**: 6.5s"), { lo: 6.5, hi: 6.5 });
  // switch off → warnings only, no section
  const off = build("off", { resolveConflicts: false });
  assert.ok(off[0].conflicts.length > 0);
  assert.ok(!readFileSync(off[0].path, "utf8").includes("## Resolved conflicts"));
  console.log("resolved conflicts OK", kinds.join(","));
}

const RECIPE_MD = `---
name: punch-in
---

# Punch In

Intro paragraph.

## How It Works

A scale punch on the beat: element starts at 1.3 and snaps to 1.

Second paragraph that must not be inlined.

## Recipe

\`\`\`html
<div class="p">x</div>
\`\`\`

\`\`\`js
tl.fromTo(".p", { scale: 1.3 }, { scale: 1, duration: 0.3 }, 0);
\`\`\`

## Variations

FULL-ONLY-TOKEN
`;

export function testRecipeSnippets() {
  const s = core.recipeSnippet(RECIPE_MD);
  assert.ok(s.startsWith("Mechanism: A scale punch"), s);
  assert.ok(s.includes('tl.fromTo(".p"'), s);
  assert.ok(!s.includes("Second paragraph") && !s.includes("FULL-ONLY-TOKEN"));
  const longCode = RECIPE_MD.replace("tl.fromTo", `${"// filler line\n".repeat(400)}tl.fromTo`);
  const big = core.recipeSnippet(longCode);
  assert.ok(Buffer.byteLength(big) <= 2048, `snippet ${Buffer.byteLength(big)} > 2048`);
  assert.ok(big.endsWith("```") && big.includes("truncated — Read the full recipe"), big.slice(-200));

  const { dir, card, sb, frames } = conflictProject();
  const build = (out, inlineRecipes) => readFileSync(core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, out), animationDir: dir,
    cardPath: card, frameMdPath: join(dir, "frame.md"), storyboard: sb, inlineRecipes,
  })[0].path, "utf8");
  const off = build("o", "off");
  assert.ok(!off.includes("## Rule recipes") && !off.includes("A scale punch"));
  assert.ok(off.includes("## Rule index (open a recipe file only while authoring its phase)"));
  const snip = build("s", "snippet");
  assert.ok(snip.includes("## Rule recipes (snippets)") && snip.includes("### punch-in"));
  assert.ok(snip.includes("rules/punch-in.md"), "rule index kept");
  assert.ok(snip.includes("Read a full `RULES_DIR/<id>.md` only when a detail"));
  assert.ok(!snip.includes("FULL-ONLY-TOKEN"));
  const full = build("f", "full");
  assert.ok(full.includes("## Rule recipes (full)") && full.includes("FULL-ONLY-TOKEN"));
  // env plumbing — default ON (snippet); off/0 disables; unknown → snippet
  const prev = process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES;
  try {
    delete process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES;
    assert.strictEqual(core.inlineRecipesMode(), "snippet", "default ON = snippet");
    process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES = "full";
    assert.strictEqual(core.inlineRecipesMode(), "full");
    process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES = "0";
    assert.strictEqual(core.inlineRecipesMode(), "off");
    process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES = "bogus";
    assert.strictEqual(core.inlineRecipesMode(), "snippet");
  } finally {
    if (prev === undefined) delete process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES;
    else process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES = prev;
  }
  console.log("recipe snippets OK", Buffer.byteLength(s), "bytes");
}

const DUAL_BLUEPRINT_STORYBOARD = `---
format: landscape
---
## Blueprint assignment

F1 = titlecard-reveal (Reproduce)
F2 = kinetic-type-beats (Adapt)

## Frame 1 — Hook

- status: outline
- src: compositions/frames/01-hook.html
- duration: 6s
- blueprint: kinetic-type-beats

- blueprint: titlecard-reveal (Reproduce)

Scene 1 (0.0–2.0s): eyebrow.
Scene 2 (2.0–6.0s): headline.

## Frame 2 — Answer

- status: outline
- src: compositions/frames/02-answer.html
- duration: 5s
- blueprint: kinetic-type-beats (Adapt)
- blueprint: grid-card-assemble

Scene 1 (0.0–5.0s): grid.
`;

export function testSelectBlueprint() {
  const a = core.selectBlueprint("- blueprint: kinetic-type-beats\n\n- blueprint: titlecard-reveal (Reproduce)\n");
  assert.strictEqual(a.id, "titlecard-reveal");
  assert.strictEqual(a.mode, "Reproduce");
  assert.ok(a.conflict);
  // moded line wins even when it is not the last one
  const b = core.selectBlueprint("- blueprint: kinetic-type-beats (Adapt)\n- blueprint: grid-card-assemble\n");
  assert.strictEqual(b.id, "kinetic-type-beats");
  assert.strictEqual(b.mode, "Adapt");
  // no mode anywhere → last wins
  const c = core.selectBlueprint("- blueprint: a-one\n- blueprint: b-two\n");
  assert.strictEqual(c.id, "b-two");
  assert.strictEqual(c.mode, null);
  // single, same id twice → no conflict
  const d = core.selectBlueprint("- blueprint: x-y\n- blueprint: x-y (Adapt)\n");
  assert.strictEqual(d.conflict, false);
  assert.strictEqual(core.selectBlueprint("- duration: 4s\n"), null);
  // bare mode word is a mode, never an id
  const e = core.selectBlueprint("- blueprint: Adapt\n");
  assert.strictEqual(e.id, "");
  assert.strictEqual(e.mode, "Adapt");
  assert.ok(e.modeOnly && !e.conflict);
  const f = core.selectBlueprint("- blueprint: typewriter-reveal\n- blueprint: reproduce\n");
  assert.strictEqual(f.id, "typewriter-reveal");
  assert.strictEqual(f.mode, "Reproduce");
  assert.ok(!f.conflict);
  const g = core.withAssignment(e, { id: "typewriter-reveal", mode: "Adapt" });
  assert.strictEqual(g.id, "typewriter-reveal");
  assert.strictEqual(g.mode, "Adapt");
  assert.strictEqual(core.withAssignment(e, undefined).id, "");
  console.log("selectBlueprint OK");
}

const BARE_MODE_STORYBOARD = `---
format: landscape
---
## Video direction

**Negative list.** No rotation or skew on type. Background always cream.

## Blueprint assignment

F3 = typewriter-reveal (Adapt)

## Frame 3 — Type

- status: outline
- src: compositions/frames/03-type.html
- duration: 4s
- blueprint: Adapt

Scene 1 (0.0–4.0s): headline types on while the card tilts in with perspective.

## Frame 4 — Orphan

- status: outline
- src: compositions/frames/04-orphan.html
- duration: 4s
- blueprint: Reproduce

Scene 1 (0.0–4.0s): flat hold.
`;

export function testBareModeBlueprint() {
  const dir = mkdtempSync(join(tmpdir(), "fh-bare-"));
  writeFileSync(join(dir, "STORYBOARD.md"), BARE_MODE_STORYBOARD);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  writeFileSync(join(dir, "frame.md"), FIX_FRAME_MD);
  mkdirSync(join(dir, "rules"), { recursive: true });
  mkdirSync(join(dir, "blueprints"), { recursive: true });
  writeFileSync(join(dir, "blueprints", "typewriter-reveal.md"),
    "# tw\n\n**duration**: 3–5s (x)\n\nBODY-typewriter: card rotateY 12deg with perspective 1200px.");
  const sb = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const docs = core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, "out"), animationDir: dir,
    cardPath: card, frameMdPath: join(dir, "frame.md"), storyboard: sb,
  });
  const t3 = readFileSync(docs[0].path, "utf8");
  assert.ok(t3.includes("## Selected blueprint: typewriter-reveal (mode: Adapt)"), "id from assignment");
  assert.ok(t3.includes("BODY-typewriter"));
  const kinds = docs[0].conflicts.map((c) => c.kind);
  assert.ok(!kinds.includes("blueprint-assignment") && !kinds.includes("blueprint-fields"), kinds.join(","));
  assert.ok(!/`(?:Adapt|Reproduce)` is the frame's blueprint/i.test(t3));
  assert.ok(!t3.includes("ignore the assignment"));
  // type-only rotation ban vs card tilt → scoped ruling, not the global 3D drop
  assert.ok(kinds.includes("type-rotation-scope"), kinds.join(","));
  assert.ok(!kinds.includes("negative-list-3d"), "type-scoped ban must not drop card 3D");
  const tail = t3.slice(t3.indexOf("## Resolved conflicts"));
  assert.ok(tail.includes("applies to type only") && tail.includes("perspective run exactly as the recipe/blueprint"));
  // no assignment entry → no blueprint, no ruling about a mode word
  const t4 = readFileSync(docs[1].path, "utf8");
  assert.ok(!t4.includes("## Selected blueprint"));
  assert.deepStrictEqual(docs[1].conflicts, []);
  // detectConflicts never rules a mode word to be the blueprint
  const c = core.detectConflicts({
    block: "- blueprint: Adapt\n",
    sel: { id: "Adapt", raw: "Adapt", mode: null, all: [], conflict: false },
    assigned: { id: "typewriter-reveal", mode: "Adapt" },
  });
  assert.deepStrictEqual(c, []);
  // general negative list without the type scope still drops 3D
  const g = core.detectConflicts({
    block: "Scene 1 (0.0–4.0s): card tilts in with perspective.\n",
    sel: null, assigned: null, direction: "**Negative list.** No 3D anywhere.",
  });
  assert.deepStrictEqual(g.map((x) => x.kind), ["negative-list-3d"]);
  // "no 3D extrude" is a material/bevel ban and MUST NOT falsely ban CSS rotateY flips
  const extrudeOnly = core.detectConflicts({
    block: "Scene 1 (0.0–4.0s): card flips 180deg on rotateY with perspective.\n",
    sel: null, assigned: null, direction: "**Negative list.** No 3D extrude, no drop-shadow blur.",
  });
  assert.deepStrictEqual(extrudeOnly.map((x) => x.kind), []);
  console.log("bare-mode blueprint + type rotation scope OK");
}

export function testDualBlueprintDispatch() {
  const dir = mkdtempSync(join(tmpdir(), "fh-bp-"));
  writeFileSync(join(dir, "STORYBOARD.md"), DUAL_BLUEPRINT_STORYBOARD);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  writeFileSync(join(dir, "frame.md"), FIX_FRAME_MD);
  mkdirSync(join(dir, "rules"), { recursive: true });
  mkdirSync(join(dir, "blueprints"), { recursive: true });
  for (const id of ["kinetic-type-beats", "titlecard-reveal", "grid-card-assemble"])
    writeFileSync(join(dir, "blueprints", `${id}.md`), `BODY-${id}`);
  const sb = readFileSync(join(dir, "STORYBOARD.md"), "utf8");
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const assignment = core.blueprintAssignment(sb);
  assert.strictEqual(assignment.get(1).id, "titlecard-reveal");
  assert.strictEqual(assignment.get(2).mode, "Adapt");
  const docs = core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, "out"), animationDir: dir,
    cardPath: card, frameMdPath: join(dir, "frame.md"), storyboard: sb,
  });
  const t1 = readFileSync(docs[0].path, "utf8");
  assert.ok(t1.includes("## Selected blueprint: titlecard-reveal (mode: Reproduce)"), "frame 1 heading");
  assert.ok(t1.includes("BODY-titlecard-reveal"), "frame 1 inlines the moded blueprint");
  assert.ok(!t1.includes("BODY-kinetic-type-beats"), "frame 1 must not inline the superseded blueprint");
  assert.ok(t1.includes("superseded"), "frame 1 notes the superseded field");
  const t2 = readFileSync(docs[1].path, "utf8");
  assert.ok(t2.includes("## Selected blueprint: kinetic-type-beats (mode: Adapt)"), "frame 2 heading");
  assert.ok(!t2.includes("BODY-grid-card-assemble"));
  // legacy packet path uses the same selection
  const pk = core.buildFramePackets({
    projectDir: dir, outDir: join(dir, "pk"), animationDir: dir,
    corePath: card, deltaPath: card,
  });
  const p1 = readFileSync(pk.packets[0].path, "utf8");
  assert.ok(p1.includes("BODY-titlecard-reveal") && !p1.includes("BODY-kinetic-type-beats"));
  console.log("dual blueprint dispatch OK");
}

export function testSceneTimelineDrift() {
  const ok = core.sceneTimelineDrift("- duration: 6s\nScene 1 (0.0–2.0s): a\nScene 2 (2.0–6.0s): b\n");
  assert.strictEqual(ok.drift, 0);
  const bad = "- duration: 6.153s\nScene 1 (0.0–1.2s): a\nScene 3 (2.0–5.0s): b\n";
  assert.ok(Math.abs(core.sceneTimelineDrift(bad).drift + 1.153) < 1e-9);
  assert.strictEqual(core.sceneTimelineDrift("- duration: 4s\nno scenes\n"), null);
  const prev = process.env.HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT;
  process.env.HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT = "1";
  try {
    assert.throws(() => core.checkSceneTimeline(bad, "f1"), /drift/);
    core.checkSceneTimeline("- duration: 6s\nScene 1 (0.0–6.05s): a\n", "f2"); // within 0.1s
  } finally {
    if (prev === undefined) delete process.env.HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT;
    else process.env.HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT = prev;
  }
  console.log("scene timeline drift OK");
}

const ANIM = join(ROOT, "skills", "hyperframes-animation");
const KINETIC = readFileSync(join(ANIM, "blueprints", "kinetic-type-beats.md"), "utf8");

function withEnv(name, value, fn) {
  const prev = process.env[name];
  if (value === undefined) delete process.env[name]; else process.env[name] = value;
  try { return fn(); } finally {
    if (prev === undefined) delete process.env[name]; else process.env[name] = prev;
  }
}

// Generic structure (not kinetic-specific): roles, a Scene with an OR-chain and
// role variants, a vocabulary, a rule mapping and an appendix.
const SYNTH_BLUEPRINT = `# synth — Synthetic

**intent**: a test shot.

**roles served**

- Hook (from \`synth-hook-flash\`): a flash open.
- CTA (from \`synth-cta-lockup\`): a lockup close.

**duration**: 3–6s

**shot structure**

- **Scene 1 (0.0–1.0s) — open.** Text enters via ONE move: slide-up OR wipe-in OR blur-in OR spin-in.
${["Hook (flash)", "Hook (relay)", "CTA", "CTA (button)", "Problem", "Benefits"].map((v) =>
    `  - _Variant — ${v}_: ${"filler words describing this variant at length. ".repeat(12)}`).join("\n")}

**motion vocabulary**: ${"lots of verbs; ".repeat(120)}

**rule mapping**

- letter-scramble resolve with divider ticks → \`hacker-flip-3d\` (glyph decode)
- confetti burst up-and-drift → \`particle-burst\`
${"- unrelated thing number x → `gsap-effects` (padding)\n".repeat(40)}
**HARD RULE — keep me**: never drop this.

---

PROVENANCE-ONLY
`;

export function testNarrowBlueprint() {
  // real blueprint, no role → conservative trim
  const plain = core.narrowBlueprint(KINETIC, { block: "", role: null });
  const plainBytes = Buffer.byteLength(plain.text);
  assert.ok(plain.narrowed);
  assert.ok(plainBytes <= 12000, `kinetic-type-beats narrowed to ${plainBytes} > 12000`);
  assert.ok(plain.text.startsWith(core.NARROWED_BLUEPRINT_NOTE));
  for (const keep of ["**intent**", "**roles served**", "**shot structure**", "**rule mapping**", "Sub-shape A", "Scene N"])
    assert.ok(plain.text.includes(keep), `must keep ${keep}`);
  assert.ok(plain.text.includes("pick one option and build, do not enumerate"));
  // Hook frame doing an in-place token swap → Hook variants only, flash preferred
  const block = "## Frame 1 — Buying on emotion\n\n- type: hook\n- blueprint: kinetic-type-beats (Reproduce)\n"
    + "Scene 1 (0.0–1.0s): the word types on with a trailing caret.\n"
    + "Scene 2 (1.0–3.0s): \"feeling\" hard-cuts in place to \"panic\" — instant token swap, no roll.\n";
  assert.strictEqual(core.frameRole(block), "Hook");
  const hook = core.narrowBlueprint(KINETIC, { block });
  assert.ok(Buffer.byteLength(hook.text) < plainBytes, "role narrowing is smaller");
  assert.ok(Buffer.byteLength(hook.text) <= 10000, `hook narrowing ${Buffer.byteLength(hook.text)}`);
  assert.ok(hook.text.includes("_Variant — Hook (flash)_"), "token-swap frame keeps the flash variant");
  assert.ok(!/_Variant — (?:CTA|Problem|Benefits|Brand_Outro|Product_Intro)/.test(hook.text), "other roles dropped");
  assert.ok(!hook.text.includes("- CTA (from"), "roles served narrowed to Hook");
  assert.ok(hook.text.includes("`discrete-text-sequence` + `context-sensitive-cursor`"), "relevant rule mapping kept");
  assert.ok(!hook.text.includes("`center-outward-expansion`"), "irrelevant rule mapping dropped");
  // generic structure
  const s = core.narrowBlueprint(SYNTH_BLUEPRINT, { block: "- type: cta\nScene 1: a confetti burst up-and-drift then a wipe-in." });
  assert.ok(s.text.includes("_Variant — CTA_") && s.text.includes("_Variant — CTA (button)_"));
  assert.ok(!s.text.includes("Hook (relay)") && !s.text.includes("- Hook (from"));
  assert.ok(s.text.includes("slide-up OR wipe-in. _(+2 alternatives omitted)_"), "OR chain capped to lead + most relevant");
  assert.ok(s.text.includes("confetti burst up-and-drift → `particle-burst`"));
  assert.ok(!s.text.includes("(glyph decode)") && !s.text.includes("(padding)"), "rule-side parentheticals stripped");
  assert.ok(s.text.includes("mappings for motions this frame does not ask for omitted"));
  assert.ok(s.text.includes("**HARD RULE — keep me**"), "unknown sections kept verbatim");
  assert.ok(!s.text.includes("PROVENANCE-ONLY"), "appendix after --- dropped");
  // small blueprints pass through untouched
  const small = core.narrowBlueprint("# t\n\n**rule mapping**\n\n- a → `b`\n", { block: "" });
  assert.ok(!small.narrowed && small.text === "# t\n\n**rule mapping**\n\n- a → `b`");
  // dispatch plumbing + env switch (default ON)
  const dir = mkdtempSync(join(tmpdir(), "fh-narrow-"));
  mkdirSync(join(dir, "blueprints"), { recursive: true });
  mkdirSync(join(dir, "rules"), { recursive: true });
  writeFileSync(join(dir, "blueprints", "kinetic-type-beats.md"), KINETIC);
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  const sb = `## Frame 1 — Hook\n\n- src: compositions/frames/01-hook.html\n- duration: 4s\n${block.split("\n").slice(2).join("\n")}`;
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const build = (out) => readFileSync(core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, out), animationDir: dir, cardPath: card, storyboard: sb,
  })[0].path, "utf8");
  const on = withEnv("HTMLVIDEO_FRAME_PACKETS_NARROW_BLUEPRINT", undefined, () => build("on"));
  assert.ok(on.includes(`## Selected blueprint: kinetic-type-beats (mode: Reproduce)\n\n${core.NARROWED_BLUEPRINT_NOTE}`));
  const off = withEnv("HTMLVIDEO_FRAME_PACKETS_NARROW_BLUEPRINT", "0", () => build("off"));
  assert.ok(!off.includes(core.NARROWED_BLUEPRINT_NOTE) && off.includes(KINETIC.trim()), "off → full blueprint");
  console.log("narrow blueprint OK", plainBytes, Buffer.byteLength(hook.text), "bytes");
}

export function testInferCitedRules() {
  const ruleIds = core.knownRuleIds(ANIM);
  const block = "## Frame 1 — Open\n\n- rules:\n- duration: 4s\n"
    + "Scene 1 (0.0–2.0s): \"feeling\" type-on at the tip with a blinking caret.\n";
  assert.deepStrictEqual(core.citedRules(block, ruleIds), []);
  const inferred = core.inferCitedRules(block, ruleIds);
  assert.ok(inferred.includes("discrete-text-sequence"), `type-on → discrete-text-sequence (${inferred})`);
  assert.ok(inferred.includes("context-sensitive-cursor"), "caret → context-sensitive-cursor");
  // the selected blueprint's rule mapping is consulted first
  const viaMap = core.inferCitedRules("Scene 1: the in-place token cycle hard-cut swaps the last word.", ruleIds,
    { blueprintText: KINETIC });
  assert.strictEqual(viaMap[0], "discrete-text-sequence", `mapping hit ${viaMap}`);
  const synth = core.inferCitedRules("Scene 2: the title runs a letter-scramble resolve.", ruleIds,
    { blueprintText: SYNTH_BLUEPRINT });
  assert.deepStrictEqual(synth, ["hacker-flip-3d"], "phrase-table-free id comes from the mapping");
  // negation, unknown ids, exclude, max
  assert.deepStrictEqual(core.inferCitedRules("Scene 1: no type-on, no caret, no confetti.", ruleIds), []);
  assert.deepStrictEqual(core.inferCitedRules("types on with a caret", ["context-sensitive-cursor"]), ["context-sensitive-cursor"]);
  assert.deepStrictEqual(core.inferCitedRules("types on with a caret", ruleIds, { exclude: ["discrete-text-sequence"], max: 1 }),
    ["context-sensitive-cursor"]);
  const { rules, cited } = core.frameRules(`${block}- rules: counting-dynamic-scale\n`, ruleIds);
  assert.deepStrictEqual(cited, ["counting-dynamic-scale"]);
  assert.strictEqual(rules[0], "counting-dynamic-scale", "explicit rules stay first");
  // dispatch: inferred ids reach the Rule index and the snippet recipes
  const dir = mkdtempSync(join(tmpdir(), "fh-infer-"));
  mkdirSync(join(dir, "rules"), { recursive: true });
  for (const id of ["discrete-text-sequence", "context-sensitive-cursor"])
    writeFileSync(join(dir, "rules", `${id}.md`), readFileSync(join(ANIM, "rules", `${id}.md`), "utf8"));
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  const sb = block.replace("- rules:\n", "- src: compositions/frames/01-open.html\n");
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const build = (out) => readFileSync(core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, out), animationDir: dir, cardPath: card, storyboard: sb,
    inlineRecipes: "snippet",
  })[0].path, "utf8");
  const on = withEnv("HTMLVIDEO_FRAME_PACKETS_INFER_RULES", undefined, () => build("on"));
  assert.ok(!on.includes("(no motion rules cited)"));
  assert.ok(on.includes("- `discrete-text-sequence` → ") && on.includes("(inferred from the block's motion phrases)"));
  assert.ok(on.includes("## Rule recipes (snippets)") && on.includes("### discrete-text-sequence"));
  const off = withEnv("HTMLVIDEO_FRAME_PACKETS_INFER_RULES", "off", () => build("off"));
  assert.ok(off.includes("(no motion rules cited)") && !off.includes("## Rule recipes"));
  console.log("infer cited rules OK", inferred.join(","));
}

export function testIdleLoopConflict() {
  const direction = "**Motion grammar.** A deterministic bottom ticker drifts L→R as a calm, ever-present motif (pure CSS/GSAP loop). No idle looping — every element resolves to stillness.";
  const kinds = (args) => core.detectConflicts({ block: "Scene 1 (0.0–4.0s): x\n", sel: null, assigned: null, ...args })
    .filter((c) => c.kind === "idle-loop-vs-continuous-motion");
  const [c] = kinds({ direction });
  assert.ok(c, "ticker vs no-idle-loop must be ruled");
  assert.ok(c.ruling.includes("repeat: -1") && c.ruling.includes("finite"), c.ruling);
  assert.ok(c.ruling.includes("this block does not, so omit it"));
  const [withTicker] = kinds({ direction, block: "Scene 1 (0.0–4.0s): cards settle over the bottom ticker.\n" });
  assert.ok(withTicker.ruling.includes("this block lists it, so build it"));
  // block-side drift against a frame.md ban
  const [drift] = kinds({ frameMd: "## Composition Rules\n\nNo perpetual looping.", block: "Scene 1: a slow-drifting mint blob.\n" });
  assert.ok(drift && !drift.ruling.includes("ticker"), "drift-only ruling has no ticker clause");
  assert.deepStrictEqual(kinds({ direction: "A bottom ticker drifts L→R." }), [], "no ban → no conflict");
  assert.deepStrictEqual(kinds({ direction: "No ticker, no marquee. No idle looping." }), [], "negated asks don't count");
  // dispatch: the section stays the last one
  const dir = mkdtempSync(join(tmpdir(), "fh-loop-"));
  const card = join(dir, "card.md"); writeFileSync(card, "role card text");
  writeFileSync(join(dir, "frame.md"), REAL_FRAME_MD);
  const sb = `## Video direction\n\n${direction}\n\n## Frame 1 — X\n\n- src: compositions/frames/01-x.html\n- duration: 4s\n\nScene 1 (0.0–4.0s): headline settles.\n`;
  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const [doc] = core.buildDispatchDocs({
    projectDir: dir, frames, outDir: join(dir, "out"), animationDir: dir, cardPath: card,
    frameMdPath: join(dir, "frame.md"), storyboard: sb,
  });
  const t = readFileSync(doc.path, "utf8");
  assert.ok(t.indexOf("## Resolved conflicts") > t.indexOf("## frame.md design tokens"));
  assert.ok(t.slice(t.indexOf("## Resolved conflicts")).includes("idle-loop-vs-continuous-motion"));
  console.log("idle-loop conflict OK");
}

export function testSharedStageSkeletonsAndRuntime() {
  const selfcheck = join(ROOT, "scripts", "frame-selfcheck.mjs");
  const dir = mkdtempSync(join(tmpdir(), "fh-stage-"));
  writeFileSync(join(dir, "frame.md"), REAL_FRAME_MD);
  mkdirSync(join(dir, ".hyperframes", "visual"), { recursive: true });
  const designMd = `## Video direction

**Motion grammar.** Crisp neo-brutalist snaps.

## Blueprint assignment
F1 = kinetic-type-beats (Adapt)
F2 = card-morph-anchor (Adapt)
F3 = card-morph-anchor (Adapt)
F4 = grid-card-assemble (Adapt)

## Shared stage spec
layout: left-rail-right-card
title: 3-POINT CHECK
slots: 01 Understand | 02 Durable edge | 03 Reason intact
F1: none
F2: [active:coral, dim, dim]
F3: top-stepper-center-card [done:coral, active:coral, dim]
F4: center-scorecard [done:coral, done:coral, done:coral]
`;
  writeFileSync(join(dir, ".hyperframes", "visual", "_design.md"), designMd);
  const sb = `---
format: landscape
---
## Frame 1 — Hook
- src: compositions/frames/01-hook.html
- duration: 4s
Scene 1 (0.0–4.0s): hook.

## Frame 2 — Rail
- src: compositions/frames/02-rail.html
- duration: 5s
Scene 1 (0.0–5.0s): rail.

## Frame 3 — Stepper
- src: compositions/frames/03-stepper.html
- duration: 5s
Scene 1 (0.0–5.0s): stepper.

## Frame 4 — Scorecard
- src: compositions/frames/04-scorecard.html
- duration: 5s
Scene 1 (0.0–5.0s): scorecard.
`;
  writeFileSync(join(dir, "STORYBOARD.md"), sb);
  const spec = core.parseSharedStageSpec(designMd);
  assert.strictEqual(spec.layout, "left-rail-right-card");
  assert.strictEqual(spec.title, "3-POINT CHECK");
  assert.deepStrictEqual(spec.slots, ["01 Understand", "02 Durable edge", "03 Reason intact"]);
  assert.strictEqual(spec.frames.get(1).layout, "none");
  assert.strictEqual(spec.frames.get(2).layout, "left-rail-right-card");
  assert.strictEqual(spec.frames.get(3).layout, "top-stepper-center-card");
  assert.strictEqual(spec.frames.get(4).layout, "center-scorecard");
  // Unknown layout names fall back safely (only the 3 approved presets + none exist)
  const bogus = core.parseSharedStageSpec("## Shared stage spec\nlayout: split-vs\nslots: A | B\nF1: [active, dim]\n");
  assert.strictEqual(bogus.layout, "none");

  const frames = core.splitFrames(sb).map((f) => ({ ...f, frameId: core.frameId(f) }));
  const skels = core.buildSkeletons({ projectDir: dir, frames, canvas: { width: 1920, height: 1080 }, storyboard: sb });
  // Every raw skeleton (both non-stage F1 and shared-stage F2/F3/F4) must fail ONLY skeleton_unfilled
  for (const s of skels) {
    const html = readFileSync(s.path, "utf8");
    assert.ok(html.includes("const __hf ="), `${s.frameId} must carry __hf runtime`);
    assertOnlyUnfilled(selfcheck, s.path);
  }
  const f1 = readFileSync(skels[0].path, "utf8");
  assert.ok(!f1.includes('id="stage-card"'), "F1: none has no shared stage");
  const f2 = readFileSync(skels[1].path, "utf8");
  assert.ok(f2.includes('data-stage-layout="left-rail-right-card"') && f2.includes('id="stage-card"'));
  assert.ok(f2.includes("left: 5cqw") && f2.includes("left: 34cqw"), "non-overlapping rail + card geometry");
  const f3 = readFileSync(skels[2].path, "utf8");
  assert.ok(f3.includes('data-stage-layout="top-stepper-center-card"') && f3.includes('data-state="done"'));
  const f4 = readFileSync(skels[3].path, "utf8");
  assert.ok(f4.includes('data-stage-layout="center-scorecard"'));
  // 3 slots stay on one row; the title is crossfade-safe (allow-overlap)
  assert.ok(f4.includes("grid-template-columns: repeat(3, minmax(0, 1fr))"), "3 slots = one row");
  assert.ok(f4.includes("top: 8cqh") && f4.includes("top: 38cqh"), "lockup lifted, card unchanged");
  assert.ok(/class="hf-stage-title"[^>]*data-layout-allow-overlap="true"/.test(f4), "title allow-overlap");
  // ≥4 slots wrap to 2 columns (6b0795df: 4×~250px cells overflowed 30–37 char labels)
  assert.strictEqual(core.centerScorecardColumns(1), 1);
  assert.strictEqual(core.centerScorecardColumns(3), 3);
  assert.strictEqual(core.centerScorecardColumns(4), 2);
  assert.strictEqual(core.centerScorecardColumns(8), 2);

  // Filling #stage-card + calling __hf.type passes frame-selfcheck cleanly
  const filledF2 = f2
    .replace('data-skeleton="bg"', "")
    .replace('data-skeleton="card"', "")
    .replace("<!-- FILL THIS FRAME'S HERO CONTENT HERE -->", '<h2 class="hero-line">Understand It</h2>')
    .replace(/<\/script>\s*<\/template>/, '__hf.type(tl, ".hero-line", null, "Understand It", 0.1, 0.8);\n</script>\n</template>');
  const filledPath = join(dir, "compositions", "frames", "02-rail.html");
  writeFileSync(filledPath, filledF2);
  const okRun = spawnSync("node", [selfcheck, filledPath], { encoding: "utf8" });
  assert.strictEqual(okRun.status, 0, `${okRun.stdout}${okRun.stderr}`);

  // Infinite repeat: -1 fails selfcheck, while finite repeat: 1 passes
  const infPath = join(dir, "compositions", "frames", "02-rail.html");
  writeFileSync(infPath, filledF2.replace(/<\/script>\s*<\/template>/, 'tl.to(".hero-line", { opacity: 0.5, repeat: -1, yoyo: true, duration: 0.4 }, 1);\n</script>\n</template>'));
  const infRun = spawnSync("node", [selfcheck, infPath], { encoding: "utf8" });
  assert.strictEqual(infRun.status, 1);
  assert.ok(`${infRun.stdout}${infRun.stderr}`.includes("gsap_infinite_repeat"));

  writeFileSync(infPath, filledF2.replace(/<\/script>\s*<\/template>/, 'tl.to(".hero-line", { scale: 0.95, repeat: 1, yoyo: true, duration: 0.1 }, 1);\n</script>\n</template>'));
  const finRun = spawnSync("node", [selfcheck, infPath], { encoding: "utf8" });
  assert.strictEqual(finRun.status, 0, `${finRun.stdout}${finRun.stderr}`);

  // MAX_RULES = 3 cap
  const ruleIds = core.knownRuleIds(ANIM);
  const many = core.frameRules("- rules: discrete-text-sequence, context-sensitive-cursor, spring-pop-entrance, svg-path-draw, particle-burst\n", ruleIds);
  assert.strictEqual(many.rules.length, 3);

  // Dual-state slot icons (both dot + check SVG pre-rendered), data-accent CSS (no inline style="background:..."), & shadow token
  assert.ok(f2.includes('class="hf-stage-dot"') && f2.includes('class="hf-stage-check"'),
    "every shared-stage slot must pre-render both dot and check icons");
  assert.ok(f2.includes('.hf-stage-slot[data-state="done"] .hf-stage-check { display: block; }'));
  assert.ok(!/class="hf-stage-slot"[^>]*style="background:/i.test(f2),
    "shared-stage slots must use data-accent CSS instead of inline background styles");
  assert.ok(f2.includes('data-accent="coral"') && f2.includes('.hf-stage-slot[data-accent="coral"]:not([data-state="dim"])'),
    "shared-stage slots must preserve arbitrary palette accent via data-accent CSS");

  // P0-1 & P1-2: frameMdExcerpt overrides deck placeholder rule (`— figure —`) and floating-pill atmosphere words (`VISION`, `FUTURE`)
  const excerpt = core.frameMdExcerpt(
    '---\ncolors:\n  shadow: "rgba(26,26,26,0.08)"\n---\n\n## Approved Entities\n\nNo real logos. Floating-pill words are neutral atmospheres ("VISION", "FUTURE", "NEXT"), never content-specific.\n\n## Numerals & Claims (hard rule)\n\nNever invent figures. Render slots as `— figure —`, `{metric}`, `N%`.\n',
  );
  assert.ok(excerpt.includes("Autonomous-video override"), excerpt);
  assert.ok(!/Render slots as/.test(excerpt), excerpt);
  assert.ok(excerpt.includes("textless solid-color geometric shapes"), excerpt);
  assert.ok(!/Floating-pill words are neutral atmospheres/.test(excerpt), excerpt);

  // P0-1 & P0-3: frame-selfcheck catches inline JS syntax error, literal placeholder, and uncentered left:50% card
  const synPath = join(dir, "compositions", "frames", "02-rail.html");
  writeFileSync(synPath, filledF2.replace(/<\/script>\s*<\/template>/, 'tl.to(".hero-line", { y: 2.2cqh, duration: 0.5 }, 0);\n</script>\n</template>'));
  const synRun = spawnSync("node", [selfcheck, synPath], { encoding: "utf8" });
  assert.strictEqual(synRun.status, 1, "unquoted CSS unit in JS must fail selfcheck");
  assert.ok(`${synRun.stdout}${synRun.stderr}`.includes("invalid_inline_script_syntax"));

  writeFileSync(synPath, filledF2.replace("Understand It", "— figure —"));
  const phRun = spawnSync("node", [selfcheck, synPath], { encoding: "utf8" });
  assert.strictEqual(phRun.status, 1, "literal placeholder must fail selfcheck");
  assert.ok(`${phRun.stdout}${phRun.stderr}`.includes("literal_placeholder"));

  writeFileSync(synPath, filledF2.replace("</style>", ".pill-card { position: absolute; left: 50%; top: 42cqh; min-width: 30cqw; }\n</style>"));
  const uncRun = spawnSync("node", [selfcheck, synPath], { encoding: "utf8" });
  assert.strictEqual(uncRun.status, 1, "uncentered left:50% pill-card must fail selfcheck");
  assert.ok(`${uncRun.stdout}${uncRun.stderr}`.includes("uncentered_left_50"));

  writeFileSync(synPath, filledF2.replace("</style>", ".pill-card { position: absolute; left: 50%; top: 42cqh; transform: translateX(-50%); }\n.right-col { position: absolute; left: 50%; width: 45cqw; }\n</style>"));
  const cenRun = spawnSync("node", [selfcheck, synPath], { encoding: "utf8" });
  assert.strictEqual(cenRun.status, 0, `centered pill-card + right-col must pass:\n${cenRun.stdout}${cenRun.stderr}`);

  console.log("shared stage skeletons + __hf runtime + selfcheck syntax/placeholder/center gates OK");
}

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testNarrowBlueprint();
  testInferCitedRules();
  testIdleLoopConflict();
  testCard(); testDispatchDocs(); testSkeletons();
  testDispatchInlinesFrontmatterTokens();
  testDispatchInlinesLedger();
  testDispatchIngestsDesignContract();
  testSelectBlueprint();
  testDualBlueprintDispatch();
  testBareModeBlueprint();
  testSceneTimelineDrift();
  testSelfcheckSkeletonUnfilled();
  testRichSkeleton();
  testResolvedConflicts();
  testRecipeSnippets();
  testSharedStageSkeletonsAndRuntime();
}
