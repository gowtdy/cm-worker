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
 *                 verify-all's prepare-phase ordered autofix pass (deterministic, no model, once per verify session).
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
 *     digitSelectors: string[],    // e.g. ["02-x.html: digit-led selector …"]
 *     lintErrors: [{ code, msg, file }],
 *     checkErrors: [{ code, msg, selector, container, file, fixHint,
 *                     dataAttributes, mark, section }],
 *     transitions: { status: number|null, error: string|null, text: string },
 *     missingFrames: string[],     // frames in STORYBOARD but not on disk
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
import { trunc, triageOverlaps } from "./lib/verify-check-parse.mjs";

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

/** Affected-file list for a bald block of check/lint findings. */
function where(list, cap = 4) {
  const parts = list.slice(0, cap).map((e) => {
    const side = e.container ? `${e.selector} inside ${e.container}` : (e.selector || e.msg.split(" — ")[0]);
    return `${e.file || "?"}: ${trunc(side, 70)}`;
  });
  if (list.length > cap) parts.push(`…+${list.length - cap}`);
  return parts.join("; ");
}

const byCode = (code) => (ctx) => (ctx.checkErrors || []).filter((e) => e.code === code);

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
      const rows = ["Follow this triage (never blanket-allow):"];
      if (tri.designed_overlay.length) rows.push(`- designed_overlay×${tri.designed_overlay.length} (${where(tri.designed_overlay)}) → add data-layout-allow-overlap="true" on the text or an inner container (NOT the scene root).`);
      if (tri.real_collision.length) rows.push(`- real_collision×${tri.real_collision.length} (${where(tri.real_collision)}) → reposition / reduce size; do NOT allow-overlap.`);
      if (tri.ambiguous.length) rows.push(`- ambiguous×${tri.ambiguous.length} (${where(tri.ambiguous)}) → inspect the frame layout intent first, then pick allow-overlap vs reposition.`);
      rows.push(
        "- caption keep-out band: if either side is a `#caption-word-*`, move the frame text above `--cap-band-top` or delete the in-frame caption strip — NEVER add data-layout-allow-overlap for it.",
      );
      return rows.join("\n");
    },
    reason:
      "Safe designed_overlay (strong decor tokens / dataAttributes) is auto-fixed deterministically upstream via VERIFY_FINDINGS_JSON + verify_overlap_autofix.py; what reaches the model is the residual (real / ambiguous / weak tokens).",
  },

  {
    id: "text-occluded",
    title: "text_occluded",
    match: byCode("text_occluded"),
    mode: "model",
    prompt:
      'If the layering is intentional, add data-layout-allow-occlusion="true" on the text element or an ancestor — this is the occlusion opt-out (NOT data-layout-allow-overlap, which only exempts content_overlap). If the text is accidentally buried under an opaque sibling, raise its stacking order (z-index) or move it out from under the covering element instead.',
    reason:
      "The CLI's occlusion probe hits an opaque element over the glyphs. data-layout-allow-overlap does NOT clear it (2026-11-22 22点 run looped 3 rounds on this before failing). Correct opt-out = closest('[data-layout-allow-occlusion]').",
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
      (e) => e.code === "text_box_overflow" && /#caption-word|\.caption-line/.test(e.msg),
    ),
    mode: "ignore",
    prompt:
      "A snug caption line-height lets a heavy glyph spill a few px into its own padding — nothing is actually clipped. Ignore; act only when the finding names a frame element (`#el-NN-*`), not a caption word.",
    reason: "caption pill uses a deliberately snug line-height and has no overflow:hidden; a heavy glyph's ink spills a few px into its own padding.",
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