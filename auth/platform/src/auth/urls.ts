import {
	AUTH_API_BASE_URL,
	AUTH_API_PATHS,
	AUTH_PROFILE_PATH,
	BASE_URL,
	ROUTE_PATHS,
} from "@/configs/settings";
import { joinUrl } from "@/utils/url";

// 当前应用的授权回调地址
export function buildAuthCallbackUrl(redirectUri: string): string {
	const callbackUrl = new URL(
		joinUrl(BASE_URL, ROUTE_PATHS.authCallback),
		window.location.origin,
	);
	callbackUrl.searchParams.set("redirect_uri", redirectUri);
	return callbackUrl.toString();
}

// 认证中心授权地址
export function buildAuthorizeApiUrl(
	clientId: string,
	redirectUri: string,
): string {
	const query = new URLSearchParams({
		client_id: clientId,
		redirect_uri: redirectUri,
	}).toString();
	const authorizePath = `${AUTH_API_PATHS.authorize}?${query}`;
	return joinUrl(AUTH_API_BASE_URL, authorizePath);
}

// 认证中心个人信息页面地址
export function buildAuthProfileUrl(redirectUri: string): string {
	const query = new URLSearchParams({
		redirect_uri: redirectUri,
	}).toString();
	return `${AUTH_PROFILE_PATH}?${query}`;
}
