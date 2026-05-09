import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  AuthErrorScreen,
  AuthLoadingScreen,
  clearAccessToken,
  clearAuthorizationRequest,
  handleAuthCallback,
  useAuthStore,
} from "@/features/auth";
import { ROUTE_PATHS } from "@/shared/config/settings";

export default function AuthCallbackPage() {
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const navigate = useNavigate();
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
        navigate(returnTo || ROUTE_PATHS.home, {
          replace: true,
        });
      } catch (error) {
        if (cancelled) return;
        clearAccessToken();
        clearAuthorizationRequest(state);
        clearAuth();
        const message = error instanceof Error ? error.message : "登录状态建立失败，请重试";
        setErrorMessage(message);
        toast.error(message);
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [clearAuth, navigate]);

  if (!errorMessage) {
    return <AuthLoadingScreen />;
  }

  return (
    <AuthErrorScreen
      title="登录状态建立失败"
      message={errorMessage}
      actionLabel="回到首页"
      onAction={() => navigate(ROUTE_PATHS.home, { replace: true })}
    />
  );
}
