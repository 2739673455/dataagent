import { useEffect } from "react";
import { AuthLoadingScreen } from "@/auth/AuthLoadingScreen";
import {
	buildAuthCallbackUrl,
	buildAuthorizeUrl,
	checkAuth,
} from "@/auth/authorize";
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
		window.location.replace(buildAuthorizeUrl(buildAuthCallbackUrl(from)));
		return <AuthLoadingScreen />;
	}

	if (requiredScopes && !hasScope(requiredScopes)) {
		window.location.replace("/");
		return <AuthLoadingScreen />;
	}

	return <>{children}</>;
}

// 认证守卫
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
	return <RequireAuth>{children}</RequireAuth>;
}

// 权限守卫
export function PermissionRoute({ children }: { children: React.ReactNode }) {
	return <RequireAuth requiredScopes={["*"]}>{children}</RequireAuth>;
}
