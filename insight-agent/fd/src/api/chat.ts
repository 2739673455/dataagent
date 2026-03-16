import { CHAT_API_ROUTES, getAppWsBaseUrl } from "@/config/constants";
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
		return appClient.get<ConversationListResponse>(
			CHAT_API_ROUTES.listConversations,
		);
	},

	createConversation(isDraft: 0 | 1 = 0) {
		return appClient.post<ConversationResponse>(
			CHAT_API_ROUTES.createConversation,
			{
				is_draft: isDraft,
			},
		);
	},

	getMessages(conversationId: number) {
		return appClient.get<MessageListResponse>(
			CHAT_API_ROUTES.getMessages(conversationId),
		);
	},

	uploadAttachment(conversationId: number, file: File) {
		const formData = new FormData();
		formData.append("conversation_id", String(conversationId));
		formData.append("file", file);
		return appClient.post<UploadAttachmentResponse>(
			CHAT_API_ROUTES.uploadAttachment,
			formData,
		);
	},

	deleteAttachment(conversationId: number, path: string) {
		return appClient.post(CHAT_API_ROUTES.deleteAttachment, {
			conversation_id: conversationId,
			path,
		});
	},

	fetchAttachmentFile(conversationId: number, path: string) {
		return appClient.get<Blob>(CHAT_API_ROUTES.getAttachment, {
			params: {
				conversation_id: conversationId,
				path,
			},
			responseType: "blob",
		});
	},

	deleteConversations(conversationIds: number[]) {
		return appClient.post(CHAT_API_ROUTES.deleteConversations, {
			conversation_ids: conversationIds,
		});
	},

	createWebSocketToken() {
		return appClient.post<WebSocketTokenResponse>(
			CHAT_API_ROUTES.createWebSocketToken,
		);
	},

	buildChatSocket(conversationId: number, websocketToken: string) {
		const url = new URL(`${getAppWsBaseUrl()}${CHAT_API_ROUTES.chatWebSocket}`);
		url.searchParams.set("conversation_id", String(conversationId));
		url.searchParams.set("websocket_token", websocketToken);
		return new WebSocket(url.toString());
	},

	serializeChatRequest(body: WebSocketChatRequest) {
		return JSON.stringify(body);
	},
};
