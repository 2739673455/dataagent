import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  handleAuthCallback,
  AuthErrorScreen,
  AuthLoadingScreen,
  clearAuthorizationRequest,
  clearAccessToken,
  useAuthStore,
  joinUrl,
} from "@/auth-client";
import { BASE_URL, ROUTE_PATHS } from "@/configs/settings";

export default function AuthCallbackPage() {
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      const searchParams = new URLSearchParams(window.location.search);
      const code = searchParams.get("code");
      const state = searchParams.get("state");

      if (!code) {
        clearAccessToken();
        toast.error("缺少授权码");
        setErrorMessage("缺少授权码，请返回首页后重试登录");
        return;
      }
      if (!state) {
        clearAccessToken();
        toast.error("缺少授权状态");
        setErrorMessage("缺少授权状态，请返回首页后重试登录");
        return;
      }

      try {
        const returnTo = await handleAuthCallback(code, state);
        if (cancelled) return;
        window.location.replace(
          returnTo || joinUrl(BASE_URL, ROUTE_PATHS.home),
        );
      } catch (error) {
        if (cancelled) return;
        clearAccessToken();
        clearAuthorizationRequest(state);
        clearAuth();
        const message =
          error instanceof Error ? error.message : "登录状态建立失败，请重试";
        setErrorMessage(message);
        toast.error(message);
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [clearAuth]);

  if (!errorMessage) {
    return <AuthLoadingScreen />;
  }

  return (
    <AuthErrorScreen
      title="登录状态建立失败"
      message={errorMessage}
      actionLabel="回到首页"
      onAction={() =>
        window.location.replace(joinUrl(BASE_URL, ROUTE_PATHS.home))
      }
    />
  );
}
