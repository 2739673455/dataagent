import {
  buildAuthorizeUrl,
  clearAccessToken,
  useAuthStore,
} from "@/auth-client";

// 统一处理 401 未授权响应，清理状态后重新发起登录
export function handleUnauthorizedError(error: unknown): boolean {
  if (
    (error as { response?: { status?: number } } | undefined)?.response
      ?.status !== 401
  ) {
    return false;
  }

  clearAccessToken();
  useAuthStore.getState().clearAuth();
  void buildAuthorizeUrl(
    `${window.location.pathname}${window.location.search}`,
  ).then((url) => window.location.replace(url));
  return true;
}
