import {
	AUTH_API_BASE_URL,
	AUTH_API_PATHS,
	AUTH_PROFILE_PATH,
	BASE_URL,
	ROUTE_PATHS,
} from "@/configs/settings";
import { joinUrl } from "@/utils/url";
import {
	createCodeChallenge,
	createRandomBase64Url32,
	saveAuthorizationRequest,
} from "./oauth";

// 当前应用的授权回调地址
export function buildAuthCallbackUrl(): string {
	const callbackUrl = new URL(
		joinUrl(BASE_URL, ROUTE_PATHS.authCallback),
		window.location.origin,
	);
	return callbackUrl.toString();
}

// 认证中心授权地址
export async function buildAuthorizeApiUrl(
	clientId: string,
	returnTo: string,
): Promise<string> {
	const state = createRandomBase64Url32();
	const codeVerifier = createRandomBase64Url32();
	const codeChallenge = await createCodeChallenge(codeVerifier);
	const redirectUri = buildAuthCallbackUrl();

	saveAuthorizationRequest({
		clientId,
		redirectUri,
		returnTo,
		state,
		codeVerifier,
	});

	const query = new URLSearchParams({
		response_type: "code",
		client_id: clientId,
		redirect_uri: redirectUri,
		state,
		code_challenge: codeChallenge,
		code_challenge_method: "S256",
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
