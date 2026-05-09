import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router";
import { AuthLoadingScreen, buildAuthorizeUrl, checkAuth, useAuthStore } from "@/features/auth";
import { ROUTE_PATHS } from "@/shared/config/settings";

function useAuthBootstrap(): boolean {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const { isLoading } = useQuery({
    queryKey: ["auth", "introspect"],
    queryFn: checkAuth,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  if (!isAuthenticated && !isLoading) {
    return false;
  }

  return isLoading;
}

export function GuestOnlyRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthBootstrap();

  if (isLoading) return <AuthLoadingScreen />;
  if (isAuthenticated) {
    return <Navigate to={ROUTE_PATHS.home} replace />;
  }
  return <>{children}</>;
}

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
    void buildAuthorizeUrl(`${window.location.pathname}${window.location.search}`).then((url) =>
      window.location.replace(url)
    );
    return <AuthLoadingScreen />;
  }

  if (requiredScopes && !hasScope(requiredScopes)) {
    return <Navigate to={ROUTE_PATHS.home} replace />;
  }

  return <>{children}</>;
}
