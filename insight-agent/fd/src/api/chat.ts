import type {
  ConversationListResponse,
  ConversationResponse,
  MessageListResponse,
  WebSocketChatRequest,
} from "@/types";
import { getAppWsBaseUrl } from "@/lib/env";
import appClient from "./appClient";

export const chatApi = {
  listConversations() {
    return appClient.get<ConversationListResponse>("/chat/ls");
  },

  createConversation() {
    return appClient.post<ConversationResponse>("/chat/create");
  },

  getMessages(conversationId: number) {
    return appClient.get<MessageListResponse>(`/chat/ls/${conversationId}`);
  },

  buildChatSocket(conversationId: number, accessToken: string) {
    const url = new URL("/chat/ws/chat", getAppWsBaseUrl());
    url.searchParams.set("conversation_id", String(conversationId));
    url.searchParams.set("access_token", accessToken);
    return new WebSocket(url.toString());
  },

  serializeChatRequest(body: WebSocketChatRequest) {
    return JSON.stringify(body);
  },
};
