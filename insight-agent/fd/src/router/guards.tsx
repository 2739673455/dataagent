import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ROUTES } from "@/config/constants";
import { useAuthStore } from "@/stores/authStore";

interface ProtectedRouteProps {
	children: ReactNode;
}

function FullPageLoading() {
	return (
		<div className="flex min-h-screen items-center justify-center">
			<Loader2 className="h-8 w-8 animate-spin text-indigo-600" />
		</div>
	);
}

export function EntryRoute() {
	const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
	const isLoading = useAuthStore((state) => state.isLoading);
	const checkAuth = useAuthStore((state) => state.checkAuth);

	useEffect(() => {
		if (isLoading) {
			void checkAuth();
		}
	}, [checkAuth, isLoading]);

	if (isLoading) {
		return <FullPageLoading />;
	}

	return <Navigate to={isAuthenticated ? ROUTES.chat : ROUTES.login} replace />;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
	const location = useLocation();
	const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
	const isLoading = useAuthStore((state) => state.isLoading);
	const checkAuth = useAuthStore((state) => state.checkAuth);

	useEffect(() => {
		if (isLoading) {
			void checkAuth();
		}
	}, [checkAuth, isLoading]);

	if (isLoading) {
		return <FullPageLoading />;
	}

	if (!isAuthenticated) {
		const from = `${location.pathname}${location.search}`;
		return <Navigate to={ROUTES.login} state={{ from }} replace />;
	}

	return <>{children}</>;
}
