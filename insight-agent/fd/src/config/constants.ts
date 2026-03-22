export const ROUTES = {
	authCallback: "/auth/callback",
	chat: "/chat",
	chatConversation: (conversationId: number | string) =>
		`/chat/${conversationId}`,
	profile: "/profile",
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

export function getAppWsBaseUrl() {
	const url = new URL(window.location.origin);
	url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
	return url.toString().replace(/\/+$/, "");
}
