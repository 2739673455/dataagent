import ky from "ky";
import { getConfig } from "./config";
import type { IntrospectionResponse, TokenResponse } from "./types";
import { joinUrl } from "./url";

const authClient = ky.create({
	retry: 0,
	throwHttpErrors: false,
});

async function requestAuthApi<T>(path: string, init?: RequestInit): Promise<T> {
	const { authApiBaseUrl } = getConfig();
	const response = await authClient(joinUrl(authApiBaseUrl, path), init);
	if (!response.ok) {
		throw new Error(`Auth API request failed with status ${response.status}`);
	}
	return (await response.json()) as T;
}

export function exchangeToken(
	code: string,
	redirectUri: string,
	codeVerifier: string,
): Promise<TokenResponse> {
	const { clientId, authApiPaths } = getConfig();
	return requestAuthApi<TokenResponse>(authApiPaths.token, {
		method: "POST",
		body: new URLSearchParams({
			grant_type: "authorization_code",
			code,
			client_id: clientId,
			redirect_uri: redirectUri,
			code_verifier: codeVerifier,
		}),
		headers: { "Content-Type": "application/x-www-form-urlencoded" },
	});
}

export function introspect(token: string): Promise<IntrospectionResponse> {
	const { authApiPaths } = getConfig();
	return requestAuthApi<IntrospectionResponse>(authApiPaths.introspection, {
		method: "POST",
		headers: { Authorization: `Bearer ${token}` },
	});
}
