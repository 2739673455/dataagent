import { useEffect } from "react";
import { Navigate } from "react-router";
import { checkAuth } from "@/auth/authorize";
import { AuthLoadingScreen } from "@/auth/components";
import { useAuthStore } from "@/auth/store";
import { buildAuthorizeApiUrl } from "@/auth/urls";
import { CLIENT_ID, ROUTE_PATHS } from "@/configs/settings";

function useAuthBootstrap(): boolean {
	const isLoading = useAuthStore((state) => state.isLoading);

	useEffect(() => {
		if (isLoading) {
			void checkAuth();
		}
	}, [isLoading]);

	return isLoading;
}

// 仅游客守卫
export function GuestOnlyRoute({ children }: { children: React.ReactNode }) {
	const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
	const isLoading = useAuthBootstrap();

	if (isLoading) return <AuthLoadingScreen />;
	if (isAuthenticated) {
		return <Navigate to={ROUTE_PATHS.home} replace />;
	}
	return <>{children}</>;
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
		void buildAuthorizeApiUrl(
			CLIENT_ID,
			`${window.location.pathname}${window.location.search}`,
		).then((url) => window.location.replace(url));
		return <AuthLoadingScreen />;
	}

	if (requiredScopes && !hasScope(requiredScopes)) {
		return <Navigate to={ROUTE_PATHS.home} replace />;
	}

	return <>{children}</>;
}
