#!/usr/bin/env node
/**
 * Unit tests for scripts/lib/verify-check-parse.mjs
 *   node test/test_verify_check_parse.mjs
 */
import assert from "node:assert/strict";
import {
  parseCheckJson,
  parseCheckText,
  extractJsonObject,
  checkParseContradiction,
  relPath,
} from "../scripts/lib/verify-check-parse.mjs";

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

testRelPath();
testParseCheckJsonLayoutOverlap();
testParseCheckTextLayoutLines();
testExtractJsonAndContradiction();
console.log("all verify-check-parse tests passed");
