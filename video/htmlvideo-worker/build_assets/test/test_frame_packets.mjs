import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert";

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

if (process.argv[1] && process.argv[1].endsWith("test_frame_packets.mjs")) {
  testCard();
}