import { useEffect } from "react";
import { toast } from "sonner";
import { AuthLoadingScreen } from "@/auth/AuthLoadingScreen";
import { authApi } from "@/auth/api";
import { useAuthStore } from "@/auth/store";
import { setAccessToken } from "@/auth/token";
import type { UserResponse } from "@/auth/types";

type CallbackAuthResult = {
	token: string;
	user: UserResponse;
	scopes: string[];
};

let inflightCode: string | null = null;
let inflightTask: Promise<CallbackAuthResult> | null = null;
const handledCodes: string[] = [];

export default function AuthCallbackPage() {
	// 授权码回调完成后写入前端认证状态
	const setAuth = useAuthStore((state) => state.setAuth);
	const clearAuth = useAuthStore((state) => state.clearAuth);

	useEffect(() => {
		let cancelled = false;

		const run = async () => {
			const searchParams = new URLSearchParams(window.location.search);
			const code = searchParams.get("code");

			// 缺少授权码或重复消费授权码时直接终止
			if (!code || handledCodes.includes(code)) {
				if (!code) toast.error("缺少授权码");
				if (code && handledCodes.includes(code)) toast.error("重复处理授权码");
				window.location.replace("/");
				return;
			}

			try {
				if (!inflightTask || inflightCode !== code) {
					inflightCode = code;
					// 用授权码换 access token，再同步用户信息和权限
					inflightTask = (async () => {
						const tokenResponse = await authApi.exchangeToken(code);
						const token = tokenResponse.data.access_token;
						const introspectionResponse = await authApi.introspect(token);
						if (!introspectionResponse.data.active) {
							throw new Error("访问令牌无效");
						}
						const userResponse = await authApi.getMe(token);
						return {
							token,
							user: userResponse.data,
							scopes: introspectionResponse.data.scope ?? [],
						};
					})().finally(() => {
						inflightCode = null;
						inflightTask = null;
					});
				}

				const { token, user, scopes } = await inflightTask;
				handledCodes.push(code);
				if (handledCodes.length > 20) handledCodes.shift();
				if (cancelled) return;
				setAccessToken(token);
				setAuth(user, scopes);
				// 跳转业务目标页
				window.location.replace(searchParams.get("redirect_uri") || "/");
			} catch {
				if (cancelled) return;
				clearAuth();
				toast.error("登录状态建立失败，请重试");
				window.location.replace("/");
			}
		};

		void run();
		return () => {
			cancelled = true;
		};
	}, [clearAuth, setAuth]);

	return <AuthLoadingScreen />;
}
