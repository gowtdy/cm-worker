// Shared frame-packet builder — the script half of the frame-worker core/delta split.
//
// Each narrative workflow ships a thin `scripts/frame-packets.mjs` wrapper that pins
// its own paths (animation skill, role delta, design-truth resolution, extra packet
// sections) and delegates everything else here, exactly as the markdown half already
// does with `references/frame-worker-core.md` + each workflow's delta. One owner for
// the packet-building logic; the wrappers own only what genuinely differs per workflow.
//
// Packet (<frame_id>.md) = project inputs + the frame's exact `## Frame N` block
// + the blueprint body + every cited rule recipe, inlined — so a worker never opens
// the shared STORYBOARD.md or any skill document. Cited motions are found
// mechanically: the explicit `- rules:` field when present, plus every valid rule id
// (a filename under the animation skill's rules/) mentioned in the block, topped up
// by inferCitedRules (blueprint rule mapping + motion-phrase table). Large
// blueprints are narrowed per frame (narrowBlueprint) before inlining.
//
// _role.md = frame-worker-core.md + the workflow's sub-agents/frame-worker.md,
// concatenated verbatim — the complete worker role, assembled from the two source
// documents so nothing is hand-maintained twice.

import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export function field(block, name) {
  const match = block.match(new RegExp(`^-\\s+${name}:\\s*(.+)$`, "im"));
  return match?.[1]?.trim() ?? null;
}

const BLUEPRINT_MODE_RE = /\(\s*(adapt|reproduce)\s*\)/i;
const BARE_MODE_RE = /^(adapt|reproduce)$/i;
const titleMode = (m) => m[0].toUpperCase() + m.slice(1).toLowerCase();

// A frame block may carry several `- blueprint:` lines (frame-visual appends a
// moded `- blueprint: <id> (Adapt|Reproduce)` below the outline's bare one).
// The moded line wins; among equals the LAST one wins. The id is the leading
// kebab token — `(Adapt)` and any trailing prose never reach the filename.
// A bare `- blueprint: Adapt` names only the mode: id is "" (modeOnly) and the
// real id comes from `## Blueprint assignment` (see withAssignment).
export function selectBlueprint(block) {
  const raws = [...block.matchAll(/^-\s+blueprint:\s*(.+)$/gim)].map((m) => m[1].trim());
  if (raws.length === 0) return null;
  const parsed = raws.map((raw) => {
    let mode = raw.match(BLUEPRINT_MODE_RE)?.[1] ?? null;
    let id = raw.replace(BLUEPRINT_MODE_RE, "").trim().match(/^[\w.-]+/)?.[0] ?? "";
    if (BARE_MODE_RE.test(id)) { mode = mode ?? id; id = ""; }
    return { raw, id, mode: mode ? titleMode(mode) : null };
  });
  const named = parsed.filter((p) => p.id);
  const moded = named.filter((p) => p.mode);
  const modeOnly = parsed.filter((p) => !p.id && p.mode).at(-1);
  let chosen = (moded.length ? moded : named).at(-1);
  if (chosen && !chosen.mode && modeOnly) chosen = { ...chosen, mode: modeOnly.mode };
  if (!chosen) chosen = modeOnly ? { ...modeOnly, modeOnly: true } : parsed.at(-1);
  const ids = [...new Set(named.map((p) => p.id.toLowerCase()))];
  return { ...chosen, all: parsed, conflict: ids.length > 1 };
}

// Fills a mode-only selection (`- blueprint: Adapt`) from the frame's
// `## Blueprint assignment` entry; without one the frame gets no blueprint.
export function withAssignment(sel, assigned, warnLabel) {
  if (!sel?.modeOnly) return sel;
  if (assigned?.id) return { ...sel, id: assigned.id, mode: sel.mode ?? assigned.mode, fromAssignment: true };
  if (warnLabel) {
    console.warn(
      `frame-packets: ${warnLabel}: \`- blueprint: ${sel.raw}\` names only a mode and \`## Blueprint assignment\` has no entry — no blueprint inlined`,
    );
  }
  return sel;
}

// `## Blueprint assignment` (or tolerant bold/heading variant) → Map(frameNumber → { id, mode }).
export function blueprintAssignment(storyboard) {
  const out = new Map();
  const sec = /^[ \t]*(?:#{1,6}[ \t]*|\*\*[ \t]*)?Blueprint\s+assignment(?:[ \t]*\*\*)?[ \t]*[:：.]?[ \t]*$([\s\S]*?)(?=^## |(?![\s\S]))/im.exec(storyboard ?? "");
  if (!sec) return out;
  for (const m of sec[1].matchAll(/^\s*[-*]?\s*F(\d+)\s*[=:]\s*(.+)$/gm)) {
    const sel = selectBlueprint(`- blueprint: ${m[2].trim()}`);
    if (sel) out.set(Number(m[1]), { id: sel.id, mode: sel.mode });
  }
  return out;
}

function frameNumber(heading) {
  const m = /^(\d+)/.exec(String(heading ?? "").trim());
  return m ? Number(m[1]) : null;
}

// ---- packet-time blueprint narrowing ----------------------------------------
//
// Big blueprints fold many roles and sub-shapes into one file (`_Variant — Role_`
// lists, OR-chains of alternative entrances, a long rule mapping). A worker given
// all of it re-enumerates every branch. Narrowing is structural, not per-file:
// sections are the `**label**` paragraphs, variants are bullets labelled
// `_Variant — X_` / `**Variant — X**`, alternatives are ` OR `-joined clauses.

const ROLE_KEYS = {
  hook: "Hook",
  problem: "Problem", pain: "Problem",
  product_intro: "Product_Intro", productintro: "Product_Intro", intro: "Product_Intro",
  key_feature: "Key_Feature", keyfeature: "Key_Feature", feature: "Key_Feature",
  feature_showcase: "Key_Feature", demo: "Key_Feature",
  benefits: "Benefits", benefit: "Benefits", benefit_highlight: "Benefits",
  cta: "CTA", call_to_action: "CTA",
  brand_outro: "Brand_Outro", outro: "Brand_Outro",
  social_proof: "Social_Proof", socialproof: "Social_Proof", proof: "Social_Proof",
};
const roleKey = (raw) => ROLE_KEYS[String(raw ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_")] ?? null;

// Narrative role of a frame: `- type:` / `- role:` field first, then the
// `## Frame N — <Role>` heading words.
export function frameRole(block) {
  for (const name of ["role", "type", "beat_role", "shot_role"]) {
    const r = roleKey((field(block, name) ?? "").split(/[\s(—,;]/)[0]);
    if (r) return r;
  }
  const heading = /^## Frame\s+[^\n]*?[—:-]\s*(.+)$/m.exec(block)?.[1] ?? "";
  for (const word of heading.split(/[^A-Za-z_]+/)) {
    const r = roleKey(word);
    if (r) return r;
  }
  return null;
}

const RELEVANCE_STOP = new Set(("a an the and or of to in on at by for with from into onto over under then than "
  + "that this these those its it is are be as via per each every one two three any all some only just more most "
  + "less very also plus their our your no not never without while when where who which what out up down off").split(" "));
const stemWord = (w) => {
  let s = w.toLowerCase();
  if (s.length > 3) s = s.replace(/(?:ing|ed|es|s)$/, "");
  if (s.length > 3) s = s.replace(/e$/, "");
  return s;
};
// Stems too common in blueprint/storyboard prose to signal a specific motion.
const GENERIC_STEMS = new Set(["word", "text", "typ", "lin", "fram", "beat", "scen", "shot", "phras",
  "element", "final", "small", "larg", "big", "full", "accent", "color", "colour", "bg", "background",
  "center", "centr", "left", "right", "top", "bottom", "card", "item", "stat", "mov", "motion", "effect",
  "brief", "short", "long", "slow", "fast", "quick", "singl", "distinct", "behind", "besid", "below",
  "abov", "hold", "settl", "entranc", "exit", "reveal", "field", "label", "headlin", "optional", "key",
  "new", "next", "prior", "first", "last", "whol", "rul", "pattern", "look", "form", "plain", "fill"]);

function relevanceTerms(phrase) {
  const clean = String(phrase ?? "").toLowerCase().replace(/`[^`]*`|\[[^\]]*\]/g, " ");
  const terms = new Map();
  for (const tok of clean.match(/[a-z0-9]+(?:-[a-z0-9]+)+|[a-z0-9]+/g) ?? []) {
    if (tok.includes("-")) {
      const parts = tok.split("-").filter((p) => !RELEVANCE_STOP.has(p) || p === "on" || p === "in").map(stemWord);
      if (parts.length > 1) terms.set(parts.join(" "), 2);
      else if (parts.length === 1 && !GENERIC_STEMS.has(parts[0])) terms.set(parts[0], 1);
    } else if (tok.length >= 3 && !RELEVANCE_STOP.has(tok)) {
      const s = stemWord(tok);
      if (!GENERIC_STEMS.has(s)) terms.set(s, 1);
    }
  }
  return terms;
}

// Negated spans ("no roll", "never orbiting", "its push-in is dropped") must
// not count as asked-for motion.
export function relevanceIndex(text) {
  const kept = String(text ?? "").toLowerCase()
    .replace(/\b(?:no|never|without|not|zero)\s+(?:[\w-]+\s+)?[\w-]+/g, " ")
    .replace(/(?:[\w'/-]+\s+){0,2}[\w'/-]+\s+(?:is|are|gets?)\s+(?:dropped|removed|replaced|cut|skipped)\b/g, " ");
  const stems = (kept.match(/[a-z0-9]+/g) ?? []).map(stemWord);
  return { joined: ` ${stems.join(" ")} `, set: new Set(stems) };
}

export function relevanceMatch(phrase, index) {
  let score = 0;
  let total = 0;
  let compound = false;
  for (const [term, weight] of relevanceTerms(phrase)) {
    total += weight;
    if (weight === 2 ? index.joined.includes(` ${term} `) : index.set.has(term)) {
      score += weight;
      if (weight === 2) compound = true;
    }
  }
  // A hyphenated motion term ("type-on", "hard-cut") is specific enough on its
  // own; loose single words must cover at least half of the phrase.
  return { score, strong: score >= 2 && (compound || score / total >= 0.5) };
}

export const relevanceScore = (phrase, index) => relevanceMatch(phrase, index).score;

// Split a blueprint into its `**label**` paragraphs; a bare `---` starts an appendix.
function blueprintSections(md) {
  const sections = [{ kind: "head", label: "", lines: [] }];
  for (const line of String(md).split("\n")) {
    const label = /^\*\*([^*\n]+)\*\*/.exec(line)?.[1];
    if (label != null) {
      const l = label.toLowerCase();
      const kind = l.startsWith("roles served") ? "roles"
        : l.startsWith("shot structure") ? "shot"
        : l.startsWith("motion vocabulary") ? "vocab"
        : l.startsWith("rule mapping") ? "mapping" : "keep";
      sections.push({ kind, label, lines: [line] });
    } else if (/^---\s*$/.test(line)) {
      sections.push({ kind: "appendix", label: "---", lines: [line] });
    } else {
      sections.at(-1).lines.push(line);
    }
  }
  return sections;
}

// Bullet items with their continuation lines; non-bullet lines are text items.
function bulletItems(lines) {
  const items = [];
  for (const line of lines) {
    const bullet = /^(\s*)[-*]\s+/.exec(line);
    const indent = (/^\s*/.exec(line)?.[0] ?? "").length;
    const last = items.at(-1);
    if (bullet) items.push({ indent, bullet: true, text: line });
    else if (line.trim() && last?.bullet && indent > last.indent) last.text += `\n${line}`;
    else items.push({ indent, bullet: false, text: line });
  }
  return items;
}

const VARIANT_RE = /^\s*[-*]\s+(?:_|\*\*)Variant\s+[—–-]\s+([^*:\n]+?)\s*(?:_(?=[\s:(]|$)|\*\*)/;
const variantRole = (text) => roleKey((VARIANT_RE.exec(text)?.[1] ?? "").split(/[\s(]/)[0]);

// Keep the clause's lead + its most relevant alternative from each ` OR `-chain.
function capAlternatives(text, index) {
  return text.split(/(?<=[.;])\s+(?=\S)/).map((sentence) => {
    const segs = sentence.split(/,?\s+OR\s+/);
    if (segs.length <= 2) return sentence;
    let best = 1;
    for (let i = 2; i < segs.length; i++) {
      if (relevanceScore(segs[i], index) > relevanceScore(segs[best], index)) best = i;
    }
    const tail = /[.;]$/.test(sentence) && !/[.;]$/.test(segs[best]) ? sentence.slice(-1) : "";
    return `${segs[0]} OR ${segs[best]}${tail} _(+${segs.length - 2} alternatives omitted)_`;
  }).join(" ");
}

// Variant label qualifier words: `_Variant — Hook (flash)_` → {flash};
// `**Variant — Key_Feature grid**` → {grid}.
function variantQualifiers(text) {
  const label = VARIANT_RE.exec(text)?.[1] ?? "";
  const rest = label.replace(/^[A-Za-z_]+/, "");
  return [...relevanceTerms(rest).keys()].flatMap((t) => t.split(" "));
}

// The `roles served` bullet (for this role) the block reads most like; its
// words — including the `(from \`slug\`)` source id — name the preferred variant.
function preferredRoleTerms(rolesLines, role, index) {
  if (!rolesLines) return new Set();
  const bullets = bulletItems(rolesLines).filter((it) => it.bullet)
    .filter((it) => !role || roleKey(/^\s*[-*]\s+([A-Za-z_]+)/.exec(it.text)?.[1]) === role)
    .map((it) => ({ it, s: relevanceScore(it.text, index) }))
    .sort((a, b) => b.s - a.s);
  if (!bullets.length || bullets[0].s === 0 || bullets[0].s === bullets[1]?.s) return new Set();
  const words = bullets[0].it.text.toLowerCase().replace(/`/g, " ").match(/[a-z0-9]+/g) ?? [];
  return new Set(words.map(stemWord));
}

function narrowShot(lines, { role, index, preferred = new Set() }) {
  const items = bulletItems(lines);
  const out = [];
  for (let i = 0; i < items.length; i++) {
    if (!VARIANT_RE.test(items[i].text)) {
      out.push(items[i].bullet ? capAlternatives(items[i].text, index) : items[i].text);
      continue;
    }
    const group = [];
    let j = i;
    while (j < items.length) {
      if (VARIANT_RE.test(items[j].text)) { group.push({ ...items[j], pos: group.length }); j++; continue; }
      let k = j;
      while (k < items.length && !items[k].bullet && !items[k].text.trim()) k++;
      if (k > j && k < items.length && VARIANT_RE.test(items[k].text)) { j = k; continue; }
      break;
    }
    i = j - 1;
    const matched = role ? group.filter((g) => variantRole(g.text) === role) : [];
    const pool = matched.length ? matched : group;
    const keep = new Set(pool
      .map((g) => ({
        g,
        s: relevanceScore(g.text, index) + (variantQualifiers(g.text).some((q) => preferred.has(q)) ? 4 : 0),
      }))
      .sort((a, b) => b.s - a.s || a.g.pos - b.g.pos)
      .slice(0, 2)
      .map((x) => x.g.pos));
    for (const g of group) if (keep.has(g.pos)) out.push(capAlternatives(g.text, index));
    const dropped = group.length - keep.size;
    if (dropped > 0) {
      out.push(`${" ".repeat(group[0].indent)}- _(${dropped} more variant${dropped > 1 ? "s" : ""} omitted at packet time${role && matched.length ? ` — kept ${role} options` : ""}; pick one option and build, do not enumerate.)_`);
    }
  }
  return out;
}

function narrowRoles(lines, role) {
  const items = bulletItems(lines);
  const bullets = items.filter((it) => it.bullet);
  const matched = role ? bullets.filter((it) => roleKey(/^\s*[-*]\s+([A-Za-z_]+)/.exec(it.text)?.[1]) === role) : [];
  if (matched.length) {
    const dropped = bullets.length - matched.length;
    return [lines[0], "", ...matched.map((it) => it.text),
      ...(dropped ? [`- _(${dropped} other role${dropped > 1 ? "s" : ""} omitted.)_`] : []), ""];
  }
  return items.map((it) => (it.bullet ? cutBytes(it.text.replace(/\s+/g, " "), 160) : it.text));
}

// Mapping bullets the block asks for first, then those backing the kept shot
// options; parentheticals on the rule side are dropped.
function narrowMapping(lines, blockIndex, shotIndex, budget = 2000) {
  const items = bulletItems(lines.slice(1));
  const bullets = items.filter((it) => it.bullet).map((it, pos) => {
    const flat = it.text.replace(/\s+/g, " ").trim();
    const arrow = flat.indexOf("→");
    const left = arrow < 0 ? flat : flat.slice(0, arrow);
    let right = arrow < 0 ? "" : flat.slice(arrow + 1);
    for (let k = 0; k < 3; k++) right = right.replace(/\s*\([^()]*\)/g, "");
    const compact = arrow < 0 ? flat : `${left.trim()} → ${right.trim()}`;
    const b = relevanceMatch(left, blockIndex);
    const tier = b.strong ? 2 : relevanceMatch(left, shotIndex).strong ? 1 : 0;
    return { pos, compact, tier, score: b.score };
  });
  const ranked = [...bullets].sort((a, b) => b.tier - a.tier || b.score - a.score || a.pos - b.pos);
  const keep = new Set();
  let used = 0;
  for (const b of ranked) {
    if (!b.tier && !(keep.size < 3 && b.score >= 1)) break;
    const limit = b.tier === 2 ? budget : budget / 2;
    if (used + Buffer.byteLength(b.compact) > limit) continue;
    keep.add(b.pos);
    used += Buffer.byteLength(b.compact) + 1;
  }
  const kept = bullets.filter((b) => keep.has(b.pos)).map((b) => b.compact);
  const dropped = bullets.length - kept.length;
  return [lines[0], "", ...kept,
    ...(dropped ? [`- _(${dropped} mapping${dropped > 1 ? "s" : ""} for motions this frame does not ask for omitted.)_`] : []), ""];
}

export const NARROWED_BLUEPRINT_NOTE = "> Packet-time narrowed blueprint — do not re-expand OR branches; pick one option and build.";

// Deterministic, structure-based trim of a blueprint body for one frame. Small
// blueprints (≤ minBytes, lowered from 8000 → 3500 so medium blueprints like
// panel-edit-live-sync / card-morph-anchor also shed irrelevant role variants)
// and bodies without the standard sections pass through.
export function narrowBlueprint(md, { block = "", role = frameRole(block), minBytes = 3500 } = {}) {
  const text = String(md ?? "").trim();
  if (Buffer.byteLength(text) <= minBytes) return { text, narrowed: false };
  const sections = blueprintSections(text);
  if (!sections.some((s) => s.kind === "shot" || s.kind === "mapping")) return { text, narrowed: false };
  const blockIndex = relevanceIndex(block);
  const shot = sections.find((s) => s.kind === "shot");
  const preferred = preferredRoleTerms(sections.find((s) => s.kind === "roles")?.lines, role, blockIndex);
  const shotLines = shot ? narrowShot(shot.lines, { role, index: blockIndex, preferred }) : [];
  const shotIndex = relevanceIndex(shotLines.join("\n"));
  const out = [];
  for (const s of sections) {
    if (s.kind === "appendix") continue;
    if (s.kind === "shot") out.push(...shotLines);
    else if (s.kind === "roles") out.push(...narrowRoles(s.lines, role));
    else if (s.kind === "vocab") out.push(cutBytes(s.lines.join("\n").trim(), 700), "");
    else if (s.kind === "mapping") out.push(...narrowMapping(s.lines, blockIndex, shotIndex));
    else out.push(...s.lines);
  }
  const body = out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return { text: `${NARROWED_BLUEPRINT_NOTE}\n\n${body}`, narrowed: true, role };
}

// ---- rule inference (when the block cites few/no rules) ----------------------

function blueprintRuleMapping(blueprintText) {
  const sec = blueprintSections(blueprintText ?? "").find((s) => s.kind === "mapping");
  if (!sec) return [];
  return bulletItems(sec.lines.slice(1)).filter((it) => it.bullet).map((it) => {
    const flat = it.text.replace(/\s+/g, " ");
    const arrow = flat.indexOf("→");
    if (arrow < 0) return null;
    let right = flat.slice(arrow + 1);
    const nextArrow = right.indexOf("→");
    if (nextArrow >= 0) right = right.slice(0, nextArrow).replace(/;[^;]*$/, "");
    for (let k = 0; k < 3; k++) right = right.replace(/\s*\([^()]*\)/g, "");
    const primary = right.split(/\s+(?:OR|or)\s+/)[0];
    return { left: flat.slice(0, arrow), ids: [...primary.matchAll(/`([\w.-]+)`/g)].map((m) => m[1]) };
  }).filter(Boolean);
}

// Common storyboard motion phrases → rule ids (only ids present under rules/ are used).
export const PHRASE_RULES = [
  { re: /\btyp(?:es?|ed|ing)[- ]on\b|\btypewriter\b|\bcharacter[- ]by[- ]character\b|\bchar[- ]by[- ]char\b|\bbackspac\w*|\bretyp\w*/gi, ids: ["discrete-text-sequence"] },
  { re: /\b(?:blinking\s+)?caret\b|\bblinking cursor\b/gi, ids: ["discrete-text-sequence", "context-sensitive-cursor"] },
  { re: /\b(?:hard[- ]cut|instant)\s+(?:token\s+|word\s+)?swap\w*|\btoken swap\w*|\bhard[- ]cuts? in place\b|\bswaps? in place\b/gi, ids: ["discrete-text-sequence"] },
  { re: /\bcount(?:s|ing)?[- ]up\b|\bcounting\b|\btally\b|\bsnap-counts?\b|\bticks? up\b|\bodometer\b/gi, ids: ["counting-dynamic-scale"] },
  { re: /\bpop[- ]stagger\w*|\bspring[- ]pop\w*|\bspring[- ]overshoot\w*|\bpops? in\b|\bscale-pop\w*/gi, ids: ["spring-pop-entrance"] },
  { re: /\bslam\w*|\bkinetic beats?\b/gi, ids: ["kinetic-beat-slam"] },
  { re: /\bwaterfall\b|\bcascad\w+/gi, ids: ["waterfall-entry"] },
  { re: /\bmask[- ]?wip\w*|\bmasked slide\b|\bclip-path reveal\b|\bmarquee\b|\bbottom ticker\b|\bticker drifts?\b/gi, ids: ["gsap-effects"] },
  { re: /\bslot[- ]machine\b|\bvertical ticker\b|\brolls? through\b/gi, ids: ["vertical-spring-ticker"] },
  { re: /\bdraws?[- ](?:on|in)\b|\bdraw-on\b|\btrim[- ]path\b|\bline draws\b|\bconnector lines? draw\w*/gi, ids: ["svg-path-draw"] },
  { re: /\b(?:earnings|growth|progress)\s+bar\b|\bbars? (?:grow|draw)s?\b|\bprogress fill\b/gi, ids: ["stat-bars-and-fills"] },
  { re: /\bripple\w*|\bcursor (?:click|lands)\w*/gi, ids: ["cursor-click-ripple"] },
  { re: /\bcompress together\b|\blockstep press\b/gi, ids: ["physics-press-reaction"] },
  { re: /\bglow (?:bloom|sweep|travels)\w*|\btraveling glow\b|\bglow settle\b/gi, ids: ["ambient-glow-bloom"] },
  { re: /\bsame[- ]center morph\b|\bcondenses? at the same\b|\bscale[- ]swap\b/gi, ids: ["scale-swap-transition"] },
  { re: /\bconfetti\b|\bparticle burst\b|\bdot burst\b/gi, ids: ["particle-burst"] },
  { re: /\bchromatic\b|\brgb[- ]split\b|\bglitch\w*/gi, ids: ["chromatic-glitch"] },
  { re: /\bgradient sweep\w*|\bhue[- ]sweep\w*/gi, ids: ["gradient-text-sweep"] },
  { re: /\bmotion[- ]blur\b|\bstreak\w*/gi, ids: ["motion-blur-streak"] },
  { re: /\bunderline draws?\b|\bstrike[- ]through\b|\bhighlight sweep\b/gi, ids: ["css-marker-patterns"] },
];

// Rule ids implied by the frame block: first the selected blueprint's
// `**rule mapping**` bullets whose motion phrase the block asks for, then the
// PHRASE_RULES table. Excludes ids in `exclude`; at most `max` results.
export function inferCitedRules(block, ruleIds, { blueprintText = "", exclude = [], max = 5 } = {}) {
  const known = new Set(ruleIds);
  const out = [];
  const add = (id) => {
    if (out.length < max && known.has(id) && !exclude.includes(id) && !out.includes(id)) out.push(id);
  };
  const index = relevanceIndex(block);
  blueprintRuleMapping(blueprintText)
    .map((m, pos) => ({ ...m, pos, ...relevanceMatch(m.left, index) }))
    .filter((m) => m.strong)
    .sort((a, b) => b.score - a.score || a.pos - b.pos)
    .forEach((m) => m.ids.forEach(add));
  for (const { re, ids } of PHRASE_RULES) {
    if (positiveMentions(block, re).length) ids.forEach(add);
  }
  return out;
}

const MAX_RULES = 3;

function maxRulesCap() {
  const raw = String(process.env.HTMLVIDEO_FRAME_PACKETS_MAX_RULES ?? "").trim();
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : MAX_RULES;
}

// citedRules + (HTMLVIDEO_FRAME_PACKETS_INFER_RULES, default ON) inferred ids
// topping the list up to MAX_RULES (capped so excess recipes do not bloat dispatch).
export function frameRules(block, ruleIds, { blueprintText = "", infer = envSwitch("HTMLVIDEO_FRAME_PACKETS_INFER_RULES", true) } = {}) {
  const cap = maxRulesCap();
  const cited = citedRules(block, ruleIds).slice(0, cap);
  const inferred = infer && cited.length < cap
    ? inferCitedRules(block, ruleIds, { blueprintText, exclude: cited, max: cap - cited.length })
    : [];
  return { rules: [...cited, ...inferred], cited, inferred };
}

function blueprintSectionFor(block, animationDir, heading, warnLabel, assigned = null, narrow = envSwitch("HTMLVIDEO_FRAME_PACKETS_NARROW_BLUEPRINT", true)) {
  const sel = withAssignment(selectBlueprint(block), assigned, warnLabel);
  if (!sel || !sel.id || sel.id.toLowerCase() === "compose") return { sel, section: "" };
  if (sel.conflict && warnLabel) {
    console.warn(
      `frame-packets: ${warnLabel}: multiple blueprint fields (${sel.all.map((p) => p.raw).join(" | ")}) — using "${sel.id}"${sel.mode ? ` (${sel.mode})` : ""}`,
    );
  }
  const path = join(animationDir, "blueprints", `${sel.id}.md`);
  if (!existsSync(path)) {
    if (warnLabel) console.warn(`frame-packets: ${warnLabel}: blueprint file missing: ${path}`);
    return { sel, section: "" };
  }
  const title = `${heading}: ${sel.id}${sel.mode ? ` (mode: ${sel.mode})` : ""}`;
  const blueprintText = readFileSync(path, "utf8");
  const body = narrow ? narrowBlueprint(blueprintText, { block }).text : blueprintText.trim();
  let section = `\n## ${title}\n\n${body}\n`;
  if (sel.conflict) {
    section = section.replace(
      `## ${title}\n`,
      `## ${title}\n\n> Authoritative blueprint for this frame. Other \`- blueprint:\` lines in the block (${sel.all.filter((p) => p.id && p.raw !== sel.raw).map((p) => `\`${p.raw}\``).join(", ")}) are superseded.\n`,
    );
  }
  return { sel, section, blueprintText };
}

const SCENE_WINDOW_RE = /Scene\s+\d+\s*\(\s*~?(\d+(?:\.\d+)?)\s*s?\s*[–—-]\s*~?(\d+(?:\.\d+)?)\s*s?\s*\)/gi;

export function parseDurationSeconds(raw) {
  const m = String(raw ?? "").trim().match(/^(\d+(?:\.\d+)?)\s*s?$/i);
  return m ? Number(m[1]) : null;
}

// Last `Scene N (a–b s)` end vs the block's `- duration:`. Returns null when
// either side is absent.
export function sceneTimelineDrift(block) {
  const dur = parseDurationSeconds(field(block, "duration") ?? field(block, "durationSeconds"));
  const ends = [...block.matchAll(SCENE_WINDOW_RE)].map((m) => Number(m[2]));
  if (dur == null || ends.length === 0) return null;
  const lastEnd = Math.max(...ends);
  return { duration: dur, lastEnd, drift: lastEnd - dur };
}

// >0.1s drift → warning; HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT=1 → throw.
export function checkSceneTimeline(block, label, tolerance = 0.1) {
  const d = sceneTimelineDrift(block);
  if (!d || Math.abs(d.drift) <= tolerance) return d;
  const msg = `${label}: last Scene ends at ${d.lastEnd}s but duration is ${d.duration}s (drift ${d.drift.toFixed(3)}s) — run audio sync-durations with scene scaling`;
  const strict = ["1", "true", "on", "yes"].includes(
    String(process.env.HTMLVIDEO_FRAME_PACKETS_SCENE_STRICT ?? "").trim().toLowerCase());
  if (strict) throw new Error(msg);
  console.warn(`frame-packets: ${msg}`);
  return d;
}

export function splitFrames(storyboard) {
  const matches = [...storyboard.matchAll(/^## Frame\s+([^\n]+)$/gm)];
  return matches.map((match, index) => {
    const start = match.index;
    const end = matches[index + 1]?.index ?? storyboard.length;
    return {
      heading: match[1].trim(),
      block: storyboard.slice(start, end).trim(),
    };
  });
}

export function frameId(frame) {
  const src = field(frame.block, "src");
  if (!src) throw new Error(`${frame.heading}: missing src`);
  return basename(src).replace(/\.html?$/i, "");
}

export function selectedFile(path, heading) {
  if (!path || !existsSync(path)) return "";
  return `\n## ${heading}\n\n${readFileSync(path, "utf8").trim()}\n`;
}

function escapeRegExp(id) {
  return id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function knownRuleIds(animationDir) {
  const rulesDir = join(animationDir, "rules");
  if (!existsSync(rulesDir)) {
    console.warn(
      `frame-packets: no rules dir at ${rulesDir} — packets will inline no motion recipes`,
    );
    return [];
  }
  return readdirSync(rulesDir)
    .filter((name) => name.endsWith(".md"))
    .map((name) => name.replace(/\.md$/, ""));
}

export function citedRules(block, ruleIds) {
  const explicit = (field(block, "rules") ?? "")
    .split(/[,\s]+/)
    .map((rule) => rule.trim())
    .filter(Boolean);
  const mentioned = ruleIds.filter((id) =>
    new RegExp(`(?<![\\w-])${escapeRegExp(id)}(?![\\w-])`, "i").test(block),
  );
  return [...new Set([...explicit, ...mentioned])].filter((id) => ruleIds.includes(id));
}

export function resourceSections(block, { animationDir, ruleIds, assigned = null }) {
  const { section, blueprintText = "" } = blueprintSectionFor(block, animationDir, "Selected blueprint", null, assigned);
  let sections = section;
  for (const rule of frameRules(block, ruleIds, { blueprintText }).rules) {
    sections += selectedFile(
      join(animationDir, "rules", `${rule}.md`),
      `Selected motion rule: ${rule}`,
    );
  }
  return sections;
}

export function buildRolePayload({ corePath, deltaPath, outDir }) {
  const core = readFileSync(corePath, "utf8").trim();
  const delta = readFileSync(deltaPath, "utf8").trim();
  const role = `${core}\n\n---\n\n${delta}\n`;
  mkdirSync(outDir, { recursive: true });
  const path = join(outDir, "_role.md");
  writeFileSync(path, role);
  return { path, bytes: Buffer.byteLength(role) };
}

const FRAME_MD_EXCERPT_HEADINGS = [
  "## Colors", "## Typography", "## Depth & Surface", "## Shapes",
  "## Components", "## Composition Rules",
  "## Approved Real Entities", "## Approved Entities",
  "## Numerals & Claims (hard rule)",
];

function frameMdFrontmatter(frameMd) {
  const m = /^(?:﻿)?---\r?\n([\s\S]*?)\r?\n---/m.exec(frameMd);
  return m ? m[1] : "";
}

// Extract a top-level frontmatter block (`colors:` / `typography:`) verbatim —
// the key line through its indented entries, up to the next top-level key.
// These carry the exact hex/font VALUES that the `## Colors` / `## Typography`
// narrative sections reference only symbolically ({colors.cream}). The worker is
// told never to open frame.md, so the dispatch must inline these values.
function frontmatterBlock(fm, key) {
  if (!fm) return "";
  const lines = fm.split("\n");
  let i = lines.findIndex((l) => l.startsWith(`${key}:`));
  if (i < 0) return "";
  const out = [lines[i]];
  for (i += 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() !== "" && !/^[ \t]/.test(line) && /^[A-Za-z][\w .-]*:/.test(line)) break;
    out.push(line);
  }
  return out.join("\n").trim();
}

export function frameMdExcerpt(frameMd, maxFallbackBytes = 12000) {
  if (!frameMd) return "";
  const fm = frameMdFrontmatter(frameMd);
  const tokenBlock = ["colors", "typography"]
    .map((k) => frontmatterBlock(fm, k))
    .filter(Boolean)
    .join("\n\n");
  const headingRe = (h) => new RegExp(`^${escapeRegExp(h)}\\s*$`, "m");
  const bounds = FRAME_MD_EXCERPT_HEADINGS.map((h) => {
    const m = headingRe(h).exec(frameMd);
    return m ? { h, start: m.index } : null;
  }).filter(Boolean);
  let out = tokenBlock
    ? `## frame.md tokens (resolved values)\n\n${tokenBlock}\n`
    : "";
  if (bounds.length === 0) {
    if (!tokenBlock) {
      console.warn("frame-packets: no whitelisted frame.md sections found; falling back to head excerpt");
      return `> (head-excerpt fallback: whitelisted sections not found)\n\n${frameMd.slice(0, maxFallbackBytes)}`;
    }
    return out.trim();
  }
  const nextHeading = /^##\s/m;
  for (const { h, start } of bounds) {
    const rest = frameMd.slice(start + h.length);
    const endM = rest.search(nextHeading);
    let body = (endM === -1 ? rest : rest.slice(0, endM)).trim();
    if (h === "## Approved Entities" || h === "## Approved Real Entities") {
      // Capsule preset's deck rule tells authors to render off-topic English
      // atmosphere words ("VISION", "FUTURE", "NEXT") inside floating pills
      // (6b07fd3a F7). Autonomous videos use textless geometric pills instead.
      body = body.replace(
        /Floating-pill words are neutral atmospheres[^.\n]*\.?/gi,
        "Background floating pills are textless solid-color geometric shapes only — NEVER render off-topic atmosphere words (\"VISION\", \"FUTURE\", \"NEXT\", \"FLOW\", \"PACE\", \"SIGNAL\") inside decorative pills.",
      );
    }
    if (h === "## Numerals & Claims (hard rule)") {
      // Deck presets tell slide authors to emit literal `— figure —` / `{metric}`
      // placeholders; autonomous videos render directly to MP4 and must NEVER
      // put literal placeholders on screen (6b07a9c1 F4).
      body = body
        .replace(/Render slots as[^\n]*(?:\n[^\n]*placeholders[^\n]*)?/gi, "")
        .replace(/[^\n]*carry placeholders until the script supplies values\.?/gi, "")
        .trim();
      const override =
        "Autonomous-video override: NEVER render literal placeholders (`— figure —`, " +
        "`{metric}`, `N%`, `TBD`) on screen. When the script supplies no concrete number, " +
        "use short qualitative labels (e.g. `Low` vs `Intrinsic`, `Cheaper` vs `Worth more`) " +
        "or visual bar/scale proportions instead.";
      body = body ? `${body}\n\n${override}` : override;
    }
    out += `\n${h}\n\n${body}\n`;
  }
  return out.trim();
}

function envSwitch(name, defaultOn) {
  const raw = String(process.env[name] ?? "").trim().toLowerCase();
  if (!raw) return defaultOn;
  return !["0", "false", "off", "no"].includes(raw);
}

// HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES: snippet (default ON) | full | off.
// Set off/0/false/no to disable. Unknown values fall back to snippet.
export function inlineRecipesMode() {
  const raw = String(process.env.HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES ?? "").trim().toLowerCase();
  if (!raw) return "snippet";
  if (raw === "snippet" || raw === "full") return raw;
  if (["off", "0", "false", "no"].includes(raw)) return "off";
  console.warn(
    `frame-packets: unknown HTMLVIDEO_FRAME_PACKETS_INLINE_RECIPES=${raw} — using snippet`);
  return "snippet";
}

function cutBytes(text, maxBytes) {
  if (Buffer.byteLength(text) <= maxBytes) return text;
  let out = "";
  for (const word of text.split(/(\s+)/)) {
    if (Buffer.byteLength(out + word) > maxBytes - 3) break;
    out += word;
  }
  return `${out.trimEnd()}…`;
}

function mdSection(body, name) {
  const m = new RegExp(`^##\\s+${escapeRegExp(name)}\\s*$`, "im").exec(body);
  if (!m) return "";
  const rest = body.slice(m.index + m[0].length);
  const end = rest.search(/^##\s/m);
  return (end === -1 ? rest : rest.slice(0, end)).trim();
}

// Mechanism paragraph + the recipe's core JS block, bounded to ~maxBytes. The
// code block is cut on line boundaries and always closed.
export function recipeSnippet(md, maxBytes = 2048) {
  const body = String(md ?? "").replace(/^(?:﻿)?---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trim();
  let how = mdSection(body, "How It Works");
  if (!how) {
    const afterH1 = body.replace(/^#\s[^\n]*\n/, "").trim();
    how = afterH1.split(/\n\s*\n/)[0] ?? "";
    if (how.startsWith("```") || how.startsWith("#")) how = "";
  }
  how = how.split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
  const recipe = mdSection(body, "Recipe") || body;
  const code = /```(?:js|javascript)\s*\n([\s\S]*?)```/.exec(recipe)
    ?? /```(?:js|javascript)\s*\n([\s\S]*?)```/.exec(body)
    ?? /```\w*\s*\n([\s\S]*?)```/.exec(recipe);
  const mech = how ? `Mechanism: ${cutBytes(how, code ? Math.floor(maxBytes * 0.4) : maxBytes)}` : "";
  if (!code) return mech;
  const head = mech ? `${mech}\n\n` : "";
  const fenceOpen = "```js\n";
  const fenceClose = "\n```";
  const tail = "\n// … truncated — Read the full recipe for the rest";
  const budget = maxBytes - Buffer.byteLength(head + fenceOpen + fenceClose);
  const lines = code[1].trimEnd().split("\n");
  let kept = "";
  let truncated = false;
  for (const line of lines) {
    const next = kept ? `${kept}\n${line}` : line;
    if (Buffer.byteLength(next) > budget - Buffer.byteLength(tail)) { truncated = true; break; }
    kept = next;
  }
  return `${head}${fenceOpen}${kept}${truncated ? tail : ""}${fenceClose}`;
}

function recipeSections(rules, animationDir, mode) {
  if (mode === "off" || rules.length === 0) return "";
  const parts = rules.map((r) => {
    const path = join(animationDir, "rules", `${r}.md`);
    if (!existsSync(path)) return "";
    const text = readFileSync(path, "utf8");
    const body = mode === "full" ? text.trim() : recipeSnippet(text);
    return body ? `### ${r}\n\n${body}\n` : "";
  }).filter(Boolean);
  if (parts.length === 0) return "";
  const lead = mode === "full"
    ? "> Every cited recipe is inlined in full below — do NOT Read `RULES_DIR/<id>.md`. This overrides the card's first-turn recipe batch read."
    : "> Each cited recipe's mechanism + core code is summarized below. Do NOT batch-read the recipes; Read a full `RULES_DIR/<id>.md` only when a detail you need is missing (at most once per recipe). This overrides the card's first-turn recipe batch read.";
  return `\n## Rule recipes (${mode === "full" ? "full" : "snippets"})\n\n${lead}\n\n${parts.join("\n")}`;
}

// ---- deterministic conflict checks (P1-2) ----------------------------------

const NEGATION_RE = /\b(?:no|never|without|not|avoid|avoiding|forbid\w*|instead of|don't|isn't|zero)\b/i;
const MOTION_BANS = [
  {
    key: "3d",
    // Do not treat surface/material bans ("no 3D extrude", "no 3D bevel/mesh/render")
    // as banning CSS 3D card flips (`rotateY` / `perspective`).
    banned: /\b3d\b(?!\s+(?:extrud\w*|bevel\w*|render\w*|mesh\w*|model\w*|shad\w*|light\w*))|\brotate[xyz]?\b|\bperspective\b|\btilt/i,
    used: /\b3d\b|\brotat(?:e|ion)[XY]\b|\bperspective\b|\btilt(?:s|ed|ing)?\b|\bflip(?:s|ped|ping)?\b/gi,
    // Blueprint bodies enumerate variants ("status pill flips", "tilted grid"):
    // only unambiguous 3D vocabulary counts there.
    usedInBlueprint: /\b3d\b|\brotat(?:e|ion)[XY]\b|\bperspective\b/gi,
    ruling: "drop every 3D intent (rotateX/rotateY/perspective/tilt/flip) and use a flat 2D equivalent (x/y/scale/opacity/clip-path); cited 3D recipes keep only their timing",
  },
  {
    key: "orbit",
    banned: /\borbit/i,
    used: /\borbit(?:s|ing|al|ed)?\b/gi,
    ruling: "no orbital motion — nodes arrive on short straight paths (x/y) into fixed slots",
  },
  {
    key: "spin",
    banned: /\bspin|\btumble|\bscrew/i,
    used: /\bspin(?:s|ning)?\b|\btumbl\w*|\bscrew\w*/gi,
    ruling: "no spin/tumble — use a scale-pop or short rise for emphasis",
  },
];

// "No rotation or skew on type" style bans are scoped to text, not to cards.
const TYPE_ROTATION_BAN_RE = /\b(?:no|never|avoid|without)\b[^.\n;]{0,40}?\b(?:rotat\w*|3d|tilt\w*|skew\w*|perspective)\b[^.\n;]{0,40}?\b(?:on|of|for|to)\s+(?:the\s+|any\s+)?(?:type|text|typography|letters?|glyphs?|words?|headlines?|copy)\b/i;
const CARD_3D_RE = /\b3d\b|\brotat(?:e|ion)Y\b|\bperspective\b|\btilt(?:s|ed|ing)?\b/gi;

// "No idle looping" / "no perpetual looping" vs a ticker or continuous drift.
const IDLE_LOOP_BAN_RE = /\b(?:no|never|without|avoid)\b[^.\n;]{0,40}?\b(?:idle[- ]loop\w*|perpetual(?:ly)?[- ]loop\w*|infinite[- ]loop\w*|endless[- ]loop\w*|loop(?:s|ing)?)\b/i;
const LOOP_ASK_RE = /\bticker\b|\bmarquee\b|\blooping\s+drift\w*|\bcontinuous(?:ly)?\s+(?:scroll|drift|loop)\w*|\bdrifts?\s+(?:continuously|forever|endlessly)\b|\bever-present\b|\bperpetual(?:ly)?\s+(?:drift|scroll|motion)\w*|\binfinite\s+(?:scroll|drift)\w*|\b(?:css|gsap)(?:\/gsap)?\s+loop\b|\bslow-drifting\b|\bidle\s+drift\w*/gi;

function negativeListText(direction) {
  const i = String(direction ?? "").search(/negative list/i);
  if (i < 0) return "";
  const rest = direction.slice(i);
  const end = rest.search(/\n\s*\n|\n#{1,3}\s/);
  return end === -1 ? rest : rest.slice(0, end);
}

// Positive (non-negated) mentions: the 4 words before a hit, within its
// sentence, must not carry a negation.
function positiveMentions(text, re) {
  const hits = [];
  for (const m of String(text ?? "").matchAll(re)) {
    const before = text.slice(Math.max(0, m.index - 120), m.index);
    const clause = before.split(/[.;:!?\n]/).pop();
    const window = clause.trim().split(/\s+/).slice(-4).join(" ");
    if (NEGATION_RE.test(window)) continue;
    const s = Math.max(0, m.index - 30);
    hits.push(text.slice(s, m.index + m[0].length + 30).replace(/\s+/g, " ").trim());
  }
  return hits;
}

function videoDirection(storyboard) {
  const m = /^## Video direction\s*$([\s\S]*?)(?=^## |(?![\s\S]))/m.exec(storyboard ?? "");
  return m ? m[1] : "";
}

function paletteNames(frameMd) {
  const block = frontmatterBlock(frameMdFrontmatter(frameMd ?? ""), "colors");
  return [...block.matchAll(/^\s+([A-Za-z][\w-]*)\s*:/gm)].map((m) => m[1].toLowerCase());
}

export function blueprintDurationRange(blueprintMd) {
  const line = /^\*\*duration\*\*\s*:\s*(.+)$/im.exec(blueprintMd ?? "");
  if (!line) return null;
  const range = /~?(\d+(?:\.\d+)?)\s*s?\s*[–—-]\s*~?(\d+(?:\.\d+)?)\s*s\b/.exec(line[1]);
  if (range) return { lo: Number(range[1]), hi: Number(range[2]) };
  const one = /~?(\d+(?:\.\d+)?)\s*s\b/.exec(line[1]);
  return one ? { lo: Number(one[1]), hi: Number(one[1]) } : null;
}

const fmtS = (n) => `${Number(n.toFixed(3))}s`;

// Static contradictions between the frame block, its selected blueprint, the
// Blueprint assignment and the video-wide direction/negative list. Each entry
// carries a ruling the worker applies verbatim instead of deliberating.
export function detectConflicts({ block, sel, assigned, blueprintText = "", direction = "", frameMd = "" }) {
  const out = [];
  // A mode word is never a blueprint id — no ruling may name `Adapt`/`Reproduce`.
  if (sel?.id && BARE_MODE_RE.test(sel.id)) sel = { ...sel, id: "", modeOnly: true };
  if (assigned?.id && BARE_MODE_RE.test(assigned.id)) assigned = null;
  if (sel?.modeOnly && assigned?.id) sel = withAssignment(sel, assigned, null);
  if (sel?.conflict && sel.id) {
    const others = sel.all.filter((p) => p.id && p.id.toLowerCase() !== sel.id.toLowerCase()).map((p) => `\`${p.raw}\``);
    out.push({
      kind: "blueprint-fields",
      detail: `the block lists several \`- blueprint:\` lines (${others.join(", ")} vs \`${sel.raw}\`)`,
      ruling: `\`${sel.id}\`${sel.mode ? ` (${sel.mode})` : ""} is the frame's blueprint; the other lines are superseded — do not blend their shot structures.`,
    });
  }
  if (assigned && sel?.id && assigned.id.toLowerCase() !== sel.id.toLowerCase()) {
    out.push({
      kind: "blueprint-assignment",
      detail: `\`## Blueprint assignment\` names \`${assigned.id}\` but the block selects \`${sel.id}\``,
      ruling: `follow \`${sel.id}\` (the block's own moded line, which its Scene list was written against); ignore the assignment entry for this frame.`,
    });
  }
  const dur = parseDurationSeconds(field(block, "duration") ?? field(block, "durationSeconds"));
  const scenes = [...String(block).matchAll(SCENE_WINDOW_RE)].map((m) => ({
    label: m[0].replace(/\s*\(.*$/, ""), a: Number(m[1]), b: Number(m[2]),
  }));
  if (dur != null && scenes.length) {
    const lastEnd = Math.max(...scenes.map((s) => s.b));
    if (lastEnd - dur > 0.1) {
      const k = dur / lastEnd;
      const windows = scenes.map((s) => `${s.label} ${fmtS(s.a * k)}–${fmtS(s.b * k)}`).join("; ");
      out.push({
        kind: "scene-overflow",
        detail: `the last Scene ends at ${fmtS(lastEnd)} but \`- duration: ${fmtS(dur)}\``,
        ruling: `duration is the hard clip length. Keep every Scene's order and content but rescale its window ×${k.toFixed(3)}: ${windows}. Nothing may be scheduled after ${fmtS(dur)}.`,
      });
    } else if (dur - lastEnd > 0.1) {
      out.push({
        kind: "scene-underflow",
        detail: `the last Scene ends at ${fmtS(lastEnd)} but \`- duration: ${fmtS(dur)}\``,
        ruling: `keep the Scene windows as written and hold (alive, low-amplitude) the final state through ${fmtS(dur)}.`,
      });
    }
  }
  const range = blueprintDurationRange(blueprintText);
  if (dur != null && range && sel?.id) {
    const tol = Math.max(0.25, range.hi * 0.1);
    if (dur < range.lo - tol || dur > range.hi + tol) {
      out.push({
        kind: "blueprint-duration",
        detail: `\`${sel.id}\` is authored for ${range.lo === range.hi ? fmtS(range.lo) : `${fmtS(range.lo)}–${fmtS(range.hi)}`} but this frame runs ${fmtS(dur)}`,
        ruling: dur > range.hi
          ? "the Scene list wins — stretch the blueprint's phases onto the Scene windows and fill the extra time with the hold/develop beats the Scenes describe; do not invent extra blueprint variants."
          : "the Scene list wins — compress the blueprint's phases into the Scene windows and drop its optional variant beats; never extend past the duration.",
      });
    }
  }
  const typeBan = [direction, frameMd].map((t) => TYPE_ROTATION_BAN_RE.exec(t ?? "")?.[0]).find(Boolean);
  const neg = negativeListText(direction).replace(new RegExp(TYPE_ROTATION_BAN_RE.source, "gi"), "");
  if (typeBan) {
    const fromBlock = positiveMentions(block, CARD_3D_RE);
    const fromBp = positiveMentions(blueprintText, CARD_3D_RE);
    if (fromBlock.length || fromBp.length) {
      const where = [
        fromBlock.length ? `block: "…${fromBlock[0]}…"` : "",
        fromBp.length ? `blueprint \`${sel?.id || "?"}\`: "…${fromBp[0]}…"` : "",
      ].filter(Boolean).join("; ");
      out.push({
        kind: "type-rotation-scope",
        detail: `the design constraints say "${typeBan.replace(/\s+/g, " ").trim()}" while 3D/tilt is asked for (${where})`,
        ruling: "the rotation/3D ban applies to type only — glyphs, words and text lines stay upright and flat (no rotate/rotateX/rotateY/skew on text). Card/container tilt, rotateY and perspective run exactly as the recipe/blueprint specifies; text inside a tilted card rides along with its card, never with its own rotation tween.",
      });
    }
  }
  if (neg) {
    for (const ban of MOTION_BANS) {
      if (!ban.banned.test(neg)) continue;
      const fromBlock = positiveMentions(block, ban.used);
      const fromBp = positiveMentions(blueprintText, ban.usedInBlueprint ?? ban.used);
      if (!fromBlock.length && !fromBp.length) continue;
      const where = [
        fromBlock.length ? `block: "…${fromBlock[0]}…"` : "",
        fromBp.length ? `blueprint \`${sel?.id ?? "?"}\`: "…${fromBp[0]}…"` : "",
      ].filter(Boolean).join("; ");
      out.push({
        kind: `negative-list-${ban.key}`,
        detail: `the video-wide negative list bans ${ban.key} motion but it is asked for (${where})`,
        ruling: `the negative list wins: ${ban.ruling}.`,
      });
    }
  }
  const loopBan = [direction, frameMd].map((t) => IDLE_LOOP_BAN_RE.exec(t ?? "")?.[0]).find(Boolean);
  if (loopBan) {
    const fromDir = positiveMentions(direction, LOOP_ASK_RE);
    const fromBlock = positiveMentions(block, LOOP_ASK_RE);
    if (fromDir.length || fromBlock.length) {
      const where = [
        fromDir.length ? `direction: "…${fromDir[0]}…"` : "",
        fromBlock.length ? `block: "…${fromBlock[0]}…"` : "",
      ].filter(Boolean).join("; ");
      const tickerAsked = /\bticker|\bmarquee/i.test([...fromDir, ...fromBlock].join(" "));
      const blockTicker = positiveMentions(block, /\bticker\b|\bmarquee\b/gi).length > 0;
      out.push({
        kind: "idle-loop-vs-continuous-motion",
        detail: `the constraints say "${loopBan.replace(/\s+/g, " ").trim()}" while continuous/looping motion is asked for (${where})`,
        ruling: `continuous motion is allowed only as ONE finite, one-way tween bounded by this clip — e.g. \`tl.fromTo(el, { x: A }, { x: B, duration: <clip duration>, ease: "none" }, 0)\` — never \`repeat: -1\`, yoyo, CSS @keyframes/infinite animation, or a wrap-around reset. Ambient drift (blobs, gradients, floats) runs once after its entrance and settles or slows to a stop before the frame ends; every other element resolves to stillness before the transition.${tickerAsked ? ` The ticker/marquee is that one sanctioned linear drift${blockTicker ? " — this block lists it, so build it" : " — build it only on frames whose block lists it; this block does not, so omit it"}.` : ""}`,
      });
    }
  }
  const bgRule = /background\s+(?:is\s+|stays\s+)?always\s+([A-Za-z]+)/i.exec(direction ?? "");
  if (bgRule) {
    const required = bgRule[1].toLowerCase();
    const names = new Set([...paletteNames(frameMd), "black", "white", "dark", "gradient"]);
    const seen = new Set();
    for (const m of String(block).matchAll(/\b([A-Za-z]+)\s+(?:field|background|backdrop|ground)\b/gi)) {
      const name = m[1].toLowerCase();
      if (name === required || !names.has(name) || seen.has(name)) continue;
      const clause = block.slice(Math.max(0, m.index - 60), m.index).split(/[.;:!?\n]/).pop();
      if (NEGATION_RE.test(clause.trim().split(/\s+/).slice(-4).join(" "))) continue;
      seen.add(name);
      out.push({
        kind: "background-color",
        detail: `the direction fixes the background to ${required} but the block asks for "${m[0]}"`,
        ruling: `the ground layer stays ${required}; render the ${name} area as a card/panel surface on top of it.`,
      });
    }
  }
  return out;
}

export function resolvedConflictsSection(conflicts) {
  if (!conflicts.length) return "";
  const items = conflicts.map((c, i) => `${i + 1}. **${c.kind}** — ${c.detail}.\n   **Ruling:** ${c.ruling}`);
  return `\n## Resolved conflicts\n\n> Deterministic packet-time checks found these contradictions in the inputs above. The rulings are final and override every section above (Scene list > design contract > blueprint > prose) — apply them, do not re-deliberate.\n\n${items.join("\n")}\n`;
}

// P1-3: trim per-frame assignment table (each frame already gets `## Selected
// blueprint`) and duplicate verbatim palette/type block when frame.md tokens
// are already inlined right above. Keeps the creative body 100% shared across
// frames so cross-worker prompt prefix caching hits.
export function trimDesignContractForDispatch(designContract, hasFrameTokens = false) {
  let out = String(designContract ?? "").trim();
  if (!out) return "";
  out = out.replace(/^[ \t]*(?:#{1,6}[ \t]*|\*\*[ \t]*)?Blueprint\s+assignment(?:[ \t]*\*\*)?[ \t]*[:：.]?[ \t]*$[\s\S]*$/im, "").trim();
  if (hasFrameTokens) {
    out = out.replace(/^\*\*Palette & type\b[^\n]*\*\*\n(?:(?!\n\n)[\s\S])*?(?:\n\n|$)/im, "").trim();
  }
  return out.replace(/\n{3,}/g, "\n\n").trim();
}

export function buildDispatchDocs({ projectDir, frames, outDir, animationDir, cardPath, frameMdPath, storyboard, maxDispatchBytes = 40000, inlineRecipes = inlineRecipesMode(), resolveConflicts = envSwitch("HTMLVIDEO_FRAME_PACKETS_RESOLVE_CONFLICTS", true) }) {
  const card = readFileSync(cardPath, "utf8").trim();
  const frameMd = frameMdPath && existsSync(frameMdPath) ? readFileSync(frameMdPath, "utf8") : "";
  const excerpt = frameMdExcerpt(frameMd);
  // P2: the frame-visual design-contract phase writes this anchor; when present,
  // inline it so the frame-html worker translates a recipe instead of re-deriving
  // the video-wide palette/grammar/negative-list itself. Skip when absent (test
  // projects built frame-html-first have no _design.md).
  const designMdPath = join(projectDir, ".hyperframes", "visual", "_design.md");
  const designContract = existsSync(designMdPath) ? readFileSync(designMdPath, "utf8").trim() : "";
  const hasFrameTokens = excerpt.includes("## frame.md tokens (resolved values)");
  const sharedDesign = trimDesignContractForDispatch(designContract, hasFrameTokens);
  const ruleIds = knownRuleIds(animationDir);
  const assignment = blueprintAssignment(storyboard);
  const direction = [videoDirection(storyboard), designContract].filter(Boolean).join("\n\n");
  mkdirSync(outDir, { recursive: true });
  return frames.map((frame) => {
    const assigned = assignment.get(frameNumber(frame.heading));
    const { sel, section: blueprintSection, blueprintText = "" } = blueprintSectionFor(
      frame.block, animationDir, "Selected blueprint", frame.frameId, assigned);
    if (assigned && sel?.id && assigned.id.toLowerCase() !== sel.id.toLowerCase()) {
      console.warn(
        `frame-packets: ${frame.frameId}: selected blueprint "${sel.id}" ≠ Blueprint assignment "${assigned.id}"`,
      );
    }
    const conflicts = detectConflicts({
      block: frame.block, sel, assigned, blueprintText, direction, frameMd,
    });
    for (const c of conflicts) console.warn(`frame-packets: ${frame.frameId}: conflict ${c.kind}: ${c.detail}`);
    const { rules, inferred } = frameRules(frame.block, ruleIds, { blueprintText });
    const ruleIndex = rules.length
      ? rules.map((r) => `- \`${r}\` → ${join(animationDir, "rules", `${r}.md`)}${inferred.includes(r) ? " (inferred from the block's motion phrases)" : ""}`).join("\n")
      : "- (no motion rules cited)";
    const indexHeading = inlineRecipes === "off"
      ? "## Rule index (open a recipe file only while authoring its phase)"
      : `## Rule index (recipes ${inlineRecipes === "full" ? "inlined in full" : "summarized"} below — open a file only if a detail is missing)`;
    const recipes = recipeSections(rules, animationDir, inlineRecipes);
    // P0-2: shared cross-frame prefix FIRST (Project inputs → Role card →
    // frame.md excerpt → video-wide Design contract) so concurrent workers hit
    // prompt prefix cache; frame-specific sections follow `# Dispatch: <id>`.
    const doc = `## Project inputs

- Project: ${resolve(projectDir)}
- RULES_DIR: ${join(animationDir, "rules")}

## Role (executable rule card)

${card}

## frame.md design tokens (excerpt)

${excerpt}
${sharedDesign ? `\n## Design contract (video-wide)\n\n${sharedDesign}\n` : ""}
# Dispatch: ${frame.frameId}

## Assigned storyboard block

${frame.block}
${blueprintSection}
${indexHeading}

${ruleIndex}
${recipes}${resolveConflicts ? resolvedConflictsSection(conflicts) : ""}`;
    const bytes = Buffer.byteLength(doc);
    if (bytes > maxDispatchBytes)
      console.warn(`frame-packets: dispatch ${frame.frameId} is ${bytes} bytes (budget ${maxDispatchBytes})`);
    const path = join(outDir, `${frame.frameId}.dispatch.md`);
    writeFileSync(path, doc);
    return { frameId: frame.frameId, path, bytes, conflicts };
  });
}

// Pre-built per-frame skeleton files: minimal valid `<template>` + #root attrs
// + marker comment, deterministic and selfcheck-clean ("last write wins" — the
// filler worker path rewrites them; packets are fair game to refresh).
function htmlCommentSafe(text) {
  return String(text).replace(/--+/g, "—").replace(/\s+/g, " ").trim();
}

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Approved collision-free shared-stage presets ONLY (no ad-hoc layout names).
export const SHARED_STAGE_LAYOUTS = new Set([
  "top-stepper-center-card",
  "left-rail-right-card",
  "center-scorecard",
  "none",
]);

function parseSlotStates(raw) {
  const inside = /\[([^\]]*)\]/.exec(String(raw ?? ""))?.[1] ?? "";
  if (!inside.trim()) return [];
  return inside.split(",").map((tok) => {
    const clean = tok.trim();
    const [statusRaw, colorRaw] = clean.split(":").map((s) => s.trim());
    const st = (statusRaw || "dim").toLowerCase();
    const status = st === "done" || st === "active" ? st : "dim";
    return { status, color: colorRaw ? colorRaw.toLowerCase() : "" };
  });
}

// Parse `## Shared stage spec` from `_design.md` or `STORYBOARD.md`.
export function parseSharedStageSpec(text) {
  const sec = /^[ \t]*(?:#{1,6}[ \t]*|\*\*[ \t]*)?Shared\s+stage\s+spec(?:[ \t]*\*\*)?[ \t]*[:：.]?[ \t]*$([\s\S]*?)(?=^## |(?![\s\S]))/im.exec(text ?? "");
  if (!sec) return null;
  const body = sec[1];
  const rawLayout = (/^\s*layout\s*:\s*([\w-]+)/im.exec(body)?.[1] ?? "none").trim().toLowerCase();
  const baseLayout = SHARED_STAGE_LAYOUTS.has(rawLayout) ? rawLayout : "none";
  const rawTitle = (/^\s*title\s*:\s*(.+)$/im.exec(body)?.[1] ?? "").trim();
  const title = /^none$/i.test(rawTitle) ? "" : rawTitle;
  const rawSlots = (/^\s*slots\s*:\s*(.+)$/im.exec(body)?.[1] ?? "").trim();
  const slots = rawSlots && !/^none$/i.test(rawSlots)
    ? rawSlots.split("|").map((s) => s.trim()).filter(Boolean)
    : [];
  const frames = new Map();
  for (const m of body.matchAll(/^\s*[-*]?\s*F(\d+)\s*:\s*(.+)$/gim)) {
    const fn = Number(m[1]);
    const val = m[2].trim();
    if (!val || /^none\b/i.test(val)) {
      frames.set(fn, { layout: "none", states: [] });
      continue;
    }
    const over = /^([a-z][\w-]*)\s+(\[[^\]]*\])/i.exec(val);
    let layout = baseLayout;
    let statePart = val;
    if (over) {
      const cand = over[1].toLowerCase();
      layout = SHARED_STAGE_LAYOUTS.has(cand) ? cand : baseLayout;
      statePart = over[2];
    }
    if (layout === "none") {
      frames.set(fn, { layout: "none", states: [] });
      continue;
    }
    frames.set(fn, { layout, states: parseSlotStates(statePart) });
  }
  return { layout: baseLayout, title, slots, frames };
}

export function parseFrameStageTokens(frameMd) {
  const defaults = {
    colors: {
      cream: "#F5F5F0",
      canvas: "#F5F5F0",
      ink: "#1A1A1A",
      outline: "#1E1E1E",
      white: "#FFFFFF",
      sky: "#8BB4F7",
      coral: "#E85D4E",
      mint: "#A8E6CF",
      yellow: "#F6D365",
      lavender: "#C9B6F2",
      peach: "#F7B79C",
      lime: "#C8E870",
    },
    bodyFont: "Space Grotesk",
    displayFont: "Bodoni Moda",
  };
  if (!frameMd) return defaults;
  const fm = frameMdFrontmatter(frameMd);
  const colorBlock = frontmatterBlock(fm, "colors");
  for (const m of colorBlock.matchAll(/^\s+([\w-]+)\s*:\s*["']?(#[0-9A-Fa-f]{3,8}|rgba?\([^)]+\))["']?/gm)) {
    defaults.colors[m[1].toLowerCase()] = m[2];
  }
  const typeBlock = frontmatterBlock(fm, "typography");
  const bodyFam = /^\s+body\s*:[^\n]*fontFamily\s*:\s*["']([^"']+)["']/m.exec(typeBlock)?.[1]
    ?? /fontFamily\s*:\s*["']([^"']+)["']/.exec(typeBlock)?.[1];
  const dispFam = /^\s+(?:display|headline)\s*:[^\n]*fontFamily\s*:\s*["']([^"']+)["']/m.exec(typeBlock)?.[1];
  if (bodyFam) defaults.bodyFont = bodyFam;
  if (dispFam) defaults.displayFont = dispFam;
  return defaults;
}

const HF_MICRO_RUNTIME = `/* hf-runtime-start */
const __hf = {
  type(tl, textEl, caretEl, str, at, dur) {
    const s = String(str ?? "");
    const el = typeof textEl === "string" ? document.querySelector(textEl) : textEl;
    const caret = caretEl ? (typeof caretEl === "string" ? document.querySelector(caretEl) : caretEl) : null;
    if (!el) return tl;
    const d = Math.max(0.05, Number(dur) || 0.6);
    const proxy = { n: 0 };
    tl.set(el, { textContent: "" }, at);
    if (caret) tl.set(caret, { autoAlpha: 1 }, at);
    tl.fromTo(proxy, { n: 0 }, {
      n: s.length,
      duration: d,
      ease: "none",
      immediateRender: false,
      onUpdate() { el.textContent = s.slice(0, Math.round(proxy.n)); },
      onComplete() { el.textContent = s; },
    }, at);
    if (caret) tl.set(caret, { autoAlpha: 0 }, at + d + 0.15);
    return tl;
  },
  draw(tl, target, at, dur, ease = "power2.out") {
    const els = typeof target === "string" ? [...document.querySelectorAll(target)] : [target].filter(Boolean);
    for (const el of els) {
      const len = typeof el.getTotalLength === "function" ? Math.ceil(el.getTotalLength()) : 600;
      tl.set(el, { strokeDasharray: len, strokeDashoffset: len }, 0);
      tl.fromTo(el, { strokeDashoffset: len }, { strokeDashoffset: 0, duration: Math.max(0.05, Number(dur) || 0.5), ease, immediateRender: false }, at);
    }
    return tl;
  },
};
/* hf-runtime-end */`;

/** Grid columns for the center-scorecard slot rail: one row up to 3 slots, else 2 columns. */
export function centerScorecardColumns(slotCount) {
  const n = Math.max(1, Number(slotCount) || 1);
  return n <= 3 ? n : 2;
}

function buildSharedStageMarkupAndCss(stageSpec, entry, tokens, dur) {
  if (!stageSpec || !entry || !entry.layout || entry.layout === "none" || !stageSpec.slots.length) {
    return null;
  }
  const c = tokens.colors;
  const ground = c.cream || c.canvas || "#F5F5F0";
  const shadow = c.shadow || c.outline;
  const resolveAccent = (name, fallback) => (name && c[name]) || fallback || c.sky || "#8BB4F7";
  // Pre-render BOTH `.hf-stage-dot` and `.hf-stage-check` in every slot and
  // toggle visibility via `[data-state="done"]` CSS; drive slot fill via
  // `data-accent` CSS rules (no inline `style="background:..."`) so workers can
  // animate or switch states without fighting inline styles (6b07a9c1 F5, 6b07fd3a F7).
  const accentKeys = Object.keys(c).filter(
    (k) => typeof c[k] === "string" && c[k].trim() && !["shadow", "cream", "canvas", "ink", "outline"].includes(k),
  );
  const accentCss = accentKeys
    .map((k) => `.hf-stage-slot[data-accent="${k}"]:not([data-state="dim"]) { background: ${c[k]}; }`)
    .join("\n");
  // Per-slot column accent inheritance across frames (6b082a5b F3): if a slot
  // starts `dim` with no explicit `:color` on this frame, inherit the first
  // valid `:color` declared for that same slot index on any sibling frame
  // (falling back to a stage-wide single accent only when the column has none).
  // CSS applies `[data-accent="..."]:not([data-state="dim"])`, so `dim` slots
  // stay white/faded until switched to `active`/`done`, while preserving
  // per-slot multi-color specs like `[done:coral, done:sky, done:lime]`.
  const allStageFrames = stageSpec.frames instanceof Map ? [...stageSpec.frames.values()] : [];
  const stageWideColors = new Set();
  for (const fe of allStageFrames) {
    for (const st of fe?.states || []) {
      if (st?.color && c[st.color]) stageWideColors.add(st.color);
    }
  }
  const singleStageAccent = stageWideColors.size === 1 ? [...stageWideColors][0] : "";
  const slotHtml = stageSpec.slots.map((label, idx) => {
    const st = entry.states[idx] ?? { status: "dim", color: "" };
    let accentName = (st.color && c[st.color]) ? st.color : "";
    if (!accentName) {
      for (const fe of allStageFrames) {
        const colColor = fe?.states?.[idx]?.color;
        if (colColor && c[colColor]) {
          accentName = colColor;
          break;
        }
      }
    }
    if (!accentName && singleStageAccent) accentName = singleStageAccent;
    const accentAttr = accentName ? ` data-accent="${escapeHtml(accentName)}"` : "";
    const icons =
      `<span class="hf-stage-dot" data-skeleton="stage"></span>` +
      `<svg class="hf-stage-check" data-skeleton="stage" viewBox="0 0 16 16" aria-hidden="true"><path data-skeleton="stage" d="M3 8.5L6.5 12L13 4.5" fill="none" stroke="${c.ink}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    return `      <div class="hf-stage-slot" data-skeleton="stage" data-slot-index="${idx + 1}" data-state="${st.status}"${accentAttr}>${icons}<span class="hf-stage-label" data-skeleton="stage">${escapeHtml(label)}</span></div>`;
  }).join("\n");
  // The title is the lockup's only title node, so it can never collide inside
  // its own frame — but pixel-locked sibling frames make it overlap itself
  // during every crossfade (6b0795df: t=26.6s F4→F5, t=41.5s F7→F8), which
  // `hyperframes check` mis-attributes to the first frame carrying the class.
  const titleHtml = stageSpec.title
    ? `    <div class="hf-stage-title" data-skeleton="stage" data-layout-allow-overlap="true">${escapeHtml(stageSpec.title)}</div>\n`
    : "";
  let layoutCss = "";
  if (entry.layout === "left-rail-right-card") {
    layoutCss = `
.hf-stage-lockup { position: absolute; left: 5cqw; top: 15cqh; width: 25cqw; display: flex; flex-direction: column; gap: 1.4cqh; padding: 2.2cqh 1.6cqw; background: ${c.white}; border: 3px solid ${c.outline}; border-radius: 1cqw; box-shadow: 6px 6px 0 ${shadow}; box-sizing: border-box; z-index: 2; }
.hf-stage-slots { display: flex; flex-direction: column; gap: 1.1cqh; }
#stage-card { position: absolute; left: 34cqw; top: 14cqh; width: 60cqw; min-height: 48cqh; max-height: 64cqh; padding: 3cqh 2.8cqw; background: ${c.white}; border: 3px solid ${c.outline}; border-radius: 1.1cqw; box-shadow: 8px 8px 0 ${shadow}; display: flex; flex-direction: column; justify-content: center; gap: 2cqh; box-sizing: border-box; overflow: hidden; z-index: 2; }`;
  } else if (entry.layout === "top-stepper-center-card") {
    layoutCss = `
.hf-stage-lockup { position: absolute; left: 10cqw; top: 5cqh; width: 80cqw; display: flex; flex-direction: row; align-items: center; justify-content: space-between; gap: 1.4cqw; padding: 1.2cqh 1.8cqw; background: ${c.white}; border: 3px solid ${c.outline}; border-radius: 0.9cqw; box-shadow: 5px 5px 0 ${shadow}; box-sizing: border-box; z-index: 2; }
.hf-stage-slots { display: flex; flex-direction: row; flex: 1; justify-content: flex-end; gap: 1cqw; min-width: 0; }
#stage-card { position: absolute; left: 18cqw; top: 19cqh; width: 64cqw; min-height: 46cqh; max-height: 59cqh; padding: 3cqh 3cqw; background: ${c.white}; border: 3px solid ${c.outline}; border-radius: 1.1cqw; box-shadow: 8px 8px 0 ${shadow}; display: flex; flex-direction: column; justify-content: center; gap: 2cqh; box-sizing: border-box; overflow: hidden; z-index: 2; }`;
  } else if (entry.layout === "center-scorecard") {
    // ≥4 slots in one row leave ~250px per cell; 30–37 char labels (6b0795df
    // F2) then collide. Wrap to a 2-column grid (~520px per cell) and lift the
    // lockup to 8cqh so up to 8 slots (4 rows) still clear #stage-card at 38cqh.
    layoutCss = `
.hf-stage-lockup { position: absolute; left: 20cqw; top: 8cqh; width: 60cqw; display: flex; flex-direction: column; gap: 1.2cqh; padding: 2cqh 2.4cqw; background: ${c.white}; border: 3px solid ${c.outline}; border-radius: 1cqw; box-shadow: 6px 6px 0 ${shadow}; box-sizing: border-box; z-index: 2; }
.hf-stage-slots { display: grid; grid-template-columns: repeat(${centerScorecardColumns(stageSpec.slots.length)}, minmax(0, 1fr)); gap: 1cqh 1cqw; }
#stage-card { position: absolute; left: 20cqw; top: 38cqh; width: 60cqw; min-height: 28cqh; max-height: 40cqh; padding: 2.6cqh 2.8cqw; background: ${c.white}; border: 3px solid ${c.outline}; border-radius: 1.1cqw; box-shadow: 8px 8px 0 ${shadow}; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 1.6cqh; box-sizing: border-box; overflow: hidden; z-index: 2; }`;
  } else {
    return null;
  }
  const css = `#root { position: relative; width: 100cqw; height: 100cqh; overflow: hidden; font-family: "${tokens.bodyFont}", sans-serif; color: ${c.ink}; }
.bg { position: absolute; inset: 0; background: ${ground}; }
.hf-stage-title { font-size: 1.05cqw; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: ${c.ink}; white-space: nowrap; }
.hf-stage-slot { display: flex; align-items: center; gap: 0.65cqw; padding: 0.7cqh 0.95cqw; border: 2px solid ${c.outline}; border-radius: 0.6cqw; font-size: 1.12cqw; font-weight: 700; line-height: 1.2; color: ${c.ink}; box-sizing: border-box; min-width: 0; }
.hf-stage-slot[data-state="dim"] { background: ${c.white}; opacity: 0.46; }
.hf-stage-slot[data-state="active"] { background: ${resolveAccent("", c.sky)}; box-shadow: 3px 3px 0 ${shadow}; opacity: 1; }
.hf-stage-slot[data-state="done"] { background: ${resolveAccent("", c.mint)}; opacity: 0.92; }
${accentCss}
.hf-stage-check { width: 1.1cqw; height: 1.1cqw; flex-shrink: 0; display: none; }
.hf-stage-dot { width: 0.65cqw; height: 0.65cqw; border-radius: 50%; border: 2px solid ${c.outline}; flex-shrink: 0; }
.hf-stage-slot[data-state="done"] .hf-stage-check { display: block; }
.hf-stage-slot[data-state="done"] .hf-stage-dot { display: none; }
.hf-stage-label { overflow: hidden; text-overflow: ellipsis; }${layoutCss}
/* Add this frame's hero styles for children inside #stage-card below: */`;
  const dom = `  <!-- Shared stage lockup (${entry.layout}): pixel-locked across sibling frames — KEEP intact, do not animate lockup scale/position or rewrite slot DOM (both .hf-stage-dot and .hf-stage-check are pre-built). -->
  <div class="clip hf-stage-lockup" data-skeleton="stage" data-stage-layout="${entry.layout}" data-start="0" data-duration="${dur}" data-track-index="1">
${titleHtml}    <div class="hf-stage-slots" data-skeleton="stage">
${slotHtml}
    </div>
  </div>
  <!-- Hero stage card (track 2): drop data-skeleton from #stage-card and place this frame's hero content inside it. -->
  <div id="stage-card" class="clip hf-stage-card" data-skeleton="card" data-start="0" data-duration="${dur}" data-track-index="2">
    <!-- FILL THIS FRAME'S HERO CONTENT HERE -->
  </div>`;
  return { css, dom };
}

// HTMLVIDEO_FRAME_HTML_RICH_SKELETON (default ON; set 0 to disable): full-duration
// ground clip (tagged data-skeleton so frame-selfcheck still sees the file as
// unfilled), optional shared-stage scaffold, __hf micro-helpers, per-Scene
// placeholder comments and Scene labels on the registered timeline.
function richSkeleton(frame, dims, dur, { stageSpec = null, stageEntry = null, tokens = null } = {}) {
  const scenes = [...String(frame.block).matchAll(SCENE_WINDOW_RE)].map((m, i) => {
    const lineEnd = frame.block.indexOf("\n", m.index);
    const line = frame.block.slice(m.index, lineEnd === -1 ? undefined : lineEnd);
    const desc = htmlCommentSafe(line.slice(m[0].length).replace(/^\s*[:—-]\s*/, "")).slice(0, 100);
    return { n: i + 1, a: Number(m[1]), b: Number(m[2]), head: htmlCommentSafe(m[0]), desc };
  });
  const lastEnd = scenes.length ? Math.max(...scenes.map((s) => s.b)) : 0;
  if (lastEnd - Number(dur) > 0.1) {
    const k = Number(dur) / lastEnd;
    for (const s of scenes) {
      s.a = Number((s.a * k).toFixed(3));
      s.b = Number((s.b * k).toFixed(3));
      s.head += ` [rescaled to ${s.a}–${s.b}s]`;
    }
  }
  const sceneComments = scenes.map((s) => `  <!-- ${s.head}: ${s.desc}${s.desc.length >= 100 ? "…" : ""} -->`).join("\n");
  const labels = scenes.map((s) => `tl.addLabel("scene${s.n}", ${s.a});`).join("\n");
  const sceneTodos = scenes.map((s) => `// ${s.head} — tweens at "scene${s.n}" (${s.a}s) … ${s.b}s`).join("\n");
  const shared = buildSharedStageMarkupAndCss(stageSpec, stageEntry, tokens ?? parseFrameStageTokens(""), dur);
  const styleBody = shared
    ? `\n${shared.css}\n`
    : "/* fill: frame styles — .bg is the full-bleed ground (position:absolute; inset:0) */";
  const stageDom = shared ? `\n${shared.dom}\n` : "\n";
  return `<!-- skeleton-v1 (rich): fill style/body/script per .hyperframes/frame-packets/${frame.frameId}.dispatch.md — last write wins -->
<template>
<div id="root" data-composition-id="${frame.frameId}"${dims} data-duration="${dur}">
  <!-- Ground layer (track 0, full duration): style it as the frame's background and drop data-skeleton; keep other clips off track 0 or end-to-end. -->
  <div class="clip bg" data-skeleton="bg" data-start="0" data-duration="${dur}" data-track-index="0"></div>${stageDom}${sceneComments ? `${sceneComments}\n` : ""}</div>
<style>${styleBody}</style>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
window.__timelines["${frame.frameId}"] = tl;
${HF_MICRO_RUNTIME}
${labels ? `${labels}\n` : ""}${sceneTodos ? `${sceneTodos}\n` : ""}</script>
</template>
`;
}

export function buildSkeletons({ projectDir, frames, canvas, rich = envSwitch("HTMLVIDEO_FRAME_HTML_RICH_SKELETON", true), storyboard = "" }) {
  const framesDir = resolve(projectDir, "compositions", "frames");
  mkdirSync(framesDir, { recursive: true });
  const dims = canvas && canvas.width && canvas.height
    ? ` data-width="${canvas.width}" data-height="${canvas.height}"` : "";
  const designMdPath = join(projectDir, ".hyperframes", "visual", "_design.md");
  const designMd = existsSync(designMdPath) ? readFileSync(designMdPath, "utf8") : "";
  const sbPath = join(projectDir, "STORYBOARD.md");
  const sbText = storyboard || (existsSync(sbPath) ? readFileSync(sbPath, "utf8") : "");
  const stageSpec = rich
    ? (parseSharedStageSpec(designMd) ?? parseSharedStageSpec(sbText))
    : null;
  const frameMdPath = join(projectDir, "frame.md");
  const tokens = stageSpec && existsSync(frameMdPath)
    ? parseFrameStageTokens(readFileSync(frameMdPath, "utf8"))
    : parseFrameStageTokens("");
  return frames.map((frame) => {
    const rawDur = field(frame.block, "duration") ?? field(frame.block, "durationSeconds");
    if (!rawDur) throw new Error(`${frame.frameId}: skeleton needs a - duration field on the storyboard block`);
    // Storyboard often writes "4s"; transitions inject / frame-selfcheck require a bare number.
    const durMatch = String(rawDur).trim().match(/^(\d+(?:\.\d+)?)s?$/i);
    if (!durMatch) {
      throw new Error(
        `${frame.frameId}: skeleton duration "${rawDur}" is not a plain number (e.g. 4 or 4s)`,
      );
    }
    const dur = durMatch[1];
    const fn = frameNumber(frame.heading);
    const stageEntry = stageSpec && fn != null ? stageSpec.frames.get(fn) ?? null : null;
    const skeleton = rich ? richSkeleton(frame, dims, dur, { stageSpec, stageEntry, tokens }) : `<!-- skeleton-v1: fill style/body/script per .hyperframes/frame-packets/${frame.frameId}.dispatch.md — last write wins -->
<template>
<div id="root" data-composition-id="${frame.frameId}"${dims} data-duration="${dur}"></div>
<style>/* fill: frame styles */</style>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {};
window.__timelines["${frame.frameId}"] = gsap.timeline({ paused: true });
</script>
</template>
`;
    const path = join(framesDir, `${frame.frameId}.html`);
    writeFileSync(path, skeleton);
    return { frameId: frame.frameId, path };
  });
}

export function buildFramePackets({
  projectDir,
  storyboardPath = join(projectDir, "STORYBOARD.md"),
  outDir = join(projectDir, ".hyperframes", "frame-packets"),
  maxPacketBytes = 100_000, // gowtd-mod: frame-packets maxPacketBytes
  animationDir,
  corePath,
  deltaPath,
  // Per-workflow hooks (all optional):
  // designTruthLine(projectDir) -> the packet's design-truth input line
  // validateFrame(frame, id)   -> throw to reject a frame before packing
  // extraSections(block)       -> extra packet sections appended after the rule recipes
  designTruthLine = (dir) => `- Design tokens: ${join(resolve(dir), "frame.md")}`,
  validateFrame,
  extraSections,
}) {
  const storyboard = readFileSync(storyboardPath, "utf8");
  const frames = splitFrames(storyboard);
  if (frames.length === 0) throw new Error("STORYBOARD.md has no frame blocks");
  const ruleIds = knownRuleIds(animationDir);
  const assignment = blueprintAssignment(storyboard);

  const packets = frames.map((frame) => {
    const id = frameId(frame);
    if (validateFrame) validateFrame(frame, id);
    checkSceneTimeline(frame.block, id);
    const assigned = assignment.get(frameNumber(frame.heading));
    const packet = `# Frame packet: ${id}\n\n## Project inputs\n\n- Project: ${resolve(projectDir)}\n${designTruthLine(projectDir)}\n- RULES_DIR: ${join(animationDir, "rules")}\n\n## Assigned storyboard block\n\n${frame.block}\n${resourceSections(frame.block, { animationDir, ruleIds, assigned })}${extraSections ? extraSections(frame.block) : ""}`;
    const bytes = Buffer.byteLength(packet);
    if (bytes > maxPacketBytes) {
      throw new Error(`${id}: frame packet is ${bytes} bytes (limit ${maxPacketBytes})`);
    }
    return { frameId: id, path: join(outDir, `${id}.md`), bytes, packet };
  });

  mkdirSync(outDir, { recursive: true });
  for (const { path, packet } of packets) writeFileSync(path, packet);
  const role = buildRolePayload({ corePath, deltaPath, outDir });
  return {
    packets: packets.map(({ packet: _packet, ...result }) => result),
    role,
  };
}

export function flag(argv, name, fallback) {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
}

// realpath both sides: on macOS /tmp → /private/tmp, and node resolves the main
// module's symlinks in import.meta.url while argv[1] keeps the invoked spelling —
// a raw compare silently skips main() when invoked through any symlinked path.
export function isMainModule(importMetaUrl) {
  if (!process.argv[1]) return false;
  try {
    return pathToFileURL(realpathSync(process.argv[1])).href === importMetaUrl;
  } catch {
    return false;
  }
}

export function runCli({ buildFramePackets: build, buildExtras }) {
  const argv = process.argv.slice(2);
  const projectDir = resolve(flag(argv, "project", "."));
  const outDir = resolve(flag(argv, "out-dir", join(projectDir, ".hyperframes", "frame-packets")));
  try {
    const { packets, role } = build({
      projectDir,
      storyboardPath: resolve(flag(argv, "storyboard", join(projectDir, "STORYBOARD.md"))),
      outDir,
    });
    if (buildExtras) {
      const storyboardPath = resolve(flag(argv, "storyboard", join(projectDir, "STORYBOARD.md")));
      const frames = splitFrames(readFileSync(storyboardPath, "utf8")).map((f) => ({ ...f, frameId: frameId(f) }));
      buildExtras({ projectDir, storyboardPath, outDir, frames, packets });
    }
    console.log(`✓ frame packets: ${packets.length} bounded packet(s)`);
    for (const packet of packets)
      console.log(`  ${packet.frameId}: ${packet.bytes} bytes → ${packet.path}`);
    console.log(`  worker role: ${role.bytes} bytes → ${role.path}`);
  } catch (error) {
    console.error(`✗ frame packets: ${error.message}`);
    process.exit(1);
  }
}
