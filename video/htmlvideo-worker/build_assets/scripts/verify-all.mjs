#!/usr/bin/env node
/**
 * verify-all.mjs — one-command mechanical verify loop for /aiflow-verify-frame.
 *
 * Two phases, selected with --phase all|prepare|verify (default all):
 *
 *   prepare — once at the start of each verify session. Every step is
 *      idempotent. Exit 0 means ready; exit 2 is a blocker (never 1):
 *      1. known-failure autofix
 *      2. font-imports — idempotent marked @import / brand @font-face.
 *         Non-fatal. Skipped when HTMLVIDEO_FONT_IMPORTS is 0/false/off/no.
 *      3. transitions inject — idempotent `inject` (strips any prior stamp
 *         first).
 *
 *   verify — the part the fix loop reruns. Exit 0/1/2 matches verify-report:
 *      full report (the single diagnosis + gate; its output is teed verbatim),
 *      then on gate pass only frame-midpoints → snapshot → verify-pixels, so
 *      the contact sheet exists on one run.
 *
 *   all (default) — prepare, then verify. A prepare exit 2 stops before verify.
 *
 * Usage:
 *   node /app/scripts/verify-all.mjs --videodir /abs/project
 *   node /app/scripts/verify-all.mjs --videodir /abs/project --phase prepare
 *   node /app/scripts/verify-all.mjs --videodir /abs/project --phase verify
 *
 * Exit codes:
 *   prepare: 0 = ready, 2 = blocker
 *   verify / all (mirror verify-report):
 *     0 = gate pass + snapshot written + pixels no-FAIL
 *     1 = findings to fix (report exit 1, or pixels has a FAIL) -> fix & rerun
 *     2 = infrastructure / project blocker -> surface and stop
 *
 * Runs every child with cwd = videodir (never trusts the caller's shell cwd).
 * Mutates: prepare: autofix + font-imports + transitions inject; verify: snapshot only.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { KNOWN_FAILURES } from "./known-failures.mjs";

const argv = process.argv.slice(2);
const vi = argv.indexOf("--videodir");
const videodir = vi >= 0 && argv[vi + 1] ? path.resolve(argv[vi + 1]) : null;
if (!videodir) {
  console.error("verify-all.mjs: --videodir <dir> is required");
  process.exit(2);
}

const pi = argv.indexOf("--phase");
const phase = pi >= 0 && argv[pi + 1] ? argv[pi + 1] : "all";
if (!["all", "prepare", "verify"].includes(phase)) {
  console.error(`verify-all.mjs: --phase must be all|prepare|verify (got ${phase})`);
  process.exit(2);
}

function run(cmd, args, { timeout = 300_000 } = {}) {
  const r = spawnSync(cmd, args, {
    cwd: videodir, timeout, encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
  });
  if (r.error) {
    return {
      error: r.signal === "SIGTERM"
        ? `timeout after ${timeout / 1000}s`
        : r.error.message,
      status: null, stdout: "", stderr: "",
    };
  }
  return { error: null, status: r.status, stdout: r.stdout || "", stderr: r.stderr || "" };
}

const combined = (r) => (r.stdout + "\n" + r.stderr).trim();

function prepare() {
  /* ── known-failure autofix pass (deterministic, no model) ───────────── */
  /* Catalog entries with mode="autofix" run here, in catalog order, before the
   * font-imports / transitions steps so their fixes land upstream of the checks
   * that depend on them (assemble before transitions, duration-strip before
   * transitions inject).                                  */
  const isStub = () => {
    try {
      const idx = fs.readFileSync(path.join(videodir, "index.html"), "utf8");
      return (idx.match(/data-composition-src="compositions\/frames\//g) || []).length === 0;
    } catch {
      return false;
    }
  };
  console.log("\n── known-failure autofix pass ──");
  {
    const autofixCtx = {
      videodir,
      stub: isStub(),
      digitSelectors: [], lintErrors: [], checkErrors: [],
      transitions: { status: 0, error: null }, missingFrames: [],
      digitTell: new Map(), googleImport: new Map(),
    };
    let changedAny = false;
    for (const entry of KNOWN_FAILURES) {
      if (entry.mode !== "autofix" || typeof entry.autofix !== "function") continue;
      const hits = entry.match(autofixCtx) || [];
      if (!hits.length) continue;
      try {
        const r = entry.autofix(autofixCtx, hits);
        const mark = r.changed ? "✓" : "✗";
        console.log(`  ${mark} ${entry.id}: ${r.note}${r.changed ? ` (${hits.length} hit(s))` : ""}`);
        if (r.changed) changedAny = true;
      } catch (e) {
        console.log(`  ✗ ${entry.id}: autofix crashed: ${e.message}`);
        return 2;
      }
    }
    if (isStub()) {
      console.log("  ✗ index.html is still an un-assembled stub after autofix");
      return 2;
    }
    console.log(changedAny
      ? "  → autofix applied changes; continuing to font-imports + transitions inject."
      : "  → no autofix hits (or autofixes idempotent); continuing.");
  }

  /* ── font-imports (non-fatal) ────────────────────────────────────────── */
  console.log("\n── font-imports ──");
  const fontFlag = (process.env.HTMLVIDEO_FONT_IMPORTS || "").trim().toLowerCase();
  if (fontFlag === "0" || fontFlag === "false" || fontFlag === "off" || fontFlag === "no") {
    console.log(`  ⚠ skipped (HTMLVIDEO_FONT_IMPORTS=${fontFlag})`);
  } else {
    const fi = run(
      "node",
      ["/app/scripts/font-imports.mjs", "--videodir", videodir, "--scope", "all"],
      { timeout: 60_000 },
    );
    const text = combined(fi);
    if (fi.error || fi.status == null || fi.status > 1) {
      console.log("  ⚠ font-imports failed: " + (fi.error || text || `exit ${fi.status}`));
    } else if (fi.status === 1) {
      console.log("  ⚠ unresolved families (non-fatal):\n  " + text.split("\n").join("\n  "));
    } else {
      console.log("  " + (text || "ok").split("\n").join("\n  "));
      console.log("  ✓ font-imports");
    }
  }

  /* ── transitions inject (idempotent) ─────────────────────────────────── */
  console.log("\n── transitions inject ──");
  {
    const inj = run("node", ["/app/scripts/transitions.mjs", "inject", "--videodir", videodir], { timeout: 60_000 });
    if (inj.error || inj.status !== 0) {
      console.log("  ✗ inject failed: " + (inj.error || combined(inj)));
      console.log("  → prepare blocker (exit 2) — not a model fix");
      return 2;
    } else {
      console.log("  " + combined(inj).split("\n").join("\n  "));
      console.log("  ✓ transitions inject");
    }
  }

  console.log("verify-all: prepare done ✓");
  return 0;
}

function verify() {
  /* ── full report ─────────────────────────────────────────────────────── */
  console.log("\n── verify-report (full) ──");
  const rep = run("node", ["/app/scripts/verify-report.mjs", "--videodir", videodir], { timeout: 600_000 });
  if (rep.stdout) process.stdout.write(rep.stdout + (rep.stdout.endsWith("\n") ? "" : "\n"));
  if (rep.stderr) process.stderr.write(rep.stderr + (rep.stderr.endsWith("\n") ? "" : "\n"));
  if (rep.error) {
    console.error(`verify-all: verify-report ${rep.error}`);
    return 2;
  }
  if (rep.status !== 0) {
    console.log(`\nverify-all: report exit ${rep.status} — fix the findings above, then rerun the verify phase (prepare already ran).`);
    return rep.status;
  }

  /* ── snapshot + pixels (gate passed) ──────────────────────────────────── */
  console.log("\n── snapshot + pixels (gate passed) ──");
  const mp = run("node", ["/app/scripts/frame-midpoints.mjs", "--videodir", videodir], { timeout: 30_000 });
  if (mp.error || !mp.stdout.trim()) {
    console.error(`verify-all: frame midpoints unavailable: ${mp.error || "empty output"}`);
    return 2;
  }
  const AT = mp.stdout.trim();
  if (mp.stderr.trim()) console.log("  " + mp.stderr.trim().split("\n").join("\n  "));
  console.log("  AT:", AT);
  const snap = run("npx", ["hyperframes", "snapshot", videodir, "--at", AT], { timeout: 300_000 });
  if (snap.error || snap.status !== 0) {
    console.error(`verify-all: snapshot failed: ${snap.error || combined(snap)}`);
    return 2;
  }
  if (snap.stdout) process.stdout.write(snap.stdout);
  if (snap.stderr) process.stderr.write(snap.stderr);
  const vp = run("python3", ["/app/scripts/verify-pixels.py", "--videodir", videodir], { timeout: 120_000 });
  if (vp.stdout) process.stdout.write(vp.stdout + (vp.stdout.endsWith("\n") ? "" : "\n"));
  if (vp.stderr) process.stderr.write(vp.stderr);
  if (vp.error) {
    console.error(`verify-all: verify-pixels unavailable: ${vp.error}`);
    return 2;
  }

  console.log(`\nverify-all: GATE PASS ✓ — contact sheet written, pixels ${vp.status === 0 ? "clean" : `FAIL (${vp.status})`}.`);
  return vp.status === 0 ? 0 : 1;
}

console.log(`══ verify-all [${phase}] ══`);
console.log("videodir:", videodir);
let rc = 0;
if (phase === "all" || phase === "prepare") rc = prepare();
if (rc === 0 && (phase === "all" || phase === "verify")) rc = verify();
process.exit(rc);
