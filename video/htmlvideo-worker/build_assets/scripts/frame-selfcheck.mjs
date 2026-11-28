#!/usr/bin/env node
/**
 * frame-selfcheck.mjs — structural self-check for ONE HyperFrames frame file.
 *
 * Deterministic file-local subset of `hyperframes lint` for a frame that is
 * NOT yet wired into an assembled index.html (lint/check report on the
 * assembled project graph, so they cannot vet a single unwired frame).
 *
 * Usage:
 *   node /app/scripts/frame-selfcheck.mjs compositions/frames/NN-name.html
 *
 * Exit 0 = PASS (warnings allowed), exit 1 = FAIL.
 * The frame_id is derived from the file basename; the file path IS the id
 * (compositions/frames/<frame_id>.html), so a mismatch is itself a finding.
 *
 * Checks (each mirrors a `hyperframes lint` structural rule):
 *   - file exists and is non-empty
 *   - a single <template>…</template> wraps the sub-composition
 *   - #root lives INSIDE the template, carries data-composition-id AND a
 *     numeric data-duration (required by transitions inject)
 *   - template bounds: every <style>/<script> (incl. the GSAP load) is
 *     inside the template — a style/script outside the template is a
 *     missing_template_wrapper-class defect
 *   - every element with class="clip" carries data-start + data-duration +
 *     data-track-index (clip_missing_data_attrs)
 *   - clips on the same data-track-index never overlap in time
 *     (timeline_track_too_dense)
 *   - exactly one paused timeline registered at window.__timelines["<id>"]
 *     (timeline_not_paused / timeline_not_registered)
 *   - no digit-led id/class selectors ("#07-…", ".01-…") in CSS or JS
 *     selector strings (id_requires_css_escape — promoted to FAIL: CSS drops
 *     the rule and querySelectorAll throws at runtime)
 *   - skeleton_unfilled: a `skeleton-v1` file whose #root has no real content
 *     or whose timeline has no tween (driver-only code, not a lint rule)
 *   WARN (do not fail exit code): css transition usage, background painted
 *     on #root, data-composition-id != basename, font_out_of_ramp,
 *     font_dead_fallback
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import {
  GENERIC_FAMILIES,
  extractDeclaredFamilies,
  extractFontStacks,
  extractGoogleFamilies,
  extractUsedFamilies,
  loadCliAliases,
  normFamily,
  parseFrameFamilies,
} from "./lib/fonts.mjs";

const [fileArg] = process.argv.slice(2);
const fails = [];
const warns = [];
function fail(msg) { fails.push(`[FAIL] ${msg}`); }
function warn(msg) { warns.push(`[WARN] ${msg}`); }

if (!fileArg) {
  console.error("usage: node frame-selfcheck.mjs compositions/frames/<frame_id>.html");
  process.exit(2);
}
if (!fs.existsSync(fileArg)) {
  console.error(`[FAIL] file not found: ${fileArg}`);
  process.exit(1);
}
const html = fs.readFileSync(fileArg, "utf8");
const frameId = fileArg.split("/").pop().replace(/\.html$/i, "");
if (html.trim().length === 0) fail("file is empty");

// --- template wrapper -----------------------------------------------------
const tplOpen = html.indexOf("<template");
const tplClose = html.lastIndexOf("</template>");
if (tplOpen === -1) fail("missing <template> wrapper (missing_template_wrapper)");
if (tplClose === -1) fail("missing </template> close");
const inTpl = (i) => tplOpen !== -1 && tplClose !== -1 && i > tplOpen && i < tplClose;

// --- root element ---------------------------------------------------------
const rootRe = /<(div)\b[^>]*\bid="root"[^>]*>/;
const rootM = rootRe.exec(html);
if (!rootM) {
  fail("no #root div found");
} else {
  const rootTag = rootM[0];
  if (!inTpl(rootM.index)) fail("root is outside the <template>");
  const compId = /data-composition-id="([^"]*)"/.exec(rootTag);
  if (!compId) fail("root missing data-composition-id (missing_composition_id)");
  else if (compId[1] !== frameId) warn(`root data-composition-id "${compId[1]}" != file id "${frameId}"`);
  const dur = /data-duration="([^"]*)"/.exec(rootTag);
  if (!dur) fail("root missing data-duration (missing_root_duration — breaks transitions inject)");
  else if (!/^\d+(\.\d+)?$/.test(dur[1])) {
    fail(
      `root data-duration "${dur[1]}" must be a plain number with no unit suffix ` +
        `(use data-duration="N", not "Ns") — transitions inject requires a bare numeric value`,
    );
  } else if (!(parseFloat(dur[1]) > 0)) fail(`root data-duration "${dur[1]}" is not a positive number`);
  if (/<div\b[^>]*\bclass="[^"]*"\s[^>]*id="root"/.test(html) || /<div\b[^>]*id="root"[^>]*\bclass="[^"]*"/.test(html)) {
    fail("root styled via class — style the frame root via #root, never a class (subcomposition_root_styled_by_class)");
  }
  const roots = (html.match(/<div\b[^>]*\bid="root"[^>]*>/g) || []);
  if (roots.length > 1) fail(`multiple #root divs (${roots.length})`);
}

// --- template bounds: style/script must be inside template ----------------
for (const m of html.matchAll(/<(style|script)\b[^>]*>/g)) {
  if (!inTpl(m.index)) fail(`<${m[1]}> sits OUTSIDE the <template> (template transport) — the assembled project drops it`);
}

// --- clip contract --------------------------------------------------------
const clipRe = /<([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*\bclass="([^"]*)"[^>]*)>/g;
const clips = [];
for (const m of html.matchAll(clipRe)) {
  const tag = m[2];
  if (!/\bclip\b/.test(m[3])) continue;
  const start = /data-start="([^"]*)"/.exec(tag);
  const dur = /data-duration="([^"]*)"/.exec(tag);
  const track = /data-track-index="([^"]*)"/.exec(tag);
  if (!start || !dur || !track) fail(`clip ${m[0].slice(0, 60)}… missing ${!start ? "data-start" : !dur ? "data-duration" : "data-track-index"} (clip_missing_data_attrs)`);
  else {
    const s = parseFloat(start[1]), d = parseFloat(dur[1]);
    if (!(s >= 0) || !(d > 0)) fail(`clip has non-numeric timing: ${m[0].slice(0, 80)}…`);
    else clips.push({ s, d, t: track[1], tag: m[0].slice(0, 60) });
  }
}
const byTrack = new Map();
for (const c of clips) {
  if (!byTrack.has(c.t)) byTrack.set(c.t, []);
  const lane = byTrack.get(c.t);
  for (const other of lane) {
    const a = c.s, b = c.s + c.d, x = other.s, y = other.s + other.d;
    if (a < y - 1e-6 && x < b - 1e-6) {
      fail(`clip overlap on track ${c.t}: [${a.toFixed(2)}–${b.toFixed(2)}] × [${x.toFixed(2)}–${y.toFixed(2)}] (timeline_track_too_dense) — nest décor or split tracks`);
    }
  }
  lane.push(c);
}

// --- timeline registration -------------------------------------------------
if (!/gsap\.timeline\s*\(/.test(html) || !/\bpaused\s*:\s*true\b/.test(html)) {
  fail("no `gsap.timeline({ paused: true })` found (timeline_not_paused)");
}
const reg = new RegExp(`__timelines\\s*\\[\\s*["']${frameId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["']\\s*\\]`);
if (!reg.test(html)) fail(`timeline not registered at window.__timelines["${frameId}"] (timeline_not_registered)`);

// --- skeleton_unfilled -------------------------------------------------------
// The marker alone proves nothing (a filled frame may keep the comment); it only
// arms the check. FAIL needs unfilled evidence: #root without real children
// (elements tagged data-skeleton are scaffold, not content) or a timeline with
// no tween. An empty style block alone is only a WARN.
function rootInner(src, from) {
  const tagRe = /<(\/?)div\b[^>]*>/gi;
  tagRe.lastIndex = from;
  let depth = 1;
  for (let m = tagRe.exec(src); m; m = tagRe.exec(src)) {
    depth += m[1] ? -1 : 1;
    if (depth === 0) return src.slice(from, m.index);
  }
  return src.slice(from);
}
const SKELETON_MARK = "skeleton-v1";
if (html.includes(SKELETON_MARK) && rootM) {
  const evidence = [];
  const inner = rootInner(html, rootM.index + rootM[0].length).replace(/<!--[\s\S]*?-->/g, "");
  const realTags = [...inner.matchAll(/<([a-zA-Z][\w-]*)\b([^>]*)>/g)]
    .filter((m) => !/\bdata-skeleton\b/.test(m[2]));
  if (realTags.length === 0 && (inner.includes("data-skeleton") || inner.replace(/<[^>]*>/g, "").trim() === "")) {
    evidence.push("root-empty");
  }
  const stripRuntime = (s) => s.replace(/\/\* hf-runtime-start \*\/[\s\S]*?\/\* hf-runtime-end \*\//g, "");
  const stripJs = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<!:)\/\/[^\n]*/g, "");
  const inlineJs = [...html.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
    .map((m) => stripJs(stripRuntime(m[1]))).join("\n");
  const hasTween = /\.(?:to|from|fromTo|set|call|staggerTo|staggerFrom|staggerFromTo)\s*\(/.test(inlineJs)
    || /\b(?:tl|timeline)\.add\s*\(/.test(inlineJs)
    || /\b__hf\.(?:type|draw)\s*\(/.test(inlineJs);
  if (!hasTween) evidence.push("no-tween");
  const css = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)]
    .map((m) => m[1].replace(/\/\*[\s\S]*?\*\//g, "")).join("").trim();
  const placeholderStyle = css === "";
  if (evidence.length) {
    if (placeholderStyle) evidence.push("placeholder-style");
    fail(`skeleton_unfilled: file still carries the ${SKELETON_MARK} marker and is not filled (evidence: ${evidence.join(", ")}) — write the #root body markup and the timeline tweens (skeleton_unfilled)`);
  } else if (placeholderStyle) {
    warn(`${SKELETON_MARK} marker with an empty <style> block — styles still the skeleton placeholder?`);
  }
}

// --- digit-led id/class selectors (id_requires_css_escape) -----------------
// CSS silently DROPS an entire selector rule when the id/class starts with a
// digit ("#07-the-harder-half-p1 { … }"), and querySelector(All) / GSAP
// throw SyntaxError on the same strings in JS. Post-build verify repeatedly
// collapses whole frames to the top-left corner because of this.
// Matcher is pinned to BOTH the pipeline's NN-name convention (#/. + 1-2
// digits + frame-name separator + letter) AND to a selector-use context:
// after a line start / CSS punctuation (with same-line whitespace only), or
// directly adjacent to a quote/backtick in JS selector strings. Everything
// legal misses it: hex colors (#1E1E1E — no separator after the digits),
// decimals and prose ("6.4-end", "0.75-second" — not in a selector context),
// version numbers (3.14.2), URL fragments (%230…, url(#0..)),
// getElementById("07-…") (no #/. prefix), the fixed attribute forms
// [id="07-…"] / [class~="01-…"], and escaped selectors (.\31 0-… — the
// backslash breaks the context). The scan is restricted to <style>/<script>
// bodies (text content like "#01-…" in a headline is not a selector) with
// comments blanked out (/* … */, // …): a comment that merely mentions a
// selector is not one, so it must not fail the gate.
const digitSelRe = /(?:^[ \t]*|[{};,>+~():][ \t]*|["'`])([#.][0-9]{1,2}[-_][A-Za-z][A-Za-z0-9_-]*)/gm;
let masked = html.replace(/[^\n]/g, " ");
for (const b of html.matchAll(/<(style|script)\b[^>]*>([\s\S]*?)<\/\1>/gi)) {
  const inner = b[2]
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "))
    .replace(/(?<!:)\/\/[^\n]*/g, (c) => c.replace(/[^\n]/g, " "));
  const innerStart = b.index + b[0].indexOf(b[2]);
  masked = masked.slice(0, innerStart) + inner + masked.slice(innerStart + inner.length);
}
for (const m of masked.matchAll(digitSelRe)) {
  const idx = m.index + m[0].indexOf(m[1]);
  const lineNo = html.slice(0, idx).split("\n").length;
  const colNo = idx - html.lastIndexOf("\n", idx - 1);
  fail(`digit-led selector "${m[1]}" at line ${lineNo} col ${colNo} — CSS drops the whole rule / querySelectorAll throws SyntaxError; convert to [id="…"] or [class~="…"], leave getElementById alone (id_requires_css_escape)`);
}

// --- inline JS syntax check (invalid_inline_script_syntax) ------------------
// Catch syntax errors like unquoted CSS units (`y: 2.2cqh`) in 0.1s locally
// instead of paying a full verify round (6b07a9c1 F4).
for (const b of html.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)) {
  const code = b[1];
  try {
    new vm.Script(code, { filename: `${frameId}.inline.js` });
  } catch (err) {
    fail(`invalid inline script syntax: ${err.message} (invalid_inline_script_syntax)`);
  }
}

// --- visible literal placeholder check (literal_placeholder) ----------------
// Autonomous videos render directly to MP4 — never let deck placeholders like
// `— figure —` or `{metric}` reach the screen.
{
  const bodyOnly = (tplOpen !== -1 && tplClose !== -1 ? html.slice(tplOpen, tplClose) : html)
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<[^>]+>/g, " ");
  const phRe = /(?:—|--)\s*(?:figure|metric|value|number|placeholder|stat)\s*(?:—|--)|\{\s*(?:metric|figure|value|number|placeholder|stat)\s*\}/gi;
  const seenPh = new Set();
  for (const m of bodyOnly.matchAll(phRe)) {
    const hit = m[0].trim();
    if (seenPh.has(hit.toLowerCase())) continue;
    seenPh.add(hit.toLowerCase());
    fail(`literal placeholder "${hit}" in visible text — replace with real copy or qualitative label (literal_placeholder)`);
  }
}

// --- infinite GSAP repeat: -1 (gsap_infinite_repeat) ------------------------
for (const b of html.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)) {
  const cleanScript = b[1]
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "))
    .replace(/(?<!:)\/\/[^\n]*/g, (c) => c.replace(/[^\n]/g, " "));
  for (const rep of cleanScript.matchAll(/\brepeat\s*:\s*-1\b/g)) {
    const idx = b.index + b[0].indexOf(b[1]) + rep.index;
    const lineNo = html.slice(0, idx).split("\n").length;
    fail(`infinite GSAP \`repeat: -1\` at line ${lineNo} — renderer seeks deterministically; use a finite tween bounded by data-duration (gsap_infinite_repeat)`);
  }
}

// --- uncentered left: 50% check (uncentered_left_50) ------------------------
// Catch `left: 50%` missing `translateX(-50%)` / `xPercent: -50` (6b07a9c1 F1).
// Skip thin dividers, explicit right-anchored/half-width panes, and rules that
// already center via transform, negative margin-left, or GSAP xPercent: -50.
{
  const stripRuntime = (s) => s.replace(/\/\* hf-runtime-start \*\/[\s\S]*?\/\* hf-runtime-end \*\//g, "");
  const stripJsComments = (s) => s
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "))
    .replace(/(?<!:)\/\/[^\n]*/g, (c) => c.replace(/[^\n]/g, " "));
  const inlineScripts = [...html.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  const allJs = inlineScripts.map((b) => stripJsComments(stripRuntime(b[1]))).join("\n");
  const hasJsXPercent = /\bxPercent\s*:\s*-50\b/.test(allJs);
  const allCss = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)]
    .map((b) => b[1].replace(/\/\*[\s\S]*?\*\//g, " ")).join("\n");
  for (const m of allCss.matchAll(/([^{}@]+)\{([^{}]+)\}/g)) {
    const sel = m[1].trim();
    const decls = m[2];
    if (!/\bleft\s*:\s*50%/.test(decls)) continue;
    if (/\bright\s*:/.test(decls)) continue;
    if (/translate(?:X|3d)?\s*\(\s*-50%/.test(decls)) continue;
    if (/\bmargin-left\s*:\s*-/.test(decls)) continue;
    if (/\bwidth\s*:\s*(?:[0-4](?:\.\d+)?px|0?\.\d+cqw|50%|[4-5]\dcqw)\b/i.test(decls)) continue;
    if (hasJsXPercent) continue;
    if (/(?:right|col-?2|pane-?2|divider|axis|line|rule|caret|cursor)/i.test(sel)) continue;
    if (/(?:card|pill|cta|lockup|modal|headline|title)/i.test(sel)) {
      fail(`uncentered \`left: 50%\` on "${sel}" without translateX(-50%) / xPercent: -50 — element will sit off-center to the right (uncentered_left_50)`);
    } else {
      warn(`uncentered \`left: 50%\` on "${sel}" without translateX(-50%) / xPercent: -50 (uncentered_left_50)`);
    }
  }

  // --- S1-2: gsap_camelcase_data_prop ---------------------------------------
  // GSAP ignores bare camelCase `dataState:` / `dataAccent:` in tween vars;
  // callers must use `attr: { "data-state": ... }` (6b082a5b F3).
  for (const b of inlineScripts) {
    const clean = stripJsComments(stripRuntime(b[1]));
    // Mask string literals so prose inside strings never triggers.
    const noStr = clean.replace(/(["'`])(?:\\.|(?!\1)[^\\\n])*\1/g, (s) => " ".repeat(s.length));
    for (const dm of noStr.matchAll(/(?<![.\w$])(data[A-Z][A-Za-z0-9]*)\s*:/g)) {
      const prop = dm[1];
      const kebab = prop.replace(/([A-Z])/g, "-$1").toLowerCase();
      fail(`camelCase GSAP data property "${prop}:" — GSAP does not mutate data-* attributes via camelCase; use \`attr: { "${kebab}": ... }\` (gsap_camelcase_data_prop)`);
    }

    // --- S1-2: gsap_label_concat --------------------------------------------
    // `"scene1" + i * 0.14` evaluates to `"scene10.14"` (invalid label) instead
    // of `` `scene1+=${i * 0.14}` `` (6b083ee7 F2).
    for (const lm of clean.matchAll(/["'](scene\d*)["']\s*\+\s*(?!\s*["'`]\s*[+-]=)/g)) {
      fail(`broken GSAP label concatenation \`"${lm[1]}" + ...\` — JS stringifies to "${lm[1]}0.14"; use template literal \`\\\`${lm[1]}+=\${...}\\\`\` (gsap_label_concat)`);
    }

    // --- S1-2: gsap_fromto_delayed_leak -------------------------------------
    // `tl.fromTo(target, { autoAlpha: 0.6 }, ..., at > 0)` without
    // `immediateRender: false` paints the `from` state at t=0 (6b083ee7 F6).
    // Exempt pure exit tweens (`autoAlpha/opacity: 1 -> 0`) on elements that
    // were NOT previously hidden by a `0 -> 1` reveal in the same script
    // (6b0a4a1a F7 `.sig-icon.check`), and catch delayed `line-through`
    // decoration leaks (6b0a4a1a F4).
    const ftRe = /\b(?:tl|timeline|gsap)\.fromTo\s*\(/g;
    const ftCalls = [];
    for (const fm of clean.matchAll(ftRe)) {
      const startIdx = fm.index + fm[0].length;
      let depth = 1;
      let inStr = null;
      let i = startIdx;
      const args = [];
      let argStart = startIdx;
      for (; i < clean.length && depth > 0; i++) {
        const ch = clean[i];
        if (inStr) {
          if (ch === "\\") { i++; continue; }
          if (ch === inStr) inStr = null;
          continue;
        }
        if (ch === '"' || ch === "'" || ch === "`") { inStr = ch; continue; }
        if (ch === "(" || ch === "{" || ch === "[") depth++;
        else if (ch === ")" || ch === "}" || ch === "]") {
          depth--;
          if (depth === 0) {
            args.push(clean.slice(argStart, i).trim());
            break;
          }
        } else if (ch === "," && depth === 1) {
          args.push(clean.slice(argStart, i).trim());
          argStart = i + 1;
        }
      }
      if (args.length >= 4) ftCalls.push(args);
    }
    const hiddenFromTargets = new Set();
    for (const [targetArg, fromArg] of ftCalls) {
      if (/\b(?:autoAlpha|opacity)\s*:\s*0(?:\.0+)?\b/.test(fromArg)) {
        hiddenFromTargets.add(targetArg.trim());
      }
    }
    for (const [targetArg, fromArg, toArg, posArg] of ftCalls) {
      const pos = posArg.trim();
      const isZeroPos =
        /^0(?:\.0+)?$/.test(pos)
        || /^["'](?:0|scene1)["']$/.test(pos);
      const fromVisible = /\b(?:autoAlpha|opacity)\s*:\s*(?:0?\.[1-9]\d*|1(?:\.0+)?)\b/.test(fromArg);
      const toHidden = /\b(?:autoAlpha|opacity)\s*:\s*0(?:\.0+)?\b/.test(toArg);
      const isCleanExit = fromVisible && toHidden && !hiddenFromTargets.has(targetArg.trim());
      const fromDecorLeak = /\btextDecoration(?:Line)?\s*:\s*["'][^"']*line-through[^"']*["']/i.test(fromArg);
      const hasNoImm = /\bimmediateRender\s*:\s*false\b/.test(fromArg) || /\bimmediateRender\s*:\s*false\b/.test(toArg);
      if (!isZeroPos && ((fromVisible && !isCleanExit) || fromDecorLeak) && !hasNoImm) {
        fail(`delayed \`fromTo(${targetArg}, ...)\` at position ${pos} paints visible \`from\` state at t=0 — add \`immediateRender: false\` (gsap_fromto_delayed_leak)`);
      }
    }

    // --- P0 (6b0a4a1a F3): gsap_pseudo_element_target -----------------------
    // GSAP cannot target CSS pseudo-elements (`::before` / `::after`); it logs
    // "GSAP target not found" and silently skips the animation.
    const seenPseudo = new Set();
    for (const pm of clean.matchAll(/\b(?:tl|timeline|gsap)\.(to|from|fromTo|set)\s*\(\s*(["'`])([^"'`]*::?(?:before|after)\b[^"'`]*)\2/gi)) {
      const sel = pm[3].trim();
      if (seenPseudo.has(sel)) continue;
      seenPseudo.add(sel);
      fail(`GSAP \`.${pm[1]}("${sel}")\` targets a CSS pseudo-element — GSAP cannot animate ::before/::after; use a real child element instead (gsap_pseudo_element_target)`);
    }
  }

  // --- S1-2: abs_zero_xpercent_center ---------------------------------------
  // `position: absolute; left: 0;` (without `width: 100%` or `right: 0`) paired
  // with GSAP `xPercent: -50` pins the element center on the left edge (6b083ee7 F2).
  if (hasJsXPercent) {
    for (const m of allCss.matchAll(/([^{}@]+)\{([^{}]+)\}/g)) {
      const sel = m[1].trim();
      const decls = m[2];
      if (!/\bposition\s*:\s*absolute\b/i.test(decls)) continue;
      if (!/\bleft\s*:\s*0(?:px|%|cqw|cqh|em|rem)?\s*(?:;|$)/i.test(decls)) continue;
      if (/\bwidth\s*:\s*100(?:%|cqw)\b/i.test(decls)) continue;
      if (/\bright\s*:\s*0\b/i.test(decls)) continue;
      if (/\binset\s*:/i.test(decls)) continue;
      const selTokens = [...sel.matchAll(/[#.][A-Za-z_-][A-Za-z0-9_-]*/g)].map((x) => x[0]);
      if (!selTokens.length) continue;
      const referencedInJs = selTokens.some((tok) => {
        const esc = tok.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        return new RegExp(`["'\`][^"'\`]*${esc}(?![A-Za-z0-9_-])[^"'\`]*["'\`]`).test(allJs);
      });
      if (referencedInJs) {
        fail(`\`position: absolute; left: 0\` on "${sel}" combined with GSAP \`xPercent: -50\` pins element center on the left edge — use \`left: 50%; top: 50%\` for centered positioning (abs_zero_xpercent_center)`);
      }
    }
  }

  // --- S1-2: missing_dom_selector -------------------------------------------
  // Validate static `#id` / `.class` selectors passed to `__hf.draw` / `__hf.type`
  // exist in the <template> DOM (6b083ee7 F6 `#capsule-check` vs `.capsule-check`).
  const tplHtml = (tplOpen !== -1 && tplClose !== -1 ? html.slice(tplOpen, tplClose) : html)
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ");

  // --- P0-2 (6b08f96c F1): flow_element_xpercent_center ----------------------
  // Applying GSAP `xPercent: -50` / `yPercent: -50` to a `position: relative`
  // or normal-flow/flex element shifts it up-left by half its own size (clipping
  // inside `overflow: hidden` cards). Aggregate CSS rules across each matched
  // DOM element's id + all classes so `<div class="seal" id="check-seal">` with
  // `.seal { position: absolute }` never false-positives.
  if (/\b[xy]Percent\s*:\s*-50\b/.test(allJs)) {
    const cssRules = [];
    for (const cm of allCss.matchAll(/([^{}@]+)\{([^{}]+)\}/g)) {
      const sels = cm[1].split(",").map((s) => s.trim()).filter(Boolean);
      cssRules.push({ sels, decls: cm[2] });
    }
    const domNodes = [];
    for (const tm of tplHtml.matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>/g)) {
      const tag = tm[1].toLowerCase();
      const attrStr = tm[2];
      const idM = /\bid\s*=\s*["']([^"']+)["']/i.exec(attrStr);
      const clsM = /\bclass\s*=\s*["']([^"']+)["']/i.exec(attrStr);
      const styleM = /\bstyle\s*=\s*["']([^"']+)["']/i.exec(attrStr);
      domNodes.push({
        tag,
        id: idM ? idM[1].trim() : "",
        classes: new Set(clsM ? clsM[1].trim().split(/\s+/).filter(Boolean) : []),
        inlineStyle: styleM ? styleM[1] : "",
      });
    }
    const matchesSimpleSel = (node, simpleSel) => {
      // Only match the rightmost compound (after any combinator) if simple
      const compound = simpleSel.trim().split(/[\s>+~]+/).pop() || "";
      if (!compound) return false;
      const cleanComp = compound.replace(/:[a-zA-Z-]+(?:\([^)]*\))?/g, "").replace(/\[[^\]]*\]/g, "");
      const ids = [...cleanComp.matchAll(/#([A-Za-z_-][A-Za-z0-9_-]*)/g)].map((x) => x[1]);
      const clss = [...cleanComp.matchAll(/\.([A-Za-z_-][A-Za-z0-9_-]*)/g)].map((x) => x[1]);
      if (!ids.length && !clss.length) return false;
      const tagM = /^([a-zA-Z][a-zA-Z0-9-]*)/.exec(cleanComp);
      if (tagM && tagM[1].toLowerCase() !== node.tag) return false;
      if (ids.some((id) => node.id !== id)) return false;
      if (clss.some((c) => !node.classes.has(c))) return false;
      return true;
    };
    const seenFlowSels = new Set();
    for (const jm of allJs.matchAll(/\.(?:set|to|from|fromTo)\s*\(\s*["']([^"']+)["']\s*,\s*\{([^}]*)\}/g)) {
      const rawSel = jm[1];
      const varsStr = jm[2];
      if (!/\b[xy]Percent\s*:\s*-50\b/.test(varsStr)) continue;
      for (const part of rawSel.split(",")) {
        const sel = part.trim();
        if (!sel || seenFlowSels.has(sel)) continue;
        // Skip complex descendant selectors for target lookup unless simple compound
        if (/[\s>+~]/.test(sel)) continue;
        const matched = domNodes.filter((n) => matchesSimpleSel(n, sel));
        if (!matched.length) continue;
        for (const node of matched) {
          const declParts = [];
          if (node.inlineStyle) declParts.push(node.inlineStyle);
          for (const rule of cssRules) {
            if (rule.sels.some((rs) => matchesSimpleSel(node, rs))) {
              declParts.push(rule.decls);
            }
          }
          const combined = declParts.join("; ");
          if (/\bposition\s*:\s*(?:absolute|fixed)\b/i.test(combined)) continue;
          const posMatches = [...combined.matchAll(/\bposition\s*:\s*([a-zA-Z-]+)/gi)];
          const explicitPos = posMatches.length ? posMatches[posMatches.length - 1][1].toLowerCase() : "";
          const hasOffset = /\b(?:left|top|inset)\s*:/i.test(combined);
          if (explicitPos === "relative" || explicitPos === "static" || (!explicitPos && !hasOffset)) {
            seenFlowSels.add(sel);
            fail(`GSAP \`xPercent/yPercent: -50\` on non-absolute element "${sel}" (${explicitPos ? `position: ${explicitPos}` : "no position: absolute"}) shifts a flex/flow element off-center and risks clipping — remove \`xPercent/yPercent: -50\` on flex/flow children or use \`position: absolute; left: 50%; top: 50%\` (flow_element_xpercent_center)`);
            break;
          }
        }
      }
    }
  }
  const domIds = new Set();
  const domClasses = new Set();
  for (const im of tplHtml.matchAll(/\bid\s*=\s*["']([^"']+)["']/gi)) {
    domIds.add(im[1].trim());
  }
  for (const cm of tplHtml.matchAll(/\bclass\s*=\s*["']([^"']+)["']/gi)) {
    for (const c of cm[1].split(/\s+/)) {
      if (c) domClasses.add(c);
    }
  }
  const checkStaticSelector = (rawSel, helperName) => {
    if (!rawSel || rawSel.includes("${")) return;
    for (const part of rawSel.split(",")) {
      const p = part.trim();
      if (!p) continue;
      for (const idM of p.matchAll(/#([A-Za-z_-][A-Za-z0-9_-]*)/g)) {
        if (!domIds.has(idM[1])) {
          fail(`${helperName} selector "${rawSel}" references missing id "#${idM[1]}" not found in <template> DOM (missing_dom_selector)`);
        }
      }
      for (const clsM of p.matchAll(/\.([A-Za-z_-][A-Za-z0-9_-]*)/g)) {
        if (!domClasses.has(clsM[1])) {
          fail(`${helperName} selector "${rawSel}" references missing class ".${clsM[1]}" not found in <template> DOM (missing_dom_selector)`);
        }
      }
    }
  };
  for (const dm of allJs.matchAll(/\b__hf\.draw\s*\(\s*[^,]+,\s*["']([^"']+)["']/g)) {
    checkStaticSelector(dm[1], "__hf.draw");
  }
  for (const tm of allJs.matchAll(/\b__hf\.type\s*\(\s*[^,]+,\s*["']([^"']+)["'](?:\s*,\s*["']([^"']+)["'])?/g)) {
    checkStaticSelector(tm[1], "__hf.type");
    if (tm[2]) {
      checkStaticSelector(tm[2], "__hf.type");
      // P0-2 (6b0941c6 F4): caret <span> must appear AFTER text <span> in DOM order
      // so the typing cursor trails on the right instead of pinning on the left (`| Cheaper o`).
      const findFirstTagIndex = (sel) => {
        const clean = sel.trim().split(/[\s>+~]+/).pop() || "";
        const idM = /#([A-Za-z_-][A-Za-z0-9_-]*)/.exec(clean);
        if (idM) {
          const esc = idM[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          const m = new RegExp(`<[^>]*\\bid\\s*=\\s*["']${esc}["']`, "i").exec(tplHtml);
          return m ? m.index : -1;
        }
        const clsM = /\.([A-Za-z_-][A-Za-z0-9_-]*)/.exec(clean);
        if (clsM) {
          const esc = clsM[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          const m = new RegExp(`<[^>]*\\bclass\\s*=\\s*["'][^"']*\\b${esc}\\b[^"']*["']`, "i").exec(tplHtml);
          return m ? m.index : -1;
        }
        return -1;
      };
      const textIdx = findFirstTagIndex(tm[1]);
      const caretIdx = findFirstTagIndex(tm[2]);
      if (textIdx >= 0 && caretIdx >= 0 && caretIdx < textIdx) {
        fail(`__hf.type caret "${tm[2]}" precedes text element "${tm[1]}" in DOM order — place caret after text so the cursor trails on the right (type_caret_before_text)`);
      }
    }
  }

  // --- P0-2 (6b0941c6 F4): gsap_css_transform_conflict -----------------------
  // Mirror hyperframes lint's gsap_css_transform_conflict for simple selectors:
  // CSS `transform: ...scale/translate...` paired with non-from/fromTo GSAP
  // `set`/`to` animating the same transform family fails the verify mechanical gate.
  const cssScaleSels = new Map();
  const cssTranslateSels = new Map();
  for (const cm of allCss.matchAll(/([#.][A-Za-z_-][A-Za-z0-9_-]*)\s*\{([^{}]+)\}/g)) {
    const sel = cm[1].trim();
    const tMatch = /\btransform\s*:\s*([^;]+)/i.exec(cm[2]);
    if (!tMatch) continue;
    const tVal = tMatch[1].trim();
    if (/\bscale[XY3D]?\s*\(/i.test(tVal)) cssScaleSels.set(sel, tVal);
    if (/\btranslate[XY3D]?\s*\(/i.test(tVal)) cssTranslateSels.set(sel, tVal);
  }
  if (cssScaleSels.size || cssTranslateSels.size) {
    const reportedConflict = new Set();
    for (const jm of allJs.matchAll(/\b(?:tl|gsap)\.(set|to)\s*\(\s*["']([^"']+)["']\s*,\s*\{([^}]*)\}/g)) {
      const rawSel = jm[2];
      const vars = jm[3];
      const hasScale = /\b(?:scale|scaleX|scaleY)\s*:/.test(vars);
      const hasTranslate = /\b(?:x|y|xPercent|yPercent)\s*:/.test(vars);
      if (!hasScale && !hasTranslate) continue;
      for (const part of rawSel.split(",")) {
        const sel = part.trim();
        if (!sel || reportedConflict.has(sel)) continue;
        const cssScale = hasScale ? cssScaleSels.get(sel) : undefined;
        const cssTrans = hasTranslate ? cssTranslateSels.get(sel) : undefined;
        if (cssScale || cssTrans) {
          reportedConflict.add(sel);
          const cssVal = cssScale || cssTrans;
          fail(`"${sel}" has CSS \`transform: ${cssVal}\` and GSAP \`.${jm[1]}()\` animates transform — remove CSS transform and let GSAP own the initial state (gsap_css_transform_conflict)`);
        }
      }
    }
  }

  // --- P0-2 (6b096712 F8): gsap_non_transform_motion -------------------------
  // Mirror hyperframes lint's gsap_non_transform_motion: animating layout props
  // (left/top/right/bottom/margin*), text-reflow props (fontSize/letterSpacing/
  // wordSpacing), or roundProps in to/from/fromTo stutters under frame-by-frame
  // seek capture (instantaneous .set() is exempt; positional props are exempt
  // when a <canvas layoutsubtree> exists).
  {
    const hasLayoutSubtree = /<canvas\b[^>]*\blayoutsubtree\b/i.test(tplHtml);
    const LAYOUT_PROPS = new Set([
      "left", "right", "top", "bottom",
      "margin", "marginLeft", "marginRight", "marginTop", "marginBottom",
    ]);
    const REFLOW_OR_ROUND = new Set([
      "fontSize", "letterSpacing", "wordSpacing", "roundProps",
    ]);
    const extractTopLevelKeys = (objLiteral) => {
      const s = (objLiteral || "").trim();
      if (!s.startsWith("{") || !s.endsWith("}")) return [];
      const keys = [];
      let depth = 0;
      let inStr = null;
      for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (inStr) {
          if (ch === "\\") { i++; continue; }
          if (ch === inStr) inStr = null;
          continue;
        }
        if (ch === '"' || ch === "'" || ch === "`") { inStr = ch; continue; }
        if (ch === "{" || ch === "[" || ch === "(") { depth++; continue; }
        if (ch === "}" || ch === "]" || ch === ")") { depth--; continue; }
        if (depth === 1 && /[A-Za-z_$]/.test(ch)) {
          const prev = i > 0 ? s[i - 1] : "";
          if (/[A-Za-z0-9_$.]/.test(prev)) continue;
          let j = i;
          while (j < s.length && /[A-Za-z0-9_$]/.test(s[j])) j++;
          let k = j;
          while (k < s.length && /\s/.test(s[k])) k++;
          if (s[k] === ":") keys.push(s.slice(i, j));
          i = j - 1;
        }
      }
      return keys;
    };
    for (const b of inlineScripts) {
      const clean = stripJsComments(stripRuntime(b[1]));
      const tweenRe = /\b(?:tl|timeline|gsap)\.(to|from|fromTo)\s*\(/g;
      for (const tm of clean.matchAll(tweenRe)) {
        const method = tm[1];
        const startIdx = tm.index + tm[0].length;
        let depth = 1;
        let inStr = null;
        const args = [];
        let argStart = startIdx;
        for (let i = startIdx; i < clean.length && depth > 0; i++) {
          const ch = clean[i];
          if (inStr) {
            if (ch === "\\") { i++; continue; }
            if (ch === inStr) inStr = null;
            continue;
          }
          if (ch === '"' || ch === "'" || ch === "`") { inStr = ch; continue; }
          if (ch === "(" || ch === "{" || ch === "[") depth++;
          else if (ch === ")" || ch === "}" || ch === "]") {
            depth--;
            if (depth === 0) {
              args.push(clean.slice(argStart, i).trim());
              break;
            }
          } else if (ch === "," && depth === 1) {
            args.push(clean.slice(argStart, i).trim());
            argStart = i + 1;
          }
        }
        const varArgs = method === "fromTo" ? [args[1], args[2]] : [args[1]];
        const flagged = [];
        for (const va of varArgs) {
          for (const key of extractTopLevelKeys(va)) {
            if (REFLOW_OR_ROUND.has(key) || (!hasLayoutSubtree && LAYOUT_PROPS.has(key))) {
              if (!flagged.includes(key)) flagged.push(key);
            }
          }
        }
        if (flagged.length) {
          fail(`GSAP \`.${method}(${args[0] || ""})\` animates non-transform property (${flagged.join(", ")}) — animate transforms (\`x\`/\`y\`/\`scale\`) instead of layout/reflow properties (gsap_non_transform_motion)`);
        }
      }
    }
  }
}

// --- warnings (do not gate) --------------------------------------------------
if (/(^|[^{};]\s)transition\s*:/.test(html)) warn("CSS `transition` usage found (css_transition_used)");
const rootBg = /#root\s*\{[^}]*\bbackground\s*:\s*([^;}]*)/.exec(html);
if (rootBg && !/^\s*(transparent|none|initial)\s*$/.test(rootBg[1])) {
  warn("background painted on #root — full-bleed ground should be a class=\"clip\" layer, not the root");
}

// Font ramp warnings. frame.md is the ramp; CLI aliases are bundled and silent.
// font_dead_fallback only fires when alias keys loaded (otherwise a real alias
// could look orphan). Neither warning changes the exit code.
try {
  let dir = path.dirname(path.resolve(fileArg));
  let frameMd = null;
  for (let i = 0; i < 4; i++) {
    const cand = path.join(dir, "frame.md");
    if (fs.existsSync(cand)) { frameMd = cand; break; }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  if (frameMd) {
    const ramp = new Set(parseFrameFamilies(fs.readFileSync(frameMd, "utf8")).map((f) => normFamily(f)));
    const { keys } = loadCliAliases();
    const declared = extractDeclaredFamilies(html);
    const google = extractGoogleFamilies(html);
    const aliasHas = (n) => !!(keys && keys.has(n));
    for (const name of extractUsedFamilies(html)) {
      if (ramp.has(name) || aliasHas(name)) continue;
      warn(`font_out_of_ramp: "${name}" is not in frame.md typography`);
    }
    if (keys) {
      const seen = new Set();
      for (const stack of extractFontStacks(html)) {
        stack.forEach((name, i) => {
          if (i === 0 || !name || seen.has(name) || GENERIC_FAMILIES.has(name)) return;
          if (ramp.has(name) || declared.has(name) || google.has(name) || keys.has(name)) return;
          seen.add(name);
          warn(`font_dead_fallback: "${name}" is an orphan fallback (not primary) — drop it`);
        });
      }
    }
  }
} catch {
  /* font warnings are best-effort and never fail the structural gate */
}

if (fails.length === 0) {
  for (const w of warns) console.error(w);
  console.log(`PASS ${frameId}`);
  process.exit(0);
}
for (const f of fails) console.error(f);
for (const w of warns) console.error(w);
console.error(`FAIL ${frameId} (${fails.length} finding${fails.length > 1 ? "s" : ""})`);
process.exit(1);