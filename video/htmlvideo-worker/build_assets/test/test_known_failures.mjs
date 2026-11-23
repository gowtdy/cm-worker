// test_known_failures.mjs — unit tests for scripts/known-failures.mjs (the
// single source of verify-frame "known failures").
//
// Run: node test/test_known_failures.mjs

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  KNOWN_FAILURES,
  matchKnownFailures,
  renderPrompt,
} from "../scripts/known-failures.mjs";

const MODES = new Set(["autofix", "model", "ignore", "info"]);

function entry(id) {
  const e = KNOWN_FAILURES.find((x) => x.id === id);
  assert.ok(e, `catalog entry missing: ${id}`);
  return e;
}

function mkCtx(overrides = {}) {
  return {
    videodir: "/proj",
    stub: false,
    digitSelectors: [],
    lintErrors: [],
    checkErrors: [],
    transitions: { status: 0, error: null },
    digitTell: new Map(),
    googleImport: new Map(),
    ...overrides,
  };
}

test("catalog integrity — ids, modes, and per-mode payloads", () => {
  const ids = new Set();
  for (const e of KNOWN_FAILURES) {
    assert.ok(e.id && /^[a-z0-9-]+$/.test(e.id), `bad id: ${e.id}`);
    assert.ok(!ids.has(e.id), `duplicate id: ${e.id}`);
    ids.add(e.id);
    assert.ok(e.title, `${e.id}: missing title`);
    assert.ok(e.reason, `${e.id}: missing reason`);
    assert.ok(MODES.has(e.mode), `${e.id}: bad mode ${e.mode}`);
    if (e.mode === "autofix") {
      assert.equal(typeof e.autofix, "function", `${e.id}: autofix missing`);
      assert.ok(e.prompt, `${e.id}: autofix entry needs a prompt for the manual path`);
    }
    if (e.mode === "model") {
      assert.ok(
        typeof e.prompt === "string" || typeof e.prompt === "function",
        `${e.id}: model entry needs a prompt`,
      );
    }
  }
});

test("text_occluded matches and its rule names the correct opt-out", () => {
  const ctx = mkCtx({
    checkErrors: [{
      code: "text_occluded",
      msg: "div.anchor-line.buy inside div.notecard \"BUY\" — Text is hidden beneath an opaque element.",
      selector: "div.anchor-line.buy",
      container: "div.notecard",
      file: "06-x.html",
    }],
  });
  const matched = matchKnownFailures(ctx);
  const m = matched.find((x) => x.entry.id === "text-occluded");
  assert.ok(m, "text_occluded finding did not match the text-occluded entry");
  assert.equal(m.hits.length, 1);
  const prompt = renderPrompt(m.entry, ctx, m.hits);
  // The 2026-11-22 looped-on-this bug: the model used allow-overlap, which
  // does not clear occlusion. The rule must name allow-occlusion explicitly.
  assert.match(prompt, /data-layout-allow-occlusion/);
  assert.match(prompt, /NOT data-layout-allow-overlap/i);
});

test("caption text_box_overflow is an ignore (false-positive) entry", () => {
  const ctx = mkCtx({
    checkErrors: [{
      code: "text_box_overflow",
      msg: "#caption-word-0-0 — spills 2px",
      file: "captions.html",
    }],
  });
  const m = matchKnownFailures(ctx).find((x) => x.entry.id === "caption-text-box-overflow");
  assert.ok(m, "caption overflow did not match");
  assert.equal(m.entry.mode, "ignore");
});

test("digit-led selectors and content_overlap triage match", () => {
  const ctx = mkCtx({
    digitSelectors: ["02-x.html: digit-led selector #02-x"],
    checkErrors: [
      { code: "content_overlap", msg: "div.f8-ticker inside span.f8-row-text — x", selector: "div.f8-ticker", container: "span.f8-row-text", file: "06.html" },
    ],
  });
  const matched = matchKnownFailures(ctx);
  const ids = matched.map((x) => x.entry.id);
  assert.ok(ids.includes("digit-led-selectors"));
  assert.ok(ids.includes("content-overlap"));
  const overlap = matched.find((x) => x.entry.id === "content-overlap");
  const prompt = renderPrompt(overlap.entry, ctx, overlap.hits);
  // ticker is a designed_overlay → its row renders; the caption keep-out band
  // prohibition is always present. (real_collision/ambiguous wording is only
  // emitted when those categories have findings — covered by triageOverlaps.)
  assert.match(prompt, /designed_overlay/);
  assert.match(prompt, /never blanket-allow/);
  assert.match(prompt, /caption keep-out band/);
});

test("assemble-index-stub matches on stub", () => {
  const ctx = mkCtx({ stub: true });
  const m = matchKnownFailures(ctx).find((x) => x.entry.id === "assemble-index-stub");
  assert.ok(m, "stub did not match assemble-index-stub");
});

test("data-duration unit suffix autofix strips and is idempotent", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-duration-"));
  const frames = path.join(dir, "compositions", "frames");
  fs.mkdirSync(frames, { recursive: true });
  const frame = path.join(frames, "01.html");
  fs.writeFileSync(frame, '<div id="root" data-duration="6.664s"></div>', "utf8");
  try {
    const ctx = mkCtx({ videodir: dir });
    const m = matchKnownFailures(ctx).find((x) => x.entry.id === "data-duration-unit-suffix");
    assert.ok(m, "suffix did not match");
    const r1 = m.entry.autofix(ctx, m.hits);
    assert.equal(r1.changed, true);
    assert.equal(fs.readFileSync(frame, "utf8"), '<div id="root" data-duration="6.664"></div>');
    // idempotent: second pass matches nothing / changes nothing
    const m2 = matchKnownFailures(ctx).find((x) => x.entry.id === "data-duration-unit-suffix");
    assert.ok(!m2, "suffix should no longer match after strip");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("transitions boundary mismatch is info (self-healing, non-actionable)", () => {
  const ctx = mkCtx({ transitions: { status: 1, error: null } });
  const m = matchKnownFailures(ctx).find((x) => x.entry.id === "transitions-verify");
  assert.ok(m, "transitions mismatch did not match");
  assert.equal(m.entry.mode, "info");
});