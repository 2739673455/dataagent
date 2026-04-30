import ky from "ky";
import {
	AUTH_API_BASE_URL,
	AUTH_API_PATHS,
	CLIENT_ID,
} from "@/configs/settings";
import { joinUrl } from "@/utils/url";

interface TokenResponse {
	access_token: string;
	token_type: string;
}

interface IntrospectionResponse {
	active: boolean;
	sub?: number;
	exp?: number;
	scope?: string[];
}

const authClient = ky.create({
	retry: 0, // 不重试
	throwHttpErrors: false, // 不抛出 HTTP 错误
});

async function requestAuthApi<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await authClient(joinUrl(AUTH_API_BASE_URL, path), init);
	if (!response.ok) {
		throw new Error(`Auth API request failed with status ${response.status}`);
	}
	return (await response.json()) as T;
}

export const authApi = {
	// 用授权码换访问令牌
	exchangeToken: (
		code: string,
		redirectUri: string,
		codeVerifier: string,
	) =>
		requestAuthApi<TokenResponse>(AUTH_API_PATHS.token, {
			method: "POST",
			body: new URLSearchParams({
				grant_type: "authorization_code",
				code,
				client_id: CLIENT_ID,
				redirect_uri: redirectUri,
				code_verifier: codeVerifier,
			}),
			headers: { "Content-Type": "application/x-www-form-urlencoded" },
		}),

	// 校验访问令牌状态并返回 scope
	introspect: (token: string) =>
		requestAuthApi<IntrospectionResponse>(AUTH_API_PATHS.introspection, {
			method: "POST",
			headers: { Authorization: `Bearer ${token}` },
		}),
};
