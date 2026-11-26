/**
 * Parse `hyperframes check` output into {errors, warns, infos}.
 *
 * Prefers `--json` (layout/runtime/… findings[].severity). Text fallback also
 * accepts the human-report shapes the old `✗ code:` regex silently dropped
 * (2026-11-22 incident):
 *   ✗ t=20.4, 20.7s content_overlap #sel inside #other — message
 *   ✗ t=1-5s (3 samples) content_overlap #sel — message
 *   ✗ #title 2.1:1 (need 4.5:1, t=1.2s)            (Contrast)
 */

const MARK = { error: "✗", warning: "⚠", info: "ℹ" };

export function trunc(s, n = 140) {
  const t = String(s ?? "");
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

function bucketFor(mark, buckets) {
  if (mark === "✗") return buckets.errors;
  if (mark === "⚠") return buckets.warns;
  return buckets.infos;
}

function titleCase(section) {
  const s = String(section || "?");
  return s ? s[0].toUpperCase() + s.slice(1) : "?";
}

/** Relativize an absolute project path when ``root`` is provided. */
export function relPath(p, root = "") {
  if (!p) return "";
  const s = String(p);
  if (root && s.startsWith(root)) {
    const cut = s.slice(root.length);
    return cut.startsWith("/") ? cut.slice(1) : cut;
  }
  return s.replace(/^\.\//, "");
}

/**
 * @param {object} json  Parsed `hyperframes check --json` body
 * @param {{ root?: string, truncMsg?: number }} [opts]
 */
export function parseCheckJson(json, opts = {}) {
  const root = opts.root || "";
  const n = opts.truncMsg ?? 130;
  const buckets = { errors: [], warns: [], infos: [] };
  if (!json || typeof json !== "object") return buckets;
  for (const [section, val] of Object.entries(json)) {
    if (section === "lint") continue; // lint is handled separately via lint --json
    if (!val || typeof val !== "object" || !Array.isArray(val.findings)) continue;
    for (const f of val.findings) {
      const sev = String(f.severity || "info").toLowerCase();
      const mark = MARK[sev] || "ℹ";
      const selector = f.selector ? String(f.selector) : "";
      const container = f.containerSelector ? String(f.containerSelector) : "";
      const text = f.text ? String(f.text) : "";
      const message = f.message ? String(f.message) : "";
      const code = String(f.code || "unknown");
      const isOverlap = code.includes("overlap");
      const isCanvasOverflow = code === "canvas_overflow";
      const relation = isOverlap ? "overlaps" : "inside";
      const file = relPath(f.sourceFile || "", root);
      let geo = "";
      if (isOverlap || isCanvasOverflow) {
        const parts = [];
        const frameStart = opts.frameStarts instanceof Map ? opts.frameStarts.get(file) : undefined;
        const hasLocal = typeof frameStart === "number" && Number.isFinite(frameStart) && frameStart > 0;
        if (typeof f.firstSeen === "number" && typeof f.lastSeen === "number" && f.lastSeen > f.firstSeen) {
          let tStr = `t=${f.firstSeen.toFixed(1)}-${f.lastSeen.toFixed(1)}s`;
          if (hasLocal) {
            const lo = Math.max(0, f.firstSeen - frameStart);
            const hi = Math.max(0, f.lastSeen - frameStart);
            tStr += ` (frame t=${lo.toFixed(1)}-${hi.toFixed(1)}s)`;
          }
          parts.push(tStr);
        } else if (typeof f.time === "number") {
          let tStr = `t=${f.time.toFixed(1)}s`;
          if (hasLocal) {
            const loc = Math.max(0, f.time - frameStart);
            tStr += ` (frame t=${loc.toFixed(1)}s)`;
          }
          parts.push(tStr);
        }
        const fmtBox = (r) => {
          if (!r || typeof r.width !== "number" || typeof r.height !== "number") return "";
          const x = typeof r.left === "number" ? r.left : r.x;
          const y = typeof r.top === "number" ? r.top : r.y;
          if (typeof x !== "number" || typeof y !== "number") return "";
          return `box(${Math.round(x)},${Math.round(y)},${Math.round(r.width)}x${Math.round(r.height)})`;
        };
        const b1 = fmtBox(f.rect || f.bbox);
        const b2 = isOverlap ? fmtBox(f.containerRect || f.otherRect || f.targetRect) : "";
        if (b1 && b2) parts.push(`${b1} vs ${b2}`);
        else if (b1) parts.push(b1);
        if (parts.length) geo = `[${parts.join(" ")}]`;
      }
      const head = [
        selector,
        container ? `${relation} ${container}` : "",
        text ? `"${trunc(text, 40)}"` : "",
        geo,
      ].filter(Boolean).join(" ");
      const msg = trunc([head, message].filter(Boolean).join(" — "), (isOverlap || isCanvasOverflow) ? Math.max(n, 220) : n);
      const item = {
        mark,
        code,
        msg,
        section: titleCase(section),
        file,
        selector,
        container,
        text,
        fixHint: f.fixHint ? String(f.fixHint) : "",
        dataAttributes:
          f.dataAttributes && typeof f.dataAttributes === "object" ? f.dataAttributes : {},
      };
      if (f.rect && typeof f.rect === "object") item.rect = f.rect;
      else if (f.bbox && typeof f.bbox === "object") item.rect = f.bbox;
      if (typeof f.ratio === "number" && Number.isFinite(f.ratio)) item.ratio = f.ratio;
      if (typeof f.requiredRatio === "number" && Number.isFinite(f.requiredRatio)) {
        item.requiredRatio = f.requiredRatio;
      }
      if (f.fg) item.fg = String(f.fg);
      if (f.bg) item.bg = String(f.bg);
      if (f.suggestedColor) item.suggestedColor = String(f.suggestedColor);
      if (typeof f.time === "number") item.time = f.time;
      if (typeof f.firstSeen === "number") item.firstSeen = f.firstSeen;
      if (typeof f.lastSeen === "number") item.lastSeen = f.lastSeen;
      if (typeof f.occurrences === "number") item.occurrences = f.occurrences;
      bucketFor(mark, buckets).push(item);
    }
  }
  return buckets;
}

/**
 * Error count the CLI itself declares for the non-lint sections parseCheckJson
 * buckets (``errorCount`` per section; falls back to counting findings).
 * @param {object} json
 */
export function jsonDeclaredErrorCount(json) {
  let total = 0;
  if (!json || typeof json !== "object") return total;
  for (const [section, val] of Object.entries(json)) {
    if (section === "lint") continue;
    if (!val || typeof val !== "object" || !Array.isArray(val.findings)) continue;
    if (typeof val.errorCount === "number" && Number.isFinite(val.errorCount)) {
      total += val.errorCount;
    } else {
      total += val.findings.filter(
        (f) => String(f?.severity || "").toLowerCase() === "error",
      ).length;
    }
  }
  return total;
}

/** Lint errors inside the check envelope (they also make check exit non-zero). */
export function jsonLintErrorCount(json) {
  const lint = json && typeof json === "object" ? json.lint : null;
  if (!lint || typeof lint !== "object") return 0;
  if (typeof lint.errorCount === "number") return lint.errorCount;
  return Array.isArray(lint.findings)
    ? lint.findings.filter((f) => String(f?.severity || "").toLowerCase() === "error").length
    : 0;
}

const TIME_PREFIX = String.raw`t=[\d.]+(?:-[\d.]+)?(?:,\s*[\d.]+)*s(?:\s+\(\d+\s+samples?\))?`;

/**
 * Text-line parser (fallback when --json is unavailable / unparseable).
 * @param {string} text
 * @param {{ truncMsg?: number }} [opts]
 */
export function parseCheckText(text, opts = {}) {
  const n = opts.truncMsg ?? 130;
  const buckets = { errors: [], warns: [], infos: [] };
  let section = "?";
  let cur = null;
  for (const raw of String(text || "").split("\n")) {
    // eslint-disable-next-line no-control-regex
    const line = (raw.endsWith("\r") ? raw.slice(0, -1) : raw).replace(/\x1b\[[0-9;]*m/g, "");
    if (/^[A-Z][A-Za-z]+$/.test(line.trim()) && line === line.trim()) {
      section = line.trim();
      cur = null;
      continue;
    }
    // Classic: "✗ code: rest"  (lint-style / older check)
    let m = /^\s*([✗⚠ℹ])\s+([a-z_]+):\s?(.*)$/.exec(line);
    // Layout: "✗ t=1.2s code …" | "t=1-5s (3 samples) code …" | "t=1.2, 3.4s code …"
    if (!m) {
      m = new RegExp(String.raw`^\s*([✗⚠ℹ])\s+${TIME_PREFIX}\s+([a-z_][a-z0-9_]*)\s+(.*)$`).exec(line);
    }
    // Contrast: "✗ #title 2.1:1 (need 4.5:1, t=1.2s)" — the CLI prints ✗ for
    // every contrast finding regardless of severity.
    if (!m) {
      const c = /^\s*([✗⚠ℹ])\s+([#.[]\S*)\s+([\d.]+):1\s+\(need\s+([\d.]+):1(?:,\s*t=[\d.]+s)?\)\s*$/.exec(line);
      if (c) m = [c[0], c[1], "contrast_aa_failure", `${c[2]} — ${c[3]}:1 (need ${c[4]}:1)`, c[2]];
    }
    // Bare: "✗ content_overlap selector — msg" (no timestamp)
    if (!m) {
      m = /^\s*([✗⚠ℹ])\s+([a-z_][a-z0-9_]*)\s+(.*)$/.exec(line);
    }
    if (m) {
      cur = {
        mark: m[1],
        code: m[2],
        msg: trunc(m[3] || "", n),
        section: m[2] === "contrast_aa_failure" && section === "?" ? "Contrast" : section,
        file: "",
      };
      if (m[4]) cur.selector = m[4];
      bucketFor(m[1], buckets).push(cur);
      continue;
    }
    const fix = /^\s{2,}Fix:\s*(.+)$/.exec(line);
    if (fix && cur) {
      cur.fixHint = fix[1].trim();
      continue;
    }
    const src = /^\s{2,}Try\s+.*?;\s*source\s+(\S+\.html)\s*$/.exec(line);
    if (src && cur && !cur.file) {
      cur.file = src[1].replace(/^\.\//, "");
      continue;
    }
    const f =
      /^\s{2,}(\/\S+\.html)\s+(.+)$/.exec(line) ||
      /^\s{2,}((?:compositions\/)?\S+\.html)\s*/.exec(line);
    if (f && cur && !cur.file) cur.file = f[1].replace(/^\.\//, "");
  }
  return buckets;
}

/**
 * Extract a JSON object from mixed stdout (debug noise possible).
 * @param {string} stdout
 * @returns {object|null}
 */
export function extractJsonObject(stdout) {
  const s = String(stdout || "").trim();
  if (!s) return null;
  try {
    const j = JSON.parse(s);
    return j && typeof j === "object" ? j : null;
  } catch {
    /* fall through */
  }
  const start = s.indexOf("{");
  if (start < 0) return null;
  // Prefer last complete object (CLI may print progress then JSON).
  for (let i = s.length - 1; i >= start; i--) {
    if (s[i] !== "}") continue;
    try {
      const j = JSON.parse(s.slice(start, i + 1));
      if (j && typeof j === "object") return j;
    } catch {
      /* keep scanning */
    }
  }
  return null;
}

/**
 * True when the parsed findings cannot explain the CLI result:
 *  - check exited non-zero but zero errors were parsed (the 2026-11-22
 *    "GATE FAIL + 0 error finding(s)" contradiction), unless the envelope's
 *    lint section (``opts.lintErrorCount``) already explains the exit; or
 *  - ``opts.declaredErrorCount`` is given and differs from the parsed count
 *    (partial miss — e.g. 8 declared, 1 parsed).
 * @param {number|null} status
 * @param {{ errors?: unknown[] }} buckets
 * @param {{ declaredErrorCount?: number, lintErrorCount?: number }} [opts]
 */
export function checkParseContradiction(status, buckets, opts = {}) {
  const parsed = buckets?.errors?.length ?? 0;
  if (typeof opts.declaredErrorCount === "number" && parsed !== opts.declaredErrorCount) {
    return true;
  }
  if ((opts.lintErrorCount ?? 0) > 0) return false;
  return status !== 0 && status != null && parsed === 0;
}

/** Declared-vs-parsed error mismatch, or null when they agree. */
export function checkParseMismatch(declared, parsed) {
  if (typeof declared !== "number" || typeof parsed !== "number") return null;
  return declared === parsed ? null : { declared, parsed };
}

const DECOR_TOKENS = new Set([
  "badge", "stamp", "overlay", "watermark", "seal", "sticker", "pill", "corner",
  "ribbon", "chip", "tag", "decor", "decoration", "ornament", "logo", "icon",
  "ticker", "dot",
]);
// Subset of DECOR_TOKENS strong enough to write data-layout-allow-overlap
// without a model. Generic UI words (tag / pill / chip / dot / badge / icon …)
// name real copy too often (`price-tag`, `span.tag`), so they only decide kind.
const AUTOFIX_DECOR_TOKENS = new Set([
  "watermark", "stamp", "seal", "sticker", "ribbon", "ornament", "decor",
  "decoration", "ticker",
]);
// Runtime caption words (scripts/captions.mjs); frame text overlapping them
// sits inside the caption keep-out band and must move, never be exempted.
const CAPTION_WORD_RE = /^(?:[a-z][\w-]*)?#caption-word-\d+-\d+$/i;
const TEXT_TOKENS = new Set([
  "headline", "heading", "title", "subtitle", "body", "line", "copy", "caption",
  "paragraph", "lede", "quote", "text", "h1", "h2", "h3", "h4", "p",
  "word", "sub", "subline", "kicker", "eyebrow", "deck",
]);
// A number + checkmark that sit on top of each other in the SAME badge circle
// (`.f04-num` / `.f04-ck`) is a status overlay, not a collision: the check
// fades in over the number. "ck"/"num" as standalone tokens (split on non-alnum)
// are distinctive enough to autofix — a real two-number collision is num×num and
// never hits the check token. `tick`/`check`/`number` cover the spelled-out forms.
const CHECK_TOKENS = new Set(["ck", "check", "checkmark", "tick", "checked"]);
const NUM_TOKENS = new Set(["num", "number"]);
// Occlusion-only. Not consulted by triageContentOverlap. A card / CTA / label
// sitting on text is usually intentional layering (allow-occlusion), unlike
// two solid text blocks.
const OCCLUSION_SOFT_TOKENS = new Set([
  "card", "notecard", "anchor", "cta", "buy", "label",
]);

function tokens(s) {
  return String(s || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
    .flatMap((t) => [t, t.replace(/\d+$/, "")]);
}

/** Split a layout msg "sel overlaps|inside #cont \"text\" — message" into its sides. */
function overlapSides(item) {
  if (item.selector || item.container) {
    return { selector: item.selector || "", container: item.container || "" };
  }
  const head = String(item.msg || "").split(" — ")[0];
  const m = /^(.*?)\s+(?:overlaps|inside)\s+(\S+)/.exec(head);
  if (m) return { selector: m[1], container: m[2] };
  return { selector: head.replace(/\s+".*$/, ""), container: "" };
}

/**
 * Heuristic split of a content_overlap finding:
 *  designed_overlay → data-layout-allow-overlap is appropriate
 *  real_collision   → reposition / resize; do not blanket-allow
 *  ambiguous        → inspect the midpoint snapshot first
 * fixHint is ignored: the CLI emits the same "…or mark intentional layering
 * with data-layout-allow-overlap." for every content_overlap, so it carries
 * no per-finding signal.
 * Both sides of a content_overlap are solid text blocks (the CLI only pairs
 * visible elements with own text and colour alpha >= 0.35).
 * ``autofix`` is true only for designed_overlay verdicts the harness may fix
 * without a model: dataAttributes, or a strong (AUTOFIX_DECOR_TOKENS) token on
 * exactly one side. ``mark_side`` then names the side to mark; it is null
 * whenever ``autofix`` is false.
 * @returns {{ kind: "designed_overlay"|"real_collision"|"ambiguous", reason: string,
 *   autofix: boolean, mark_side: "selector"|"container"|null }}
 */
export function triageContentOverlap(item) {
  const { selector, container } = overlapSides(item || {});
  const verdict = (kind, reason, markSide = null) =>
    ({ kind, reason, autofix: markSide !== null, mark_side: markSide });
  const attrs = Object.keys(item?.dataAttributes || {}).join(" ");
  if (/allow-overlap|layout-ignore|decor|overlay/i.test(attrs)) {
    return verdict("designed_overlay", "dataAttributes mark it intentional", "selector");
  }
  if (CAPTION_WORD_RE.test(selector.trim()) || CAPTION_WORD_RE.test(container.trim())) {
    return verdict("real_collision", "frame text inside caption keep-out band");
  }
  const selT = tokens(selector);
  const conT = tokens(container);
  const selText = selT.some((t) => TEXT_TOKENS.has(t));
  const conText = conT.some((t) => TEXT_TOKENS.has(t));
  if (container && selText && conText) {
    return verdict("real_collision", "both sides are text blocks");
  }
  const selDecor = selT.filter((t) => DECOR_TOKENS.has(t));
  const conDecor = conT.filter((t) => DECOR_TOKENS.has(t));
  if (selDecor.length && conDecor.length) {
    return verdict(
      "designed_overlay",
      `decorative tokens on both sides ("${selDecor[0]}" / "${conDecor[0]}")`,
    );
  }
  if (selDecor.length || conDecor.length) {
    const side = selDecor.length ? "selector" : "container";
    const found = selDecor.length ? selDecor : conDecor;
    const strong = found.find((t) => AUTOFIX_DECOR_TOKENS.has(t));
    if (strong) return verdict("designed_overlay", `decorative token "${strong}"`, side);
    return verdict("designed_overlay", `weak decorative token "${found[0]}"`);
  }
  // Number + checkmark in the SAME badge = status overlay (check fades in over
  // the number). Mark the check side so the overlay is allowed, deterministically.
  const selCheck = selT.some((t) => CHECK_TOKENS.has(t));
  const conCheck = conT.some((t) => CHECK_TOKENS.has(t));
  const selNum = selT.some((t) => NUM_TOKENS.has(t));
  const conNum = conT.some((t) => NUM_TOKENS.has(t));
  if ((selCheck && conNum) || (conCheck && selNum)) {
    const side = selCheck ? "selector" : "container";
    return verdict("designed_overlay", "number + check status overlay", side);
  }
  return verdict("ambiguous", "no decorative or text-block signal");
}

/** Group content_overlap errors by triage kind. */
export function triageOverlaps(errors) {
  const groups = { designed_overlay: [], real_collision: [], ambiguous: [] };
  for (const e of errors || []) {
    if (!String(e?.code || "").includes("overlap")) continue;
    const t = triageContentOverlap(e);
    groups[t.kind].push({ ...e, triage: t });
  }
  return groups;
}

function sideIsCaptionWord(side) {
  const s = String(side || "").trim();
  if (!s) return false;
  return CAPTION_WORD_RE.test(s) || /caption-word/i.test(s);
}

/**
 * Heuristic split of a text_occluded finding. Does not change overlap triage.
 *  designed_layering → data-layout-allow-occlusion is appropriate
 *  buried            → raise z-index / move; do not blanket-allow (caption
 *                      keep-out included — never paper over a caption-word)
 *  ambiguous         → inspect layering intent first
 * Order matches the overlap helper: dataAttributes, then caption, then
 * text-vs-text, then decorative / soft-layer tokens.
 * @returns {{ kind: "designed_layering"|"buried"|"ambiguous", reason: string }}
 */
export function triageOcclusion(item) {
  const { selector, container } = overlapSides(item || {});
  const attrs = [
    ...Object.keys(item?.dataAttributes || {}),
    ...Object.values(item?.dataAttributes || {}).map((v) => String(v ?? "")),
  ].join(" ");
  if (/allow-occlusion|allow-overlap|layout-ignore|decor|overlay/i.test(attrs)) {
    return { kind: "designed_layering", reason: "dataAttributes mark the layering intentional" };
  }
  if (sideIsCaptionWord(selector) || sideIsCaptionWord(container)) {
    return {
      kind: "buried",
      reason: "caption keep-out: move the frame text; do not allow-occlusion",
    };
  }
  const selT = tokens(selector);
  const conT = tokens(container);
  const hasText = (ts) => ts.some((t) => TEXT_TOKENS.has(t));
  if (selector && container && hasText(selT) && hasText(conT)) {
    return { kind: "buried", reason: "both sides are text blocks" };
  }
  const soft = (ts) => ts.some((t) => DECOR_TOKENS.has(t) || OCCLUSION_SOFT_TOKENS.has(t));
  if (soft(selT) || soft(conT)) {
    const found = [...selT, ...conT].find((t) => DECOR_TOKENS.has(t) || OCCLUSION_SOFT_TOKENS.has(t));
    return { kind: "designed_layering", reason: `decorative or soft-layer token "${found}"` };
  }
  return { kind: "ambiguous", reason: "no decorative, soft-layer, or text-block signal" };
}

/** Group text_occluded errors by triage kind. Other codes are ignored. */
export function triageOcclusions(errors) {
  const groups = { designed_layering: [], buried: [], ambiguous: [] };
  for (const e of errors || []) {
    if (e?.code !== "text_occluded") continue;
    const t = triageOcclusion(e);
    groups[t.kind].push({ ...e, triage: t });
  }
  return groups;
}

/** Line prefix the Python harness greps for (``html_utility/verify_overlap_autofix.py``). */
export const FINDINGS_JSON_PREFIX = "VERIFY_FINDINGS_JSON ";

/* ── shared-stage crossfade false positives ─────────────────────────────── */
// The shared-stage scaffold (`.hf-stage-*`, frame-packets) is pixel-locked
// across sibling frames, so while two frames are both on screen during a
// transition the same stage node overlaps ITSELF. `hyperframes check` dedups
// by selector and attributes the finding to the first frame that carries the
// class (6b0795df: F2 blamed for F4→F5 / F7→F8 crossfades), making it
// unfixable — verify then burns rounds editing the wrong file.

const TRANSITION_PRE_PAD = 0.1;
const TRANSITION_POST_PAD = 0.6; // transitions.mjs injects 0.5s cross/push tweens

/**
 * Map each frame's relative path (`compositions/frames/<id>.html`) to its
 * global `data-start` offset in the assembled `index.html`.
 * @param {string} indexHtml
 * @returns {Map<string, number>}
 */
export function frameStartTimes(indexHtml) {
  const starts = new Map();
  for (const m of String(indexHtml || "").matchAll(/<div\b[^>]*>/g)) {
    const tag = m[0];
    const srcM = /data-composition-src="([^"]*compositions\/frames\/[^"]+)"/.exec(tag);
    if (!srcM) continue;
    const start = Number(/data-start="([\d.]+)"/.exec(tag)?.[1]);
    if (!Number.isFinite(start)) continue;
    const rel = srcM[1].replace(/^\.\//, "");
    starts.set(rel, start);
  }
  return starts;
}

/**
 * Frame boundary windows from the assembled root ``index.html``.
 * @param {string} indexHtml
 * @returns {Array<[number, number]>}
 */
export function transitionWindows(indexHtml) {
  const wins = [];
  for (const m of String(indexHtml || "").matchAll(/<div\b[^>]*>/g)) {
    const tag = m[0];
    if (!/data-composition-src="[^"]*compositions\/frames\//.test(tag)) continue;
    const start = Number(/data-start="([\d.]+)"/.exec(tag)?.[1]);
    if (!Number.isFinite(start) || start <= 0) continue;
    wins.push([start - TRANSITION_PRE_PAD, start + TRANSITION_POST_PAD]);
  }
  return wins.sort((a, b) => a[0] - b[0]);
}

function stageClass(side) {
  return /\.(hf-stage-[a-z-]+)/i.exec(String(side || ""))?.[1]?.toLowerCase() || "";
}

/**
 * True when an overlap is a shared-stage node colliding with its own copy in
 * the neighbouring frame during a transition: both sides carry the SAME
 * `.hf-stage-*` class and every sampled time falls inside a transition window.
 * A real in-frame collision (any sample outside a window) is never matched.
 */
export function isSharedStageCrossfadeOverlap(item, windows) {
  if (!item || !String(item.code || "").includes("overlap")) return false;
  if (!Array.isArray(windows) || !windows.length) return false;
  const { selector, container } = overlapSides(item);
  const a = stageClass(selector);
  if (!a || a !== stageClass(container)) return false;
  const times = [item.firstSeen, item.lastSeen, item.time].filter((t) => typeof t === "number");
  if (!times.length) return false;
  const inWin = (t) => windows.some(([lo, hi]) => t >= lo && t <= hi);
  if (!times.every(inWin)) return false;
  // first/last only bound the samples; a high count means in-frame hits too.
  if (typeof item.occurrences === "number" && item.occurrences > windows.length * 2) return false;
  return true;
}

/** Split errors into kept vs shared-stage crossfade false positives. */
export function splitSharedStageCrossfade(errors, windows) {
  const kept = [];
  const demoted = [];
  for (const e of errors || []) {
    (isSharedStageCrossfadeOverlap(e, windows) ? demoted : kept).push(e);
  }
  return { kept, demoted };
}

/**
 * True when a `canvas_overflow` info finding represents real off-canvas text
 * clipping (not captions, not mid-transition push-slide motion, and exceeding
 * the composition bounds by at least `minBleedPx`).
 */
export function isBlockingCanvasOverflow(item, windows = [], opts = {}) {
  if (!item || String(item.code || "") !== "canvas_overflow") return false;
  const targetText = `${item.selector || ""} ${item.msg || ""}`;
  if (/#caption-word|\.caption-line/i.test(targetText)) return false;
  if (Array.isArray(windows) && windows.length) {
    const times = [item.firstSeen, item.lastSeen, item.time].filter((t) => typeof t === "number");
    if (times.length && times.every((t) => windows.some(([lo, hi]) => t >= lo && t <= hi))) {
      return false;
    }
  }
  const width = Number(opts.width) || 1920;
  const height = Number(opts.height) || 1080;
  const minBleedPx = Number.isFinite(opts.minBleedPx) ? opts.minBleedPx : 8;
  const r = item.rect;
  if (r && typeof r === "object") {
    const left = typeof r.left === "number" ? r.left : r.x;
    const top = typeof r.top === "number" ? r.top : r.y;
    const right = typeof r.right === "number"
      ? r.right
      : (typeof left === "number" && typeof r.width === "number" ? left + r.width : undefined);
    const bottom = typeof r.bottom === "number"
      ? r.bottom
      : (typeof top === "number" && typeof r.height === "number" ? top + r.height : undefined);
    const bleeds = [];
    if (typeof left === "number") bleeds.push(-left);
    if (typeof right === "number") bleeds.push(right - width);
    if (typeof top === "number") bleeds.push(-top);
    if (typeof bottom === "number") bleeds.push(bottom - height);
    if (bleeds.length) {
      const maxBleed = Math.max(0, ...bleeds);
      if (maxBleed < minBleedPx) return false;
    }
  }
  return true;
}

/** Split infos into kept vs promoted blocking `canvas_overflow` findings. */
export function splitCanvasOverflow(infos, windows = [], opts = {}) {
  const kept = [];
  const promoted = [];
  for (const info of infos || []) {
    if (isBlockingCanvasOverflow(info, windows, opts)) {
      promoted.push({
        ...info,
        mark: "✗",
        fixHint:
          info.fixHint
          || "Remove `white-space: nowrap` from multi-word sentence/headline containers, constrain `max-width` (e.g. <= 84cqw), or reduce font-size so text stays fully inside the composition canvas.",
      });
    } else {
      kept.push(info);
    }
  }
  return { kept, promoted };
}

/**
 * Machine-readable triage of check errors for deterministic auto-fixes.
 * Each overlap row carries both sides plus ``mark_side``: the harness marks
 * only that side and never falls back to the other one.
 * @param {Array<object>} errors  parseCheckJson/parseCheckText ``errors``
 */
export function findingsPayload(errors) {
  const tri = triageOverlaps(errors);
  const row = (e) => {
    const { selector, container } = overlapSides(e);
    return {
      file: e.file || "",
      code: String(e.code || ""),
      selector,
      container,
      reason: e.triage?.reason || "",
      autofix_ok: e.triage?.autofix === true,
      mark_side: e.triage?.mark_side ?? null,
    };
  };
  const overlapCount =
    tri.designed_overlay.length + tri.real_collision.length + tri.ambiguous.length;
  return {
    version: 1,
    designed_overlay: tri.designed_overlay.map(row),
    real_collision: tri.real_collision.map(row),
    ambiguous: tri.ambiguous.map(row),
    other_check_errors: Math.max(0, (errors || []).length - overlapCount),
  };
}
