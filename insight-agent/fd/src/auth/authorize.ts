import { authApi } from "@/auth/api";
import {
	AUTH_API_BASE_URL,
	AUTH_APIS,
	AUTH_CALLBACK_ROUTE,
	AUTH_PROFILE_ROUTE,
	AUTH_WEB_BASE_URL,
	CLIENT_ID,
} from "@/auth/constants";
import { useAuthStore } from "@/auth/store";
import { clearAccessToken, getAccessToken } from "@/auth/token";

// 构造认证回调地址，并将业务回跳地址写入 redirect_uri 参数
export function buildAuthCallbackUrl(targetRedirect: string): string {
	const callbackUrl = new URL(AUTH_CALLBACK_ROUTE, window.location.origin);
	callbackUrl.searchParams.set("redirect_uri", targetRedirect);
	return callbackUrl.toString();
}

// 基于 redirect_uri 构造认证中心授权入口地址
export function buildAuthorizeUrl(targetRedirect: string): string {
	const authorizePath = `${AUTH_APIS.authorize}?${new URLSearchParams({
		client_id: CLIENT_ID,
		redirect_uri: targetRedirect,
	}).toString()}`;
	// 配置了认证中心源地址时拼接完整地址，否则保留相对路径交给当前源或代理处理
	return AUTH_API_BASE_URL
		? `${AUTH_API_BASE_URL.replace(/\/$/, "")}${authorizePath}`
		: authorizePath;
}

// 构造认证中心个人中心地址，并将当前页面写入 redirect_uri 参数
export function buildAuthProfileRedirectUrl(redirectUri: string): string {
	const profilePath = `${AUTH_PROFILE_ROUTE}?${new URLSearchParams({
		redirect_uri: redirectUri,
	}).toString()}`;
	return AUTH_WEB_BASE_URL
		? `${AUTH_WEB_BASE_URL.replace(/\/$/, "")}${profilePath}`
		: profilePath;
}

// 根据本地 access token 检查并恢复登录态
export async function checkAuth(): Promise<void> {
	const authStore = useAuthStore.getState();
	const token = getAccessToken();
	// 本地没有 access token 时直接清空认证状态
	if (!token) {
		authStore.clearAuth();
		return;
	}

	try {
		const introspectionResponse = await authApi.introspect(token);
		// access token 已失效时清空认证状态
		if (!introspectionResponse.data.active) {
			clearAccessToken();
			authStore.clearAuth();
			return;
		}
		// access token 有效时同步拉取用户信息与权限，恢复前端登录态
		const userResponse = await authApi.getMe(token);
		const scope = introspectionResponse.data.scope ?? [];
		authStore.setAuth(userResponse.data, scope);
	} catch {
		// 认证检查失败时清空认证状态
		clearAccessToken();
		authStore.clearAuth();
	}
}
