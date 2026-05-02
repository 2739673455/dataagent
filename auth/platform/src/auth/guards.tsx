import { useEffect } from "react";
import { Navigate } from "react-router";
import { checkAuth, useAuthStore, AuthLoadingScreen, buildAuthorizeUrl } from "@/auth-client";
import { ROUTE_PATHS } from "@/configs/settings";

function useAuthBootstrap(): boolean {
  const isLoading = useAuthStore((state) => state.isLoading);

  useEffect(() => {
    if (isLoading) {
      void checkAuth();
    }
  }, [isLoading]);

  return isLoading;
}

// 认证与权限校验的基础守卫
export function RequireAuth({
  children,
  requiredScopes,
}: {
  children: React.ReactNode;
  requiredScopes?: string[];
}) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const hasScope = useAuthStore((state) => state.hasScope);
  const isLoading = useAuthBootstrap();

  if (isLoading) return <AuthLoadingScreen />;

  if (!isAuthenticated) {
    void buildAuthorizeUrl(
      `${window.location.pathname}${window.location.search}`,
    ).then((url) => window.location.replace(url));
    return <AuthLoadingScreen />;
  }

  if (requiredScopes && !hasScope(requiredScopes)) {
    return <Navigate to={ROUTE_PATHS.home} replace />;
  }

  return <>{children}</>;
}
