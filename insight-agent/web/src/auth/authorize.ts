import { authApi } from "@/auth/api";
import { getConfig } from "@/auth/config";
import {
  clearAuthorizationRequest,
  createCodeChallenge,
  createRandomBase64Url32,
  loadAuthorizationRequest,
  saveAuthorizationRequest,
} from "@/auth/oauth";
import { useAuthStore } from "@/auth/store";
import { clearAccessToken, getAccessToken, setAccessToken } from "@/auth/token";

// 构造 OAuth 回调地址：认证中心授权后回跳当前应用
function buildAuthCallbackUrl(): string {
  const { baseUrl, authCallbackPath } = getConfig();
  return new URL(
    `${baseUrl.replace(/\/+$/, "")}${authCallbackPath}`,
    window.location.origin
  ).toString();
}

// 构造授权入口 URL，浏览器跳转到认证中心授权页
export async function buildAuthorizeUrl(returnTo?: string): Promise<string> {
  const { clientId, authApiBaseUrl, authApiPaths } = getConfig();
  const state = createRandomBase64Url32();
  const codeVerifier = createRandomBase64Url32();
  const codeChallenge = await createCodeChallenge(codeVerifier);
  const redirectUri = buildAuthCallbackUrl();

  saveAuthorizationRequest({
    clientId,
    redirectUri,
    returnTo: returnTo ?? "/",
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
  const base = authApiBaseUrl.replace(/\/$/, "");
  return `${base}${authApiPaths.authorize}?${query}`;
}

// 用授权码 + state 完成令牌交换并恢复登录态，返回业务回跳地址
export async function handleAuthCallback(code: string, state: string): Promise<string> {
  const authRequest = loadAuthorizationRequest(state);
  if (!authRequest) {
    throw new Error("授权请求已失效，请重新登录");
  }
  if (!authRequest.codeVerifier) {
    throw new Error("授权请求参数不完整，请重新登录");
  }

  const tokenResponse = await authApi.exchangeToken(
    code,
    authRequest.redirectUri,
    authRequest.codeVerifier
  );
  const token = tokenResponse.data.access_token;

  const introspectionResponse = await authApi.introspect(token);
  if (!introspectionResponse.data.active) {
    throw new Error("访问令牌无效");
  }

  const userResponse = await authApi.getMe(token);
  setAccessToken(token);
  useAuthStore.getState().setAuth(userResponse.data, introspectionResponse.data.scope ?? []);
  clearAuthorizationRequest(state);

  return authRequest.returnTo;
}

// 构造认证中心个人中心地址
export function buildAuthProfileRedirectUrl(redirectUri: string): string {
  const { authWebBaseUrl } = getConfig();
  const base = authWebBaseUrl.replace(/\/$/, "");
  return `${base}/profile?${new URLSearchParams({
    redirect_uri: redirectUri,
  }).toString()}`;
}

// 根据本地 access token 检查并恢复登录态
export async function checkAuth(): Promise<void> {
  const authStore = useAuthStore.getState();
  const token = getAccessToken();
  if (!token) {
    authStore.clearAuth();
    return;
  }

  try {
    const introspectionResponse = await authApi.introspect(token);
    if (!introspectionResponse.data.active) {
      clearAccessToken();
      authStore.clearAuth();
      return;
    }
    const userResponse = await authApi.getMe(token);
    const scope = introspectionResponse.data.scope ?? [];
    authStore.setAuth(userResponse.data, scope);
  } catch {
    clearAccessToken();
    authStore.clearAuth();
  }
}
