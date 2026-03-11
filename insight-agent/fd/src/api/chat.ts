import { getAppWsBaseUrl } from "@/lib/env";
import type {
	ConversationListResponse,
	ConversationResponse,
	MessageListResponse,
	WebSocketChatRequest,
} from "@/types";
import appClient from "./appClient";

export const chatApi = {
	listConversations() {
		return appClient.get<ConversationListResponse>("chat/ls");
	},

	createConversation() {
		return appClient.post<ConversationResponse>("chat/create");
	},

	getMessages(conversationId: number) {
		return appClient.get<MessageListResponse>(`chat/ls/${conversationId}`);
	},

	buildChatSocket(conversationId: number, accessToken: string) {
		const url = new URL(`${getAppWsBaseUrl()}/chat/ws/chat`);
		url.searchParams.set("conversation_id", String(conversationId));
		url.searchParams.set("access_token", accessToken);
		return new WebSocket(url.toString());
	},

	serializeChatRequest(body: WebSocketChatRequest) {
		return JSON.stringify(body);
	},
};
