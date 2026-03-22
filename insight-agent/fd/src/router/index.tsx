import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import AuthCallbackPage from "@/auth/AuthCallbackPage";
import { AUTH_CALLBACK_ROUTE } from "@/auth/constants";
import { ProtectedRoute } from "@/auth/guards";
import { ROUTES } from "@/config/constants";

const ChatPage = lazy(() => import("@/pages/Chat"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function PageLoading() {
	return (
		<div className="flex min-h-screen items-center justify-center">
			<Loader2 className="h-8 w-8 animate-spin text-slate-700" />
		</div>
	);
}

function SuspenseWrapper({ children }: { children: ReactNode }) {
	return <Suspense fallback={<PageLoading />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
	{
		path: "/",
		element: <Navigate to={ROUTES.chat} replace />,
	},
	{
		path: AUTH_CALLBACK_ROUTE,
		element: <AuthCallbackPage />,
	},
	{
		path: ROUTES.chat,
		element: (
			<ProtectedRoute>
				<SuspenseWrapper>
					<ChatPage />
				</SuspenseWrapper>
			</ProtectedRoute>
		),
	},
	{
		path: `${ROUTES.chat}/:conversationId`,
		element: (
			<ProtectedRoute>
				<SuspenseWrapper>
					<ChatPage />
				</SuspenseWrapper>
			</ProtectedRoute>
		),
	},
	{
		path: "*",
		element: (
			<SuspenseWrapper>
				<NotFound />
			</SuspenseWrapper>
		),
	},
]);
