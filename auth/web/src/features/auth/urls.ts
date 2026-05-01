import { AUTH_API_BASE_URL, AUTH_API_PATHS, BASE_URL, ROUTE_PATHS } from "@/shared/config/settings";
import { joinUrl } from "@/shared/libs/url";
import { createCodeChallenge, createRandomBase64Url32, saveAuthorizationRequest } from "./oauth";

// 当前应用的授权回调地址
function buildAuthCallbackUrl(): string {
  const callbackUrl = new URL(joinUrl(BASE_URL, ROUTE_PATHS.authCallback), window.location.origin);
  return callbackUrl.toString();
}

// 认证中心授权地址
export async function buildAuthorizeApiUrl(clientId: string, returnTo: string): Promise<string> {
  const state = createRandomBase64Url32();
  const codeVerifier = createRandomBase64Url32();
  const codeChallenge = await createCodeChallenge(codeVerifier);
  const redirectUri = buildAuthCallbackUrl();

  saveAuthorizationRequest({
    clientId,
    redirectUri,
    returnTo,
    state,
    codeVerifier,
  });

  const query = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  }).toString();
  const authorizePath = `${AUTH_API_PATHS.authorize}?${query}`;
  return joinUrl(AUTH_API_BASE_URL, authorizePath);
}

export function buildAuthorizeApiUrlFromParams(params: URLSearchParams): string {
  const authorizePath = `${AUTH_API_PATHS.authorize}?${params.toString()}`;
  return joinUrl(AUTH_API_BASE_URL, authorizePath);
}
