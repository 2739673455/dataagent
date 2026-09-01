import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { checkAuth } from "@/auth/session";
import { useAuthStore } from "@/auth/store";
import { PageLoadingScreen } from "@/components/PageLoadingScreen";
import { ROUTES } from "@/config/settings";

// 认证与权限校验的基础守卫
function RequireAuth({
  children,
  requireAdmin,
}: {
  children: React.ReactNode;
  requireAdmin?: boolean;
}) {
  const location = useLocation();
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthStore((state) => state.isLoading);
  const isAdmin = useAuthStore((state) => state.isAdmin);

  useEffect(() => {
    if (isLoading) {
      void checkAuth();
    }
  }, [isLoading]);

  if (isLoading) {
    return <PageLoadingScreen message="正在验证登录状态..." />;
  }

  if (!isAuthenticated) {
    const returnTo = `${location.pathname}${location.search}`;
    return (
      <Navigate to={`${ROUTES.login}?${new URLSearchParams({ return_to: returnTo })}`} replace />
    );
  }

  if (requireAdmin && !isAdmin()) {
    toast.error("无权限访问此页面");
    return <Navigate to={ROUTES.chat} replace />;
  }

  return <>{children}</>;
}

// 认证守卫
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  return <RequireAuth>{children}</RequireAuth>;
}

export function AdminRoute({ children }: { children: React.ReactNode }) {
  return <RequireAuth requireAdmin>{children}</RequireAuth>;
}
