/**
 * known-failures.mjs — single source of truth for verify-frame "known failures".
 *
 * This is the ONE place the mechanical fix knowledge lives. Both the
 * deterministic autofix phase (run once by verify-all.mjs --phase prepare) and the model-facing
 * fix prompt (emitted by verify-report.mjs, forwarded by html_utility/verify.py)
 * consume the SAME catalog, so SKILL.md's playbook and verify.py's cheat sheet
 * can never drift away from the report again.
 *
 * ── Accumulating a new failure ────────────────────────────────────────────
 * Add one entry below. Each entry needs:
 *   id      stable kebab-case slug
 *   title   short human label (the actionable-summary heading)
 *   match   (ctx) => hit[]   — return the matched findings (or [] if none)
 *   mode    "autofix" | "model" | "ignore" | "info"
 *       autofix → has `autofix(ctx, hits) => { changed, note }`, run by
 *                 verify-all's prepare-phase ordered autofix pass (deterministic,
 *                 no model, once per verify session). Not rendered in the
 *                 verify-report actionable summary.
 *       model   → has `prompt` (string, or (ctx, hits) => string); emitted into
 *                 the actionable summary and loaded into the model prompt only
 *                 when the finding is actually present.
 *       ignore  → known false-positive / non-issue; `prompt` is an informational
 *                 note, never blocks.
 *       info    → tell / non-blocking warning the model does not act on.
 *   prompt  the markdown rule handed to the model (see modes above). Do NOT
 *           restate the title as the first sentence — the report already prints
 *           the title as the heading.
 *   reason  why this happens / when it was seen / why it is fixed this way
 *           (accumulation notes; keep it terse).
 *
 * ── ctx shape (built by the caller) ───────────────────────────────────────
 *   {
 *     videodir: string,            // absolute project root
 *     stub: boolean,               // index.html has 0 composition refs
 *                                  // (prepare autofix only; report omits)
 *     digitSelectors: string[],    // e.g. ["02-x.html: digit-led selector …"]
 *     lintErrors: [{ code, msg, file }],
 *     checkErrors: [{ code, msg, selector, container, file, fixHint,
 *                     dataAttributes, mark, section }],
 *     transitions: { status: number|null, error: string|null, text: string },
 *     digitTell: Record<string,number>|Map,  // id_requires_css_escape per file
 *     googleImport: Record<string,number>|Map,
 *   }
 *
 * Non-findings / negative guidance that has no matching finding (e.g. the
 * "frame data-duration drifts from the index wrapper by design — do not grep to
 * confirm" non-issue) lives in the header note below, not as a list entry.
 *
 * ORDER MATTERS for the autofix pass: assemble-index runs before transitions
 * (transitions needs the assembled index); the data-duration suffix strip runs
 * before transitions verify (a `"Ns"` suffix breaks the transitions regex).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  canonicalImport,
  renderBlock,
  loadCliAliases,
  normFamily,
  parseBrandFaces,
  parseFrameFamilies,
  planFile,
} from "./lib/fonts.mjs";
import { trunc, triageOcclusions, triageOverlaps } from "./lib/verify-check-parse.mjs";

/* ── helpers ─────────────────────────────────────────────────────────────── */

/** HTML files a known-failure autofix or matcher scans (frames + the index). */
function compositionHtmlFiles(videodir) {
  const framesDir = path.join(videodir, "compositions", "frames");
  const files = [];
  try {
    for (const name of fs.readdirSync(framesDir)) {
      if (name.endsWith(".html")) files.push(path.join(framesDir, name));
    }
  } catch {
    /* frames dir missing — surfaced elsewhere */
  }
  const index = path.join(videodir, "index.html");
  if (fs.existsSync(index)) files.push(index);
  return files;
}

/** `data-duration="6.664s"` → strip the unit suffix, idempotent. */
function stripDurationSuffix(text) {
  return text.replace(/(\bdata-duration="[\d.]+)s"/g, '$1"');
}

/** Find frames/index files that still carry a `data-duration="Ns"` suffix. */
function scanDurationSuffixes(ctx) {
  const hits = [];
  for (const abs of compositionHtmlFiles(ctx.videodir)) {
    const text = fs.readFileSync(abs, "utf8");
    if (/\bdata-duration="[\d.]+s"/.test(text)) {
      hits.push({ file: path.relative(ctx.videodir, abs), text });
    }
  }
  return hits;
}

/**
 * Font residual plan for `font_family_without_font_face` errors (moved from
 * verify-report.mjs so the catalog owns the single copy).
 */
function fontActionLines(ctx, errors) {
  const root = ctx.videodir;
  const lines = [];
  try {
    const mdPath = path.join(root, "frame.md");
    const md = fs.existsSync(mdPath) ? fs.readFileSync(mdPath, "utf8") : "";
    const frameFamilies = parseFrameFamilies(md);
    const ramp = new Set(frameFamilies.map((f) => normFamily(f)));
    const brandFaces = parseBrandFaces(md);
    const aliases = loadCliAliases();
    const files = [...new Set(
      errors.filter((e) => e.code === "font_family_without_font_face").map((e) => e.file),
    )];
    const injected = [];
    const seenImp = new Set();
    const faceRules = [];
    const drops = [];
    for (const relFile of files) {
      const abs = path.resolve(root, relFile);
      if (!fs.existsSync(abs)) continue;
      const plan = planFile(fs.readFileSync(abs, "utf8"), {
        frameFamilies, brandFaces, aliases, prune: true,
      });
      for (const fam of plan.faces) {
        const n = normFamily(fam);
        if (!n || seenImp.has(n)) continue;
        seenImp.add(n);
        for (const rule of brandFaces.get(n)?.rules || []) faceRules.push(rule);
      }
      for (const fam of plan.injected) {
        const n = normFamily(fam);
        if (n && !seenImp.has(n)) { seenImp.add(n); injected.push(fam); }
      }
      for (const fam of plan.unresolved) {
        const hit = frameFamilies.find((f) => normFamily(f) === fam);
        if (hit && !seenImp.has(normFamily(hit))) {
          seenImp.add(normFamily(hit));
          injected.push(hit);
        }
      }
      const names = new Set([
        ...plan.dropped,
        ...plan.unresolved.filter((u) => !ramp.has(u)),
      ]);
      for (const name of names) drops.push(`${name} (${relFile})`);
    }
    const block = renderBlock(canonicalImport(injected), faceRules);
    if (block) {
      lines.push("     paste at the top of the first <style> inside <template> (after @charset):");
      for (const l of block.split("\n")) lines.push("     " + l);
    }
    if (drops.length) {
      lines.push("     drop:");
      for (const d of drops) lines.push("     " + d);
    } else if (!block) {
      lines.push("     no paste line — ramp families are already covered; drop dead non-primary fallbacks named in the lint message.");
    }
  } catch (err) {
    lines.push("     font plan unavailable (" + (err && err.message ? err.message : err) + ") — rerun node /app/scripts/font-imports.mjs --videodir <project> --scope all --dry-run for the plan.");
  }
  return lines;
}

/** Scan project via `hyperframes lint --json` for gsap_exit_missing_hard_kill hits. */
function scanGsapExitMissingHardKill(ctx) {
  if (Array.isArray(ctx.lintErrors) && ctx.lintErrors.length) {
    return ctx.lintErrors.filter((e) => e.code === "gsap_exit_missing_hard_kill");
  }
  // Only spawn lint inside prepare (stub is a boolean only in prepare's autofixCtx)
  // when a real project exists on disk.
  if (typeof ctx.stub !== "boolean" || !ctx.videodir) return [];
  if (!fs.existsSync(path.join(ctx.videodir, "hyperframes.json"))) return [];
  if (!compositionHtmlFiles(ctx.videodir).length) return [];
  const r = spawnSync("npx", ["hyperframes", "lint", ".", "--json"], {
    cwd: ctx.videodir, encoding: "utf8", timeout: 120_000, maxBuffer: 32 * 1024 * 1024,
  });
  if (r.error || !r.stdout) return [];
  const first = r.stdout.indexOf("{");
  const last = r.stdout.lastIndexOf("}");
  if (first === -1 || last <= first) return [];
  let parsed;
  try {
    parsed = JSON.parse(r.stdout.slice(first, last + 1));
  } catch {
    return [];
  }
  const findings = Array.isArray(parsed?.findings) ? parsed.findings : [];
  return findings
    .filter((f) => f.severity === "error" && f.code === "gsap_exit_missing_hard_kill")
    .map((f) => {
      const rawFile = String(f.file || "");
      const file = rawFile.startsWith(ctx.videodir)
        ? rawFile.slice(ctx.videodir.length + 1)
        : rawFile.replace(/^\.\//, "");
      return {
        file,
        code: f.code,
        selector: f.selector ? String(f.selector) : "",
        fixHint: f.fixHint ? String(f.fixHint) : "",
        snippet: f.snippet ? String(f.snippet) : "",
        msg: f.message ? String(f.message) : "",
      };
    });
}

/**
 * Insert the deterministic `tl.set(...)` hard kill suggested by lint's fixHint
 * right after the exiting GSAP tween. Skips clip-element hints (which require
 * an inner wrapper and stay with the model). Idempotent.
 */
function insertGsapHardKill(source, hit) {
  const hint = String(hit.fixHint || "");
  const m = /Add `(tl\.set\([^`]+\))` after the exit tween\./.exec(hint);
  if (!m) return { text: source, changed: false };
  const setStmt = m[1].trim().replace(/;?$/, ";");
  if (source.includes(setStmt)) return { text: source, changed: false };

  let anchorIdx = -1;
  const rawSnippet = String(hit.snippet || "").trim().replace(/…+$/, "").trim();
  if (rawSnippet) {
    const firstLine = rawSnippet.split("\n")[0].trim();
    if (firstLine) {
      anchorIdx = source.indexOf(firstLine);
    }
  }
  if (anchorIdx < 0 && hit.selector) {
    const esc = String(hit.selector).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(`tl\\.(?:to|fromTo)\\(\\s*["']${esc}["'][^;]*?(?:autoAlpha|opacity|visibility)\\s*:\\s*(?:0|["']hidden["'])`, "g");
    let lastMatch = null;
    let cur;
    while ((cur = re.exec(source)) !== null) lastMatch = cur;
    if (lastMatch) anchorIdx = lastMatch.index;
  }
  if (anchorIdx < 0) return { text: source, changed: false };

  const semiIdx = source.indexOf(";", anchorIdx);
  if (semiIdx < 0) return { text: source, changed: false };
  const lineStart = source.lastIndexOf("\n", anchorIdx) + 1;
  const indentMatch = /^[ \t]*/.exec(source.slice(lineStart, anchorIdx));
  const indent = indentMatch ? indentMatch[0] : "";
  const insertAt = semiIdx + 1;
  const next = source.slice(0, insertAt) + `\n${indent}${setStmt}` + source.slice(insertAt);
  return { text: next, changed: true };
}

/** Affected-file list for a bald block of check/lint findings. */
function where(list, cap = 4) {
  const parts = list.slice(0, cap).map((e) => {
    const rel = String(e.code || "").includes("overlap") ? "overlaps" : "inside";
    const side = e.container ? `${e.selector} ${rel} ${e.container}` : (e.selector || e.msg.split(" — ")[0]);
    return `${e.file || "?"}: ${trunc(side, 70)}`;
  });
  if (list.length > cap) parts.push(`…+${list.length - cap}`);
  return parts.join("; ");
}

const byCode = (code) => (ctx) => (ctx.checkErrors || []).filter((e) => e.code === code);

const IN_FRAME_CAPTION_MARKER = "/* aiflow:hide-in-frame-caption */";
const IN_FRAME_CAPTION_CLASS_RE =
  /^(?:[a-z]+\d*[-_])?(?:caption[-_](?:band|bar|strip|pill|box|wrap|row|footer)|cap[-_](?:band|bar|strip|sub|scrim)|bottom[-_]caption|footer[-_]caption)$|^f\d+[-_]caption$/i;

/**
 * Scan `compositions/frames/*.html` for redundant in-frame bottom caption strips
 * when `compositions/captions.html` already renders the root karaoke captions
 * (2026-11-25 6b06dfa0a2a2d105: F2 `.caption-band`, F5 `.f5-caption`, F6
 * `.caption-band` all rendered duplicate bottom bars without colliding with
 * `#caption-word-*` at check sample timestamps).
 */
function scanInFrameCaptionBands(ctx) {
  if (!ctx?.videodir) return [];
  const captionsTrack = path.join(ctx.videodir, "compositions", "captions.html");
  if (!fs.existsSync(captionsTrack)) return [];
  const framesDir = path.join(ctx.videodir, "compositions", "frames");
  if (!fs.existsSync(framesDir)) return [];
  const hits = [];
  let names = [];
  try {
    names = fs.readdirSync(framesDir).filter((n) => n.endsWith(".html")).sort();
  } catch {
    return [];
  }
  for (const name of names) {
    const abs = path.join(framesDir, name);
    const text = fs.readFileSync(abs, "utf8");
    if (text.includes(IN_FRAME_CAPTION_MARKER) || !text.includes("</style>")) continue;
    const classes = new Set();
    for (const m of text.matchAll(/\bclass\s*=\s*["']([^"']+)["']/gi)) {
      const tokens = m[1].trim().split(/\s+/).filter(Boolean);
      // Never hide a root or a multi-purpose `.clip` unless it is dedicated to the caption strip.
      for (const tok of tokens) {
        if (IN_FRAME_CAPTION_CLASS_RE.test(tok)) classes.add(tok);
      }
    }
    if (classes.size) {
      hits.push({
        file: path.join("compositions", "frames", name),
        classes: [...classes],
      });
    }
  }
  return hits;
}

function injectHideInFrameCaptionCss(source, classes) {
  if (!classes?.length || source.includes(IN_FRAME_CAPTION_MARKER)) {
    return { text: source, changed: false };
  }
  const idx = source.lastIndexOf("</style>");
  if (idx < 0) return { text: source, changed: false };
  const sel = classes.map((c) => `.${c}`).join(", ");
  const rule = `  ${IN_FRAME_CAPTION_MARKER}\n  ${sel} { display: none !important; visibility: hidden !important; pointer-events: none !important; }\n`;
  return {
    text: source.slice(0, idx) + rule + source.slice(idx),
    changed: true,
  };
}

/**
 * checkErrors not claimed by a specific check entry above the catch-all.
 * Owned set lives here only: overlap substring (content-overlap), plus the
 * codes eaten by text-occluded / text-not-painted / contrast-aa-failure / the
 * two text_box_overflow entries (caption ignore + non-caption model).
 */
function unownedCheckErrors(ctx) {
  const owned = (code) => {
    const c = String(code || "");
    if (c.includes("overlap")) return true;
    return (
      c === "text_occluded"
      || c === "text_not_painted"
      || c === "contrast_aa_failure"
      || c === "canvas_overflow"
      || c === "text_box_overflow"
    );
  };
  return (ctx.checkErrors || []).filter((e) => !owned(e.code));
}

const CAPTION_OVERFLOW_RE = /#caption-word|\.caption-line/;

/* ── the catalog ─────────────────────────────────────────────────────────── */

export const KNOWN_FAILURES = [
  /* ————— autofix entries (deterministic, no model) ————— */

  {
    id: "assemble-index-stub",
    title: "un-assembled index stub",
    match: (ctx) => (ctx.stub ? [{ file: "index.html" }] : []),
    mode: "autofix",
    autofix: (ctx) => {
      const r = spawnSync("node", ["/app/scripts/assemble-index.mjs", "--videodir", ctx.videodir], {
        cwd: ctx.videodir, encoding: "utf8", timeout: 60_000,
      });
      if (r.error || r.status !== 0) {
        return { changed: false, note: `assemble-index failed: ${r.error?.message || `exit ${r.status}`}` };
      }
      return { changed: true, note: "ran assemble-index.mjs" };
    },
    prompt:
      "Run `node /app/scripts/assemble-index.mjs --videodir <project>` then rerun (the verify-all prepare phase does this automatically).",
    reason: "pipeline assemble step was skipped; deterministic recovery, <10s.",
  },

  {
    id: "data-duration-unit-suffix",
    title: "data-duration unit suffix (`Ns`)",
    match: (ctx) => scanDurationSuffixes(ctx),
    mode: "autofix",
    autofix: (ctx, hits) => {
      let changed = false;
      for (const hit of hits) {
        const abs = path.join(ctx.videodir, hit.file);
        const text = fs.readFileSync(abs, "utf8");
        const next = stripDurationSuffix(text);
        if (next !== text) {
          fs.writeFileSync(abs, next, "utf8");
          changed = true;
        }
      }
      return { changed, note: changed ? "stripped data-duration unit suffixes" : "nothing to strip" };
    },
    prompt:
      'Strip root/clip `data-duration="Ns"` to a plain number `data-duration="N"`. Do NOT diagnose transitions by reading scripts (the verify-all prepare phase strips this automatically).',
    reason: "STORYBOARD `duration: 6.664s` is copied verbatim into frame roots; the deployed transitions regex only matches a bare number (2026-11-21 regression).",
  },

  {
    id: "in-frame-caption-band",
    title: "redundant in-frame bottom caption strip",
    match: (ctx) => scanInFrameCaptionBands(ctx),
    mode: "autofix",
    autofix: (ctx, hits) => {
      let changed = false;
      let hidden = 0;
      for (const hit of hits) {
        const abs = path.join(ctx.videodir, hit.file);
        if (!fs.existsSync(abs)) continue;
        const text = fs.readFileSync(abs, "utf8");
        const res = injectHideInFrameCaptionCss(text, hit.classes);
        if (res.changed) {
          fs.writeFileSync(abs, res.text, "utf8");
          changed = true;
          hidden += hit.classes.length;
        }
      }
      return {
        changed,
        note: changed
          ? `hid ${hidden} redundant in-frame caption class(es) via CSS`
          : "no redundant in-frame caption strips to hide",
      };
    },
    prompt:
      "Hide or remove redundant in-frame bottom `.caption-band` / `.fN-caption` strips when `compositions/captions.html` is active (verify-all prepare auto-hides them).",
    reason:
      "2026-11-25 (6b06dfa0a2a2d105): frames 02, 05, 06 drew their own bottom `.caption-band` / `.f5-caption` bars alongside `compositions/captions.html` without triggering `#caption-word` overlap at sampled timestamps. CSS `display: none !important` removes the visual collision without breaking DOM refs or GSAP selectors.",
  },

  {
    id: "gsap-exit-missing-hard-kill",
    title: "gsap_exit_missing_hard_kill",
    match: (ctx) => scanGsapExitMissingHardKill(ctx),
    mode: "autofix",
    autofix: (ctx, hits) => {
      let changed = false;
      let fixed = 0;
      const byFile = new Map();
      for (const hit of hits) {
        if (!hit.file) continue;
        if (!byFile.has(hit.file)) byFile.set(hit.file, []);
        byFile.get(hit.file).push(hit);
      }
      for (const [relFile, fileHits] of byFile) {
        const abs = path.resolve(ctx.videodir, relFile);
        if (!fs.existsSync(abs)) continue;
        let text = fs.readFileSync(abs, "utf8");
        let fileChanged = false;
        for (const hit of fileHits) {
          const res = insertGsapHardKill(text, hit);
          if (res.changed) {
            text = res.text;
            fileChanged = true;
            fixed += 1;
          }
        }
        if (fileChanged) {
          fs.writeFileSync(abs, text, "utf8");
          changed = true;
        }
      }
      return {
        changed,
        note: changed
          ? `inserted ${fixed} tl.set hard kill(s)`
          : "no non-clip hard kills auto-insertable (clip elements left for model)",
      };
    },
    prompt:
      "Add the exact `tl.set(selector, { autoAlpha: 0 }, boundary)` hard kill from the lint `[hint: …]` right after the exit tween (verify-all prepare auto-inserts this for non-clip targets).",
    reason:
      "2026-11-24 (6b05ba2b5d081581): two gsap_exit_missing_hard_kill lint errors short-circuited hyperframes check and forced an entire extra verify round (43s + 2 model calls). Lint already emits the exact tl.set fixHint.",
  },

  /* ————— model entries (rule injected into the fix prompt when matched) ————— */

  {
    id: "digit-led-selectors",
    title: "digit-led selectors",
    match: (ctx) => (ctx.digitSelectors || []).length ? ctx.digitSelectors : [],
    mode: "model",
    prompt:
      'Convert digit-led selectors (`#07-…`, `.01-…`, `"#06-…"` inside CSS/JS selector strings) to attribute form `[id="…"]` / `[class~="…"]`; leave `getElementById` untouched. Fix every occurrence named above in ONE pass.',
    reason: "CSS silently drops a digit-led rule; querySelector(All) throws. Mechanical but spans multiple files (candidate for autofix later).",
  },

  {
    id: "font-family-no-face",
    title: "font_family_without_font_face",
    match: (ctx) => (ctx.lintErrors || []).filter((e) => e.code === "font_family_without_font_face"),
    mode: "model",
    prompt: (ctx, hits) =>
      "Residual only (the prepare phase already ran font-imports once): drop dead non-primary fallback names named in the lint message; if a ramp family is still missing, paste the marked block below.\n"
      + fontActionLines(ctx, hits).join("\n")
      + "\nDo not add `@font-face`, do not hunt local fonts, do not fetch URL fonts.",
    reason: "verify-all prepare phase runs font-imports once per verify session (idempotent); only hand-fixable residuals reach the model.",
  },

  {
    id: "content-overlap",
    title: "content_overlap",
    match: (ctx) => (ctx.checkErrors || []).filter((e) => (e.code || "").includes("overlap")),
    mode: "model",
    prompt: (ctx, hits) => {
      const tri = triageOverlaps(hits);
      const rows = [
        "Follow this triage (never blanket-allow). Decide each finding in ≤2 sentences, then emit every Edit in your FIRST batched pass — do NOT re-derive cqw/cqh geometry or re-trace the GSAP timeline; the scan already measured the boxes.",
      ];
      if (tri.designed_overlay.length) rows.push(`- designed_overlay×${tri.designed_overlay.length} (${where(tri.designed_overlay)}) → add data-layout-allow-overlap="true" on the text or an inner container (NOT the scene root).`);
      if (tri.real_collision.length) rows.push(`- real_collision×${tri.real_collision.length} (${where(tri.real_collision)}) → reposition / reduce size; do NOT allow-overlap.`);
      if (tri.ambiguous.length) rows.push(`- ambiguous×${tri.ambiguous.length} (${where(tri.ambiguous)}) → sibling badges overlaying in the SAME circle/box (a number next to a check/tick) = data-layout-allow-overlap="true"; two text blocks that should sit APART or have no position = reposition. If still unsure, default to reposition — never re-read the file.`);
      rows.push(
        "- caption keep-out band: if either side is a `#caption-word-*`, delete the frame's own in-frame caption strip (it duplicates the auto-caption) or move it above `--cap-band-top` — NEVER add data-layout-allow-overlap for it.",
      );
      rows.push(
        "Then reply `fixed <n>` — no summary, no checklist.",
      );
      return rows.join("\n");
    },
    reason:
      "Safe designed_overlay (strong decor tokens / dataAttributes / number+check status overlay) is auto-fixed deterministically upstream via VERIFY_FINDINGS_JSON + verify_overlap_autofix.py; what reaches the model is the residual (real / ambiguous / weak tokens). The prompt is a decision tree with a default so the fix worker edits on turn 1 instead of burning its output budget thinking (2026-11-23 6b044297059bf6d4: 2× 16000-token thinking-only turns, 0 edits).",
  },

  {
    id: "text-occluded",
    title: "text_occluded",
    match: byCode("text_occluded"),
    mode: "model",
    prompt: (ctx, hits) => {
      const tri = triageOcclusions(hits);
      const rows = ["Follow this triage (never blanket-allow occlusion):"];
      if (tri.designed_layering.length) {
        rows.push(
          `- designed_layering×${tri.designed_layering.length} (${where(tri.designed_layering)}) → add data-layout-allow-occlusion="true" on the text element or an ancestor — the occlusion opt-out (NOT data-layout-allow-overlap, which only exempts content_overlap).`,
        );
      }
      if (tri.buried.length) {
        rows.push(
          `- buried×${tri.buried.length} (${where(tri.buried)}) → raise its stacking order (z-index) or move it out from under the covering element. Do not use data-layout-allow-occlusion, and NOT data-layout-allow-overlap.`,
        );
      }
      if (tri.ambiguous.length) {
        rows.push(
          `- ambiguous×${tri.ambiguous.length} (${where(tri.ambiguous)}) → inspect the layering intent first, then pick data-layout-allow-occlusion vs raise z-index / move (NOT data-layout-allow-overlap).`,
        );
      }
      rows.push(
        "- caption keep-out: if either side is a #caption-word-*, move the frame text — NEVER data-layout-allow-occlusion to paper over a caption (NOT data-layout-allow-overlap, which only exempts content_overlap).",
      );
      return rows.join("\n");
    },
    reason:
      "triageOcclusions: designed_layering (dataAttributes, DECOR_TOKENS, or card/notecard/anchor/cta/buy/label) may take data-layout-allow-occlusion; buried (both sides text, or a caption-word) must move or raise z-index — never paper over a caption. data-layout-allow-overlap does NOT clear occlusion (2026-11-22 22点 run looped 3 rounds). Correct opt-out = closest('[data-layout-allow-occlusion]').",
  },

  {
    id: "text-not-painted",
    title: "text_not_painted",
    match: byCode("text_not_painted"),
    mode: "model",
    prompt:
      "The element's `color` / `-webkit-text-fill-color` resolves to transparent, so its glyphs are invisible. Set an opaque fill; only leave it if `background-clip: text` with a real gradient/image actually paints the glyphs. Fix the named element only.",
    reason: "-webkit-text-fill-color:transparent overrides color and inherits, silently blanking descendant text (seen 2026-11-22 #qmark).",
  },

  {
    id: "contrast-aa-failure",
    title: "contrast_aa_failure",
    match: byCode("contrast_aa_failure"),
    mode: "model",
    prompt: (_ctx, hits) =>
      `Illegible foreground/background text contrast (${where(hits)}): set the text \`color\` on light/white/cream surfaces to dark \`ink\` (\`#1A1A1A\`), or on dark surfaces to \`#FFFFFF\` (keep candy hues like yellow/lime/mint/peach on the pill \`background\`, NEVER as text color on light surfaces). Apply the edit in ONE pass and reply \`fixed <n>\`.`,
    reason:
      "2026-11-25 (6b06dfa0a2a2d105 F5): `.f5-slot-live { color: #F2D160 }` on `#FFFFFF` rendered at 1.44:1 contrast (unreadable). Held or severe (<2.5:1) contrast failures now gate and route here.",
  },

  {
    id: "canvas-overflow",
    title: "canvas_overflow",
    match: byCode("canvas_overflow"),
    mode: "model",
    prompt: (_ctx, hits) =>
      `Text extends outside the 1920x1080 composition canvas and is clipped (${where(hits)}): remove \`white-space: nowrap\` from the multi-word sentence/headline container (allow natural wrapping), constrain its \`max-width\` (e.g. \`<= 84cqw\`), and tighten \`font-size\` / horizontal padding so every word fits comfortably inside the canvas. Apply the edit in ONE pass and reply \`fixed <n>\`.`,
    reason:
      "2026-11-26 (6b082a5be89ed160 F2): `.sentence { white-space: nowrap; font-size: 3.6cqw }` pushed `span.tail` to right=2032px (>1920px), clipping 'all align'. Promoted from info to blocking error.",
  },

  {
    id: "transitions-verify",
    title: "transitions verify failures",
    match: (ctx) =>
      ctx.transitions && ctx.transitions.status !== 0 && !ctx.transitions.error
        ? [{ file: "transitions" }]
        : [],
    mode: "info",
    prompt:
      "The prepare phase already ran the idempotent transitions inject once for this verify session (after stripping data-duration unit suffixes); do NOT hand-edit transition blocks or re-inject yourself.",
    reason:
      "prepare phase injects once per verify session (idempotent, after the data-duration-unit-suffix autofix); a residual failure is not a model fix.",
  },

  /* ————— ignore entries (false positive / non-issue) ————— */

  {
    id: "caption-text-box-overflow",
    title: "caption text_box_overflow",
    match: (ctx) => (ctx.checkErrors || []).filter(
      (e) => e.code === "text_box_overflow" && CAPTION_OVERFLOW_RE.test(e.msg),
    ),
    mode: "ignore",
    prompt:
      "A snug caption line-height lets a heavy glyph spill a few px into its own padding — nothing is actually clipped. Ignore; act only when the finding names a frame element (`#el-NN-*`), not a caption word.",
    reason: "caption pill uses a deliberately snug line-height and has no overflow:hidden; a heavy glyph's ink spills a few px into its own padding.",
  },

  {
    id: "text-box-overflow",
    title: "text_box_overflow",
    // After caption-text-box-overflow so the caption ignore is listed first.
    // Match is the complement of that ignore (same regex); caption rows never land here.
    match: (ctx) => (ctx.checkErrors || []).filter(
      (e) => e.code === "text_box_overflow" && !CAPTION_OVERFLOW_RE.test(e.msg),
    ),
    mode: "model",
    prompt:
      "Tighten the named element's box, lower the font-size, or adjust line-height / padding so the glyphs fit. Ignore only when the design allows the overflow and nothing is clipped by overflow:hidden. Fix the named element only.",
    reason: "Non-caption text_box_overflow is a real fit problem. The caption snug line-height false-positive stays on caption-text-box-overflow.",
  },

  {
    id: "id-requires-css-escape",
    title: "id_requires_css_escape",
    match: (ctx) => {
      const tell = ctx.digitTell;
      const size = tell && (tell instanceof Map ? tell.size : Object.keys(tell).length) || 0;
      return size ? [{ file: "n/a" }] : [];
    },
    mode: "info",
    prompt:
      "Tell warning only — not a defect by itself. Ignore unless the structural gate (digit-led selectors) is also firing.",
    reason: "A digit-led id is legal when selectors use [id=…]/[class~=…] or getElementById.",
  },

  {
    id: "google-fonts-import",
    title: "google_fonts_import",
    match: (ctx) => {
      const g = ctx.googleImport;
      return (g && (g instanceof Map ? g.size : Object.keys(g).length) || 0) ? [{ file: "n/a" }] : [];
    },
    mode: "info",
    prompt: "Non-blocking warning — ignore.",
    reason: "font sourcing note; never gates.",
  },

  /* ————— catch-alls (must stay last; specific entries above own their codes) ————— */

  {
    id: "other-check-errors",
    title: "other check errors",
    match: (ctx) => unownedCheckErrors(ctx),
    mode: "model",
    prompt: (_ctx, hits) =>
      `Cheapest safe edit for each listed item (${where(hits)}), using the \`[hint: …]\` on that error line (the CLI fixHint) and the named selector/file. Fix every listed item in ONE pass. Do NOT rerun lint, check, or snapshot.`,
    reason: "Check codes with no specific entry still need the CLI fixHint in the model prompt, instead of a generic fallback that invents the wrong opt-out.",
  },

  {
    id: "other-lint-errors",
    title: "other lint errors",
    match: (ctx) => (ctx.lintErrors || []).filter((e) => e.code !== "font_family_without_font_face"),
    mode: "model",
    prompt: (_ctx, hits) =>
      `Fix the named file according to the lint message and its \`[hint: …]\` (${where(hits)}). Do not act on google_fonts_import or id_requires_css_escape (those are warning/info).`,
    reason: "Lint errors other than font_family_without_font_face have no specific entry. google_fonts_import and id_requires_css_escape stay on their info entries.",
  },
];

/**
 * Match the catalog against a findings context, in catalog order.
 * @returns {Array<{ entry: object, hits: Array }>} matched entries with their hits.
 */
export function matchKnownFailures(ctx) {
  const out = [];
  for (const entry of KNOWN_FAILURES) {
    let hits;
    try {
      hits = entry.match(ctx) || [];
    } catch {
      hits = [];
    }
    if (hits.length) out.push({ entry, hits });
  }
  return out;
}

/** Resolve an entry's prompt (string or (ctx, hits) => string) to a string. */
export function renderPrompt(entry, ctx, hits) {
  if (typeof entry.prompt === "function") return entry.prompt(ctx, hits);
  return entry.prompt || "";
}