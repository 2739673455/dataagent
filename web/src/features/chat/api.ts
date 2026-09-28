import { getProblemDetailsMessage } from "@/api/errors";
import type { components } from "@/api/generated";
import { getSelectedUserId } from "@/identity/index";
import { CHAT_API_ROUTES } from "@/config/settings";
import type {
  ChatStreamEvent,
  ChatStreamRequest,
  ConversationListResponse,
  ConversationResponse,
  ConversationRunStatusResponse,
  MessageListResponse,
  SubagentMessageListResponse,
  SubagentRunIdentity,
} from "@/features/chat/types";
import appClient from "@/api/appClient";

type ApiSchemas = components["schemas"];
type CreateConversationRequest = ApiSchemas["CreateConversationRequest"];
type DeleteConversationRequest = ApiSchemas["DeleteConversationRequest"];
type UpdateConversationRequest = ApiSchemas["UpdateConversationRequest"];

function parseStreamEvent(frame: string): ChatStreamEvent | null {
  const payload = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  return payload ? (JSON.parse(payload) as ChatStreamEvent) : null;
}

async function streamErrorMessage(response: Response): Promise<string> {
  try {
    const problem = getProblemDetailsMessage(await response.json());
    if (problem) return problem;
  } catch {
    // 响应体无法解析时使用状态码兜底
  }
  return `聊天请求失败（${response.status}）`;
}

async function consumeChatStream(
  url: string,
  body: ChatStreamRequest | null,
  signal: AbortSignal,
  onEvent: (event: ChatStreamEvent) => void,
  method: "GET" | "POST" = "POST"
): Promise<void> {
  const userId = getSelectedUserId();
  if (!userId) throw new Error("请先选择用户");

  const response = await fetch(url, {
    method,
    headers: {
      Accept: "text/event-stream",
      "X-User-ID": userId,
      "Content-Type": "application/json",
    },
    body: body === null ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    throw new Error(await streamErrorMessage(response));
  }
  if (!response.body) {
    throw new Error("聊天响应缺少流式内容");
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
      if (event) {
        onEvent(event);
        if (event.type === "done") {
          await reader.cancel();
          return;
        }
      }
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

  createConversation(isDraft = false, initialMessage?: string) {
    return appClient.post<ConversationResponse>(CHAT_API_ROUTES.createConversation, {
      is_draft: isDraft,
      initial_message: initialMessage,
    } satisfies CreateConversationRequest);
  },

  getMessages(conversationId: string) {
    return appClient.get<MessageListResponse>(CHAT_API_ROUTES.getMessages(conversationId));
  },

  getSubagentMessages(conversationId: string, run: SubagentRunIdentity) {
    return appClient.get<SubagentMessageListResponse>(
      CHAT_API_ROUTES.getSubagentMessages(
        conversationId,
        run.analysisId,
        run.agentType,
        run.sessionId,
        run.delegationId
      )
    );
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

  updateConversation(conversationId: string, title: string) {
    return appClient.post(CHAT_API_ROUTES.updateConversation, {
      conversation_id: conversationId,
      title,
    } satisfies UpdateConversationRequest);
  },

  deleteConversations(conversationIds: string[]) {
    return appClient.post(CHAT_API_ROUTES.deleteConversations, {
      conversation_ids: conversationIds,
    } satisfies DeleteConversationRequest);
  },

  streamChat(
    conversationId: string,
    message: ChatStreamRequest["message"],
    signal: AbortSignal,
    onEvent: (event: ChatStreamEvent) => void
  ) {
    return consumeChatStream(
      CHAT_API_ROUTES.stream,
      { conversation_id: conversationId, message },
      signal,
      onEvent
    );
  },

  resumeChat(
    conversationId: string,
    signal: AbortSignal,
    onEvent: (event: ChatStreamEvent) => void
  ) {
    return consumeChatStream(CHAT_API_ROUTES.resume(conversationId), null, signal, onEvent);
  },

  getRunStatus(conversationId: string) {
    return appClient.get<ConversationRunStatusResponse>(CHAT_API_ROUTES.runStatus(conversationId));
  },

  subscribeRun(
    conversationId: string,
    signal: AbortSignal,
    onEvent: (event: ChatStreamEvent) => void
  ) {
    return consumeChatStream(
      CHAT_API_ROUTES.runEvents(conversationId),
      null,
      signal,
      onEvent,
      "GET"
    );
  },

  stopRun(conversationId: string) {
    return appClient.post(CHAT_API_ROUTES.stopRun(conversationId));
  },
};
