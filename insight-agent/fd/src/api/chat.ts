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

	createConversation(isDraft: 0 | 1 = 0) {
		return appClient.post<ConversationResponse>("api/chat/create", {
			is_draft: isDraft,
		});
	},

	getMessages(conversationId: number) {
		return appClient.get<MessageListResponse>(`api/chat/ls/${conversationId}`);
	},

	uploadAttachment(conversationId: number, file: File) {
		const formData = new FormData();
		formData.append("conversation_id", String(conversationId));
		formData.append("file", file);
		return appClient.post<UploadAttachmentResponse>(
			"api/chat/attachment/upload",
			formData,
		);
	},

	deleteAttachment(conversationId: number, path: string) {
		return appClient.post("api/chat/attachment/delete", {
			conversation_id: conversationId,
			path,
		});
	},

	fetchAttachmentFile(conversationId: number, path: string) {
		return appClient.get<Blob>("api/chat/attachment/get", {
			params: {
				conversation_id: conversationId,
				path,
			},
			responseType: "blob",
		});
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
