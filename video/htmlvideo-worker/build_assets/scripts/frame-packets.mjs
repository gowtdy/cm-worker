#!/usr/bin/env node

// Thin wrapper over the shared packet builder in hyperframes-core — this file only
// pins the paths that are specific to this workflow skill. The logic (frame
// splitting, rule citation, packet bounds, `_role.md` assembly) has one owner:
// ../../hyperframes-core/scripts/lib/frame-packets-core.mjs

import { resolve } from "node:path";
import * as core from "/app/hyperframes/skills/hyperframes-core/scripts/lib/frame-packets-core.mjs";
// gowtd-mod: frame-packets paths
const SKILL_DIR = "/app/hyperframes/skills/aiflow-build-frame-html";
const CONFIG = {
  animationDir: resolve(SKILL_DIR, "../hyperframes-animation"),
  corePath: resolve(SKILL_DIR, "../hyperframes-core/references/frame-worker-core.md"),
  deltaPath: resolve(SKILL_DIR, "sub-agents/frame-worker.md"),
};

export function buildRolePayload({ outDir }) {
  return core.buildRolePayload({ ...CONFIG, outDir });
}

export function buildFramePackets(options) {
  return core.buildFramePackets({ ...CONFIG, ...options });
}

if (core.isMainModule(import.meta.url)) core.runCli({ buildFramePackets, buildRolePayload });
