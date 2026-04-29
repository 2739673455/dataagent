import { createBrowserRouter } from "react-router";
import AuthCallbackPage from "@/auth/AuthCallbackPage";
import { RequireAuth } from "@/auth/guards";
import { BASE_URL, ROUTE_PATHS } from "@/configs/settings";
import NotFound from "@/pages/NotFound";
import Platform from "@/pages/Platform";

export const router = createBrowserRouter(
	[
		{
			// 平台首页，需先完成认证
			path: "/",
			element: (
				<RequireAuth>
					<Platform />
				</RequireAuth>
			),
		},
		{
			// 认证中心授权完成后的前端回调页
			path: ROUTE_PATHS.authCallback,
			element: <AuthCallbackPage />,
		},
		{
			// 兜底 404 页面
			path: "*",
			element: <NotFound />,
		},
	],
	{
		basename: BASE_URL.replace(/\/+$/, ""),
	},
);
