#!/usr/bin/env node
/**
 * caption-pacing.mjs — deterministic per-frame VO pacing table for the visual
 * design stage. Replaces ad-hoc `jq` / inline-python parsing: the environment
 * does not ship jq, and hand-invented shell snippets are failure round-trips
 * (see /app/log/docs/build-frame-visual-13m-analysis.md — command-not-found,
 * then a syntax error, before the third attempt succeeded).
 *
 * Usage:
 *   node /app/scripts/caption-pacing.mjs --videodir /abs/project
 *
 * Exit 0 = table printed; exit 2 = caption_groups.json absent — that is NOT a
 * blocker: pace reveals to the STORYBOARD durations instead.
 */

import { readFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

const i = process.argv.indexOf("--videodir");
const dir = resolve(i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : process.cwd());
const path = join(dir, "caption_groups.json");

if (!existsSync(path)) {
  console.error("[caption-pacing] caption_groups.json not found — pace to STORYBOARD durations instead (not a blocker)");
  process.exit(2);
}

const data = JSON.parse(readFileSync(path, "utf8"));
const groups = Array.isArray(data.groups) ? data.groups : [];
const byFrame = new Map();
for (const g of groups) {
  if (typeof g.frame !== "number") continue;
  if (!byFrame.has(g.frame)) byFrame.set(g.frame, []);
  byFrame.get(g.frame).push(g);
}
const fmt = (x) => Number(x).toFixed(2);

console.log(`total_duration_s=${fmt(data.total_duration_s)}  frames=${byFrame.size}  groups=${groups.length}`);
for (const [frame, gs] of [...byFrame.entries()].sort((a, b) => a[0] - b[0])) {
  const start = Math.min(...gs.map((g) => Number(g.start)));
  const end = Math.max(...gs.map((g) => Number(g.end)));
  console.log(`FRAME ${frame}  (abs ${fmt(start)}–${fmt(end)}, dur ${fmt(end - start)}s)`);
  for (const g of gs) console.log(`    [${fmt(g.start)}–${fmt(g.end)}] ${g.text}`);
}