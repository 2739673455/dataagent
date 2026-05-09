import { getConfig } from "./config";
import { createCodeChallenge, createRandomBase64Url32, saveAuthorizationRequest } from "./oauth";
import { joinUrl } from "./url";

function buildAuthCallbackUrl(): string {
  const { baseUrl, authCallbackPath } = getConfig();
  const callbackUrl = new URL(joinUrl(baseUrl, authCallbackPath), window.location.origin);
  return callbackUrl.toString();
}

export async function buildAuthorizeUrl(returnTo?: string): Promise<string> {
  const config = getConfig();
  const state = createRandomBase64Url32();
  const codeVerifier = createRandomBase64Url32();
  const codeChallenge = await createCodeChallenge(codeVerifier);
  const redirectUri = buildAuthCallbackUrl();

  saveAuthorizationRequest({
    clientId: config.clientId,
    redirectUri,
    returnTo: returnTo ?? "/",
    state,
    codeVerifier,
  });

  const query = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  }).toString();
  const authorizePath = `${config.authApiPaths.authorize}?${query}`;
  return joinUrl(config.authApiBaseUrl, authorizePath);
}

export function buildAuthorizeApiUrlFromParams(params: URLSearchParams): string {
  const { authApiBaseUrl, authApiPaths } = getConfig();
  const authorizePath = `${authApiPaths.authorize}?${params.toString()}`;
  return joinUrl(authApiBaseUrl, authorizePath);
}
