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
import { triageOcclusion, triageOcclusions } from "../scripts/lib/verify-check-parse.mjs";

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
  // anchor/buy/notecard is designed layering (soft-layer tokens), not buried text.
  assert.match(prompt, /designed_layering/);
  assert.match(prompt, /add data-layout-allow-occlusion/);
  // The 2026-11-22 looped-on-this bug: the model used allow-overlap, which
  // does not clear occlusion. The rule must name allow-occlusion explicitly.
  assert.match(prompt, /data-layout-allow-occlusion/);
  assert.match(prompt, /NOT data-layout-allow-overlap/i);
});

test("text_occluded triage: decor/card vs both-sides text", () => {
  const decorItem = {
    code: "text_occluded",
    msg: "span.badge inside div.card \"NEW\" — occluded",
    selector: "span.badge",
    container: "div.card",
    file: "01.html",
  };
  const textItem = {
    code: "text_occluded",
    msg: "#headline inside #subtitle \"Hi\" — occluded",
    selector: "#headline",
    container: "#subtitle",
    file: "02.html",
  };
  assert.equal(triageOcclusion(decorItem).kind, "designed_layering");
  assert.equal(triageOcclusion(textItem).kind, "buried");
  assert.equal(
    triageOcclusion({
      code: "text_occluded",
      selector: "#title",
      container: "#caption-word-1-0",
    }).kind,
    "buried",
  );
  assert.match(
    triageOcclusion({
      code: "text_occluded",
      selector: "#title",
      container: "#caption-word-1-0",
    }).reason,
    /caption/i,
  );
  // Text-vs-text wins over a soft-layer token on the same side.
  assert.equal(
    triageOcclusion({
      code: "text_occluded",
      selector: "#headline",
      container: ".title-card",
    }).kind,
    "buried",
  );
  const groups = triageOcclusions([
    decorItem,
    textItem,
    { code: "content_overlap", selector: "#a", container: "#b" },
  ]);
  assert.equal(groups.designed_layering.length, 1);
  assert.equal(groups.buried.length, 1);
  assert.equal(groups.ambiguous.length, 0);

  const decorCtx = mkCtx({ checkErrors: [decorItem] });
  const decorPrompt = renderPrompt(entry("text-occluded"), decorCtx, [decorItem]);
  assert.match(decorPrompt, /designed_layering/);
  assert.match(decorPrompt, /add data-layout-allow-occlusion/);
  assert.match(decorPrompt, /NOT data-layout-allow-overlap/i);

  const textCtx = mkCtx({ checkErrors: [textItem] });
  const textPrompt = renderPrompt(entry("text-occluded"), textCtx, [textItem]);
  assert.match(textPrompt, /buried/);
  assert.match(textPrompt, /z-index/i);
  assert.match(textPrompt, /NOT data-layout-allow-overlap/i);
  assert.doesNotMatch(textPrompt, /designed_layering/);
});

test("caption text_box_overflow is an ignore (false-positive) entry", () => {
  const ctx = mkCtx({
    checkErrors: [{
      code: "text_box_overflow",
      msg: "#caption-word-0-0 — spills 2px",
      file: "captions.html",
    }],
  });
  const matched = matchKnownFailures(ctx);
  const m = matched.find((x) => x.entry.id === "caption-text-box-overflow");
  assert.ok(m, "caption overflow did not match");
  assert.equal(m.entry.mode, "ignore");
  assert.ok(
    !matched.some((x) => x.entry.id === "text-box-overflow"),
    "caption overflow must not also match text-box-overflow",
  );
});

test("non-caption text_box_overflow is a model entry", () => {
  const ids = KNOWN_FAILURES.map((e) => e.id);
  assert.ok(
    ids.indexOf("caption-text-box-overflow") < ids.indexOf("text-box-overflow"),
    "caption ignore must precede text-box-overflow",
  );
  const ctx = mkCtx({
    checkErrors: [
      { code: "text_box_overflow", msg: "#el-03-title — spills 8px", selector: "#el-03-title", file: "03.html" },
      { code: "text_box_overflow", msg: "#caption-word-0-0 — spills 2px", file: "captions.html" },
      { code: "text_box_overflow", msg: ".caption-line — spills 1px", file: "captions.html" },
    ],
  });
  const matched = matchKnownFailures(ctx);
  const box = matched.find((x) => x.entry.id === "text-box-overflow");
  const cap = matched.find((x) => x.entry.id === "caption-text-box-overflow");
  assert.ok(box, "frame overflow did not match text-box-overflow");
  assert.equal(box.entry.mode, "model");
  assert.equal(box.hits.length, 1);
  assert.match(box.hits[0].msg, /#el-03-title/);
  assert.ok(cap, "caption rows must still match the ignore");
  assert.equal(cap.entry.mode, "ignore");
  assert.equal(cap.hits.length, 2);
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

test("other-check-errors catches unowned codes only", () => {
  const ids = KNOWN_FAILURES.map((e) => e.id);
  assert.equal(ids.at(-2), "other-check-errors");
  assert.equal(ids.at(-1), "other-lint-errors");
  const ctx = mkCtx({
    checkErrors: [
      { code: "console_error", msg: "#root — Uncaught", selector: "#root", file: "01.html", fixHint: "remove the throw" },
      { code: "content_overlap", msg: "#a inside #b — overlap", selector: "#a", container: "#b", file: "01.html" },
      { code: "text_occluded", msg: "#t inside #u — occluded", selector: "#t", container: "#u", file: "01.html" },
      { code: "text_not_painted", msg: "#q — transparent", selector: "#q", file: "01.html" },
      { code: "text_box_overflow", msg: "#el-01 — spills", selector: "#el-01", file: "01.html" },
    ],
  });
  const matched = matchKnownFailures(ctx);
  const other = matched.find((x) => x.entry.id === "other-check-errors");
  assert.ok(other, "console_error did not match other-check-errors");
  assert.equal(other.entry.mode, "model");
  assert.deepEqual(other.hits.map((h) => h.code), ["console_error"]);
  const prompt = renderPrompt(other.entry, ctx, other.hits);
  assert.match(prompt, /\[hint:/);
  assert.match(prompt, /Do NOT rerun lint, check, or snapshot/);

  const ownedOnly = mkCtx({
    checkErrors: [
      { code: "content_overlap", msg: "#a inside #b", selector: "#a", container: "#b", file: "01.html" },
      { code: "text_occluded", msg: "#t inside #u", selector: "#t", container: "#u", file: "01.html" },
    ],
  });
  assert.ok(
    !matchKnownFailures(ownedOnly).some((x) => x.entry.id === "other-check-errors"),
    "content_overlap and text_occluded must not fall into other-check-errors",
  );
});

test("other-lint-errors skips font_family_without_font_face", () => {
  const ctx = mkCtx({
    lintErrors: [
      { code: "font_family_without_font_face", msg: "missing face — Inter", file: "01.html" },
      { code: "some_lint_error", msg: "bad rule — fix the selector", file: "02.html" },
    ],
  });
  const matched = matchKnownFailures(ctx);
  const other = matched.find((x) => x.entry.id === "other-lint-errors");
  assert.ok(other, "non-font lint error did not match other-lint-errors");
  assert.equal(other.entry.mode, "model");
  assert.deepEqual(other.hits.map((h) => h.code), ["some_lint_error"]);
  assert.ok(matched.some((x) => x.entry.id === "font-family-no-face"));
  const prompt = renderPrompt(other.entry, ctx, other.hits);
  assert.match(prompt, /google_fonts_import/);
  assert.match(prompt, /id_requires_css_escape/);

  const fontOnly = mkCtx({
    lintErrors: [{ code: "font_family_without_font_face", msg: "missing face", file: "01.html" }],
  });
  assert.ok(
    !matchKnownFailures(fontOnly).some((x) => x.entry.id === "other-lint-errors"),
    "font_family_without_font_face must not fall into other-lint-errors",
  );
});

test("gsap-exit-missing-hard-kill autofix inserts tl.set for non-clip targets and skips clips", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-gsap-"));
  const frames = path.join(dir, "compositions", "frames");
  fs.mkdirSync(frames, { recursive: true });
  const frame = path.join(frames, "01.html");
  const initial = [
    "<template><script>",
    '  tl.to("#ticker", { autoAlpha: 0, duration: 0.3, ease: "power1.in" }, 1.14);',
    '  tl.to(".clip-exit", { autoAlpha: 0, duration: 0.3 }, 2.0);',
    "</script></template>\n",
  ].join("\n");
  fs.writeFileSync(frame, initial, "utf8");
  try {
    const hits = [
      {
        file: "compositions/frames/01.html",
        code: "gsap_exit_missing_hard_kill",
        selector: "#ticker",
        fixHint: 'Add `tl.set("#ticker", { autoAlpha: 0 }, 1.46)` after the exit tween.',
        snippet: 'tl.to("#ticker", { autoAlpha: 0, duration: 0.3, ease: "power1.in" }, 1.14)',
      },
      {
        file: "compositions/frames/01.html",
        code: "gsap_exit_missing_hard_kill",
        selector: ".clip-exit",
        fixHint: '".clip-exit" is a clip element — wrap in an inner <div> instead.',
        snippet: 'tl.to(".clip-exit", { autoAlpha: 0, duration: 0.3 }, 2.0)',
      },
    ];
    const ctx = mkCtx({ videodir: dir, lintErrors: hits });
    const m = matchKnownFailures(ctx).find((x) => x.entry.id === "gsap-exit-missing-hard-kill");
    assert.ok(m, "gsap-exit-missing-hard-kill did not match");
    const r1 = m.entry.autofix(ctx, m.hits);
    assert.equal(r1.changed, true);
    const after = fs.readFileSync(frame, "utf8");
    assert.match(after, /tl\.to\("#ticker"[^\n]+\n  tl\.set\("#ticker", \{ autoAlpha: 0 \}, 1\.46\);/);
    assert.doesNotMatch(after, /tl\.set\("\.clip-exit"/);
    // idempotent: second run makes no changes
    const r2 = m.entry.autofix(ctx, m.hits);
    assert.equal(r2.changed, false);
    assert.equal(fs.readFileSync(frame, "utf8"), after);
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

test("in-frame-caption-band autofix hides duplicate bottom caption strips when captions.html exists", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-capband-"));
  const frames = path.join(dir, "compositions", "frames");
  fs.mkdirSync(frames, { recursive: true });
  const f2 = path.join(frames, "02.html");
  const f5 = path.join(frames, "05.html");
  fs.writeFileSync(
    f2,
    '<template><div id="root"><div class="caption-band"><span class="caption-text">dup</span></div></div><style>#root{}</style></template>\n',
    "utf8",
  );
  fs.writeFileSync(
    f5,
    '<template><div id="root"><div class="f5-caption">dup</div></div><style>#root{}</style></template>\n',
    "utf8",
  );
  try {
    const ctx = mkCtx({ videodir: dir });
    // Without compositions/captions.html → no match
    assert.ok(!matchKnownFailures(ctx).some((x) => x.entry.id === "in-frame-caption-band"));

    // With compositions/captions.html → matches both 02 and 05 and injects display:none CSS
    fs.writeFileSync(path.join(dir, "compositions", "captions.html"), "<template></template>", "utf8");
    const m1 = matchKnownFailures(ctx).find((x) => x.entry.id === "in-frame-caption-band");
    assert.ok(m1, "in-frame-caption-band should match when captions.html exists");
    assert.equal(m1.hits.length, 2);
    const r1 = m1.entry.autofix(ctx, m1.hits);
    assert.equal(r1.changed, true);
    assert.match(fs.readFileSync(f2, "utf8"), /\.caption-band\s*\{\s*display:\s*none\s*!important/);
    assert.match(fs.readFileSync(f5, "utf8"), /\.f5-caption\s*\{\s*display:\s*none\s*!important/);

    // Idempotent: second scan matches nothing
    assert.ok(!matchKnownFailures(ctx).some((x) => x.entry.id === "in-frame-caption-band"));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("contrast_aa_failure matches dedicated contrast rule instead of other-check-errors", () => {
  const ctx = mkCtx({
    checkErrors: [{
      code: "contrast_aa_failure",
      msg: ".f5-slot-live — Contrast is 1.44:1; WCAG AA requires 4.5:1.",
      selector: ".f5-slot-live",
      file: "compositions/frames/05-check.html",
    }],
  });
  const matched = matchKnownFailures(ctx);
  const c = matched.find((x) => x.entry.id === "contrast-aa-failure");
  assert.ok(c, "contrast-aa-failure must match");
  assert.ok(!matched.some((x) => x.entry.id === "other-check-errors"));
  const prompt = renderPrompt(c.entry, ctx, c.hits);
  assert.match(prompt, /#1A1A1A/);
  assert.match(prompt, /05-check\.html/);
});