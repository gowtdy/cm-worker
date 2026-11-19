#!/usr/bin/env node

// Thin wrapper over the shared packet builder in hyperframes-core — this file only
// pins the paths that are specific to this workflow skill. The logic (frame
// splitting, rule citation, packet bounds, `_role.md` assembly) has one owner:
// ../../hyperframes-core/scripts/lib/frame-packets-core.mjs

import { resolve } from "node:path";
import { readFileSync } from "node:fs";
import * as core from "/app/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs";
import { parseFormat } from "./lib/dimensions.mjs";

// gowtd-mod: frame-packets paths
const SKILL_DIR = "/app/skills/aiflow-build-frame-html";
const CONFIG = {
  animationDir: resolve(SKILL_DIR, "../hyperframes-animation"),
  corePath: resolve(SKILL_DIR, "../hyperframes-core/references/frame-worker-core.md"),
  deltaPath: resolve(SKILL_DIR, "sub-agents/frame-worker.md"),
  cardPath: resolve(SKILL_DIR, "sub-agents/frame-worker-card.md"),
};

function switchOn(name, defaultOn = true) {
  const raw = (process.env[name] ?? "").trim().toLowerCase();
  if (!raw) return defaultOn;
  return !(raw === "0" || raw === "false" || raw === "off");
}
function dispatchVerOn() {
  const raw = (process.env["HTMLVIDEO_FRAME_PACKETS_DISPATCH_VER"] ?? "").trim();
  return raw !== "0" && raw !== "1"; // 0/1 = pinned legacy (rollback)
}

export function buildRolePayload({ outDir }) { return core.buildRolePayload({ ...CONFIG, outDir }); }
export function buildFramePackets(options) { return core.buildFramePackets({ ...CONFIG, ...options }); }

function buildExtras({ projectDir, storyboardPath, outDir, frames }) {
  if (dispatchVerOn()) {
    const docs = core.buildDispatchDocs({
      projectDir, frames, outDir, animationDir: CONFIG.animationDir,
      cardPath: CONFIG.cardPath, frameMdPath: resolve(projectDir, "frame.md"),
    });
    console.log(`✓ dispatch docs: ${docs.length} merged document(s)`);
  }
  if (switchOn("HTMLVIDEO_FRAME_HTML_SKELETON")) {
    const storyboard = readFileSync(storyboardPath, "utf8");
    const m = /^\s*-?\s*format:\s*(.+)$/m.exec(storyboard);
    const skeletons = core.buildSkeletons({
      projectDir, frames, canvas: m ? parseFormat(m[1]) : undefined,
    });
    console.log(`✓ skeletons: ${skeletons.length} pre-built frame file(s)`);
  }
}

if (core.isMainModule(import.meta.url)) core.runCli({ buildFramePackets, buildExtras });
