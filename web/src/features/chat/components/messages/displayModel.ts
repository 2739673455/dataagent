import { getAttachmentName } from "@/lib/utils";
import type {
  AgentType,
  ImageContent,
  MessagePart,
  MessageResponse,
  SubagentRunStatus,
  TextContent,
  ThinkingContent,
} from "@/features/chat/types";
import type {
  ChatTurn,
  DisplayItem,
  MessageDisplayItem,
  SubagentRunIdentity,
  ToolRunDisplayItem,
} from "@/features/chat/components/messages/types";

export const TOOL_ARGS_PREVIEW_MAX_LENGTH = 80;

export type ExecutionStatus = "idle" | "processing" | "completed" | "interrupted";

/** 根据生成状态和最终回复是否存在确定回合状态。 */
export function getExecutionStatus(
  hasFinalItem: boolean,
  isStreaming: boolean
): Exclude<ExecutionStatus, "idle"> {
  if (isStreaming) return "processing";
  return hasFinalItem ? "completed" : "interrupted";
}

/** 根据最新用户回合和流式状态确定会话执行状态。 */
export function getConversationExecutionStatus(
  conversationId: string | null,
  messages: MessageResponse[],
  isStreaming: boolean
): ExecutionStatus {
  if (isStreaming) return "processing";
  const turns = groupDisplayItemsIntoTurns(buildDisplayItems(conversationId, messages, false));
  const latestTurn = turns.at(-1);
  if (!latestTurn?.userItem) return "idle";
  return getExecutionStatus(latestTurn.finalItem !== null, false);
}

/** 优先使用消息 ID，缺少 ID 时根据角色和内容生成展示键。 */
export function getMessageKey(message: MessageResponse): string {
  if (message.message_id != null) {
    return `message-${message.message_id}`;
  }
  return `message-draft-${message.role}-${JSON.stringify(message.parts)}`;
}

/** 按内容片段类型和标识生成渲染键。 */
export function getMessagePartKey(part: MessagePart): string {
  switch (part.type) {
    case "text":
      return `text-${part.text}`;
    case "image_url":
      return `image-${part.image_url}`;
    case "thinking":
      return "thinking";
    case "tool_call":
      return `tool-call-${part.tool_call_id}-${part.name}`;
    case "tool_result":
      return `tool-result-${part.tool_call_id}-${part.name}-${part.content}`;
  }
}

const messageTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** 将有效时间转换为中文日期时间格式，无效值返回空结果。 */
export function formatMessageTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(
    messageTimeFormatter.formatToParts(date).map((part) => [part.type, part.value])
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

/** 提取用户消息摘要，正文为空时使用附件名称。 */
export function getUserMessagePreview(message: MessageDisplayItem["message"]): string {
  const content = message.parts
    .map((part) => (part.type === "text" ? part.text : "[图片]"))
    .join("\n")
    .trim();
  if (content) return content;

  const attachmentNames = message.attachments?.map((attachment) =>
    getAttachmentName(attachment.f_path)
  );
  return attachmentNames?.length ? `[附件] ${attachmentNames.join("、")}` : "空消息";
}

export type AttachmentFileType =
  | "table"
  | "code"
  | "json"
  | "markdown"
  | "text"
  | "html"
  | "image"
  | "archive"
  | "generic";

/** 结合媒体类型和文件扩展名确定附件的展示类别。 */
export function getAttachmentFileType(
  filePath: string,
  mediaType?: string | null
): AttachmentFileType {
  if (mediaType === "text/html") return "html";
  if (mediaType?.startsWith("image/")) return "image";
  const cleanPath = filePath.split("?")[0].split("#")[0];
  const ext = cleanPath.split(".").pop()?.toLowerCase() || "";
  if (["csv", "tsv", "xlsx", "xls", "parquet", "feather"].includes(ext)) {
    return "table";
  }
  if (["py", "sql", "sh", "bash", "zsh", "r", "js", "ts", "jsx", "tsx"].includes(ext)) {
    return "code";
  }
  if (["json", "yaml", "yml", "xml", "toml"].includes(ext)) {
    return "json";
  }
  if (["md", "markdown"].includes(ext)) {
    return "markdown";
  }
  if (["html", "htm"].includes(ext)) {
    return "html";
  }
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(ext)) {
    return "image";
  }
  if (["zip", "tar", "gz", "tgz", "7z", "rar", "bz2"].includes(ext)) {
    return "archive";
  }
  if (["txt", "log", "pdf", "doc", "docx"].includes(ext)) {
    return "text";
  }
  return "generic";
}

/** 根据文件扩展名判断附件是否支持图片预览。 */
export function isImageAttachment(name: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

/** 根据文件扩展名识别 HTML 预览附件。 */
export function isHtmlAttachment(name: string): boolean {
  return /\.(html?)$/i.test(name);
}

/** 将消息转换为正文和工具执行项，并按调用 ID 合并工具结果。 */
export function buildDisplayItems(
  conversationId: string | null,
  messages: MessageResponse[],
  isStreaming: boolean
): DisplayItem[] {
  const items: DisplayItem[] = [];
  const toolRuns = new Map<string, ToolRunDisplayItem>();

  for (const message of messages) {
    const regularParts: Array<TextContent | ImageContent | ThinkingContent> = [];
    const toolParts: Array<Extract<MessagePart, { type: "tool_call" | "tool_result" }>> = [];

    for (const part of message.parts) {
      if (part.type === "text") {
        if (part.text.trim()) {
          regularParts.push(part);
        }
        continue;
      }

      if (part.type === "image_url") {
        regularParts.push(part);
        continue;
      }

      if (part.type === "thinking") {
        if (part.text) regularParts.push(part);
        continue;
      }

      toolParts.push(part);
    }

    const shouldRenderAsStandaloneMessage =
      regularParts.length > 0 ||
      ((message.attachments?.length ?? 0) > 0 && message.role !== "tool");

    if (shouldRenderAsStandaloneMessage) {
      items.push({
        key: getMessageKey(message),
        type: "message",
        message: {
          key: getMessageKey(message),
          conversationId,
          createdAt: message.created_at,
          finishReason: message.finish_reason,
          role: message.role,
          attachments: message.attachments,
          parts: regularParts,
        },
      });
    }

    for (const part of toolParts) {
      if (part.type === "tool_call") {
        const item: ToolRunDisplayItem = {
          key: `tool-run-${part.tool_call_id}`,
          type: "tool_run",
          toolCallId: part.tool_call_id,
          conversationId,
          name: part.name,
          args: part.args,
          completed: false,
        };
        toolRuns.set(part.tool_call_id, item);
        items.push(item);
        continue;
      }

      const existing = toolRuns.get(part.tool_call_id);
      if (existing) {
        existing.name = part.name || existing.name;
        existing.result = part.content;
        existing.attachments = message.attachments;
        existing.completed = true;
        continue;
      }

      items.push({
        key: `tool-run-${part.tool_call_id}`,
        type: "tool_run",
        toolCallId: part.tool_call_id,
        conversationId,
        name: part.name,
        result: part.content,
        attachments: message.attachments,
        completed: true,
      });
    }
  }

  // 会话生成结束后，将缺少结果的 tool_call 标记为已中断
  if (!isStreaming) {
    for (const run of toolRuns.values()) {
      if (!run.completed) {
        run.interrupted = true;
      }
    }
  }

  return items;
}

/** 提取回合末尾的可见终答，将思考内容保留在中间过程。 */
export function splitFinalAssistantMessage(
  items: DisplayItem[],
  allowFinalMessage = true
): {
  finalItem: MessageDisplayItem | null;
  intermediateItems: DisplayItem[];
} {
  if (!allowFinalMessage || items.length === 0) {
    return { finalItem: null, intermediateItems: [...items] };
  }

  const lastItem = items[items.length - 1];
  const hasVisibleAnswer =
    lastItem.type === "message" &&
    ((lastItem.message.attachments?.length ?? 0) > 0 ||
      lastItem.message.parts.some((part) => part.type !== "thinking"));
  if (
    lastItem.type !== "message" ||
    lastItem.message.role !== "assistant" ||
    lastItem.message.finishReason === "streaming" ||
    lastItem.message.finishReason === "interrupted" ||
    !hasVisibleAnswer
  ) {
    return { finalItem: null, intermediateItems: [...items] };
  }

  const thinkingParts = lastItem.message.parts.filter((part) => part.type === "thinking");
  if (thinkingParts.length === 0) {
    return {
      finalItem: lastItem,
      intermediateItems: items.slice(0, items.length - 1),
    };
  }

  const thinkingItemKey = `${lastItem.key}-thinking`;
  const thinkingItem: MessageDisplayItem = {
    ...lastItem,
    key: thinkingItemKey,
    message: {
      ...lastItem.message,
      key: thinkingItemKey,
      attachments: null,
      parts: thinkingParts,
    },
  };
  return {
    finalItem: {
      ...lastItem,
      message: {
        ...lastItem.message,
        parts: lastItem.message.parts.filter((part) => part.type !== "thinking"),
      },
    },
    intermediateItems: [...items.slice(0, items.length - 1), thinkingItem],
  };
}

/** 以用户消息划分回合，并分离每个回合的执行过程和终答。 */
export function groupDisplayItemsIntoTurns(
  displayItems: DisplayItem[],
  allowLatestTurnFinalMessage = true
): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let currentUserItem: MessageDisplayItem | null = null;
  let currentAssistantItems: DisplayItem[] = [];

  const flushTurn = (allowFinalMessage = true) => {
    if (!currentUserItem && currentAssistantItems.length === 0) return;

    const { finalItem, intermediateItems } = splitFinalAssistantMessage(
      currentAssistantItems,
      allowFinalMessage
    );

    const turnId =
      currentUserItem?.key ??
      (finalItem?.key || intermediateItems[0]?.key || `turn-${turns.length}`);

    turns.push({
      turnId,
      userItem: currentUserItem,
      intermediateItems,
      finalItem,
    });

    currentUserItem = null;
    currentAssistantItems = [];
  };

  for (const item of displayItems) {
    if (item.type === "message" && item.message.role === "user") {
      flushTurn();
      currentUserItem = item;
    } else {
      currentAssistantItems.push(item);
    }
  }

  flushTurn(allowLatestTurnFinalMessage);
  return turns;
}

/** 将工具参数压缩为单行摘要，并按参数类别限制文本长度。 */
export function formatToolArgValue(key: string, value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") {
    const singleLine = value.replace(/\s+/g, " ").trim();
    const isContentPayload = [
      "code",
      "content",
      "query",
      "sql",
      "script",
      "text",
      "body",
      "prompt",
      "message",
    ].includes(key.toLowerCase());
    const maxLen = isContentPayload ? 36 : 64;
    if (singleLine.length <= maxLen) {
      return singleLine;
    }
    return `${singleLine.slice(0, maxLen).trimEnd()}...`;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return "[...]";
  }
  return "{...}";
}

/** 组合工具参数摘要，并限制整体展示长度。 */
export function getToolArgsPreview(args?: Record<string, unknown>): string | null {
  if (!args) return null;
  const entries = Object.entries(args);
  if (entries.length === 0) return null;

  const preview = entries
    .map(([key, value]) => `${key}=${formatToolArgValue(key, value)}`)
    .join(" ");
  if (preview.length <= TOOL_ARGS_PREVIEW_MAX_LENGTH) {
    return preview;
  }
  return `${preview.slice(0, TOOL_ARGS_PREVIEW_MAX_LENGTH).trimEnd()}...`;
}

/** 格式化 JSON 工具结果，其他内容按原文展示。 */
export function formatToolResult(result: string): string {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    return result;
  }
}

/** 从 JSON 工具结果中读取字符串状态。 */
export function getToolResultStatus(result: string | undefined): string | null {
  if (result === undefined) return null;
  try {
    const payload: unknown = JSON.parse(result);
    if (
      typeof payload === "object" &&
      payload !== null &&
      !Array.isArray(payload) &&
      "status" in payload &&
      typeof payload.status === "string"
    ) {
      return payload.status;
    }
    return null;
  } catch {
    return null;
  }
}

export interface DelegationResultPayload {
  status: string | null;
  content: string | null;
}

/** 从委派结果中读取展示所需的状态和文本。 */
export function parseDelegationResult(result: string | undefined): DelegationResultPayload | null {
  if (result === undefined) return null;
  try {
    const payload: unknown = JSON.parse(result);
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return null;

    const status =
      "status" in payload && typeof payload.status === "string" ? payload.status : null;
    const content =
      "content" in payload && typeof payload.content === "string" ? payload.content : null;
    return { status, content };
  } catch {
    return null;
  }
}

/** 根据工具结果中的状态判断调用是否失败。 */
export function isToolResultFailure(result: string | undefined): boolean {
  const status = getToolResultStatus(result);
  return status === "error" || status === "failed";
}

/** 结合最终结果、中断标记和活动事件确定委派状态。 */
export function resolveDelegationRunStatus(
  result: string | undefined,
  completed: boolean,
  interrupted: boolean,
  activityStatus: SubagentRunStatus | undefined
): SubagentRunStatus {
  const resultStatus = getToolResultStatus(result);
  if (resultStatus === "error" || resultStatus === "failed") return "failed";
  if (resultStatus === "completed") return resultStatus;
  if (interrupted) return "interrupted";
  if (activityStatus !== undefined) return activityStatus;
  return completed ? "completed" : "running";
}

/** 从委派工具调用中提取会话和委派标识。 */
export function getSubagentRunIdentity(item: ToolRunDisplayItem): SubagentRunIdentity | null {
  if (item.name !== "delegation" || !item.args) return null;
  const analysisId = item.args.analysis_id;
  const agentType = item.args.agent_type;
  const sessionId = item.args.session_id;
  if (
    typeof analysisId !== "string" ||
    typeof agentType !== "string" ||
    agentType.length === 0 ||
    typeof sessionId !== "string"
  ) {
    return null;
  }
  return {
    delegationId: item.toolCallId,
    analysisId,
    agentType: agentType as AgentType,
    sessionId,
  };
}
