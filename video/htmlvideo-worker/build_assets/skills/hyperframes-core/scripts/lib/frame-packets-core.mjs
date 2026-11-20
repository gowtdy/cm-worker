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
  let sections = "";
  const blueprint = field(block, "blueprint");
  if (blueprint && blueprint.toLowerCase() !== "compose") {
    sections += selectedFile(
      join(animationDir, "blueprints", `${blueprint}.md`),
      `Selected blueprint: ${blueprint}`,
    );
  }
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

export function buildDispatchDocs({ projectDir, frames, outDir, animationDir, cardPath, frameMdPath, maxDispatchBytes = 40000 }) {
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
  mkdirSync(outDir, { recursive: true });
  return frames.map((frame) => {
    const blueprint = field(frame.block, "blueprint");
    const blueprintSection = blueprint && blueprint.toLowerCase() !== "compose"
      ? selectedFile(join(animationDir, "blueprints", `${blueprint}.md`), `Selected blueprint: ${blueprint}`)
      : "";
    const rules = citedRules(frame.block, ruleIds);
    const ruleIndex = rules.length
      ? rules.map((r) => `- \`${r}\` → ${join(animationDir, "rules", `${r}.md`)}`).join("\n")
      : "- (no motion rules cited)";
    const doc = `# Dispatch: ${frame.frameId}

## Project inputs

- Project: ${resolve(projectDir)}
- RULES_DIR: ${join(animationDir, "rules")}

## Role (executable rule card)

${card}

## Assigned storyboard block

${frame.block}
${blueprintSection}
## Rule index (open a recipe file only while authoring its phase)

${ruleIndex}

## frame.md design tokens (excerpt)

${excerpt}
${designContract ? `\n## Design contract (video-wide)\n\n${designContract}\n` : ""}`;
    const bytes = Buffer.byteLength(doc);
    if (bytes > maxDispatchBytes)
      console.warn(`frame-packets: dispatch ${frame.frameId} is ${bytes} bytes (budget ${maxDispatchBytes})`);
    const path = join(outDir, `${frame.frameId}.dispatch.md`);
    writeFileSync(path, doc);
    return { frameId: frame.frameId, path, bytes };
  });
}

// Pre-built per-frame skeleton files: minimal valid `<template>` + #root attrs
// + marker comment, deterministic and selfcheck-clean ("last write wins" — the
// filler worker path rewrites them; packets are fair game to refresh).
export function buildSkeletons({ projectDir, frames, canvas }) {
  const framesDir = resolve(projectDir, "compositions", "frames");
  mkdirSync(framesDir, { recursive: true });
  const dims = canvas && canvas.width && canvas.height
    ? ` data-width="${canvas.width}" data-height="${canvas.height}"` : "";
  return frames.map((frame) => {
    const dur = field(frame.block, "duration") ?? field(frame.block, "durationSeconds");
    if (!dur) throw new Error(`${frame.frameId}: skeleton needs a - duration field on the storyboard block`);
    const skeleton = `<!-- skeleton-v1: fill style/body/script per .hyperframes/frame-packets/${frame.frameId}.dispatch.md — last write wins -->
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
