import {
	getAuthApiBaseUrl,
	getAuthClientId,
	getAuthRedirectUri,
} from "@/lib/env";
import type { IntrospectionResponse, TokenResponse } from "@/types";
import authClient from "./authClient";

export function buildAuthorizeUrl() {
	const url = new URL(`${getAuthApiBaseUrl()}/api/authorize`);
	url.searchParams.set("client_id", getAuthClientId());
	url.searchParams.set("redirect_uri", getAuthRedirectUri());
	return url.toString();
}

export const authApi = {
	exchangeToken(code: string) {
		const form = new URLSearchParams({
			code,
			client_id: getAuthClientId(),
			redirect_uri: getAuthRedirectUri(),
		});

		return authClient.post<TokenResponse>("api/token", form, {
			headers: {
				"Content-Type": "application/x-www-form-urlencoded",
			},
		});
	},

	introspect(token: string) {
		return authClient.post<IntrospectionResponse>(
			"api/introspection",
			undefined,
			{
				headers: {
					Authorization: `Bearer ${token}`,
				},
			},
		);
	},

	logout() {
		return authClient.post<void>("api/logout");
	},
};
