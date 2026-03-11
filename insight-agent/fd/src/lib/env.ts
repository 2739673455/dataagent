function trimTrailingSlash(value: string) {
	return value.replace(/\/+$/, "");
}

export function getAppApiBaseUrl() {
	return trimTrailingSlash(
		import.meta.env.VITE_APP_API_BASE_URL ||
			`${window.location.origin}/app-api`,
	);
}

export function getAuthApiBaseUrl() {
	return trimTrailingSlash(
		import.meta.env.VITE_AUTH_API_BASE_URL ||
			`${window.location.origin}/auth-api`,
	);
}

export function getAuthAppBaseUrl() {
	return trimTrailingSlash(
		import.meta.env.VITE_AUTH_APP_BASE_URL || "http://127.0.0.1:7100",
	);
}

export function getAppWsBaseUrl() {
	const configured = import.meta.env.VITE_APP_WS_BASE_URL;
	if (configured) {
		return trimTrailingSlash(configured);
	}

	const url = new URL(window.location.origin);
	url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
	return trimTrailingSlash(`${url.origin}/app-ws`);
}

export function getAuthClientId() {
	return "insight-agent";
}

export function getAuthRedirectUri() {
	return `${window.location.origin}/auth/callback`;
}
