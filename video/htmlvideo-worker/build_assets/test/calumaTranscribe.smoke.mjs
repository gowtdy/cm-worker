/**
 * Smoke / unit checks for Caluma transcribe transport (no real mainserver).
 *
 * Usage (from build_assets, with server env stubs):
 *   MAIN_SERVER_HOST=127.0.0.1 MAIN_SERVER_PORT=1 \
 *   LOCAL_NGINX_HOST=127.0.0.1 LOCAL_NGINX_PORT=1 \
 *   node --experimental-strip-types test/calumaTranscribe.smoke.mjs
 */
import assert from "node:assert/strict";
import {
  buildTranscribeBody,
  calumaTranscribeJson,
  downloadTranscribeJson,
  flattenAsrWords,
} from "../util/caluma.mjs";
import { generateTranscribeSignature } from "../util/signatureUtil.ts";
import { config } from "../config/config.js";

const SAMPLE_ASR = {
  schema_version: "1.0",
  engine: "whisper",
  model: "large-v3-turbo",
  language: "en",
  language_probability: 1.0,
  duration: 3.78,
  text: "I am ready. I am ready. I am ready. Oh, I hear that monster.",
  segments: [
    {
      id: 0,
      start: 0.14,
      end: 3.22,
      text: "I am ready. I am ready. I am ready. Oh, I hear that monster.",
      words: [
        { word: "I", start: 0.14, end: 0.68, probability: 0.8642578125 },
        { word: "am", start: 0.68, end: 0.84, probability: 0.904296875 },
        { word: "ready.", start: 0.84, end: 1.1, probability: 0.9716796875 },
        { word: "I", start: 1.24, end: 1.34, probability: 0.97021484375 },
        { word: "am", start: 1.34, end: 1.5, probability: 0.9970703125 },
        { word: "ready.", start: 1.5, end: 1.76, probability: 0.98828125 },
        { word: "I", start: 1.88, end: 1.96, probability: 0.93212890625 },
        { word: "am", start: 1.96, end: 2.14, probability: 0.93701171875 },
        { word: "ready.", start: 2.14, end: 2.3, probability: 0.3212890625 },
        { word: "Oh,", start: 2.38, end: 2.48, probability: 0.97802734375 },
        { word: "I", start: 2.62, end: 2.62, probability: 0.998046875 },
        { word: "hear", start: 2.62, end: 2.7, probability: 0.9951171875 },
        { word: "that", start: 2.7, end: 2.86, probability: 0.99853515625 },
        { word: "monster.", start: 2.86, end: 3.22, probability: 0.99462890625 },
      ],
    },
  ],
};

const upurl = "/media/tts/sample.mp3";
const tstamp = 1788921430;
const body = buildTranscribeBody({ upurl, language: "en", tstamp });

assert.equal(body.upurl, upurl);
assert.equal(body.language, "en");
assert.equal(body.tstamp, tstamp);
assert.equal(body.email, config.transcribe.email);
assert.equal(body.userid, config.transcribe.userid);
assert.equal(body.subscript, config.transcribe.subscript);
assert.equal(body.snature, generateTranscribeSignature(upurl, tstamp));
assert.ok(!("enurl" in body) && !("reqid" in body));

const flat = flattenAsrWords(SAMPLE_ASR);
assert.ok(flat);
assert.equal(flat.length, 14);
assert.deepEqual(flat[0], { text: "I", start: 0.14, end: 0.68 });
assert.deepEqual(flat[13], { text: "monster.", start: 2.86, end: 3.22 });
assert.equal(flattenAsrWords({ segments: [] }), null);

const jsonUri = "/asr/out.json";
const fetchMock = async (url, opts) => {
  if (opts?.method === "POST") {
    assert.ok(String(url).endsWith(config.transcribeMethod));
    const sent = JSON.parse(opts.body);
    assert.equal(sent.upurl, upurl);
    assert.equal(sent.language, "zh");
    assert.ok(sent.snature);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        ret: 0,
        msg: "ok",
        uri: jsonUri,
        enurl: "encrypted",
        reqid: "123",
      }),
      text: async () => "",
    };
  }
  assert.ok(String(url).includes(jsonUri));
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(SAMPLE_ASR),
    json: async () => SAMPLE_ASR,
  };
};

const envelope = await calumaTranscribeJson(
  buildTranscribeBody({ upurl, language: "zh", tstamp }),
  { fetch: fetchMock },
);
assert.equal(envelope.ret, 0);
assert.equal(envelope.uri, jsonUri);
assert.equal(envelope.enurl, "encrypted");

const asr = await downloadTranscribeJson(envelope.uri, { fetch: fetchMock });
const words = flattenAsrWords(asr);
assert.equal(words.length, 14);

console.log(
  JSON.stringify(
    {
      ok: true,
      bodyKeys: Object.keys(body).sort(),
      wordCount: words.length,
      first: words[0],
      last: words[words.length - 1],
    },
    null,
    2,
  ),
);
