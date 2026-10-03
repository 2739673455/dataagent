import { chatApi } from "@/features/chat/api";
import type { ChatStreamEvent, UserMessageRequest } from "@/features/chat/types";

export type StreamConnectionMode =
  | { type: "start"; message: UserMessageRequest }
  | { type: "resume" }
  | { type: "subscribe" };

/** 连接聊天执行流；连接结束后依据后台状态恢复订阅，直到完成或取消。 */
export async function connectConversationRun(
  conversationId: string,
  mode: StreamConnectionMode,
  signal: AbortSignal,
  onEvent: (event: ChatStreamEvent) => void
): Promise<void> {
  let receivedDone = false;
  const receive = (event: ChatStreamEvent) => {
    if (signal.aborted) return;
    if (event.type === "done") receivedDone = true;
    onEvent(event);
  };
  let nextMode = mode;
  while (!signal.aborted && !receivedDone) {
    let connectionError: unknown = null;
    try {
      if (nextMode.type === "start") {
        await chatApi.streamChat(conversationId, nextMode.message, signal, receive);
      } else if (nextMode.type === "resume") {
        await chatApi.resumeChat(conversationId, signal, receive);
      } else {
        await chatApi.subscribeRun(conversationId, signal, receive);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      connectionError = error;
    }

    if (signal.aborted || receivedDone) return;
    const status = await chatApi.getRunStatus(conversationId);
    if (signal.aborted) return;
    if (!status.data.running) {
      if (connectionError) throw connectionError;
      return;
    }
    // 已运行的回合通过订阅恢复进度，启动和续写请求各提交一次。
    nextMode = { type: "subscribe" };
  }
}
