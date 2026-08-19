import { getAccessToken, refreshAccessToken } from "@/auth";
import { CHAT_API_ROUTES } from "@/config/settings";
import type {
  ChatStreamEvent,
  ChatStreamRequest,
  ConversationListResponse,
  ConversationResponse,
  MessageListResponse,
  UploadAttachmentResponse,
} from "@/types";
import appClient from "./appClient";

function parseStreamEvent(frame: string): ChatStreamEvent | null {
  const payload = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  return payload ? (JSON.parse(payload) as ChatStreamEvent) : null;
}

async function consumeChatStream(
  body: ChatStreamRequest,
  signal: AbortSignal,
  onEvent: (event: ChatStreamEvent) => void,
  retried = false
): Promise<void> {
  const accessToken = getAccessToken();
  if (!accessToken) throw new Error("登录状态已失效");

  const response = await fetch(CHAT_API_ROUTES.stream, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401 && !retried) {
    await refreshAccessToken();
    return consumeChatStream(body, signal, onEvent, true);
  }
  if (!response.ok || !response.body) {
    throw new Error(`聊天请求失败（${response.status}）`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const event = parseStreamEvent(frame);
      if (event) onEvent(event);
    }
    if (done) break;
  }
  const finalEvent = parseStreamEvent(buffer);
  if (finalEvent) onEvent(finalEvent);
}

export const chatApi = {
  listConversations() {
    return appClient.get<ConversationListResponse>(CHAT_API_ROUTES.listConversations);
  },

  createConversation(isDraft: 0 | 1 = 0, initialMessage?: string) {
    return appClient.post<ConversationResponse>(CHAT_API_ROUTES.createConversation, {
      is_draft: isDraft,
      initial_message: initialMessage,
    });
  },

  getMessages(conversationId: string) {
    return appClient.get<MessageListResponse>(CHAT_API_ROUTES.getMessages(conversationId));
  },

  uploadAttachment(conversationId: string, file: File) {
    const formData = new FormData();
    formData.append("conversation_id", String(conversationId));
    formData.append("file", file);
    return appClient.post<UploadAttachmentResponse>(CHAT_API_ROUTES.uploadAttachment, formData);
  },

  deleteAttachment(conversationId: string, f_path: string) {
    return appClient.post(CHAT_API_ROUTES.deleteAttachment, {
      conversation_id: conversationId,
      f_path,
    });
  },

  fetchAttachmentFile(conversationId: string, f_path: string) {
    return appClient.get<Blob>(CHAT_API_ROUTES.getAttachment, {
      params: {
        conversation_id: conversationId,
        f_path,
      },
      responseType: "blob",
    });
  },

  deleteConversations(conversationIds: string[]) {
    return appClient.post(CHAT_API_ROUTES.deleteConversations, {
      conversation_ids: conversationIds,
    });
  },

  streamChat(
    conversationId: string,
    message: ChatStreamRequest["message"],
    signal: AbortSignal,
    onEvent: (event: ChatStreamEvent) => void
  ) {
    return consumeChatStream({ conversation_id: conversationId, message }, signal, onEvent);
  },
};
