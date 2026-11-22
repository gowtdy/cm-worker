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
  console.log("design contract ingestion OK");
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

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testCard(); testDispatchDocs(); testSkeletons();
  testDispatchInlinesFrontmatterTokens();
  testDispatchIngestsDesignContract();
  testSelectBlueprint();
  testDualBlueprintDispatch();
  testBareModeBlueprint();
  testSceneTimelineDrift();
  testSelfcheckSkeletonUnfilled();
  testRichSkeleton();
  testResolvedConflicts();
  testRecipeSnippets();
}
