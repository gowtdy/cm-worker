#!/usr/bin/env node
// frame-midpoints.mjs — emit comma-separated scene midpoints for
// `npx hyperframes snapshot --at …`.
//
// Each mounted frame wrapper in index.html carries data-composition-src plus
// data-start / data-duration. Midpoint = start + duration / 2.
//
//   node frame-midpoints.mjs --videodir .
//   node frame-midpoints.mjs --index ./index.html

import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const flag = (argv, name, def) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : def;
};

const die = (msg) => {
  console.error(`frame-midpoints: ${msg}`);
  process.exit(1);
};

const r3 = (x) => Number(x.toFixed(3));

/** @returns {{ id: string, start: number, duration: number, mid: number }[]} */
export function parseSceneMidpoints(html) {
  const scenes = [];
  const re = /<div\b([\s\S]*?)>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const attrs = m[1];
    if (!/\bdata-composition-src=/.test(attrs)) continue;
    const idm = attrs.match(/\bid="el-([A-Za-z0-9_-]+)"/);
    const startM = attrs.match(/\bdata-start="([\d.]+)"/);
    const durM = attrs.match(/\bdata-duration="([\d.]+)"/);
    if (!startM || !durM) continue;
    const start = Number(startM[1]);
    const duration = Number(durM[1]);
    if (!Number.isFinite(start) || !Number.isFinite(duration) || duration <= 0) continue;
    scenes.push({
      id: idm?.[1] ?? `scene-${scenes.length}`,
      start,
      duration,
      mid: r3(start + duration / 2),
    });
  }
  return scenes;
}

const argv = process.argv.slice(2);
const videodir = resolve(flag(argv, "videodir", "."));
const indexPath = resolve(flag(argv, "index", join(videodir, "index.html")));

if (!existsSync(indexPath)) die(`index.html not found at ${indexPath}`);

const html = readFileSync(indexPath, "utf8");
const scenes = parseSceneMidpoints(html);
if (scenes.length === 0) die(`no scene wrappers with data-composition-src in ${indexPath}`);

for (const s of scenes) {
  console.error(`  ${s.id}: start=${s.start}s dur=${s.duration}s → mid=${s.mid}s`);
}

process.stdout.write(scenes.map((s) => s.mid).join(","));
