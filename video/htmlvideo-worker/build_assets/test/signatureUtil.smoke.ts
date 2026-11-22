/**
 * Print HMAC signatures for a given text + timestamp.
 *
 * Usage:
 *   node --experimental-strip-types test/signatureUtil.smoke.ts
 *   node --experimental-strip-types test/signatureUtil.smoke.ts "你的文案" 1788921430
 */
import {
  generateTtsSignature,
  generateSoundSignature,
  generateTranscribeSignature,
} from "../util/signatureUtil.ts";

const DEFAULT_TEXT =
  "选股票这件事，多少人靠眼缘就下手了？看一眼K线，像相亲看照片——嗯，就它了。";
const DEFAULT_TSTAMP = 1788921430;

const text = process.argv[2] ?? DEFAULT_TEXT;
const tstamp = process.argv[3]
  ? Number(process.argv[3])
  : DEFAULT_TSTAMP;

if (Number.isNaN(tstamp)) {
  console.error("usage: signatureUtil.smoke.ts [text] [tstamp]");
  console.error("tstamp must be a unix seconds number");
  process.exit(1);
}

const textHex = Buffer.from(text, "utf8").toString("hex");
const signString = `text=${textHex}&timestamp=${tstamp}`;

console.log(
  JSON.stringify(
    {
      text,
      tstamp,
      textHex,
      signString,
      signatures: {
        tts: generateTtsSignature(text, tstamp),
        sound: generateSoundSignature(text, tstamp),
        transcribe: generateTranscribeSignature(text, tstamp),
      },
    },
    null,
    2,
  ),
);
