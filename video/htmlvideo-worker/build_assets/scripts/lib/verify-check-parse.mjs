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
      const head = [
        selector,
        container ? `inside ${container}` : "",
        text ? `"${trunc(text, 40)}"` : "",
      ].filter(Boolean).join(" ");
      const msg = trunc([head, message].filter(Boolean).join(" — "), n);
      const item = {
        mark,
        code: String(f.code || "unknown"),
        msg,
        section: titleCase(section),
        file: relPath(f.sourceFile || "", root),
        selector,
        container,
        text,
        fixHint: f.fixHint ? String(f.fixHint) : "",
        dataAttributes:
          f.dataAttributes && typeof f.dataAttributes === "object" ? f.dataAttributes : {},
      };
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
]);
const TEXT_TOKENS = new Set([
  "headline", "heading", "title", "subtitle", "body", "line", "copy", "caption",
  "paragraph", "lede", "quote", "text", "h1", "h2", "h3", "h4", "p",
]);

function tokens(s) {
  return String(s || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
    .flatMap((t) => [t, t.replace(/\d+$/, "")]);
}

/** Split a layout msg "sel inside #cont \"text\" — message" into its sides. */
function overlapSides(item) {
  if (item.selector || item.container) {
    return { selector: item.selector || "", container: item.container || "" };
  }
  const head = String(item.msg || "").split(" — ")[0];
  const m = /^(.*?)\s+inside\s+(\S+)/.exec(head);
  if (m) return { selector: m[1], container: m[2] };
  return { selector: head.replace(/\s+".*$/, ""), container: "" };
}

/**
 * Heuristic split of a content_overlap finding:
 *  designed_overlay → data-layout-allow-overlap is appropriate
 *  real_collision   → reposition / resize; do not blanket-allow
 *  ambiguous        → inspect the midpoint snapshot first
 * The CLI fixHint always mentions allow-overlap, so it only counts when it
 * explicitly says the overlap is intentional.
 * @returns {{ kind: "designed_overlay"|"real_collision"|"ambiguous", reason: string }}
 */
export function triageContentOverlap(item) {
  const { selector, container } = overlapSides(item || {});
  const attrs = Object.keys(item?.dataAttributes || {}).join(" ");
  if (/allow-overlap|layout-ignore|decor|overlay/i.test(attrs)) {
    return { kind: "designed_overlay", reason: "dataAttributes mark it intentional" };
  }
  if (/\bintentional\b/i.test(item?.fixHint || "")) {
    return { kind: "designed_overlay", reason: "fixHint says intentional" };
  }
  const selT = tokens(selector);
  const conT = tokens(container);
  const decor = [...selT, ...conT].find((t) => DECOR_TOKENS.has(t));
  if (decor) return { kind: "designed_overlay", reason: `decorative token "${decor}"` };
  const selText = selT.some((t) => TEXT_TOKENS.has(t));
  const conText = conT.some((t) => TEXT_TOKENS.has(t));
  if (container && selText && conText) {
    return { kind: "real_collision", reason: "both sides are text blocks" };
  }
  return { kind: "ambiguous", reason: "no decorative or text-block signal" };
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
