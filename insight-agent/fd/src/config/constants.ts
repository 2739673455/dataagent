function trimTrailingSlash(value: string) {
	return value.replace(/\/+$/, "");
}

export const AUTH_API_BASE_URL = "/auth-api";
export const AUTH_APP_BASE_URL = "http://127.0.0.1:7100";
export const AUTH_CLIENT_ID = "insight-agent";

export const ROUTES = {
	home: "/",
	login: "/login",
	authCallback: "/auth/callback",
	chat: "/chat",
	chatConversation: (conversationId: number | string) =>
		`/chat/${conversationId}`,
	profile: "/profile",
} as const;

export const AUTH_API_ROUTES = {
	authorize: "/api/authorize",
	token: "api/token",
	introspection: "api/introspection",
	logout: "api/logout",
} as const;

export const CHAT_API_ROUTES = {
	listConversations: "/api/chat/ls",
	createConversation: "/api/chat/create",
	getMessages: (conversationId: number) => `/api/chat/ls/${conversationId}`,
	uploadAttachment: "/api/chat/attachment/upload",
	deleteAttachment: "/api/chat/attachment/delete",
	getAttachment: "/api/chat/attachment/get",
	deleteConversations: "/api/chat/delete",
	createWebSocketToken: "/api/chat/ws-token",
	chatWebSocket: "/api/chat/ws/chat",
} as const;

export function getAuthRedirectUri() {
	return `${window.location.origin}${ROUTES.authCallback}`;
}

export function getAppWsBaseUrl() {
	const url = new URL(window.location.origin);
	url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
	return trimTrailingSlash(url.toString());
}

export function getAuthAppBaseUrl() {
	return trimTrailingSlash(AUTH_APP_BASE_URL);
}
