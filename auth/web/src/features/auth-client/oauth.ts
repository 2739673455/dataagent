import { getConfig } from "./config";
import type { PendingAuthorizationRequest } from "./types";

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function createRandomBase64Url32(): string {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

export async function createCodeChallenge(codeVerifier: string): Promise<string> {
  const digest = await window.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(codeVerifier)
  );
  return base64UrlEncode(new Uint8Array(digest));
}

function authRequestKey(state: string): string {
  return `${getConfig().storagePrefix}${state}`;
}

export function saveAuthorizationRequest(request: PendingAuthorizationRequest): void {
  window.sessionStorage.setItem(authRequestKey(request.state), JSON.stringify(request));
}

export function loadAuthorizationRequest(state: string): PendingAuthorizationRequest | null {
  const raw = window.sessionStorage.getItem(authRequestKey(state));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as PendingAuthorizationRequest;
  } catch {
    return null;
  }
}

export function clearAuthorizationRequest(state: string): void {
  window.sessionStorage.removeItem(authRequestKey(state));
}
