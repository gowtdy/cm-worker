#!/usr/bin/env node
/**
 * Unit tests for scripts/lib/verify-check-parse.mjs
 *   node test/test_verify_check_parse.mjs
 */
import assert from "node:assert/strict";
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
  triageContentOverlap,
  triageOverlaps,
  findingsPayload,
  FINDINGS_JSON_PREFIX,
  relPath,
  transitionWindows,
  isSharedStageCrossfadeOverlap,
  splitSharedStageCrossfade,
} from "../scripts/lib/verify-check-parse.mjs";

const LAYOUT_AUDIT_JS = "/app/hyperframes/packages/cli/dist/commands/layout-audit.browser.js";
const CLI_OVERLAP_FIX_HINT = (() => {
  const fallback =
    "Give each block its own zone, or mark intentional layering with data-layout-allow-overlap.";
  if (!fs.existsSync(LAYOUT_AUDIT_JS)) return fallback;
  const m = /"([^"]*intentional layering with data-layout-allow-overlap[^"]*)"/.exec(
    fs.readFileSync(LAYOUT_AUDIT_JS, "utf8"),
  );
  return m ? m[1] : fallback;
})();

const SAMPLE_6B01 =
  "/app/videos/2026-11-21/userclm17919893695531772/6b01e0245007d730";

function testRelPath() {
  assert.equal(
    relPath("/app/proj/compositions/frames/01.html", "/app/proj"),
    "compositions/frames/01.html",
  );
  assert.equal(relPath("compositions/frames/01.html"), "compositions/frames/01.html");
  console.log("relPath OK");
}

function testParseCheckJsonLayoutOverlap() {
  const j = {
    ok: false,
    layout: {
      findings: [
        {
          code: "content_overlap",
          severity: "error",
          message: "Two text blocks overlap and may render unreadable.",
          selector: "#cui-bg > div",
          containerSelector: "#cui-badge",
          firstSeen: 2.1,
          lastSeen: 4.3,
          rect: { left: 100, top: 200, width: 300, height: 80 },
          sourceFile: "/app/proj/compositions/frames/03.html",
        },
        {
          code: "content_overlap",
          severity: "info",
          message: "decorative",
          selector: "#a",
          sourceFile: "/app/proj/compositions/frames/01.html",
        },
      ],
    },
    runtime: {
      findings: [
        {
          code: "http_error",
          severity: "error",
          message: "404",
          sourceFile: "/app/proj/index.html",
        },
      ],
    },
    lint: {
      findings: [
        { code: "should_be_ignored", severity: "error", message: "x" },
      ],
    },
  };
  const b = parseCheckJson(j, { root: "/app/proj" });
  assert.equal(b.errors.length, 2);
  assert.equal(b.infos.length, 1);
  assert.ok(b.errors.some((e) => e.code === "content_overlap"));
  assert.ok(b.errors.some((e) => e.code === "http_error"));
  const ov = b.errors.find((e) => e.code === "content_overlap");
  assert.equal(ov.file, "compositions/frames/03.html");
  assert.ok(ov.msg.includes("#cui-bg > div overlaps #cui-badge"), ov.msg);
  assert.ok(!ov.msg.includes(" inside "), ov.msg);
  assert.ok(ov.msg.includes("[t=2.1-4.3s box(100,200,300x80)]"), ov.msg);
  assert.ok(!b.errors.some((e) => e.code === "should_be_ignored"), "lint skipped");
  const cJson = parseCheckJson({
    contrast: {
      findings: [{
        code: "contrast_aa_failure",
        severity: "warning",
        ratio: 1.44,
        requiredRatio: 4.5,
        fg: "rgb(242, 209, 96)",
        bg: "rgb(255, 255, 255)",
        suggestedColor: "rgb(26, 26, 26)",
        selector: ".f5-slot-live",
        sourceFile: "/app/proj/compositions/frames/05.html",
      }],
    },
  }, { root: "/app/proj" });
  assert.equal(cJson.warns[0].ratio, 1.44);
  assert.equal(cJson.warns[0].fg, "rgb(242, 209, 96)");
  assert.equal(cJson.warns[0].suggestedColor, "rgb(26, 26, 26)");
  console.log("parseCheckJson OK");
}

function testParseCheckTextLayoutLines() {
  // The 2026-11-22 incident format — no `code:` colon after the mark.
  const text = `
Layout
  ✗ t=20.445, 20.737, 21.029, 21.321s content_overlap #cui-bg > div:nth-of-type(5) inside #cui-badge "NEXT" — Two text blocks overlap and may render unreadable.
  ℹ t=2.337s content_overlap #line1 inside #line2 "buy on a hunch" — Two text blocks overlap and may render unreadable.
Contrast
  ⚠ some_warn: ratio low
    /app/proj/compositions/frames/04.html  detail
`;
  const b = parseCheckText(text);
  assert.equal(b.errors.length, 1, JSON.stringify(b.errors));
  assert.equal(b.errors[0].code, "content_overlap");
  assert.equal(b.errors[0].section, "Layout");
  assert.ok(b.errors[0].msg.includes("cui-bg"));
  assert.equal(b.infos.length, 1);
  assert.equal(b.warns.length, 1);
  assert.equal(b.warns[0].code, "some_warn");
  assert.equal(b.warns[0].file, "/app/proj/compositions/frames/04.html");
  // Classic lint-style still works
  const classic = parseCheckText("Lint\n  ✗ font_family_without_font_face: missing\n");
  assert.equal(classic.errors[0].code, "font_family_without_font_face");
  console.log("parseCheckText OK");
}

function testExtractJsonAndContradiction() {
  const mixed = 'noise\n{"ok":false,"layout":{"findings":[]}}\n';
  const j = extractJsonObject(mixed);
  assert.equal(j.ok, false);
  assert.equal(checkParseContradiction(1, { errors: [] }), true);
  assert.equal(checkParseContradiction(1, { errors: [{ code: "x" }] }), false);
  assert.equal(checkParseContradiction(0, { errors: [] }), false);
  console.log("extractJson + contradiction OK");
}

function testParseCheckTextRangeAndContrast() {
  const text = `
Layout
  ✗ t=1-5s (3 samples) content_overlap #headline inside #body-copy "Buy now" — Two text blocks overlap and may render unreadable.
    Fix: Give each block its own zone, or put data-layout-allow-overlap on the text.
  ✗ t=2.5s text_box_overflow #caption-word-3 overflowed right 2px — overflow
  ✗ t=20.445, 20.737, 21.029s content_overlap #a inside #b — x
Contrast
  ✗ #title 2.1:1 (need 4.5:1, t=1.2s)
    Try rgb(0,0,0); source compositions/frames/02-x.html
  ✗ .eyebrow 4.43:1 (need 4.5:1)
  ✗ [data-role=kicker] 3:1 (need 4.5:1, t=7s)
`;
  const b = parseCheckText(text);
  const codes = b.errors.map((e) => e.code);
  assert.deepEqual(codes, [
    "content_overlap",
    "text_box_overflow",
    "content_overlap",
    "contrast_aa_failure",
    "contrast_aa_failure",
    "contrast_aa_failure",
  ]);
  assert.equal(b.errors[0].section, "Layout");
  assert.ok(b.errors[0].msg.startsWith("#headline inside #body-copy"));
  assert.ok(b.errors[0].fixHint.includes("allow-overlap"));
  const contrast = b.errors.filter((e) => e.code === "contrast_aa_failure");
  assert.ok(contrast.every((e) => e.section === "Contrast"));
  assert.equal(contrast[0].selector, "#title");
  assert.equal(contrast[0].file, "compositions/frames/02-x.html");
  assert.ok(contrast[0].msg.includes("2.1:1"));
  assert.equal(contrast[2].selector, "[data-role=kicker]");
  // ANSI colour codes must not hide the mark
  const ansi = parseCheckText("Layout\n  \x1b[31m✗\x1b[39m t=3s content_overlap #a — x\n");
  assert.equal(ansi.errors.length, 1);
  console.log("parseCheckText range/comma/contrast OK");
}

function testDeclaredCountAndPartialMiss() {
  const j = {
    lint: { errorCount: 2, findings: [] },
    runtime: { errorCount: 1, findings: [{ severity: "error", code: "http_error" }] },
    layout: {
      findings: [
        { severity: "error", code: "content_overlap" },
        { severity: "error", code: "content_overlap" },
        { severity: "info", code: "content_overlap" },
      ],
    },
    contrast: { errorCount: 0, findings: [{ severity: "warning", code: "contrast_aa_failure" }] },
    snapshots: { files: [] },
  };
  assert.equal(jsonDeclaredErrorCount(j), 3, "lint excluded; layout counted from findings");
  assert.equal(jsonLintErrorCount(j), 2);
  const parsed = parseCheckJson(j);
  assert.equal(parsed.errors.length, 3);
  assert.equal(checkParseMismatch(3, parsed.errors.length), null);
  // Partial miss: 8 declared, 1 parsed → contradiction even though parsed > 0
  assert.deepEqual(checkParseMismatch(8, 1), { declared: 8, parsed: 1 });
  assert.equal(
    checkParseContradiction(1, { errors: [{ code: "x" }] }, { declaredErrorCount: 8 }),
    true,
  );
  assert.equal(
    checkParseContradiction(1, { errors: [{ code: "x" }] }, { declaredErrorCount: 1 }),
    false,
  );
  // Only lint errors in the envelope explain exit≠0 with 0 parsed non-lint errors
  assert.equal(
    checkParseContradiction(1, { errors: [] }, { declaredErrorCount: 0, lintErrorCount: 2 }),
    false,
  );
  assert.equal(
    checkParseContradiction(1, { errors: [] }, { declaredErrorCount: 0, lintErrorCount: 0 }),
    true,
  );
  console.log("declared count + partial miss OK");
}

function testTriage() {
  const t = (o) => triageContentOverlap({ code: "content_overlap", ...o }).kind;
  assert.equal(t({ selector: "#cui-bg > div:nth-of-type(5)", container: "#cui-badge" }), "designed_overlay");
  assert.equal(t({ selector: "#stamp-text", container: "#hero" }), "designed_overlay");
  assert.equal(t({ selector: ".label-pill span", container: "#card" }), "designed_overlay");
  assert.equal(t({ selector: "#x", container: "#y", dataAttributes: { "data-layout-allow-overlap": "" } }), "designed_overlay");
  assert.equal(t({ selector: "#line1", container: "#line2" }), "real_collision");
  assert.equal(t({ selector: "#headline", container: ".body-copy" }), "real_collision");
  assert.equal(t({ selector: "div.word", container: "div.sub" }), "real_collision");
  assert.equal(t({ selector: "div.word.sell", container: "div.sub" }), "real_collision");
  assert.equal(t({ selector: "#a", container: "#b" }), "ambiguous");
  assert.equal(t({ selector: "#headline", container: "" }), "ambiguous");
  // Generic CLI fixHint mentions allow-overlap for every finding — not a signal
  assert.equal(
    t({ selector: "#line1", container: "#line2", fixHint: "or put data-layout-allow-overlap on the text" }),
    "real_collision",
  );
  // The CLI's fixHint (layout-audit.browser.js, content_overlap) is identical
  // for every finding and says "intentional" — it must never decide the kind.
  const hint = { fixHint: CLI_OVERLAP_FIX_HINT };
  assert.equal(t({ selector: "#a", container: "#b", ...hint }), "ambiguous");
  assert.equal(t({ selector: "div.stat-value", container: "div.stat-label", ...hint }), "ambiguous");
  assert.equal(t({ selector: "div.price-num", container: "div.price-tag", ...hint }), "designed_overlay");
  assert.equal(t({ selector: "h1.headline", container: "p.body-copy", ...hint }), "real_collision");
  const full = (o) => triageContentOverlap({ code: "content_overlap", ...hint, ...o });
  assert.equal(full({ selector: "#a", container: "#b" }).autofix, false);
  assert.equal(full({ selector: "h1.headline", container: "p.body-copy" }).autofix, false);
  assert.equal(full({ selector: "h1.headline", container: "p.body-copy" }).mark_side, null);
  // Text-mode items (no structured fields) triage from msg
  const fromText = parseCheckText(
    'Layout\n  ✗ t=2s content_overlap #line1 inside #line2 "buy" — overlap\n  ✗ t=3s content_overlap #a inside #cui-stamp "NEXT" — overlap\n',
  );
  const g = triageOverlaps(fromText.errors);
  assert.equal(g.real_collision.length, 1);
  assert.equal(g.designed_overlay.length, 1);
  assert.equal(g.designed_overlay[0].triage.mark_side, "container");
  console.log("triage OK");
}

function testTriageCaptionBand() {
  const full = (o) => triageContentOverlap({ code: "content_overlap", fixHint: CLI_OVERLAP_FIX_HINT, ...o });
  for (const [selector, container] of [
    ["div.cap-sub", "#caption-word-32-0"],
    ["#caption-word-32-0", "div.cap-sub"],
    ["div.f01-cap-pill", "#caption-word-3-1"],
    ["#caption-word-3-1", "span.f01-cap-pill"],
    ["div.stamp", "#caption-word-0-0"],
    ["span#caption-word-12-4", "div.cap-sub"],
  ]) {
    const v = full({ selector, container });
    assert.deepEqual(
      v,
      { kind: "real_collision", reason: "frame text inside caption keep-out band", autofix: false, mark_side: null },
      `${selector} × ${container}`,
    );
  }
  // dataAttributes still win over the caption band
  assert.equal(
    full({ selector: "div.cap-sub", container: "#caption-word-1-0", dataAttributes: { "data-layout-allow-overlap": "" } }).kind,
    "designed_overlay",
  );
  // Not a caption word id (must not trigger caption keep-out band reason)
  assert.equal(full({ selector: "div.box", container: "#caption-words" }).kind, "ambiguous");
  const capSub = full({ selector: "div.cap-sub", container: "#caption-words" });
  assert.equal(capSub.kind, "real_collision");
  assert.equal(capSub.reason, "both sides are text blocks");
  console.log("triage caption band OK");
}

function testTriageAutofixWhitelist() {
  const full = (o) => triageContentOverlap({ code: "content_overlap", fixHint: CLI_OVERLAP_FIX_HINT, ...o });
  // Both sides decorative → designed but never auto-fixed
  const both = full({ selector: "span.f8-dot", container: "div.f8-ticker" });
  assert.equal(both.kind, "designed_overlay");
  assert.equal(both.autofix, false);
  assert.equal(both.mark_side, null);
  assert.ok(both.reason.startsWith("decorative tokens on both sides"), both.reason);
  assert.equal(full({ selector: "div.stamp", container: "div.seal" }).autofix, false);
  // Strong token on one side → autofix on that side
  assert.deepEqual(
    full({ selector: "div.f8-ticker > span:nth-of-type(5)", container: "span.f8-row-text" }),
    { kind: "designed_overlay", reason: 'decorative token "ticker"', autofix: true, mark_side: "selector" },
  );
  for (const tok of ["watermark", "stamp", "seal", "sticker", "ribbon", "ornament", "decor", "decoration"]) {
    const v = full({ selector: "p.copy", container: `div.hero-${tok}` });
    assert.equal(v.autofix, true, tok);
    assert.equal(v.mark_side, "container", tok);
    assert.equal(v.reason, `decorative token "${tok}"`);
  }
  assert.equal(full({ selector: "#stamp-text", container: "#hero" }).mark_side, "selector");
  // Mixed weak + strong on the same side: strong wins
  assert.equal(full({ selector: "div.pill-stamp", container: "#hero" }).reason, 'decorative token "stamp"');
  // Weak tokens: designed, but left for the model; reason must not read as safe
  for (const [selector, container, tok] of [
    ["span.tag", "#hero", "tag"],
    ["div.price-num", "div.price-tag", "tag"],
    ["div.price-tag", "div.price-num", "tag"],
    ["span.f8-dot", "span.f8-row-text", "dot"],
    ["#cui-bg > div:nth-of-type(5)", "#cui-badge", "badge"],
    [".label-pill span", "#card", "pill"],
  ]) {
    const v = full({ selector, container });
    assert.equal(v.kind, "designed_overlay", `${selector} × ${container}`);
    assert.equal(v.autofix, false, `${selector} × ${container}`);
    assert.equal(v.mark_side, null);
    assert.equal(v.reason, `weak decorative token "${tok}"`);
    assert.ok(!v.reason.startsWith("decorative token"));
  }
  // dataAttributes keep priority over text-vs-text / both-sides decor
  for (const [selector, container] of [["#line1", "#line2"], ["div.stamp", "div.seal"]]) {
    assert.deepEqual(
      full({ selector, container, dataAttributes: { "data-layout-allow-overlap": "" } }),
      { kind: "designed_overlay", reason: "dataAttributes mark it intentional", autofix: true, mark_side: "selector" },
    );
  }
  console.log("triage autofix whitelist OK");
}

function testTriageNumCheck() {
  // A number + checkmark in the SAME badge = status overlay → deterministic
  // allow-overlap (mark the check side). Two numbers are NOT an overlay.
  const full = (o) => triageContentOverlap({ code: "content_overlap", ...o });
  const badge = full({ selector: "span.f04-num", container: "span.f04-ck" });
  assert.deepEqual(
    badge,
    { kind: "designed_overlay", reason: "number + check status overlay", autofix: true, mark_side: "container" },
  );
  // Reversed side still overlays, marking the check side.
  assert.deepEqual(
    full({ selector: "span.ck", container: "span.num" }),
    { kind: "designed_overlay", reason: "number + check status overlay", autofix: true, mark_side: "selector" },
  );
  // Spelled-out forms.
  assert.equal(full({ selector: "span.checkmark", container: "span.number" }).kind, "designed_overlay");
  // num × num is a real collision signal, never an overlay.
  assert.equal(full({ selector: "span.num", container: "span.num" }).kind, "ambiguous");
  // A lone check (no number on the other side) is not an overlay.
  assert.equal(full({ selector: "span.ck", container: "#hero" }).kind, "ambiguous");
  // A real two-text collision still wins over number+check.
  assert.equal(full({ selector: "h1.headline", container: "p.body-copy" }).kind, "real_collision");
  console.log("triage num+check OK");
}

function test6b01Artifact() {
  const dir = path.join(SAMPLE_6B01, ".hyperframes", "artifacts", "checks");
  if (!fs.existsSync(dir)) {
    console.log("6b01 artifact SKIPPED (sample not on disk)");
    return;
  }
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json"))
    .map((f) => path.join(dir, f))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  assert.ok(files.length, "no artifact json");
  const json = JSON.parse(fs.readFileSync(files[0], "utf8"));
  const b = parseCheckJson(json, { root: SAMPLE_6B01 });
  const declared = jsonDeclaredErrorCount(json);
  const layoutDeclared = json.layout?.errorCount ?? 0;
  assert.ok(layoutDeclared > 0, "artifact should carry layout errors");
  assert.equal(b.errors.length, declared, "declared === parsed");
  assert.equal(checkParseMismatch(declared, b.errors.length), null);
  assert.equal(
    checkParseContradiction(json.ok ? 0 : 1, b, {
      declaredErrorCount: declared,
      lintErrorCount: jsonLintErrorCount(json),
    }),
    false,
  );
  const overlaps = b.errors.filter((e) => e.code === "content_overlap");
  const declaredOverlaps = json.layout.findings.filter(
    (f) => f.severity === "error" && f.code === "content_overlap",
  ).length;
  assert.ok(overlaps.length > 0);
  assert.equal(overlaps.length, declaredOverlaps);
  const g = triageOverlaps(b.errors);
  const total = g.designed_overlay.length + g.real_collision.length + g.ambiguous.length;
  assert.equal(total, overlaps.length);
  console.log(
    `6b01 artifact OK (${path.basename(files[0])}): ✗=${b.errors.length} declared=${declared} `
    + `content_overlap=${overlaps.length} triage designed=${g.designed_overlay.length} `
    + `real=${g.real_collision.length} ambiguous=${g.ambiguous.length}`,
  );
  for (const e of overlaps) {
    console.log(`  ${e.file}: ${e.selector} inside ${e.container} → ${triageContentOverlap(e).kind}`);
  }
}

testRelPath();
testParseCheckJsonLayoutOverlap();
testParseCheckTextLayoutLines();
testExtractJsonAndContradiction();
testParseCheckTextRangeAndContrast();
testDeclaredCountAndPartialMiss();
function testFindingsPayload() {
  const errors = [
    {
      code: "content_overlap", file: "compositions/frames/05.html",
      selector: "div.price-num", container: "div.price-tag", msg: "",
    },
    {
      code: "content_overlap", file: "compositions/frames/02.html",
      selector: "h1.headline", container: "p.body-copy", msg: "",
    },
    // text-fallback shape: sides only live in msg
    {
      code: "content_overlap", file: "compositions/frames/08.html",
      msg: "div.f8-ticker > span:nth-of-type(5) inside span.f8-row-text \"EXIT\" — Two text blocks overlap",
      fixHint: CLI_OVERLAP_FIX_HINT,
    },
    {
      code: "content_overlap", file: "compositions/frames/05.html",
      selector: "div.cap-sub", container: "#caption-word-32-0", msg: "",
      fixHint: CLI_OVERLAP_FIX_HINT,
    },
    { code: "contrast_aa_failure", file: "compositions/frames/01.html", msg: "#t 2:1" },
  ];
  const p = findingsPayload(errors);
  assert.equal(p.version, 1);
  assert.equal(p.designed_overlay.length, 2);
  assert.deepEqual(p.designed_overlay[0], {
    file: "compositions/frames/05.html", code: "content_overlap",
    selector: "div.price-num", container: "div.price-tag", reason: 'weak decorative token "tag"',
    autofix_ok: false, mark_side: null,
  });
  assert.deepEqual(p.designed_overlay[1], {
    file: "compositions/frames/08.html", code: "content_overlap",
    selector: "div.f8-ticker > span:nth-of-type(5)", container: "span.f8-row-text",
    reason: 'decorative token "ticker"', autofix_ok: true, mark_side: "selector",
  });
  assert.equal(p.real_collision.length, 2);
  assert.ok(p.real_collision.every((r) => r.autofix_ok === false && r.mark_side === null));
  const cap = p.real_collision.find((r) => r.selector === "div.cap-sub");
  assert.equal(cap.reason, "frame text inside caption keep-out band");
  assert.equal(p.ambiguous.length, 0);
  assert.equal(p.other_check_errors, 1);
  // ticker separators: weak token "dot" → model (covered once .f8-ticker is marked)
  const dot = findingsPayload([{
    code: "content_overlap", file: "compositions/frames/08.html",
    selector: "span.f8-dot", container: "span.f8-row-text", msg: "", fixHint: CLI_OVERLAP_FIX_HINT,
  }]);
  assert.equal(dot.designed_overlay[0].reason, 'weak decorative token "dot"');
  assert.equal(dot.designed_overlay[0].autofix_ok, false);
  // container-side strong token and dataAttributes rows carry mark_side
  const sides = findingsPayload([
    { code: "content_overlap", file: "f.html", selector: "p.copy", container: "div.hero-watermark", msg: "" },
    { code: "content_overlap", file: "f.html", selector: "#x", container: "#y", msg: "",
      dataAttributes: { "data-layout-allow-overlap": "" } },
  ]);
  assert.deepEqual(
    sides.designed_overlay.map((r) => [r.autofix_ok, r.mark_side]),
    [[true, "container"], [true, "selector"]],
  );
  const line = FINDINGS_JSON_PREFIX + JSON.stringify(p);
  assert.ok(!line.includes("\n"), "payload must stay on one line");
  assert.deepEqual(JSON.parse(line.slice(FINDINGS_JSON_PREFIX.length)), p);
}

testTriage();
testTriageCaptionBand();
testTriageAutofixWhitelist();
testTriageNumCheck();
testFindingsPayload();
test6b01Artifact();
testSharedStageCrossfade();

function testSharedStageCrossfade() {
  // 6b0795df: index.html frame boundaries (F1 at 0 is not a transition)
  const index = [
    '<div id="el-01" class="scene" data-composition-src="compositions/frames/01.html" data-start="0" data-duration="6.2"></div>',
    '<div id="el-05" class="scene" data-composition-src="compositions/frames/05.html" data-start="26.262" data-duration="5.3"></div>',
    '<div id="el-08" class="scene" data-composition-src="compositions/frames/08.html" data-start="41.251" data-duration="5.5"></div>',
    '<div id="el-captions" class="scene" data-composition-src="compositions/captions.html" data-start="0"></div>',
  ].join("\n");
  const wins = transitionWindows(index);
  assert.equal(wins.length, 2);
  const json = {
    layout: {
      findings: [
        // crossfade-only self overlap of the pinned title (F4→F5, F7→F8 midpoints)
        { code: "content_overlap", severity: "error", time: 26.569, firstSeen: 26.569, lastSeen: 41.538,
          occurrences: 2, selector: "div.hf-stage-title", containerSelector: "div.hf-stage-title",
          text: "THE INVESTOR'S CHECKLIST", sourceFile: "compositions/frames/02.html" },
        // real in-frame label collision (first sample at 8.856s, 74 hits) — must stay
        { code: "content_overlap", severity: "error", time: 8.856, firstSeen: 8.856, lastSeen: 41.538,
          occurrences: 74, selector: "span.hf-stage-label", containerSelector: "span.hf-stage-label",
          text: "01 Explainable", sourceFile: "compositions/frames/02.html" },
        // different classes inside a window — not a self overlap, must stay
        { code: "content_overlap", severity: "error", time: 26.5, firstSeen: 26.5, lastSeen: 26.5,
          occurrences: 1, selector: "div.hf-stage-title", containerSelector: "h2.hero-line",
          text: "X", sourceFile: "compositions/frames/05.html" },
      ],
    },
  };
  const { errors } = parseCheckJson(json, {});
  assert.equal(errors[0].firstSeen, 26.569);
  assert.equal(errors[0].occurrences, 2);
  assert.equal(isSharedStageCrossfadeOverlap(errors[0], wins), true);
  assert.equal(isSharedStageCrossfadeOverlap(errors[1], wins), false);
  assert.equal(isSharedStageCrossfadeOverlap(errors[2], wins), false);
  assert.equal(isSharedStageCrossfadeOverlap(errors[0], []), false, "no windows = never demote");
  const { kept, demoted } = splitSharedStageCrossfade(errors, wins);
  assert.equal(demoted.length, 1);
  assert.equal(kept.length, 2);
}

console.log("all verify-check-parse tests passed");
