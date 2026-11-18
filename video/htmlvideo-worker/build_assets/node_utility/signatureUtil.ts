import { createHmac } from "node:crypto";
import { config } from "../config/config.js";

function hmacHex(text: string, timestamp: number, secretKey: string): string {
  const signString = `text=${Buffer.from(text, "utf8").toString("hex")}&timestamp=${timestamp}`;
  return createHmac("sha256", secretKey).update(signString).digest("hex");
}

/** Generate signature for TTS requests. */
export function generateTtsSignature(text: string, timestamp: number): string {
  return hmacHex(text, timestamp, config.signature.ttsSecretKey);
}

/** Generate signature for sound / TTS requests. */
export function generateSoundSignature(text: string, timestamp: number): string {
  return hmacHex(text, timestamp, config.signature.soundSecretKey);
}

/** Generate signature for transcribe requests. */
export function generateTranscribeSignature(text: string, timestamp: number): string {
  return hmacHex(text, timestamp, config.signature.transcribeSecretKey);
}

/**
 * Hook-style wrapper kept for callers that expect useSignature().
 */
export const useSignature = () => ({
  generateTtsSignature,
  generateSoundSignature,
  generateTranscribeSignature,
});
