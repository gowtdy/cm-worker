#!/usr/bin/env node
/**
 * font-imports.mjs — idempotent font delivery for frames and captions.
 *
 *   node /app/scripts/font-imports.mjs --videodir <dir>
 *     [--scope frames|captions|all] [--dry-run] [--no-prune] [--strip] [--json]
 *
 * Exit 0: wrote (or would write) with no unresolved families.
 * Exit 1: at least one used family is still uncovered (primary orphan, etc.).
 * Exit 2: usage error.
 * Missing frame.md: warning on stderr, exit 0, no writes.
 */

import fs from "node:fs";
import path from "node:path";
import {
  loadCliAliases,
  parseFrameFamilies,
  parseBrandFaces,
  planFile,
  stripManaged,
} from "./lib/fonts.mjs";

function usage() {
  console.error(
    "usage: node font-imports.mjs --videodir <dir> [--scope frames|captions|all] [--dry-run] [--no-prune] [--strip] [--json]",
  );
  process.exit(2);
}

function parseArgs(argv) {
  const out = {
    videodir: null,
    scope: "all",
    dryRun: false,
    prune: true,
    strip: false,
    json: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--videodir") {
      const v = argv[++i];
      if (!v || v.startsWith("--")) usage();
      out.videodir = v;
    } else if (a === "--scope") {
      const v = argv[++i];
      if (!v || v.startsWith("--")) usage();
      if (v !== "frames" && v !== "captions" && v !== "all") usage();
      out.scope = v;
    } else if (a === "--dry-run") out.dryRun = true;
    else if (a === "--no-prune") out.prune = false;
    else if (a === "--strip") out.strip = true;
    else if (a === "--json") out.json = true;
    else usage();
  }
  if (!out.videodir) usage();
  return out;
}

function listFiles(videodir, scope) {
  const files = [];
  if (scope === "frames" || scope === "all") {
    const dir = path.join(videodir, "compositions", "frames");
    if (fs.existsSync(dir)) {
      for (const name of fs.readdirSync(dir).sort()) {
        if (name.endsWith(".html") && !name.startsWith(".")) {
          files.push(path.join(dir, name));
        }
      }
    }
  }
  if (scope === "captions" || scope === "all") {
    const cap = path.join(videodir, "compositions", "captions.html");
    if (fs.existsSync(cap)) files.push(cap);
  }
  return files;
}

function rel(videodir, file) {
  return path.relative(videodir, file).split(path.sep).join("/");
}

const args = parseArgs(process.argv.slice(2));
const videodir = path.resolve(args.videodir);
if (!fs.existsSync(videodir) || !fs.statSync(videodir).isDirectory()) {
  console.error(`font-imports: not a directory: ${videodir}`);
  process.exit(2);
}

const framePath = path.join(videodir, "frame.md");
if (!fs.existsSync(framePath)) {
  console.warn(`font-imports: frame.md missing at ${framePath} — skip`);
  process.exit(0);
}

let frameMd = "";
try {
  frameMd = fs.readFileSync(framePath, "utf8");
} catch (err) {
  console.warn(`font-imports: cannot read frame.md (${err.message}) — skip`);
  process.exit(0);
}

const frameFamilies = parseFrameFamilies(frameMd);
const brandFaces = parseBrandFaces(frameMd);
const aliases = loadCliAliases();
const paths = listFiles(videodir, args.scope);
const files = [];

for (const file of paths) {
  let html;
  try {
    html = fs.readFileSync(file, "utf8");
  } catch (err) {
    console.warn(`font-imports: skip ${file} (${err.message})`);
    continue;
  }
  const rec = { file: rel(videodir, file), abs: file };
  if (args.strip) {
    const next = stripManaged(html);
    rec.changed = next !== html;
    rec.injected = [];
    rec.faces = [];
    rec.dropped = [];
    rec.unresolved = [];
    rec.html = next;
  } else {
    const plan = planFile(html, {
      frameFamilies,
      brandFaces,
      aliases,
      prune: args.prune,
    });
    rec.changed = plan.changed;
    rec.injected = plan.injected;
    rec.faces = plan.faces;
    rec.dropped = plan.dropped;
    rec.unresolved = plan.unresolved;
    rec.html = plan.html;
  }
  if (rec.changed && !args.dryRun) {
    try {
      fs.writeFileSync(file, rec.html);
    } catch (err) {
      console.warn(`font-imports: write failed ${file} (${err.message})`);
      rec.unresolved = uniqUnresolved(rec.unresolved, ["<write-failed>"]);
      rec.changed = false;
    }
  }
  files.push(rec);
}

function uniqUnresolved(list, extra) {
  return [...new Set([...(list || []), ...extra])];
}

const unresolved = [];
for (const f of files) {
  for (const family of f.unresolved) unresolved.push({ file: f.file, family });
}
const changed = files.filter((f) => f.changed).length;
const report = {
  scope: args.scope,
  videodir,
  dryRun: args.dryRun,
  strip: args.strip,
  aliases: aliases.keys ? "loaded" : "conservative",
  frameFamilies,
  files: files.map((f) => ({
    file: f.file,
    changed: f.changed,
    injected: f.injected,
    faces: f.faces,
    dropped: f.dropped,
    unresolved: f.unresolved,
  })),
  changed,
  unresolved,
};

if (args.json) {
  console.log(JSON.stringify(report));
} else {
  console.log(
    `font-imports scope=${args.scope} files=${files.length} changed=${changed} unresolved=${unresolved.length}` +
      (args.dryRun ? " (dry-run)" : ""),
  );
  for (const f of files) {
    if (!f.changed && !f.unresolved.length) continue;
    const bits = [];
    if (f.changed && f.injected.length) bits.push("+import " + f.injected.join(", "));
    if (f.changed && f.faces.length) bits.push("+face " + f.faces.join(", "));
    if (f.changed && f.dropped.length) bits.push("-drop " + f.dropped.join(", "));
    if (f.unresolved.length) bits.push("!unresolved " + f.unresolved.join(", "));
    if (bits.length) console.log(`  ${f.file}  ${bits.join("  ")}`);
  }
}

process.exit(unresolved.length && !args.strip ? 1 : 0);
