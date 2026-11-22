#!/usr/bin/env node
/**
 * verify-all.mjs — one-command mechanical verify loop for /aiflow-verify-frame.
 *
 * Drives the whole deterministic phase in a single call so the orchestrating
 * model makes ONE decision instead of re-running the individual gates:
 *   0. font-imports — idempotent marked @import / brand @font-face.
 *      Non-fatal. Skipped when HTMLVIDEO_FONT_IMPORTS is 0/false/off/no.
 *   1. transitions inject guard — `verify`; if "no injected block" run the
 *      idempotent `inject`, then `verify` again. (The report re-checks anyway.)
 *   2. verify-report (full)     — the single diagnosis + gate. Its exit code is
 *      the verdict; its output is teed verbatim.
 *   3. on gate pass only        — frame-midpoints → snapshot → verify-pixels, so
 *      the contact sheet (the pipeline's verify output) exists on one run.
 *
 * Usage:
 *   node /app/scripts/verify-all.mjs --videodir /abs/project
 *
 * Exit codes (mirror verify-report):
 *   0 = gate pass + snapshot written + pixels no-FAIL
 *   1 = findings to fix (report exit 1, or pixels has a FAIL) -> fix & rerun
 *   2 = infrastructure / project blocker -> surface and stop
 *
 * Runs every child with cwd = videodir (never trusts the caller's shell cwd).
 * Mutates: font-imports (idempotent, step 0) + transitions inject + snapshot.
 */

import { spawnSync } from "node:child_process";
import path from "node:path";

const argv = process.argv.slice(2);
const vi = argv.indexOf("--videodir");
const videodir = vi >= 0 && argv[vi + 1] ? path.resolve(argv[vi + 1]) : null;
if (!videodir) {
  console.error("verify-all.mjs: --videodir <dir> is required");
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

console.log("══ verify-all ══");
console.log("videodir:", videodir);

/* ── 0. font-imports (non-fatal) ────────────────────────────────────────── */
console.log("\n── 0. font-imports ──");
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

/* ── 1. transitions inject guard ────────────────────────────────────────── */
console.log("\n── transitions inject guard ──");
let tv = run("node", ["/app/scripts/transitions.mjs", "verify", "--videodir", videodir], { timeout: 30_000 });
if (tv.error) {
  console.log(`  ⚠ cannot run transitions verify: ${tv.error}`);
} else if (tv.status === 0) {
  console.log("  " + combined(tv).split("\n").join("\n  "));
  console.log("  ✓ already stamped — skip inject");
} else if (combined(tv).includes("no injected block")) {
  console.log("  " + combined(tv).split("\n").join("\n  "));
  console.log("  → running idempotent inject");
  const inj = run("node", ["/app/scripts/transitions.mjs", "inject", "--videodir", videodir], { timeout: 60_000 });
  if (inj.error || inj.status !== 0) {
    console.log("  ✗ inject failed: " + (inj.error || combined(inj)));
    console.log("  → the full report below will surface the blocker");
  } else {
    console.log("  " + combined(inj).split("\n").join("\n  "));
    tv = run("node", ["/app/scripts/transitions.mjs", "verify", "--videodir", videodir], { timeout: 30_000 });
    console.log(tv.status === 0
      ? "  ✓ verify after inject OK"
      : "  " + combined(tv).split("\n").join("\n  "));
  }
} else {
  console.log("  " + combined(tv).split("\n").join("\n  "));
  console.log("  → non-zero (boundary mismatch) — the report's gate decides");
}

/* ── 2. full report ─────────────────────────────────────────────────────── */
console.log("\n── verify-report (full) ──");
const rep = run("node", ["/app/scripts/verify-report.mjs", "--videodir", videodir], { timeout: 600_000 });
if (rep.stdout) process.stdout.write(rep.stdout + (rep.stdout.endsWith("\n") ? "" : "\n"));
if (rep.stderr) process.stderr.write(rep.stderr + (rep.stderr.endsWith("\n") ? "" : "\n"));
if (rep.error) {
  console.error(`verify-all: verify-report ${rep.error}`);
  process.exit(2);
}
if (rep.status !== 0) {
  console.log(`\nverify-all: report exit ${rep.status} — fix the findings above, then rerun verify-all.`);
  process.exit(rep.status);
}

/* ── 3. snapshot + pixels (gate passed) ──────────────────────────────────── */
console.log("\n── snapshot + pixels (gate passed) ──");
const mp = run("node", ["/app/scripts/frame-midpoints.mjs", "--videodir", videodir], { timeout: 30_000 });
if (mp.error || !mp.stdout.trim()) {
  console.error(`verify-all: frame midpoints unavailable: ${mp.error || "empty output"}`);
  process.exit(2);
}
const AT = mp.stdout.trim();
if (mp.stderr.trim()) console.log("  " + mp.stderr.trim().split("\n").join("\n  "));
console.log("  AT:", AT);
const snap = run("npx", ["hyperframes", "snapshot", videodir, "--at", AT], { timeout: 300_000 });
if (snap.error || snap.status !== 0) {
  console.error(`verify-all: snapshot failed: ${snap.error || combined(snap)}`);
  process.exit(2);
}
if (snap.stdout) process.stdout.write(snap.stdout);
if (snap.stderr) process.stderr.write(snap.stderr);
const vp = run("python3", ["/app/scripts/verify-pixels.py", "--videodir", videodir], { timeout: 120_000 });
if (vp.stdout) process.stdout.write(vp.stdout + (vp.stdout.endsWith("\n") ? "" : "\n"));
if (vp.stderr) process.stderr.write(vp.stderr);
if (vp.error) {
  console.error(`verify-all: verify-pixels unavailable: ${vp.error}`);
  process.exit(2);
}

console.log(`\nverify-all: GATE PASS ✓ — contact sheet written, pixels ${vp.status === 0 ? "clean" : `FAIL (${vp.status})`}.`);
process.exit(vp.status === 0 ? 0 : 1);