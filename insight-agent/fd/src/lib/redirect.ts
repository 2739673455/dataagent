import {
  getAuthApiBaseUrl,
  getAuthClientId,
  getAuthRedirectUri,
} from "@/lib/env";

const RETURN_URI_STORAGE_KEY = "insight-agent:return-uri";

export function getStoredReturnUri() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(RETURN_URI_STORAGE_KEY);
}

export function setStoredReturnUri(value: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(RETURN_URI_STORAGE_KEY, value);
}

export function clearStoredReturnUri() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(RETURN_URI_STORAGE_KEY);
}

export function buildAuthorizeUrl() {
  const url = new URL("/api/authorize", getAuthApiBaseUrl());
  url.searchParams.set("client_id", getAuthClientId());
  url.searchParams.set("redirect_uri", getAuthRedirectUri());
  return url.toString();
}

export function redirectToAuthorize(returnTo?: string) {
  const current =
    returnTo || `${window.location.pathname}${window.location.search}`;
  setStoredReturnUri(current);
  window.location.assign(buildAuthorizeUrl());
}
