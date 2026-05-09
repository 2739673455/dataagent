import { buildAuthorizeUrl } from "@/auth/authorize";
import { useAuthStore } from "@/auth/store";
import { clearAccessToken } from "@/auth/token";

// 统一处理 401 未授权错误，返回值表示当前错误是否已按未授权场景处理
export function handleUnauthorizedError(error: unknown): boolean {
  // 非 401 错误不在这里处理，交由调用方继续处理
  if ((error as { response?: { status?: number } } | undefined)?.response?.status !== 401) {
    return false;
  }

  // access token 已失效时，清理本地登录态并重新进入授权流程
  const authStore = useAuthStore.getState();
  clearAccessToken();
  authStore.clearAuth();
  void buildAuthorizeUrl(`${window.location.pathname}${window.location.search}`).then((url) =>
    window.location.replace(url)
  );
  return true;
}
