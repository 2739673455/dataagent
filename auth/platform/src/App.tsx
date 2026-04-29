import { useEffect, useRef } from "react";
import { RouterProvider } from "react-router";
import { Toaster } from "sonner";
import { ACCESS_TOKEN_STORAGE_KEY, checkAuth, useAuthStore } from "@/auth";
import { useCurrentUserStore } from "@/stores/user";
import { router } from "./routers";

function App() {
	const routerRef = useRef(router);
	const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

	// 监听其他标签页修改 access token，并同步当前标签页的登录态
	useEffect(() => {
		const onStorage = (event: StorageEvent) => {
			// 仅响应 access token 对应的 localStorage 变更
			if (event.key !== ACCESS_TOKEN_STORAGE_KEY) return;
			const authStore = useAuthStore.getState();
			const currentUserStore = useCurrentUserStore.getState();

			// token 被清除时，当前标签页同步退出登录
			if (event.newValue === null) {
				authStore.clearAuth();
				currentUserStore.clearUser();
				return;
			}

			// token 被其他标签页更新后，当前标签页重新拉取登录态
			void checkAuth();
		};

		// 监听其他标签页对 localStorage 的修改
		window.addEventListener("storage", onStorage);
		// 组件卸载时移除监听
		return () => window.removeEventListener("storage", onStorage);
	}, []);

	useEffect(() => {
		const currentUserStore = useCurrentUserStore.getState();
		// 认证状态变化时，同步清理或重新拉取当前用户信息。
		if (!isAuthenticated) {
			currentUserStore.clearUser();
			return;
		}

		currentUserStore.clearUser();
		void currentUserStore.fetchCurrentUser();
	}, [isAuthenticated]);

	return (
		<>
			<RouterProvider router={routerRef.current} />
			<Toaster
				position="top-center"
				richColors
				toastOptions={{
					style: {
						border: "none",
						boxShadow: "0 2px 8px rgba(0, 0, 0, 0.1)",
					},
				}}
			/>
		</>
	);
}

export default App;
