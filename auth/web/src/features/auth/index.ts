import { configureClient } from "@/features/auth-client";
import {
  ACCESS_TOKEN_STORAGE_KEY,
  AUTH_API_BASE_URL,
  AUTH_API_PATHS,
  BASE_URL,
  CLIENT_ID,
  ROUTE_PATHS,
} from "@/shared/config/settings";

configureClient({
  clientId: CLIENT_ID,
  authApiBaseUrl: AUTH_API_BASE_URL,
  authApiPaths: AUTH_API_PATHS,
  baseUrl: BASE_URL,
  authCallbackPath: ROUTE_PATHS.authCallback,
  storagePrefix: "auth-request:",
  tokenStorageKey: ACCESS_TOKEN_STORAGE_KEY,
});

export {
  AuthErrorScreen,
  AuthLoadingScreen,
  buildAuthorizeApiUrlFromParams,
  buildAuthorizeUrl,
  checkAuth,
  clearAccessToken,
  clearAuthorizationRequest,
  getAccessToken,
  handleAuthCallback,
  handleUnauthorizedError,
  useAuthStore,
} from "@/features/auth-client";
export { ACCESS_TOKEN_STORAGE_KEY } from "@/shared/config/settings";

export { GuestOnlyRoute, RequireAuth } from "./guards";
