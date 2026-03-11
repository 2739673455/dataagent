import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { authApi } from "@/api/auth";
import { userApi } from "@/api/user";
import {
  clearStoredReturnUri,
  getStoredReturnUri,
} from "@/lib/redirect";
import { setAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";

export default function AuthCallback() {
  const navigate = useNavigate();
  const login = useAuthStore((state) => state.login);
  const clearAuth = useAuthStore((state) => state.clearAuth);

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");
      if (!code) {
        toast.error("缺少授权码");
        navigate("/login", { replace: true });
        return;
      }

      try {
        const tokenResponse = await authApi.exchangeToken(code);
        const token = tokenResponse.data.access_token;
        setAccessToken(token);

        const introspection = await authApi.introspect(token);
        if (!introspection.data.active) {
          throw new Error("invalid token");
        }

        const user = await userApi.getMe();
        if (cancelled) return;

        login(user.data, introspection.data.scope ?? []);
        const target = getStoredReturnUri() || "/chat";
        clearStoredReturnUri();
        navigate(target, { replace: true });
      } catch {
        if (cancelled) return;
        clearAuth();
        toast.error("登录状态建立失败");
        navigate("/login", { replace: true });
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [clearAuth, login, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-primary" />
    </div>
  );
}
