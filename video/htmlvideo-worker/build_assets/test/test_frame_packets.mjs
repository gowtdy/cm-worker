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
}
