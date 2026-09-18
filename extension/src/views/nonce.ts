import { randomBytes } from "crypto";

/**
 * Stamp a fresh CSP nonce into a webview's HTML.
 *
 * The content security policy allows scripts only with this nonce, so injected
 * markup cannot execute even if something slips past output escaping.
 */
export function withNonce(html: string): string {
  const nonce = randomBytes(16).toString("base64");
  return html.split("__NONCE__").join(nonce);
}
