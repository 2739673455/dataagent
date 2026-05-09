import { useEffect } from "react";
import { toast } from "sonner";
import { AuthLoadingScreen } from "@/auth/AuthLoadingScreen";
import { buildAuthorizeUrl, checkAuth } from "@/auth/authorize";
import { useAuthStore } from "@/auth/store";

// 认证与权限校验的基础守卫
function RequireAuth({
  children,
  requiredScopes,
}: {
  children: React.ReactNode;
  requiredScopes?: string[];
}) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthStore((state) => state.isLoading);
  const hasScope = useAuthStore((state) => state.hasScope);

  useEffect(() => {
    if (isLoading) {
      void checkAuth();
    }
  }, [isLoading]);

  if (isLoading) {
    return <AuthLoadingScreen />;
  }

  if (!isAuthenticated) {
    const from = `${window.location.pathname}${window.location.search}`;
    void buildAuthorizeUrl(from).then((url) => window.location.replace(url));
    return <AuthLoadingScreen />;
  }

  if (requiredScopes && !hasScope(requiredScopes)) {
    toast.error("无权限访问此页面");
    window.location.replace("/");
    return <AuthLoadingScreen />;
  }

  return <>{children}</>;
}

// 认证守卫
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  return <RequireAuth>{children}</RequireAuth>;
}
