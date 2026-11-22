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
// (a filename under the animation skill's rules/) mentioned in the block.
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

// A frame block may carry several `- blueprint:` lines (frame-visual appends a
// moded `- blueprint: <id> (Adapt|Reproduce)` below the outline's bare one).
// The moded line wins; among equals the LAST one wins. The id is the leading
// kebab token — `(Adapt)` and any trailing prose never reach the filename.
export function selectBlueprint(block) {
  const raws = [...block.matchAll(/^-\s+blueprint:\s*(.+)$/gim)].map((m) => m[1].trim());
  if (raws.length === 0) return null;
  const parsed = raws.map((raw) => {
    const mode = raw.match(BLUEPRINT_MODE_RE)?.[1] ?? null;
    const id = raw.replace(BLUEPRINT_MODE_RE, "").trim().match(/^[\w.-]+/)?.[0] ?? "";
    return { raw, id, mode: mode ? mode[0].toUpperCase() + mode.slice(1).toLowerCase() : null };
  });
  const moded = parsed.filter((p) => p.mode);
  const chosen = (moded.length ? moded : parsed).at(-1);
  const ids = [...new Set(parsed.map((p) => p.id.toLowerCase()))];
  return { ...chosen, all: parsed, conflict: ids.length > 1 };
}

// `## Blueprint assignment` → Map(frameNumber → { id, mode }).
export function blueprintAssignment(storyboard) {
  const out = new Map();
  const sec = /^## Blueprint assignment\s*$([\s\S]*?)(?=^## |(?![\s\S]))/m.exec(storyboard ?? "");
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

function blueprintSectionFor(block, animationDir, heading, warnLabel) {
  const sel = selectBlueprint(block);
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
  let section = selectedFile(path, title);
  if (sel.conflict) {
    section = section.replace(
      `## ${title}\n`,
      `## ${title}\n\n> Authoritative blueprint for this frame. Other \`- blueprint:\` lines in the block (${sel.all.filter((p) => p !== sel).map((p) => `\`${p.raw}\``).join(", ")}) are superseded.\n`,
    );
  }
  return { sel, section };
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

export function resourceSections(block, { animationDir, ruleIds }) {
  let sections = blueprintSectionFor(block, animationDir, "Selected blueprint", null).section;
  for (const rule of citedRules(block, ruleIds)) {
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
  "## Numerals & Claims (hard rule)", "## Pre-Render Self-Audit",
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
    out += `\n${h}\n\n${(endM === -1 ? rest : rest.slice(0, endM)).trim()}\n`;
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
    banned: /\b3d\b|\brotate[xyz]?\b|\bperspective\b|\btilt/i,
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
  if (sel?.conflict) {
    const others = sel.all.filter((p) => p.id.toLowerCase() !== sel.id.toLowerCase()).map((p) => `\`${p.raw}\``);
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
  const neg = negativeListText(direction);
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
  const ruleIds = knownRuleIds(animationDir);
  const assignment = blueprintAssignment(storyboard);
  const direction = [videoDirection(storyboard), designContract].filter(Boolean).join("\n\n");
  mkdirSync(outDir, { recursive: true });
  return frames.map((frame) => {
    const { sel, section: blueprintSection } = blueprintSectionFor(
      frame.block, animationDir, "Selected blueprint", frame.frameId);
    const assigned = assignment.get(frameNumber(frame.heading));
    if (assigned && sel && assigned.id.toLowerCase() !== sel.id.toLowerCase()) {
      console.warn(
        `frame-packets: ${frame.frameId}: selected blueprint "${sel.id}" ≠ Blueprint assignment "${assigned.id}"`,
      );
    }
    const bpPath = sel?.id ? join(animationDir, "blueprints", `${sel.id}.md`) : "";
    const blueprintText = bpPath && sel.id.toLowerCase() !== "compose" && existsSync(bpPath)
      ? readFileSync(bpPath, "utf8") : "";
    const conflicts = detectConflicts({
      block: frame.block, sel, assigned, blueprintText, direction, frameMd,
    });
    for (const c of conflicts) console.warn(`frame-packets: ${frame.frameId}: conflict ${c.kind}: ${c.detail}`);
    const rules = citedRules(frame.block, ruleIds);
    const ruleIndex = rules.length
      ? rules.map((r) => `- \`${r}\` → ${join(animationDir, "rules", `${r}.md`)}`).join("\n")
      : "- (no motion rules cited)";
    const indexHeading = inlineRecipes === "off"
      ? "## Rule index (open a recipe file only while authoring its phase)"
      : `## Rule index (recipes ${inlineRecipes === "full" ? "inlined in full" : "summarized"} below — open a file only if a detail is missing)`;
    const recipes = recipeSections(rules, animationDir, inlineRecipes);
    const doc = `# Dispatch: ${frame.frameId}

## Project inputs

- Project: ${resolve(projectDir)}
- RULES_DIR: ${join(animationDir, "rules")}

## Role (executable rule card)

${card}

## Assigned storyboard block

${frame.block}
${blueprintSection}
${indexHeading}

${ruleIndex}
${recipes}
## frame.md design tokens (excerpt)

${excerpt}
${designContract ? `\n## Design contract (video-wide)\n\n${designContract}\n` : ""}${resolveConflicts ? resolvedConflictsSection(conflicts) : ""}`;
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

// HTMLVIDEO_FRAME_HTML_RICH_SKELETON (default ON; set 0 to disable): full-duration
// ground clip (tagged data-skeleton so frame-selfcheck still sees the file as
// unfilled), per-Scene placeholder comments and Scene labels on the registered timeline.
function richSkeleton(frame, dims, dur) {
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
  return `<!-- skeleton-v1 (rich): fill style/body/script per .hyperframes/frame-packets/${frame.frameId}.dispatch.md — last write wins -->
<template>
<div id="root" data-composition-id="${frame.frameId}"${dims} data-duration="${dur}">
  <!-- Ground layer (track 0, full duration): style it as the frame's background and drop data-skeleton; keep other clips off track 0 or end-to-end. -->
  <div class="clip bg" data-skeleton="bg" data-start="0" data-duration="${dur}" data-track-index="0"></div>
${sceneComments ? `${sceneComments}\n` : ""}</div>
<style>/* fill: frame styles — .bg is the full-bleed ground (position:absolute; inset:0) */</style>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
window.__timelines["${frame.frameId}"] = tl;
${labels ? `${labels}\n` : ""}${sceneTodos ? `${sceneTodos}\n` : ""}</script>
</template>
`;
}

export function buildSkeletons({ projectDir, frames, canvas, rich = envSwitch("HTMLVIDEO_FRAME_HTML_RICH_SKELETON", true) }) {
  const framesDir = resolve(projectDir, "compositions", "frames");
  mkdirSync(framesDir, { recursive: true });
  const dims = canvas && canvas.width && canvas.height
    ? ` data-width="${canvas.width}" data-height="${canvas.height}"` : "";
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
    const skeleton = rich ? richSkeleton(frame, dims, dur) : `<!-- skeleton-v1: fill style/body/script per .hyperframes/frame-packets/${frame.frameId}.dispatch.md — last write wins -->
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

  const packets = frames.map((frame) => {
    const id = frameId(frame);
    if (validateFrame) validateFrame(frame, id);
    checkSceneTimeline(frame.block, id);
    const packet = `# Frame packet: ${id}\n\n## Project inputs\n\n- Project: ${resolve(projectDir)}\n${designTruthLine(projectDir)}\n- RULES_DIR: ${join(animationDir, "rules")}\n\n## Assigned storyboard block\n\n${frame.block}\n${resourceSections(frame.block, { animationDir, ruleIds })}${extraSections ? extraSections(frame.block) : ""}`;
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
