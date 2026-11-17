// caluma.mjs — Caluma / internal genaudio TTS + transcribe transport for aiflow.
// Requires the process to be started with `node --experimental-strip-types`
// so `./signatureUtil.ts` can be imported.

import { generateTtsSignature, generateTranscribeSignature } from "./signatureUtil.ts";
import { config } from "../config/config.js";

export function parseCalumaVoice(voiceId) {
  const raw = String(voiceId || config.tts.defaultVoice || "english|voice-lady-female");
  const [modelcat, modelname] = raw.split("|");
  if (!modelcat || !modelname) {
    throw new Error(
      `invalid caluma voice "${raw}" — expected modelcat|modelname (e.g. english|voice-lady-female)`,
    );
  }
  return { modelcat, modelname };
}

/** Unix timestamp in seconds (UTC). */
function utcTimestampSeconds() {
  return Math.floor(Date.now() / 1000);
}

export function buildGenAudioBody({ text, voiceId, lang, userid = config.tts.userid, tstamp = utcTimestampSeconds() }) {
  const { modelcat, modelname } = parseCalumaVoice(voiceId);
  return {
    domain: config.tts.domain,
    email: config.tts.email,
    modelcat,
    modelname,
    snature: generateTtsSignature(text, tstamp),
    subscript: config.tts.subscript,
    t: config.tts.t,
    text,
    tstamp,
    modellang: lang, // modellang 这个名字继承来的，有些迷惑，这里用于用户指定的语言
    userid: userid,
  };
}

/** POST /api/genaudio; throws on HTTP or ret !== 0. */
export async function calumaTtsJson(body, deps = {}) {
  const fetchImpl = deps.fetch ?? fetch;
  const url = `${config["mainserver-address"]}${config.ttsMethod}`;
  const res = await fetchImpl(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Referer: "caluma.ai",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `Caluma POST ${config.ttsMethod} → HTTP ${res.status}${detail ? `\n${detail.slice(0, 300)}` : ""}`,
    );
  }
  const payload = await res.json();
  if (payload?.ret !== 0) {
    throw new Error(
      `Caluma genaudio ret=${payload?.ret ?? "?"} msg=${payload?.msg ?? "(none)"}`,
    );
  }
  if (!payload.uri) {
    throw new Error("Caluma genaudio returned no uri");
  }
  return payload;
}

/** GET ${local-nginx-address}${uri} → Buffer. */
export async function downloadAudio(uri, deps = {}) {
  const fetchImpl = deps.fetch ?? fetch;
  const url = `${config["local-nginx-address"]}${uri}`;
  const res = await fetchImpl(url);
  if (!res.ok) {
    throw new Error(`Caluma audio download HTTP ${res.status}: ${url.slice(0, 120)}`);
  }
  return Buffer.from(await res.arrayBuffer());
}

/** Build POST body for /api/transcribe. HMAC text = upurl. */
export function buildTranscribeBody({ upurl, language = "en", userid = config.transcribe.userid, tstamp = utcTimestampSeconds() }) {
  if (!upurl) throw new Error("buildTranscribeBody: upurl is required");
  return {
    email: config.transcribe.email,
    snature: generateTranscribeSignature(upurl, tstamp),
    subscript: config.transcribe.subscript,
    tstamp,
    upurl,
    language,
    userid: userid,
  };
}

/** POST /api/transcribe; throws on HTTP or ret !== 0. Returns envelope with uri. */
export async function calumaTranscribeJson(body, deps = {}) {
  const fetchImpl = deps.fetch ?? fetch;
  const url = `${config["mainserver-address"]}${config.transcribeMethod}`;
  const res = await fetchImpl(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Referer: "caluma.ai",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `Caluma POST ${config.transcribeMethod} → HTTP ${res.status}${detail ? `\n${detail.slice(0, 300)}` : ""}`,
    );
  }
  const payload = await res.json();
  if (payload?.ret !== 0) {
    throw new Error(
      `Caluma transcribe ret=${payload?.ret ?? "?"} msg=${payload?.msg ?? "(none)"}`,
    );
  }
  if (!payload.uri) {
    throw new Error("Caluma transcribe returned no uri");
  }
  return payload;
}

/** GET ${local-nginx-address}${uri} → parsed JSON object. */
export async function downloadTranscribeJson(uri, deps = {}) {
  const fetchImpl = deps.fetch ?? fetch;
  const url = `${config["local-nginx-address"]}${uri}`;
  const res = await fetchImpl(url);
  if (!res.ok) {
    throw new Error(`Caluma JSON download HTTP ${res.status}: ${url.slice(0, 120)}`);
  }
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(
      `Caluma JSON download: invalid JSON at ${url.slice(0, 120)}: ${e?.message ?? e}`,
    );
  }
}

/** Flatten ASR JSON segments[].words[] → [{ text, start, end }]. Engine-agnostic. */
export function flattenAsrWords(asr) {
  const segments = asr?.segments;
  if (!Array.isArray(segments)) return null;
  const words = [];
  for (const seg of segments) {
    if (!Array.isArray(seg?.words)) continue;
    for (const w of seg.words) {
      if (w?.word == null || w.start == null || w.end == null) continue;
      words.push({ text: String(w.word), start: Number(w.start), end: Number(w.end) });
    }
  }
  return words.length ? words : null;
}
