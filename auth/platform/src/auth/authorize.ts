import { authApi } from "@/auth/api";
import {
	clearAccessToken,
	getAccessToken,
	setAccessToken,
	useAuthStore,
} from "@/auth/store";
import { buildAuthCallbackUrl, buildAuthorizeApiUrl } from "@/auth/urls";
import { CLIENT_ID } from "@/configs/settings";

let restoreTask: Promise<void> | null = null;
const handledCodes = new Set<string>();
const recentCodes: string[] = [];
const callbackTasks = new Map<string, Promise<void>>();

// 用本地 access token 恢复登录态，并复用进行中的恢复请求
export async function checkAuth(): Promise<void> {
	const token = getAccessToken();
	if (!token) {
		useAuthStore.getState().clearAuth();
		return;
	}

	if (!restoreTask) {
		restoreTask = authApi
			.introspect(token)
			.then(({ active, scope }) => {
				if (!active) throw new Error("访问令牌无效");
				useAuthStore.getState().setAuth(scope ?? []);
			})
			.catch(() => {
				clearAccessToken();
				useAuthStore.getState().clearAuth();
			})
			.finally(() => {
				restoreTask = null;
			});
	}

	await restoreTask;
}

// 消费授权码完成登录，并保证同一 code 只处理一次
export async function completeAuthCallback(code: string): Promise<void> {
	if (handledCodes.has(code)) return;

	let task = callbackTasks.get(code);
	if (!task) {
		task = (async () => {
			const { access_token } = await authApi.exchangeToken(code);
			const { active, scope } = await authApi.introspect(access_token);
			if (!active) throw new Error("访问令牌无效");
			setAccessToken(access_token);
			useAuthStore.getState().setAuth(scope ?? []);
			// 记录最近处理过的授权码，避免同一 code 被重复消费
			handledCodes.add(code);
			recentCodes.push(code);
			if (recentCodes.length > 20) {
				const expired = recentCodes.shift();
				if (expired) handledCodes.delete(expired);
			}
		})().finally(() => {
			callbackTasks.delete(code);
		});
		callbackTasks.set(code, task);
	}

	await task;
}

// 统一处理 401 未授权响应，清理状态后重新发起登录
export function handleUnauthorizedError(error: unknown): boolean {
	if (
		(error as { response?: { status?: number } } | undefined)?.response
			?.status !== 401
	) {
		return false;
	}

	clearAccessToken();
	useAuthStore.getState().clearAuth();
	window.location.replace(
		buildAuthorizeApiUrl(
			CLIENT_ID,
			buildAuthCallbackUrl(
				`${window.location.pathname}${window.location.search}`,
			),
		),
	);
	return true;
}
