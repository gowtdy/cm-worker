#!/usr/bin/env node
/**
 * verify-report.mjs — one-shot deterministic diagnosis report for /aiflow-verify-frame.
 *
 * Replaces the model-driven "poke one file / run one grep per round" discovery
 * phase with a single command that prints everything the playbook needs:
 *   --preflight : prerequisites + assembly state + frames vs STORYBOARD (~3s)
 *   (full)       : preflight + transitions-verify echo + lint --json (errors +
 *                  the two tell warnings per file) + check findings grouped
 *                  and annotated + frame midpoints + AT line
 *
 * Usage:
 *   node /app/scripts/verify-report.mjs --videodir /abs/project [--preflight]
 *
 * Exit codes:
 *   0 = gates pass (preflight: all ok)          -> go straight to snapshot
 *   1 = findings with a mechanical fix          -> playbook, batched fixes,
 *                                                  rerun ONLY this report
 *   2 = infrastructure/project blocker          -> surface and stop
 *
 * Read-only. Deterministic. No dependencies. Runs every child with
 * cwd = videodir, so it never trusts the caller's shell cwd.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import {
  parseCheckJson,
  parseCheckText,
  extractJsonObject,
  checkParseContradiction,
  checkParseMismatch,
  jsonDeclaredErrorCount,
  jsonLintErrorCount,
  findingsPayload,
  FINDINGS_JSON_PREFIX,
} from "./lib/verify-check-parse.mjs";
import { matchKnownFailures, renderPrompt } from "./known-failures.mjs";

const argv = process.argv.slice(2);
const vi = argv.indexOf("--videodir");
const videodir = path.resolve(vi >= 0 ? argv[vi + 1] || "" : process.cwd());
const preflightMode = argv.includes("--preflight");

const out = [];
const say = (...a) => out.push(a.join(" "));
const rel = (p) => (p.startsWith(videodir) ? p.slice(videodir.length + 1) : p);

function run(cmd, args, { timeout = 300_000 } = {}) {
  const r = spawnSync(cmd, args, {
    cwd: videodir, timeout, encoding: "utf8", maxBuffer: 32 * 1024 * 1024,
  });
  if (r.error) {
    return { error: String(r.signal === "SIGTERM" ? `timeout after ${timeout / 1000}s` : r.error.message), status: null, stdout: "", stderr: "" };
  }
  return { error: null, status: r.status, stdout: r.stdout || "", stderr: r.stderr || "" };
}

const trunc = (s, n = 140) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

let infraError = null;

say("══ verify-report ══");
say("videodir:", videodir);
say("mode:", preflightMode ? "--preflight" : "full");

/* ── prerequisites ──────────────────────────────────────────────────────── */
say("");
say("── prerequisites ──");
const needFiles = ["hyperframes.json", "index.html", "STORYBOARD.md"];
let missingProject = [];
for (const f of needFiles) {
  const ok = fs.existsSync(path.join(videodir, f));
  say(`  ${ok ? "✓" : "✗"} ${f}`);
  if (!ok) missingProject.push(f);
}

const framesDir = path.join(videodir, "compositions", "frames");
const diskFrames = fs.existsSync(framesDir)
  ? fs.readdirSync(framesDir).filter((f) => f.endsWith(".html"))
  : [];
if (!fs.existsSync(framesDir)) {
  say("  ✗ compositions/frames/ missing");
  missingProject.push("compositions/frames/");
} else {
  say(`  frames on disk: ${diskFrames.length}`);
}

/* storyboard frames from `- src:` lines */
let storySrcs = [];
if (!missingProject.includes("STORYBOARD.md")) {
  try {
    const sb = fs.readFileSync(path.join(videodir, "STORYBOARD.md"), "utf8");
    storySrcs = [...sb.matchAll(/^\s*-\s*src:\s*compositions\/frames\/([^\s]+\.html)/gm)].map((m) => m[1]);
  } catch (e) {
    infraError = `cannot read STORYBOARD.md: ${e.message}`;
  }
}
let missingFrames = [];
if (storySrcs.length) {
  missingFrames = storySrcs.filter((s) => !diskFrames.includes(s));
  const extraFrames = diskFrames.filter((f) => !storySrcs.includes(f));
  say(`  STORYBOARD frames: ${storySrcs.length} (disk ${diskFrames.length})`);
  if (missingFrames.length) say(`  ✗ MISSING on disk: ${missingFrames.join(", ")}`);
  if (extraFrames.length) say(`  ⓘ extra on disk (not in STORYBOARD): ${extraFrames.join(", ")}`);
} else {
  say("  ⚠ STORYBOARD has no `- src:` frame lines — cannot cross-check frames against disk");
}

const preflightOk = missingProject.length === 0 && storySrcs.every((s) => diskFrames.includes(s));

/* ── assembly state ─────────────────────────────────────────────────────── */
say("");
say("── assembly ──");
let stub = false;
if (!missingProject.includes("index.html")) {
  const idx = fs.readFileSync(path.join(videodir, "index.html"), "utf8");
  const refs = (idx.match(/data-composition-src="compositions\/frames\//g) || []).length;
  if (refs === 0) {
    stub = true;
    say(`  ✗ index.html is the un-assembled STUB (0 composition refs)`);
    say(`    → Known failure #1: node /app/scripts/assemble-index.mjs --videodir ${videodir}   (then rerun this report)`);
  } else {
    say(`  ✓ index.html assembled (${refs} composition refs)`);
  }
}

if (preflightMode) {
  if (infraError) {
    console.error(`[report] ${infraError}`);
    process.exit(2);
  }
  const ok = preflightOk && !stub;
  say("");
  say(ok ? "PREFLIGHT OK" : "PREFLIGHT: actionable findings above");
  console.log(out.join("\n"));
  process.exit(ok ? 0 : missingProject.length ? 2 : 1);
}

if (infraError) {
  console.error(`[report] ${infraError}`);
  process.exit(2);
}
if (missingProject.length) {
  console.log(out.join("\n"));
  console.error(`[report] blocker: missing ${missingProject.join(", ")} — stop and report, do not invent files.`);
  process.exit(2);
}
if (stub) {
  console.log(out.join("\n"));
  console.error("[report] index is the stub — assemble first (see recovery line above), then rerun.");
  process.exit(1);
}

/* ── per-frame structural gate (frame-selfcheck) ────────────────────────── */
say("");
say("── per-frame structural gate (frame-selfcheck) ──");
const self = { pass: 0, digit: [], other: [] };
for (const fname of diskFrames) {
  const fr = run("node", ["/app/scripts/frame-selfcheck.mjs", path.join(framesDir, fname)], { timeout: 30_000 });
  if (fr.status === 0) {
    self.pass += 1;
    continue;
  }
  const fails = (fr.stdout + "\n" + fr.stderr).split("\n").filter((l) => l.includes("[FAIL]"));
  for (const l of fails) {
    const clean = l.replace(/\[FAIL\]\s*/, "").trim();
    if (clean.startsWith("digit-led selector")) self.digit.push(`${fname}: ${clean}`);
    else self.other.push(`${fname}: ${clean}`);
  }
}
say(`  PASS ${self.pass}/${diskFrames.length}`);
for (const d of self.digit.slice(0, 30)) say(`  ✗ ${d}`);
if (self.digit.length > 30) say(`    …+${self.digit.length - 30} more`);
for (const o of self.other.slice(0, 10)) say(`  ⚠ ${o}`);
if (self.other.length > 10) say(`    …+${self.other.length - 10} more`);

/* ── transitions verify ─────────────────────────────────────────────────── */
say("");
say("── transitions verify ──");
const tv = run("node", ["/app/scripts/transitions.mjs", "verify", "--videodir", videodir], { timeout: 30_000 });
if (tv.error) {
  say(`  ⚠ cannot run: ${tv.error}`);
} else {
  const lines = (tv.stdout + tv.stderr).split("\n").filter(Boolean);
  if (tv.status === 0) {
    say("  " + lines.filter((l) => l.includes("verified")).join("\n  ") || "  ✓ ok");
  } else {
    say(`  ✗ exit ${tv.status} — ${(lines[0] || "").slice(0, 100)}`);
    for (const l of lines.slice(1, 6)) say("      " + trunc(l, 110));
  }
}

/* ── lint (json) ────────────────────────────────────────────────────────── */
say("");
say("── lint ──");
const lint = run("npx", ["hyperframes", "lint", ".", "--json"], { timeout: 120_000 });
let lintOk = null;
let lintErrors = [];
let warnByCode = new Map();
let digitTell = new Map();   // file -> count
let googleImport = new Map(); // file -> count
let otherWarnCount = 0;

function parseLintJson(stdout) {
  const first = stdout.indexOf("{");
  const last = stdout.lastIndexOf("}");
  if (first === -1 || last <= first) return null;
  try {
    return JSON.parse(stdout.slice(first, last + 1));
  } catch {
    return null;
  }
}

if (lint.error) {
  say(`  ⚠ cannot run lint: ${lint.error}`);
} else {
  const j = parseLintJson(lint.stdout);
  if (j && Array.isArray(j.findings)) {
    lintOk = !!j.ok;
    say(`  ok=${j.ok}  errors=${j.errorCount}  warnings=${j.warningCount}  infos=${j.infoCount}  files=${j.filesScanned ?? "?"}`);
    for (const f of j.findings) {
      const file = rel(f.file || "?");
      if (f.severity === "error") {
        lintErrors.push({ file, code: f.code, msg: trunc(f.message, 130) });
      } else if (f.severity === "warning") {
        const e = warnByCode.get(f.code) || 0;
        warnByCode.set(f.code, e + 1);
        if (f.code === "id_requires_css_escape") digitTell.set(file, (digitTell.get(file) || 0) + 1);
        if (f.code === "google_fonts_import") googleImport.set(file, (googleImport.get(file) || 0) + 1);
      }
    }
    if (lintErrors.length) {
      say("  ERRORS:");
      for (const e of lintErrors) say(`    ✗ ${e.code}  ${e.file} — ${e.msg}`);
    }
    if (digitTell.size) {
      say("  digit-led id names (id_requires_css_escape warnings) — the tell, NOT the defect:");
      for (const [file, n] of digitTell) say(`    ${file}: ${n}`);
      say("    (digit-led ids are legal when selectors use [id=…]/[class~=…] or getElementById — the structural gate above is the proof)");
    }
    if (googleImport.size) {
      say("  google_fonts_import (warning, non-blocker):");
      for (const [file, n] of googleImport) say(`    ${file}: ${n}`);
    }
    const others = [...warnByCode].filter(([c]) => c !== "id_requires_css_escape" && c !== "google_fonts_import");
    if (others.length) say("  other warnings: " + others.map(([c, n]) => `${c}×${n}`).join(", "));
  } else {
    say(`  ⚠ lint --json unparseable (exit ${lint.status}); raw tail:`);
    say("    " + trunc((lint.stdout + lint.stderr).split("\n").filter(Boolean).slice(-3).join(" | "), 200));
    lintOk = lint.status === 0;
  }
}

/* ── check ──────────────────────────────────────────────────────────────── */
say("");
say("── check ──");
// Prefer --json: Layout lines are `✗ t=… content_overlap …` without `code:`,
// so the old text regex silently dropped every layout error (2026-11-22).
const check = run("npx", ["hyperframes", "check", ".", "--json"], { timeout: 300_000 });
const checkOk = check.status === 0;
let chk = { errors: [], warns: [], infos: [] };
let checkParseMode = "none";
let checkContradiction = false;
let checkMismatch = null;
if (check.error) {
  say(`  ⚠ cannot run check: ${check.error}`);
} else {
  const json = extractJsonObject(check.stdout);
  const contradictionOpts = {};
  if (json) {
    chk = parseCheckJson(json, { root: videodir });
    checkParseMode = "json";
    contradictionOpts.declaredErrorCount = jsonDeclaredErrorCount(json);
    contradictionOpts.lintErrorCount = jsonLintErrorCount(json);
    checkMismatch = checkParseMismatch(contradictionOpts.declaredErrorCount, chk.errors.length);
  } else {
    chk = parseCheckText(`${check.stdout}\n${check.stderr}`);
    checkParseMode = "text-fallback";
    say("  ⚠ check --json unparseable; used text fallback parser");
  }
  checkContradiction = checkParseContradiction(check.status, chk, contradictionOpts);
  const isCaptionFP = (c) =>
    c.code === "text_box_overflow" && /#caption-word|\.caption-line/.test(c.msg);
  say(
    `  exit=${check.status}  parse=${checkParseMode}  ✗=${chk.errors.length}  ⚠=${chk.warns.length}  ℹ=${chk.infos.length}`,
  );
  // Use "in <file>" — never "@path". Claude Code treats @mentions as file
  // attachments and auto-injects whole HTML into the fix prompt (~70KB).
  const printGroup = (list) => {
    for (const c of list) {
      const tag = isCaptionFP(c) ? "  (expected false-positive — see known-failures caption-text-box-overflow)" : "";
      // Render the CLI's own fixHint for codes the catalog doesn't own
      // (content_overlap's fixHint is the same generic allow-overlap line for
      // every finding, so skip it there — the catalog triages it precisely).
      const hint = c.fixHint && c.code !== "content_overlap" ? `  [hint: ${trunc(c.fixHint, 200)}]` : "";
      const where = c.file ? ` in ${c.file}` : "";
      say(`  ${c.mark} [${c.section}] ${c.code}${where} — ${c.msg}${hint}${tag}`);
    }
    if (list.length > 20) say(`    …+${list.length - 20} more`);
  };
  if (chk.errors.length) { say("  errors:"); printGroup(chk.errors.slice(0, 20)); }
  if (chk.warns.length) { say("  warnings:"); printGroup(chk.warns.slice(0, 20)); }
  if (chk.infos.length) {
    const agg = new Map();
    for (const c of chk.infos) agg.set(c.code, (agg.get(c.code) || 0) + 1);
    say("  infos (count only): " + [...agg].map(([c, n]) => `${c}×${n}`).join(", "));
  }
  if (checkMismatch) {
    say(
      `  ✗ PARSER/INFRA: check JSON declares errorCount that parsed findings do not match — `
      + `declared=${checkMismatch.declared} parsed=${checkMismatch.parsed} `
      + "(partial miss; fix the parser before trusting the actionable list)",
    );
  } else if (checkContradiction) {
    say(
      "  ✗ PARSER/INFRA: check exited non-zero but 0 error findings were parsed — "
      + "refusing GATE FAIL with empty actionable list (would burn a no-op model turn)",
    );
  }
  if (checkContradiction) {
    const tail = (check.stdout || check.stderr || "").split("\n").filter(Boolean).slice(-8);
    for (const line of tail) say(`    ${trunc(line, 160)}`);
  }
}

/* ── midpoints ──────────────────────────────────────────────────────────── */
say("");
say("── frame midpoints ──");
const mp = run("node", ["/app/scripts/frame-midpoints.mjs", "--videodir", videodir], { timeout: 30_000 });
let atLine = "";
if (mp.error || !mp.stdout.trim()) {
  say(`  ⚠ unavailable: ${mp.error || "empty output"}`);
} else {
  say(mp.stdout.trimEnd());
  const lines = mp.stdout.split("\n").filter((l) => l.trim());
  atLine = lines[lines.length - 1];
  say("  AT: " + atLine);
}

/* ── actionable summary (known-failures single source) ───────────────────── */
say("");
say("── actionable summary ──");
const ctx = {
  videodir,
  stub,
  digitSelectors: self.digit,
  lintErrors,
  checkErrors: chk.errors,
  transitions: { status: tv.status, error: tv.error },
  missingFrames,
  digitTell,
  googleImport,
};
let n = 0;
for (const { entry, hits } of matchKnownFailures(ctx)) {
  const text = (renderPrompt(entry, ctx, hits) || "").trim();
  const [head, ...rest] = text.split("\n");
  if (entry.mode === "model" || entry.mode === "autofix") {
    say(`  ${++n}) ${entry.title} — ${head}`);
  } else {
    say(`  • ${entry.title} (${entry.mode === "ignore" ? "known false-positive" : "non-blocking"}) — ${head}`);
  }
  for (const line of rest) say(`     ${line}`);
}
const realErrors = chk.errors.length + lintErrors.length + self.digit.length;
const gate = (lintOk === true) && checkOk && self.digit.length === 0 && missingFrames.length === 0 && !tv.error && tv.status === 0;
say("");
say(`GATE: ${gate ? "PASS ✓ (lint+check+structural+transitions — go to snapshot)" : `FAIL ✗ (lint ok=${lintOk}, check exit=${check.status === null ? "n/a" : check.status}, digit-led selectors=${self.digit.length}, missing frames=${missingFrames.length}, transitions=${tv.error ? "n/a" : tv.status === 0 ? "ok" : tv.status}, ${realErrors} error finding(s))`}`);
if (!gate) {
  const payload = findingsPayload(chk.errors);
  payload.lint_errors = lintErrors.length;
  payload.digit_led_selectors = self.digit.length;
  payload.transitions_ok = !tv.error && tv.status === 0;
  say(FINDINGS_JSON_PREFIX + JSON.stringify(payload));
}

console.log(out.join("\n"));
if (lint.error || check.error || tv.error || checkContradiction) process.exit(2);
process.exit(gate ? 0 : 1);