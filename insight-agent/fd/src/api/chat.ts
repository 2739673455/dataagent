import { getAppWsBaseUrl } from "@/lib/env";
import type {
	ConversationListResponse,
	ConversationResponse,
	MessageListResponse,
	UploadAttachmentResponse,
	WebSocketChatRequest,
	WebSocketTokenResponse,
} from "@/types";
import appClient from "./appClient";

export const chatApi = {
	listConversations() {
		return appClient.get<ConversationListResponse>("api/chat/ls");
	},

	createConversation() {
		return appClient.post<ConversationResponse>("api/chat/create");
	},

	getMessages(conversationId: number) {
		return appClient.get<MessageListResponse>(`api/chat/ls/${conversationId}`);
	},

	uploadAttachment(conversationId: number, file: File) {
		const formData = new FormData();
		formData.append("file", file);
		return appClient.post<UploadAttachmentResponse>(
			`api/chat/upload?conversation_id=${conversationId}`,
			formData,
		);
	},

	deleteConversations(conversationIds: number[]) {
		return appClient.post("api/chat/delete", { conversation_ids: conversationIds });
	},

	createWebSocketToken() {
		return appClient.post<WebSocketTokenResponse>("api/chat/ws-token");
	},

	buildChatSocket(conversationId: number, websocketToken: string) {
		const url = new URL(`${getAppWsBaseUrl()}/api/chat/ws/chat`);
		url.searchParams.set("conversation_id", String(conversationId));
		url.searchParams.set("websocket_token", websocketToken);
		return new WebSocket(url.toString());
	},

	serializeChatRequest(body: WebSocketChatRequest) {
		return JSON.stringify(body);
	},
};
