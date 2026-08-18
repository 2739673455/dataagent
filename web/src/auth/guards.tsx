import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { AuthLoadingScreen } from "@/auth/AuthLoadingScreen";
import { checkAuth } from "@/auth/session";
import { useAuthStore } from "@/auth/store";
import { ROUTES } from "@/config/settings";

// 认证与权限校验的基础守卫
function RequireAuth({
  children,
  requiredRoles,
}: {
  children: React.ReactNode;
  requiredRoles?: string[];
}) {
  const location = useLocation();
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthStore((state) => state.isLoading);
  const hasRole = useAuthStore((state) => state.hasRole);

  useEffect(() => {
    if (isLoading) {
      void checkAuth();
    }
  }, [isLoading]);

  if (isLoading) {
    return <AuthLoadingScreen />;
  }

  if (!isAuthenticated) {
    const returnTo = `${location.pathname}${location.search}`;
    return (
      <Navigate to={`${ROUTES.login}?${new URLSearchParams({ return_to: returnTo })}`} replace />
    );
  }

  if (requiredRoles && !hasRole(requiredRoles)) {
    toast.error("无权限访问此页面");
    return <Navigate to={ROUTES.chat} replace />;
  }

  return <>{children}</>;
}

// 认证守卫
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  return <RequireAuth>{children}</RequireAuth>;
}
