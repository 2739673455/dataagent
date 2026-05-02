import { exchangeToken, introspect } from "./api";
import { clearAuthorizationRequest, loadAuthorizationRequest } from "./oauth";
import {
	clearAccessToken,
	getAccessToken,
	setAccessToken,
	useAuthStore,
} from "./store";
import { buildAuthorizeUrl } from "./urls";

let restoreTask: Promise<void> | null = null;
const handledCodes = new Set<string>();
const recentCodes: string[] = [];
const codeReturnTo = new Map<string, string>();
const callbackTasks = new Map<string, Promise<string>>();

export async function checkAuth(): Promise<void> {
	const token = getAccessToken();
	if (!token) {
		useAuthStore.getState().clearAuth();
		return;
	}

	if (!restoreTask) {
		restoreTask = introspect(token)
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

export async function handleAuthCallback(
	code: string,
	state: string,
): Promise<string> {
	if (handledCodes.has(code)) return codeReturnTo.get(code) ?? "/";

	let task = callbackTasks.get(code);
	if (!task) {
		task = (async () => {
			const authRequest = loadAuthorizationRequest(state);
			if (!authRequest) {
				throw new Error("授权请求已失效，请重新登录");
			}
			if (authRequest.state !== state) {
				throw new Error("授权状态校验失败，请重新登录");
			}
			const returnTo = authRequest.returnTo;

			const { access_token } = await exchangeToken(
				code,
				authRequest.redirectUri,
				authRequest.codeVerifier,
			);
			const { active, scope } = await introspect(access_token);
			if (!active) throw new Error("访问令牌无效");
			setAccessToken(access_token);
			useAuthStore.getState().setAuth(scope ?? []);
			clearAuthorizationRequest(state);
			handledCodes.add(code);
			codeReturnTo.set(code, returnTo);
			recentCodes.push(code);
			if (recentCodes.length > 20) {
				const expired = recentCodes.shift();
				if (expired) {
					handledCodes.delete(expired);
					codeReturnTo.delete(expired);
				}
			}
			return returnTo;
		})().finally(() => {
			callbackTasks.delete(code);
		});
		callbackTasks.set(code, task);
	}

	return await task;
}

export function handleUnauthorizedError(): void {
	clearAccessToken();
	useAuthStore.getState().clearAuth();
	void buildAuthorizeUrl(
		`${window.location.pathname}${window.location.search}`,
	).then((url) => window.location.replace(url));
}
