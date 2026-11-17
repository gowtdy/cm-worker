// tts_aiflow.mjs — Caluma (internal genaudio) TTS for the aiflow audio engine.
// No native word timings → caller chains transcribeWav().

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
// Docker copies build_assets → /app (same pattern as scripts/frame-packets.mjs).
import {
  buildGenAudioBody,
  buildTranscribeBody,
  calumaTtsJson,
  calumaTranscribeJson,
  downloadAudio,
  downloadTranscribeJson,
  flattenAsrWords,
} from "/app/node_utility/caluma.mjs";
import { config as aiflowConfig } from "/app/config/config.js";

export { flattenAsrWords };

// aiflow path: always Caluma genaudio (internal TTS).
export function pickProvider(userProvider) {
  return "caluma";
}

// ── voice resolution ──────────────────────────────────────────────────────────
// Caluma voices are modelcat|modelname (e.g. english|voice-lady-female).
export async function resolveVoiceId({ provider, userVoice }) {
  if (userVoice) return userVoice;
  if (provider === "caluma") return aiflowConfig.tts.defaultVoice;
  throw new Error(`unsupported TTS provider "${provider}" — aiflow only supports caluma`);
}

// ── helpers ─────────────────────────────────────────────────────────────────
export function withWordIds(words) {
  return (words ?? []).map((w, i) => ({
    id: `w${i}`,
    text: w.text,
    start: w.start,
    end: w.end,
  }));
}

// `ffmpeg -i <file>` prints a `Duration: HH:MM:SS.ms` line to stderr even
// though it exits non-zero with no output requested. Parsing pulled out as
// a pure function so the ENOENT fallback below can be tested without
// depending on whether ffprobe/ffmpeg are actually installed on the
// machine running the tests.
export function parseFfmpegDurationBanner(stderrText) {
  const match = /Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)/.exec(stderrText ?? "");
  if (!match) return NaN;
  const [, hours, minutes, seconds] = match;
  return Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds);
}

// Some "essentials"-style ffmpeg distributions (common on Windows) ship
// ffmpeg.exe without ffprobe.exe. ffprobeDuration's caller (audio.mjs)
// otherwise reads a spurious NaN as "the WAV file is corrupt" and drops an
// already-successfully-synthesized TTS line, rather than "the tool for
// measuring it is missing".
function ffmpegDurationFallback(absPath) {
  const r = spawnSync("ffmpeg", ["-i", absPath], { encoding: "utf8" });
  return parseFfmpegDurationBanner(r.stderr);
}

export function ffprobeDuration(absPath) {
  const r = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", absPath],
    { encoding: "utf8" },
  );
  if (r.error?.code === "ENOENT") return ffmpegDurationFallback(absPath);
  if (r.status !== 0) return NaN;
  return parseFloat(String(r.stdout).trim());
}

export function resolveNpxCliFromNpmExecPath(
  npmExecPath = process.env.npm_execpath,
  pathExists = existsSync,
) {
  if (!npmExecPath) return null;
  const fileName = npmExecPath.replace(/\\/g, "/").split("/").pop()?.toLowerCase();
  const npxCliPath =
    fileName === "npx-cli.js" ? npmExecPath : join(dirname(npmExecPath), "npx-cli.js");
  return pathExists(npxCliPath) ? npxCliPath : null;
}

export function resolveNpxCliPath(
  npmExecPath = process.env.npm_execpath,
  nodeExecPath = process.env.npm_node_execpath || process.execPath,
  pathExists = existsSync,
) {
  const fromNpm = resolveNpxCliFromNpmExecPath(npmExecPath, pathExists);
  if (fromNpm) return fromNpm;
  const besideNode = join(dirname(nodeExecPath), "node_modules", "npm", "bin", "npx-cli.js");
  return pathExists(besideNode) ? besideNode : null;
}

export function resolveSpawnCommand(
  cmd,
  args,
  opts = {},
  platform = process.platform,
  env = process.env,
  pathExists = existsSync,
) {
  if (cmd !== "npx" || platform !== "win32") {
    return { cmd, args, opts: { stdio: "ignore", ...opts } };
  }

  // On Windows, npx resolves to npx.cmd, which Node cannot execute directly.
  // Avoid `shell:true` and the .cmd shim entirely by invoking npm's JS CLI with
  // node, preserving request-provided values as argv data instead of shell text.
  const nodeExecPath = env.npm_node_execpath || process.execPath;
  const npxCliPath = resolveNpxCliPath(env.npm_execpath, nodeExecPath, pathExists);
  if (!npxCliPath) return null;
  return {
    cmd: nodeExecPath,
    args: [npxCliPath, ...args.map((arg) => String(arg))],
    opts: { stdio: "ignore", windowsHide: true, ...opts },
  };
}

// `platform`/`spawnFn` params (default process.platform / the real spawn)
// exist so tests can exercise the win32 branch without mocking node:child_process
// (its ESM exports are non-configurable, so mock.method can't patch it).
// One-shot so a whole batch of TTS lines doesn't repeat the same diagnostic.
let _warnedNpxResolution = false;
/** Test-only: reset the one-shot npx-resolution warning latch. */
export function _resetNpxResolutionWarnForTests() {
  _warnedNpxResolution = false;
}

export function spawnP(
  cmd,
  args,
  opts = {},
  platform = process.platform,
  spawnFn = spawn,
  env = process.env,
  pathExists = existsSync,
) {
  const resolved = resolveSpawnCommand(cmd, args, opts, platform, env, pathExists);
  if (!resolved) {
    // resolveSpawnCommand only returns null for the npx-on-win32 case where
    // neither npm's configured CLI nor the beside-node fallback exists. Without
    // this, every call silently returns status:-1 and stdio:"ignore" hides why.
    if (!_warnedNpxResolution) {
      _warnedNpxResolution = true;
      const reason = env.npm_execpath
        ? `npm_execpath (${env.npm_execpath}) and the beside-node npm fallback could not be found`
        : "npm_execpath is unset and the beside-node npm fallback could not be found";
      console.error(
        `[media-use] Cannot run "${cmd}" on Windows: ${reason}. ` +
          `Every "${cmd}" call is being skipped. Install npm with Node, or run via ` +
          `\`npx\`/\`npm run\` with a valid npm_execpath.`,
      );
    }
    return Promise.resolve({ status: -1 });
  }
  return new Promise((resolve) => {
    const p = spawnFn(resolved.cmd, resolved.args, resolved.opts);
    p.on("exit", (code) => resolve({ status: code ?? -1 }));
    p.on("error", () => resolve({ status: -1 }));
  });
}

// ── synthesize one line ───────────────────────────────────────────────────────
// Writes audio at wavAbs. Returns { ok, words, error } — words is always null
// for Caluma (caller must transcribeWav). Never throws; failures return
// { ok:false, error } where `error` states WHY.
export async function synthesizeOne({
  provider,
  text,
  voiceId,
  lang = "en",
  speed = 1.0,
  wavAbs,
  userid,
}) {
  if (provider === "caluma") return synthesizeCalumaTts({ text, voiceId, wavAbs, lang, userid });

  return { ok: false, words: null, error: `unsupported provider "${provider}"` };
}

// Shape a spawn result into { ok, words, error }, naming why on failure so the
// caller surfaces it instead of a bare "TTS failed".
export function synthResult(r, wavAbs, label) {
  if (r.status === 0 && existsSync(wavAbs)) return { ok: true, words: null };
  const why =
    r.status !== 0 ? `${label} exited with status ${r.status}` : `${label} produced no wav file`;
  return { ok: false, words: null, error: why };
}

// Caluma genaudio: POST → download mp3 at host+uri → file.
// .wav path → ffmpeg 44.1k mono; otherwise raw bytes (e.g. .mp3).
// No native word timings (caller chains transcribeWav with returned uri).
// `deps` injectable for tests.
export async function synthesizeCalumaTts({ text, voiceId, wavAbs, lang, userid }, deps = {}) {
  const requestJSON = calumaTtsJson;
  const download = downloadAudio;
  const buildBody = buildGenAudioBody;
  try {
    const body = buildBody({ text, voiceId, lang, userid });
    const payload = await requestJSON(body, deps);
    const bytes = await download(payload.uri, deps);
    mkdirSync(dirname(wavAbs), { recursive: true });
    writeFileSync(wavAbs, bytes);
    return { ok: true, words: null, uri: payload.uri };
  } catch (e) {
    return { ok: false, words: null, error: e?.message ? String(e.message) : String(e) };
  }
}

// Caluma /api/transcribe: POST upurl → download JSON at host+uri → flatten words.
// Never throws; failures return { ok:false, words:null, error }.
export async function transcribeCaluma({ upurl, lang = "en", userid, asrJsonAbs }, deps = {}) {
  const buildBody = buildTranscribeBody;
  const requestJSON = calumaTranscribeJson;
  const download = downloadTranscribeJson;
  try {
    if (!upurl) return { ok: false, words: null, error: "transcribeCaluma: upurl is required" };
    console.error(`· tts: caluma transcribe · upurl ${upurl} · lang ${lang}`);
    const body = buildBody({ upurl, language: lang, userid });
    const payload = await requestJSON(body, deps);
    console.error(
      `· tts: caluma transcribe payload · ret=${payload?.ret ?? "?"} · uri=${payload?.uri ?? "(none)"}` +
        (payload?.msg != null ? ` · msg=${payload.msg}` : "") +
        (payload?.enurl != null ? ` · enurl=${payload.enurl}` : ""),
    );
    const asr = await download(payload.uri, deps);
    if (asrJsonAbs) {
      mkdirSync(dirname(asrJsonAbs), { recursive: true });
      writeFileSync(asrJsonAbs, JSON.stringify(asr, null, 2));
      console.error(`· tts: caluma transcribe wrote ${asrJsonAbs}`);
    }
    const words = flattenAsrWords(asr);
    if (!words) return { ok: false, words: null, error: "transcribe: no words in ASR JSON" };
    console.error(`· tts: caluma transcribe ok · ${words.length} word(s)`);
    return { ok: true, words };
  } catch (e) {
    const err = e?.message ? String(e.message) : String(e);
    console.error(`· tts: caluma transcribe failed — ${err}`);
    return { ok: false, words: null, error: err };
  }
}

// Caluma remote transcribe via upurl (TTS audio uri). Returns flat
// [{text,start,end}] or null.
export async function transcribeWav({ upurl, lang = "en", userid, asrJsonAbs }) {
  if (!upurl) return null;
  const r = await transcribeCaluma({ upurl, lang, userid, asrJsonAbs });
  return r.ok ? r.words : null;
}
