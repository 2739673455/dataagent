import {
	AUTH_API_BASE_URL,
	AUTH_API_ROUTES,
	AUTH_CLIENT_ID,
	getAuthRedirectUri,
} from "@/config/constants";
import type { IntrospectionResponse, TokenResponse } from "@/types";
import authClient from "./authClient";

export function buildAuthorizeUrl() {
	const url = new URL(
		`${AUTH_API_BASE_URL}${AUTH_API_ROUTES.authorize}`,
		window.location.origin,
	);
	url.searchParams.set("client_id", AUTH_CLIENT_ID);
	url.searchParams.set("redirect_uri", getAuthRedirectUri());
	return url.toString();
}

export const authApi = {
	exchangeToken(code: string) {
		const form = new URLSearchParams({
			code,
			client_id: AUTH_CLIENT_ID,
			redirect_uri: getAuthRedirectUri(),
		});

		return authClient.post<TokenResponse>(AUTH_API_ROUTES.token, form, {
			headers: {
				"Content-Type": "application/x-www-form-urlencoded",
			},
		});
	},

	introspect(token: string) {
		return authClient.post<IntrospectionResponse>(
			AUTH_API_ROUTES.introspection,
			undefined,
			{
				headers: {
					Authorization: `Bearer ${token}`,
				},
			},
		);
	},

	logout() {
		return authClient.post<void>(AUTH_API_ROUTES.logout);
	},
};
