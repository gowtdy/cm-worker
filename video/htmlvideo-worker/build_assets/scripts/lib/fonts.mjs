/**
 * fonts.mjs — frame.md font ramp → marked Google @import / brand @font-face.
 *
 * Family parsing matches HyperFrames CLI 0.7.76 lint
 * (GENERIC_FAMILIES, used / @font-face / Google @import extraction).
 * CLI alias keys come from FONT_ALIAS_MAP* in the installed cli.js.
 * If that read fails, keys stay null: inject every non-generic frame.md
 * family and do not prune (conservative).
 *
 * The managed block is always wrapped so a second run can delete it and
 * rewrite the same bytes:
 *   /* aiflow:font-imports v1 *\/
 *   @import url('...');
 *   /* aiflow:font-imports end *\/
 */

import fs from "node:fs";

export const MARK_IMPORT = "/* aiflow:font-imports v1 */";
export const MARK_END = "/* aiflow:font-imports end */";
export const DEFAULT_CLI =
  "/usr/local/lib/node_modules/@hyperframes/cli/dist/cli.js";

export const GENERIC_FAMILIES = new Set([
  "serif",
  "sans-serif",
  "monospace",
  "cursive",
  "fantasy",
  "system-ui",
  "ui-serif",
  "ui-sans-serif",
  "ui-monospace",
  "ui-rounded",
  "math",
  "emoji",
  "fangsong",
  "-apple-system",
  "blinkmacsystemfont",
  "inherit",
  "initial",
  "unset",
  "revert",
]);

const aliasCache = new Map();

export function normFamily(part) {
  if (part == null) return null;
  const name = String(part)
    .trim()
    .replace(/\s*!important\s*$/i, "")
    .replace(/^['"]|['"]$/g, "")
    .trim()
    .toLowerCase();
  if (!name || name.includes("(") || name.includes(")")) return null;
  return name;
}

function stripCssComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, " ");
}

function maskNoise(css) {
  let s = css.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "));
  s = s.replace(/@font-face\s*\{[^}]*\}/gi, (c) => c.replace(/[^\n]/g, " "));
  return s;
}

export function iterStyleChunks(html) {
  const chunks = [];
  for (const m of String(html).matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
    chunks.push(m[1]);
  }
  for (const m of String(html).matchAll(/\bstyle\s*=\s*(["'])([\s\S]*?)\1/gi)) {
    chunks.push(m[2]);
  }
  return chunks;
}

function mapStyleRegions(html, fn) {
  let out = html.replace(/<style\b([^>]*)>([\s\S]*?)<\/style>/gi, (_full, attrs, inner) => {
    return `<style${attrs}>${fn(inner)}</style>`;
  });
  out = out.replace(/(\bstyle\s*=\s*)(["'])([\s\S]*?)\2/gi, (_full, pre, q, inner) => {
    return `${pre}${q}${fn(inner)}${q}`;
  });
  return out;
}

/** Quoted fontFamily values inside every `typography:` block. Order kept, generics dropped. */
export function parseFrameFamilies(md) {
  const lines = String(md || "").split(/\r?\n/);
  const blocks = [];
  let inBlock = false;
  let buf = [];
  for (const line of lines) {
    if (/^typography:\s*(#.*)?$/.test(line)) {
      inBlock = true;
      buf = [];
      continue;
    }
    if (!inBlock) continue;
    if (/^\S/.test(line)) {
      blocks.push(buf.join("\n"));
      inBlock = false;
      continue;
    }
    buf.push(line);
  }
  if (inBlock) blocks.push(buf.join("\n"));
  const families = [];
  const seen = new Set();
  const re = /fontFamily:\s*(?:"([^"]+)"|'([^']+)')/g;
  for (const text of blocks) {
    let m;
    while ((m = re.exec(text))) {
      const raw = m[1] ?? m[2];
      for (const part of raw.split(",")) {
        const display = part.trim().replace(/^['"]|['"]$/g, "").trim();
        const n = normFamily(display);
        if (!n || GENERIC_FAMILIES.has(n) || seen.has(n)) continue;
        seen.add(n);
        families.push(display);
      }
    }
  }
  return families;
}

/** @font-face rules authored in frame.md (brand files). Keyed by normFamily. */
export function parseBrandFaces(md) {
  const map = new Map();
  const re = /@font-face\s*\{[^}]*\}/gi;
  const familyRe = /font-family\s*:\s*(['"]?)([^;'"}]+)\1/i;
  let m;
  const text = String(md || "");
  while ((m = re.exec(text))) {
    const rule = m[0].trim();
    const fam = familyRe.exec(rule);
    if (!fam) continue;
    const display = fam[2].trim();
    const n = normFamily(display);
    if (!n) continue;
    if (!map.has(n)) map.set(n, { display, rules: [] });
    const bucket = map.get(n);
    if (!bucket.rules.includes(rule)) bucket.rules.push(rule);
  }
  return map;
}

function extractObjectLiteral(src, openIdx) {
  if (src[openIdx] !== "{") return null;
  let depth = 0;
  for (let i = openIdx; i < src.length; i++) {
    const c = src[i];
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return src.slice(openIdx, i + 1);
    }
  }
  return null;
}

function loadCliAliasesUncached(cliPath) {
  try {
    const src = fs.readFileSync(cliPath, "utf8");
    // Lint reads FONT_ALIAS_KEYS2 = keys(FONT_ALIAS_MAP2); the unsuffixed map is a fallback.
    const markers = ["FONT_ALIAS_MAP2 = {", "FONT_ALIAS_MAP = {"];
    let idx = -1;
    let marker = "";
    for (const mk of markers) {
      const i = src.indexOf(mk);
      if (i >= 0) {
        idx = i;
        marker = mk;
        break;
      }
    }
    if (idx < 0) return { keys: null, map: null };
    const open = idx + marker.length - 1;
    const body = extractObjectLiteral(src, open);
    if (!body) return { keys: null, map: null };
    const map = {};
    const re = /(?:"([^"]+)"|'([^']+)'|([A-Za-z_][\w-]*))\s*:\s*"([^"]+)"/g;
    let m;
    while ((m = re.exec(body))) {
      const key = (m[1] ?? m[2] ?? m[3]).toLowerCase();
      map[key] = m[4];
    }
    const keys = new Set(Object.keys(map));
    if (keys.size === 0) return { keys: null, map: null };
    return { keys, map };
  } catch {
    return { keys: null, map: null };
  }
}

export function loadCliAliases(cliPath = DEFAULT_CLI) {
  const key = cliPath || DEFAULT_CLI;
  if (aliasCache.has(key)) return aliasCache.get(key);
  const result = loadCliAliasesUncached(key);
  aliasCache.set(key, result);
  return result;
}

/** Bare families, deduped and sorted case-insensitively so the line does not depend on ramp order. */
export function canonicalImport(families) {
  const byNorm = new Map();
  for (const f of families || []) {
    const disp = String(f).trim().replace(/^['"]|['"]$/g, "").trim();
    const n = normFamily(disp);
    if (n && !byNorm.has(n)) byNorm.set(n, disp);
  }
  const list = [...byNorm.keys()].sort().map((n) => byNorm.get(n));
  if (!list.length) return "";
  const q = list.map((f) => "family=" + f.replace(/\s+/g, "+")).join("&");
  return `@import url('https://fonts.googleapis.com/css2?${q}&display=swap');`;
}

export function renderBlock(importLine, faceRules) {
  const rules = (faceRules || []).map((r) => String(r).trim()).filter(Boolean);
  if (!importLine && !rules.length) return "";
  const lines = [MARK_IMPORT];
  if (importLine) lines.push(importLine);
  for (const r of rules) lines.push(r);
  lines.push(MARK_END);
  return lines.join("\n");
}

export function stripManaged(html) {
  const re =
    /[ \t]*\/\* aiflow:font-imports v1 \*\/[ \t]*\r?\n[\s\S]*?\/\* aiflow:font-imports end \*\/[ \t]*\r?\n?/g;
  return String(html).replace(re, "");
}

function normalizeGoogleFamily(name) {
  const decoded = String(name).replace(/\+/g, " ").trim();
  if (!decoded) return null;
  try {
    return decodeURIComponent(decoded).trim().toLowerCase() || null;
  } catch {
    return decoded.toLowerCase();
  }
}

function familiesFromGoogleUrl(rawUrl) {
  const url = String(rawUrl).replace(/&amp;/gi, "&");
  let parsed;
  try {
    parsed = new URL(url, "https://fonts.googleapis.com");
  } catch {
    return [];
  }
  if (parsed.hostname.toLowerCase() !== "fonts.googleapis.com") return [];
  const families = [];
  for (const value of parsed.searchParams.getAll("family")) {
    for (const familySpec of value.split("|")) {
      const family = normalizeGoogleFamily(familySpec.split(":")[0] || "");
      if (family) families.push(family);
    }
  }
  return families;
}

export function extractGoogleFamilies(html) {
  const families = new Set();
  const addUrl = (url) => {
    for (const family of familiesFromGoogleUrl(url)) families.add(family);
  };
  const linkHrefRe =
    /<link\b[^>]*\bhref\s*=\s*(?:(["'])([^"']*fonts\.googleapis\.com[^"']*)\1|([^\s>]*fonts\.googleapis\.com[^\s>]*))[^>]*>/gi;
  for (const match of String(html).matchAll(linkHrefRe)) {
    const href = match[2] || match[3];
    if (href) addUrl(href);
  }
  const importUrlRe =
    /@import\s+(?:url\(\s*)?(["']?)([^"')\s]*fonts\.googleapis\.com[^"')\s]*)\1\s*\)?/gi;
  for (const chunk of iterStyleChunks(html)) {
    for (const match of chunk.matchAll(importUrlRe)) {
      if (match[2]) addUrl(match[2]);
    }
  }
  return families;
}

export function extractDeclaredFamilies(html) {
  const families = new Set();
  const fontFaceRe = /@font-face\s*\{[^}]*\}/gi;
  const familyRe = /font-family\s*:\s*(['"]?)([^;'"]+)\1/i;
  for (const chunk of iterStyleChunks(html)) {
    const content = stripCssComments(chunk);
    let match;
    while ((match = fontFaceRe.exec(content)) !== null) {
      const familyMatch = match[0].match(familyRe);
      if (familyMatch?.[2]) families.add(familyMatch[2].trim().toLowerCase());
    }
  }
  return families;
}

export function extractUsedFamilies(html) {
  const used = [];
  const seen = new Set();
  const propRe = /font-family\s*:\s*([^;}{]+)/gi;
  for (const chunk of iterStyleChunks(html)) {
    const without = stripCssComments(chunk).replace(/@font-face\s*\{[^}]*\}/gi, "");
    let match;
    while ((match = propRe.exec(without)) !== null) {
      for (const part of match[1].split(",")) {
        const name = normFamily(part);
        if (name && !GENERIC_FAMILIES.has(name) && !seen.has(name)) {
          seen.add(name);
          used.push(name);
        }
      }
    }
  }
  return used;
}

/** Each font-family stack as norm names (null if the token is not a family). Index 0 is primary. */
export function extractFontStacks(html) {
  const stacks = [];
  const propRe = /font-family\s*:\s*([^;}{]+)/gi;
  for (const chunk of iterStyleChunks(html)) {
    const masked = maskNoise(chunk);
    let match;
    while ((match = propRe.exec(masked))) {
      stacks.push(match[1].split(",").map((p) => normFamily(p)));
    }
  }
  return stacks;
}

function uniq(list) {
  const seen = new Set();
  const out = [];
  for (const x of list) {
    if (seen.has(x)) continue;
    seen.add(x);
    out.push(x);
  }
  return out;
}

function pruneHtml(html, dropName) {
  const dropped = [];
  const apply = (css) => {
    const masked = maskNoise(css);
    const re = /font-family\s*:\s*([^;}{]+)/gi;
    let out = "";
    let last = 0;
    let m;
    while ((m = re.exec(masked))) {
      const end = m.index + m[0].length;
      const raw = css.slice(end - m[1].length, end);
      const trail = /\s*$/.exec(raw)[0];
      const imp = /\s*!important\s*$/i.exec(raw.slice(0, raw.length - trail.length));
      const val = imp ? raw.slice(0, raw.length - trail.length - imp[0].length) : raw.slice(0, raw.length - trail.length);
      const parts = val.split(",");
      const kept = [];
      let any = false;
      parts.forEach((p, i) => {
        const n = normFamily(p);
        if (i > 0 && n && !GENERIC_FAMILIES.has(n) && dropName(n)) {
          any = true;
          dropped.push(n);
        } else kept.push(p.trim());
      });
      if (!any) out += css.slice(last, end);
      else {
        out +=
          css.slice(last, m.index) +
          "font-family: " +
          kept.filter(Boolean).join(", ") +
          (imp ? " !important" : "") +
          trail;
      }
      last = end;
    }
    out += css.slice(last);
    return out;
  };
  return { html: mapStyleRegions(html, apply), dropped: uniq(dropped) };
}

export function insertManaged(html, block) {
  if (!block) return html;
  const tpl = /<template\b[^>]*>/i.exec(html);
  const regionStart = tpl ? tpl.index + tpl[0].length : 0;
  let regionEnd = html.length;
  if (tpl) {
    const close = html.toLowerCase().indexOf("</template>", regionStart);
    if (close >= 0) regionEnd = close;
  }
  const region = html.slice(regionStart, regionEnd);
  const styleMatch = /<style\b[^>]*>/i.exec(region);
  if (!styleMatch) {
    let at = regionStart;
    if (html[at] === "\r") at++;
    if (html[at] === "\n") at++;
    const chunk = `<style>\n${block}\n</style>\n`;
    return html.slice(0, at) + chunk + html.slice(at);
  }
  let at = regionStart + styleMatch.index + styleMatch[0].length;
  const rest = html.slice(at, regionEnd);
  const charset = /^\s*@charset\s*(['"]).*?\1\s*;/i.exec(rest);
  if (charset) at += charset[0].length;
  if (html[at] === "\r") at++;
  if (html[at] === "\n") at++;
  return html.slice(0, at) + block + "\n" + html.slice(at);
}

/**
 * Plan one HTML file.
 * aliases.keys === null → do not prune; do not treat CLI aliases as covered.
 * prune:false → do not prune even when aliases loaded.
 */
export function planFile(html, opts = {}) {
  const frameFamilies = opts.frameFamilies || [];
  const brandFaces = opts.brandFaces || new Map();
  const aliases = opts.aliases || { keys: null };
  const allowPrune = opts.prune !== false && !!(aliases && aliases.keys);
  const stripped = stripManaged(html);
  const declared = extractDeclaredFamilies(stripped);
  const google = extractGoogleFamilies(stripped);
  const frameSet = new Set(frameFamilies.map((f) => normFamily(f)).filter(Boolean));
  const aliasHas = (n) => !!(aliases.keys && aliases.keys.has(n));
  const covered = (n) => declared.has(n) || google.has(n) || aliasHas(n);

  const injected = [];
  const faceFams = [];
  const faceRules = [];
  for (const disp of frameFamilies) {
    const n = normFamily(disp);
    if (!n || GENERIC_FAMILIES.has(n) || covered(n)) continue;
    const brand = brandFaces.get(n);
    if (brand && brand.rules.length) {
      faceFams.push(disp);
      for (const rule of brand.rules) faceRules.push(rule);
    } else injected.push(disp);
  }

  let pruned = stripped;
  let dropped = [];
  if (allowPrune) {
    const dropName = (n) => !frameSet.has(n) && !declared.has(n) && !google.has(n) && !aliasHas(n);
    const res = pruneHtml(stripped, dropName);
    pruned = res.html;
    dropped = res.dropped;
  }

  const importLine = canonicalImport(injected);
  const block = renderBlock(importLine, faceRules);
  const next = block ? insertManaged(pruned, block) : pruned;
  const importing = new Set([...injected, ...faceFams].map((f) => normFamily(f)).filter(Boolean));
  const unresolved = [];
  for (const name of extractUsedFamilies(next)) {
    if (covered(name) || importing.has(name)) continue;
    unresolved.push(name);
  }
  return {
    html: next,
    changed: next !== html,
    injected,
    faces: faceFams,
    dropped,
    unresolved,
    importLine,
    block,
  };
}
