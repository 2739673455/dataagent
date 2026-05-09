import { CHAT_API_ROUTES } from "@/config/settings";
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
    return appClient.get<ConversationListResponse>(CHAT_API_ROUTES.listConversations);
  },

  createConversation(isDraft: 0 | 1 = 0) {
    return appClient.post<ConversationResponse>(CHAT_API_ROUTES.createConversation, {
      is_draft: isDraft,
    });
  },

  getMessages(conversationId: number) {
    return appClient.get<MessageListResponse>(CHAT_API_ROUTES.getMessages(conversationId));
  },

  uploadAttachment(conversationId: number, file: File) {
    const formData = new FormData();
    formData.append("conversation_id", String(conversationId));
    formData.append("file", file);
    return appClient.post<UploadAttachmentResponse>(CHAT_API_ROUTES.uploadAttachment, formData);
  },

  deleteAttachment(conversationId: number, f_path: string) {
    return appClient.post(CHAT_API_ROUTES.deleteAttachment, {
      conversation_id: conversationId,
      f_path,
    });
  },

  fetchAttachmentFile(conversationId: number, f_path: string) {
    return appClient.get<Blob>(CHAT_API_ROUTES.getAttachment, {
      params: {
        conversation_id: conversationId,
        f_path,
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
    return appClient.post<WebSocketTokenResponse>(CHAT_API_ROUTES.createWebSocketToken);
  },

  buildChatSocket(conversationId: number, websocketToken: string) {
    const wsBase = window.location.origin.replace(/^http/, "ws");
    const url = new URL(`${wsBase}${CHAT_API_ROUTES.chatWebSocket}`);
    url.searchParams.set("conversation_id", String(conversationId));
    url.searchParams.set("websocket_token", websocketToken);
    return new WebSocket(url.toString());
  },

  serializeChatRequest(body: WebSocketChatRequest) {
    return JSON.stringify(body);
  },
};
