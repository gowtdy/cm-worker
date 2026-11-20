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
    assert.ok(text.includes("recipe body") === false,
      "rule recipes are indexed, not inlined");
    assert.ok(text.includes("## frame.md design tokens"), "has excerpt");
    assert.ok(text.includes("#111") && text.includes("9cqw"), "excerpt has tokens");
    assert.ok(text.includes("No invented stats"), "hard-rule section included");
    assert.ok(!text.includes("big catalog section"), "treatment catalog excluded");
  }
  // positive path: frame 1 cites `rules: punch-in` → index cites the rule by path
  const hookText = readFileSync(
    docs.find((d) => d.frameId === "01-hook").path, "utf8");
  assert.ok(hookText.includes("punch-in"), "rule index must cite cited rules by path");
  assert.ok(hookText.includes("rules/punch-in.md"),
    "rule index line must carry the rules/<id>.md path");
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
    assert.ok(html.includes("data-duration=\"4s\"") || html.includes("data-duration=\"5s\""), "duration stamped");
    const run = spawnSync("node", [selfcheck, path], { encoding: "utf8" });
    assert.strictEqual(run.status, 0, `selfcheck FAIL:\n${run.stdout}${run.stderr}`);
  }
  console.log("skeletons OK: all pass frame-selfcheck");
}

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testCard(); testDispatchDocs(); testSkeletons();
  testDispatchInlinesFrontmatterTokens();
  testDispatchIngestsDesignContract();
}
