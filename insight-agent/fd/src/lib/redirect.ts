import {
	AUTH_API_BASE_URL,
	AUTH_API_ROUTES,
	AUTH_CLIENT_ID,
	getAuthRedirectUri,
} from "@/config/constants";

const RETURN_URI_STORAGE_KEY = "insight-agent:return-uri";

export function getStoredReturnUri() {
	if (typeof window === "undefined") return null;
	return window.localStorage.getItem(RETURN_URI_STORAGE_KEY);
}

export function setStoredReturnUri(value: string) {
	if (typeof window === "undefined") return;
	window.localStorage.setItem(RETURN_URI_STORAGE_KEY, value);
}

export function clearStoredReturnUri() {
	if (typeof window === "undefined") return;
	window.localStorage.removeItem(RETURN_URI_STORAGE_KEY);
}

export function buildAuthorizeUrl() {
	const url = new URL(
		`${AUTH_API_BASE_URL}${AUTH_API_ROUTES.authorize}`,
		window.location.origin,
	);
	url.searchParams.set("client_id", AUTH_CLIENT_ID);
	url.searchParams.set("redirect_uri", getAuthRedirectUri());
	return url.toString();
}

export function redirectToAuthorize(returnTo?: string) {
	const current =
		returnTo || `${window.location.pathname}${window.location.search}`;
	setStoredReturnUri(current);
	window.location.assign(buildAuthorizeUrl());
}
