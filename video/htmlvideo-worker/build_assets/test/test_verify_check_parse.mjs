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
  relPath,
} from "../scripts/lib/verify-check-parse.mjs";

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
  assert.equal(
    b.errors.find((e) => e.code === "content_overlap").file,
    "compositions/frames/03.html",
  );
  assert.ok(!b.errors.some((e) => e.code === "should_be_ignored"), "lint skipped");
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
  assert.equal(t({ selector: "#a", container: "#b" }), "ambiguous");
  assert.equal(t({ selector: "#headline", container: "" }), "ambiguous");
  // Generic CLI fixHint mentions allow-overlap for every finding — not a signal
  assert.equal(
    t({ selector: "#line1", container: "#line2", fixHint: "or put data-layout-allow-overlap on the text" }),
    "real_collision",
  );
  // Text-mode items (no structured fields) triage from msg
  const fromText = parseCheckText(
    'Layout\n  ✗ t=2s content_overlap #line1 inside #line2 "buy" — overlap\n  ✗ t=3s content_overlap #a inside #cui-badge "NEXT" — overlap\n',
  );
  const g = triageOverlaps(fromText.errors);
  assert.equal(g.real_collision.length, 1);
  assert.equal(g.designed_overlay.length, 1);
  console.log("triage OK");
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
testTriage();
test6b01Artifact();
console.log("all verify-check-parse tests passed");
