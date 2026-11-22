/**
 * Parse `hyperframes check` output into {errors, warns, infos}.
 *
 * Prefers `--json` (layout/runtime/… findings[].severity). Text fallback also
 * accepts Layout lines shaped like:
 *   ✗ t=20.4, 20.7s content_overlap #sel inside #other — message
 * which the old `✗ code:` regex silently dropped (2026-11-22 incident).
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
      const message = f.message ? String(f.message) : "";
      const msg = trunc([selector, message].filter(Boolean).join(" — "), n);
      const item = {
        mark,
        code: String(f.code || "unknown"),
        msg,
        section: titleCase(section),
        file: relPath(f.sourceFile || "", root),
      };
      bucketFor(mark, buckets).push(item);
    }
  }
  return buckets;
}

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
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (/^[A-Z][A-Za-z]+$/.test(line.trim()) && line === line.trim()) {
      section = line.trim();
      cur = null;
      continue;
    }
    // Classic: "✗ code: rest"  (lint-style / older check)
    let m = /^\s*([✗⚠ℹ])\s+([a-z_]+):\s?(.*)$/.exec(line);
    // Layout/runtime: "✗ t=1.2, 3.4s content_overlap selector — msg"
    if (!m) {
      m = /^\s*([✗⚠ℹ])\s+t=[\d.,\s]+s\s+([a-z_]+)\s+(.*)$/.exec(line);
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
        section,
        file: "",
      };
      bucketFor(m[1], buckets).push(cur);
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
 * True when check exited non-zero but we still have zero parsed errors —
 * the 2026-11-22 "GATE FAIL + 0 error finding(s)" contradiction.
 */
export function checkParseContradiction(status, buckets) {
  return status !== 0 && status != null && (buckets?.errors?.length ?? 0) === 0;
}
