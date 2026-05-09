import { configureClient } from "@/auth/config";
import {
  ACCESS_TOKEN_STORAGE_KEY,
  AUTH_API_BASE_URL,
  AUTH_API_PATHS,
  AUTH_CALLBACK_ROUTE,
  AUTH_REQUEST_PREFIX,
  AUTH_WEB_BASE_URL,
  BASE_URL,
  CLIENT_ID,
} from "@/config/settings";

configureClient({
  clientId: CLIENT_ID,
  authApiBaseUrl: AUTH_API_BASE_URL,
  authApiPaths: {
    authorize: AUTH_API_PATHS.authorize,
    token: AUTH_API_PATHS.token,
    introspection: AUTH_API_PATHS.introspection,
    logout: AUTH_API_PATHS.logout,
    userinfo: AUTH_API_PATHS.me,
  },
  baseUrl: BASE_URL,
  authCallbackPath: AUTH_CALLBACK_ROUTE,
  authWebBaseUrl: AUTH_WEB_BASE_URL,
  storagePrefix: AUTH_REQUEST_PREFIX,
  tokenStorageKey: ACCESS_TOKEN_STORAGE_KEY,
});

export { default as AuthCallbackPage } from "@/auth/AuthCallbackPage";
// Components
export { AuthLoadingScreen } from "@/auth/AuthLoadingScreen";
// API client (for logout etc.)
export { authApi } from "@/auth/api";
// Auth flow
export {
  buildAuthorizeUrl,
  buildAuthProfileRedirectUrl,
  checkAuth,
  handleAuthCallback,
} from "@/auth/authorize";
// Error handling
export { handleUnauthorizedError } from "@/auth/errors";
// Guards
export { ProtectedRoute } from "@/auth/guards";
// Store
export { useAuthStore } from "@/auth/store";
// Token management
export { clearAccessToken, getAccessToken } from "@/auth/token";
// Types
export type { UserResponse } from "@/auth/types";
// Constants (re-exported from settings)
export {
  ACCESS_TOKEN_STORAGE_KEY,
  AUTH_CALLBACK_ROUTE,
} from "@/config/settings";
