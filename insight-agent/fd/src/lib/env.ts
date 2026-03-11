function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

export function getAppApiBaseUrl() {
  return trimTrailingSlash(
    import.meta.env.VITE_APP_API_BASE_URL || window.location.origin
  );
}

export function getAuthApiBaseUrl() {
  return trimTrailingSlash(
    import.meta.env.VITE_AUTH_API_BASE_URL || "http://127.0.0.1:7777"
  );
}

export function getAppWsBaseUrl() {
  const configured = import.meta.env.VITE_APP_WS_BASE_URL;
  if (configured) {
    return trimTrailingSlash(configured);
  }

  const url = new URL(getAppApiBaseUrl());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return trimTrailingSlash(url.toString());
}

export function getAuthClientId() {
  return import.meta.env.VITE_AUTH_CLIENT_ID || "insight-agent";
}

export function getAuthRedirectUri() {
  return `${window.location.origin}/auth/callback`;
}
