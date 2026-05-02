import { configureClient } from "@/auth-client";
import {
  ACCESS_TOKEN_STORAGE_KEY,
  AUTH_API_BASE_URL,
  AUTH_API_PATHS,
  BASE_URL,
  CLIENT_ID,
  ROUTE_PATHS,
} from "@/configs/settings";

configureClient({
  clientId: CLIENT_ID,
  authApiBaseUrl: AUTH_API_BASE_URL,
  authApiPaths: AUTH_API_PATHS,
  baseUrl: BASE_URL,
  authCallbackPath: ROUTE_PATHS.authCallback,
  storagePrefix: "oidc:auth-request:",
  tokenStorageKey: ACCESS_TOKEN_STORAGE_KEY,
});

export { ACCESS_TOKEN_STORAGE_KEY } from "@/configs/settings";
export { handleUnauthorizedError } from "./authorize";
export { RequireAuth } from "./guards";

// Re-exports from shared auth client
export {
  checkAuth,
  handleAuthCallback,
  buildAuthorizeUrl,
  getAccessToken,
  clearAccessToken,
  useAuthStore,
  AuthLoadingScreen,
  AuthErrorScreen,
} from "@/auth-client";

// Platform-specific exports
export { buildAuthCallbackUrl, buildAuthProfileUrl } from "./urls";
