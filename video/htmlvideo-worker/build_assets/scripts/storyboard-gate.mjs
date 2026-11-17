#!/usr/bin/env node
/**
 * storyboard-gate.mjs — deterministic structural gate for /aiflow-build-frame-visual.
 *
 * Two jobs:
 *   F1 preflight  — run FIRST, before designing: blocks re-design on a board whose
 *                   frames are already `status: animated` (the design was consumed
 *                   by build-frame-html; rewriting it would silently diverge the
 *                   storyboard from the built frames), and short-circuits when a
 *                   previous run already completed the design (idempotent skip).
 *   F3 post-write — run after the single Write: verifies every frame has time-coded
 *                   Scenes (count, window monotonicity, coverage ≥90% of duration =
 *                   no front-loading), invented focal/roles, valid transition_in and
 *                   src line, `## Video direction` present, status still outline;
 *                   src file NOT on disk is a WARNING here — the .html is the product
 *                   of the NEXT stage (build-frame-html) — as are unknown backtick
 *                   motion names (review, not gate).
 *
 * Usage:
 *   node /app/scripts/storyboard-gate.mjs --videodir /abs/project
 *
 * Exit: 0 = gate passes (or design already complete — verify-only) |
 *       1 = findings (including "already animated" blocker)          |
 *       2 = malformed STORYBOARD / missing files (infra)
 */

import { readFileSync, existsSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

const i = process.argv.indexOf("--videodir");
const dir = resolve(i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : process.cwd());
const sbPath = join(dir, "STORYBOARD.md");

let fail = 0, warn = 0;
const say = (k, msg) => { console.log(`  ${k} ${msg}`); if (k === "✗") fail++; else if (k === "⚠") warn++; };

if (!existsSync(sbPath)) {
  console.error("[storyboard-gate] STORYBOARD.md not found");
  process.exit(2);
}
const sb = readFileSync(sbPath, "utf8");
if (!/^## Frame \d+/m.test(sb)) {
  console.error("[storyboard-gate] STORYBOARD.md has no `## Frame N` sections — malformed");
  process.exit(2);
}

/* ── F1: animated blocker / complete-skip detection ────────────────────── */
const parts = sb.split(/^(?=## Frame\s+\d+)/gm);
const frameBlocks = parts.slice(1).map((text) => {
  const num = /^## Frame\s+(\d+)/m.exec(text)?.[1] ?? "?";
  return { num, text };
});
const animBlock = sb.match(/(?:^|\n)## Frame[\s\S]*?^status:\s*\S+/gm);
const statusRe = /^-\s*status:\s*(\S+)/m;
const animated = frameBlocks.filter((f) => {
  const m = statusRe.exec(f.text);
  return m && m[1] === "animated";
});
const scenesRe = /^Scene\s+\d+\s*\(/m;
const complete = frameBlocks.every((f) => scenesRe.test(f.text)) && /^## Video direction/m.test(sb);

console.log(`════ storyboard-gate: ${frameBlocks.length} frames ════`);
if (animated.length > 0) {
  say("✗", `F1 BLOCKER: ${animated.length} frame(s) already marked \`status: animated\` (frames ${animated.map((f) => f.num).join(", ")}) — the visual design was consumed by the build stage; do NOT re-design. Stop and report.`);
  process.exit(1);
}
if (complete && frameBlocks.length > 0) {
  const scenes = [...sb.matchAll(/^Scene\s+\d+/gm)].length;
  console.log(`  ✓ design already complete (${scenes} Scenes, ## Video direction present) — verify-only idempotent skip (checks below still run)`);
}
if (frameBlocks.every((f) => !scenesRe.test(f.text))) {
  console.log("  i pre-design outline board (no Scenes yet) — proceeding is expected");
}

/* ── vocabulary for motion-name review ─────────────────────────────────── */
const rulesDir = "/app/skills/hyperframes-animation/rules";
const bpDir = "/app/skills/hyperframes-animation/blueprints";
const namesOf = (d) => {
  try { return readdirSync(d).filter((f) => f.endsWith(".md")).map((f) => f.slice(0, -3)); } catch { return []; }
};
const vocab = new Set([...namesOf(rulesDir), ...namesOf(bpDir)]);
try {
  const reg = JSON.parse(readFileSync("/app/scripts/lib/transitions.json", "utf8"));
  for (const r of reg.transitions || []) if (r?.name) vocab.add(r.name.toLowerCase());
} catch {}
const EASE_RE = /^(power[1-4]|sine|quad|cubic|quart|quint|expo|circ|back|elastic|bounce)\.(in|out|inOut)$/;

/* ── F3: per-frame checks ──────────────────────────────────────────────── */
const windowRe = /^Scene\s+\d+\s*\(\s*([\d.]+)s?\s*[–—-]\s*([\d.]+)s?\s*\)/m;
const tickRe = /`([^`]+)`/g;

for (const f of frameBlocks) {
  const head = `Frame ${f.num}`;
  const durM = /^-\s*duration:\s*([\d.]+)s?/m.exec(f.text);
  const dur = durM ? Number(durM[1]) : NaN;
  if (!(dur > 0)) { say("✗", `${head}: missing/invalid - duration`); continue; }
  const stM = statusRe.exec(f.text);
  const srcM = /^-\s*src:\s*(compositions\/frames\/\S+\.html)/m.exec(f.text);
  if (!srcM) say("✗", `${head}: missing - src`);
  else if (!existsSync(join(dir, srcM[1]))) {
    // The .html named by src is the output of the NEXT stage (build-frame-html):
    // at this stage a missing file on disk is the expected steady state, not a
    // defect of this write. Only a consumed board is supposed to have it on disk
    // (status: animated) — that board is already stopped by the F1 blocker above,
    // so downgrade to WARN here. Observed cost of old behavior: gate exit 1 on all
    // 8 fresh frames, forcing a ~70s re-read of this source to re-derive "verify-only".
    if (stM?.[1] === "animated") say("✗", `${head}: src does not exist on disk (${srcM[1]}) — consumed design must match built frames`);
    else say("⚠", `${head}: src not on disk (${srcM[1]}) — expected at this stage (HTML is built by the next stage)`);
  }
  if (!stM || stM[1] !== "outline") say("✗", `${head}: status must stay \`outline\` at this stage (found: ${stM?.[1] ?? "missing"})`);
  const tiM = /^-\s*transition_in:\s*(\S+)/m.exec(f.text);
  if (!tiM) say("✗", `${head}: missing - transition_in`);
  else {
    const type = tiM[1].toLowerCase();
    if (!["cut", "none", ""].includes(type) && !vocab.has(type)) say("✗", `${head}: transition_in "${tiM[1]}" not a known transition type`);
  }
  const hasFocal = /^-\s*(focal|roles):/m.test(f.text);
  if (!hasFocal) say("✗", `${head}: no invented - focal / - roles`);
  const wins = [...f.text.matchAll(/^Scene\s+\d+\s*\(\s*([\d.]+)s?\s*[–—-]\s*([\d.]+)s?\s*\)/gm)];
  if (wins.length < 2) say("✗", `${head}: only ${wins.length} Scene(s) — need ≥2 (reveals paced across the full duration)`);
  else {
    let prevEnd = -1;
    wins.forEach((w, idx) => {
      const a = Number(w[1]), b = Number(w[2]);
      if (!(b > a)) say("✗", `${head}: Scene ${idx + 1} window inverted (${a}–${b})`);
      if (idx === 0 && Math.abs(a) > 0.05) say("✗", `${head}: first Scene does not start at 0.0 (starts ${a})`);
      if (idx > 0 && a < prevEnd - 0.05) say("✗", `${head}: Scene ${idx + 1} overlaps previous (${a} < ${prevEnd})`);
      prevEnd = Math.max(prevEnd, b);
    });
    if (prevEnd > dur + 0.2) say("✗", `${head}: last Scene ends ${prevEnd}s beyond duration ${dur}s`);
    if (prevEnd < 0.9 * dur) say("✗", `${head}: coverage ${(100 * prevEnd / dur).toFixed(0)}% of duration — front-loading`);
  }
  const unknowns = new Set();
  for (const t of f.text.matchAll(tickRe)) {
    const tok = t[1].trim();
    if (vocab.has(tok) || EASE_RE.test(tok) || /^[\d.]+$/.test(tok)) continue;
    unknowns.add(tok);
  }
  if (unknowns.size) {
    say("⚠", `${head}: unknown motion names (review): ${[...unknowns].join(", ")}`);
  }
}

if (!/^## Video direction/m.test(sb)) say("✗", "missing `## Video direction` block");

if (fail > 0) {
  console.log(`GATE: FAIL ✗ — ${fail} finding(s) to fix (${warn} warning(s)); fix with targeted Edits, rerun this script`);
  process.exit(1);
}
console.log(`GATE: PASS ✓ — ${frameBlocks.length} frame(s), no front-loading, status/transition lines valid, src line valid${warn ? ` (${warn} warning(s) — see ⚠ lines; src-not-on-disk is expected at this stage, do not create HTML to "fix" it)` : ""}`);
process.exit(0);