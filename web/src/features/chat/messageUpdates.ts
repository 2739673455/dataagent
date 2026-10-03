import type { MessageResponse } from "@/features/chat/types";

/** 按消息 ID 去重，缺少 ID 时比较角色与内容片段。 */
function messageAlreadyExists(messages: MessageResponse[], message: MessageResponse): boolean {
  if (message.message_id != null) {
    return messages.some((candidate) => candidate.message_id === message.message_id);
  }
  return messages.some(
    (candidate) =>
      candidate.message_id == null &&
      candidate.role === message.role &&
      JSON.stringify(candidate.parts) === JSON.stringify(message.parts)
  );
}

/** 以服务端快照为基础，补入本地已收到的新增助手和工具消息。 */
export function mergeMessageSnapshot(
  snapshot: MessageResponse[],
  current: MessageResponse[]
): MessageResponse[] {
  const merged = [...snapshot];
  for (const message of current) {
    if (message.role !== "user" && !messageAlreadyExists(merged, message)) {
      merged.push(message);
    }
  }
  return merged;
}

/** 按消息 ID 替换已有消息，缺少 ID 时去重后追加。 */
export function upsertMessage(
  messages: MessageResponse[],
  message: MessageResponse
): MessageResponse[] {
  if (message.message_id != null) {
    const index = messages.findIndex((candidate) => candidate.message_id === message.message_id);
    if (index >= 0) {
      const next = [...messages];
      next[index] = message;
      return next;
    }
  } else if (messageAlreadyExists(messages, message)) {
    return messages;
  }
  return [...messages, message];
}

/** 创建或更新助手消息的思考片段，支持首个增量重置。 */
export function appendThinkingDelta(
  messages: MessageResponse[],
  messageId: string,
  delta: string,
  reset: boolean | undefined
): MessageResponse[] {
  const index = messages.findIndex((message) => message.message_id === messageId);
  if (index < 0) {
    return [
      ...messages,
      {
        message_id: messageId,
        role: "assistant",
        finish_reason: "streaming",
        parts: [{ type: "thinking", text: delta, status: "streaming" }],
      },
    ];
  }

  const message = messages[index];
  const thinkingIndex = message.parts.findIndex((part) => part.type === "thinking");
  const parts = [...message.parts];
  if (thinkingIndex < 0) {
    parts.unshift({ type: "thinking", text: delta, status: "streaming" });
  } else {
    const thinking = parts[thinkingIndex];
    if (thinking.type !== "thinking") return messages;
    parts[thinkingIndex] = {
      ...thinking,
      text: reset ? delta : `${thinking.text}${delta}`,
      status: "streaming",
    };
  }
  const next = [...messages];
  next[index] = { ...message, finish_reason: "streaming", parts };
  return next;
}

/** 追加正文增量，并将已有的流式思考片段标记为完成。 */
export function appendTextDelta(
  messages: MessageResponse[],
  messageId: string,
  delta: string,
  reset: boolean | undefined
): MessageResponse[] {
  const index = messages.findIndex((message) => message.message_id === messageId);
  if (index < 0) {
    return [
      ...messages,
      {
        message_id: messageId,
        role: "assistant",
        finish_reason: "streaming",
        parts: [{ type: "text", text: delta }],
      },
    ];
  }

  const message = messages[index];
  const textIndex = message.parts.findIndex((part) => part.type === "text");
  const parts = message.parts.map((part) =>
    part.type === "thinking" && part.status === "streaming"
      ? { ...part, status: "complete" as const }
      : part
  );
  if (textIndex < 0) {
    parts.push({ type: "text", text: delta });
  } else {
    const text = parts[textIndex];
    if (text.type !== "text") return messages;
    parts[textIndex] = {
      ...text,
      text: reset ? delta : `${text.text}${delta}`,
    };
  }
  const next = [...messages];
  next[index] = { ...message, finish_reason: "streaming", parts };
  return next;
}

/** 将流式消息和思考片段收束为完成或中断状态。 */
export function settleThinking(
  messages: MessageResponse[],
  status: "complete" | "interrupted"
): MessageResponse[] {
  let changed = false;
  const next = messages.map((message) => {
    let messageChanged = false;
    const parts = message.parts.map((part) => {
      if (part.type !== "thinking" || part.status !== "streaming") return part;
      changed = true;
      messageChanged = true;
      return { ...part, status };
    });
    if (message.finish_reason === "streaming") {
      changed = true;
      messageChanged = true;
    }
    return messageChanged
      ? {
          ...message,
          finish_reason: status === "complete" ? "stop" : "interrupted",
          parts,
        }
      : message;
  });
  return changed ? next : messages;
}
