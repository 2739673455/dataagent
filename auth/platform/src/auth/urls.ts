import { AUTH_PROFILE_PATH, BASE_URL, ROUTE_PATHS } from "@/configs/settings";
import { joinUrl } from "@/auth-client";

// 当前应用的授权回调地址
export function buildAuthCallbackUrl(): string {
  const callbackUrl = new URL(
    joinUrl(BASE_URL, ROUTE_PATHS.authCallback),
    window.location.origin,
  );
  return callbackUrl.toString();
}

// 认证中心个人信息页面地址
export function buildAuthProfileUrl(redirectUri: string): string {
  const query = new URLSearchParams({
    redirect_uri: redirectUri,
  }).toString();
  return `${AUTH_PROFILE_PATH}?${query}`;
}
