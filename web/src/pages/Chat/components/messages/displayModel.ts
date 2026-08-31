import { getAttachmentName } from "@/lib/utils";
import type {
  AgentType,
  Attachment,
  ImageContent,
  MessagePart,
  MessageResponse,
  SubagentRunStatus,
  TextContent,
  ThinkingContent,
} from "@/types";
import type {
  ChatTurn,
  DisplayItem,
  MessageDisplayItem,
  SubagentRunIdentity,
  SubagentRunMap,
  ToolRunDisplayItem,
} from "./types";

export const TOOL_ARGS_PREVIEW_MAX_LENGTH = 80;

export type ExecutionStatus = "idle" | "processing" | "completed" | "interrupted";

export function getExecutionStatus(
  hasFinalItem: boolean,
  isStreaming: boolean
): Exclude<ExecutionStatus, "idle"> {
  if (isStreaming) return "processing";
  return hasFinalItem ? "completed" : "interrupted";
}

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

export function getMessageKey(message: MessageResponse): string {
  if (message.message_id != null) {
    return `message-${message.message_id}`;
  }
  return `message-draft-${message.role}-${JSON.stringify(message.parts)}`;
}

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

export function formatMessageTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(
    messageTimeFormatter.formatToParts(date).map((part) => [part.type, part.value])
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

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

export function getAttachmentFileType(
  filePath: string,
  mediaType?: string | null
): AttachmentFileType {
  if (mediaType === "application/vnd.dataagent.table+json" || /\.table\.json$/i.test(filePath)) {
    return "table";
  }
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

export function isImageAttachment(name: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

export function isHtmlAttachment(name: string): boolean {
  return /\.(html?)$/i.test(name);
}

export function isInteractiveTableAttachment(attachment: Attachment): boolean {
  return (
    attachment.media_type === "application/vnd.dataagent.table+json" ||
    /\.table\.json$/i.test(attachment.f_path)
  );
}

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
        existing.evalDelegations = message.eval_delegations;
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
        evalDelegations: message.eval_delegations,
        completed: true,
      });
    }
  }

  // 会话不再生成时，将未配对的 tool_call 标记为已中断
  if (!isStreaming) {
    for (const run of toolRuns.values()) {
      if (!run.completed) {
        run.interrupted = true;
      }
    }
  }

  return items;
}

export function buildEvalDelegationItems(
  parent: ToolRunDisplayItem,
  subagentRuns: SubagentRunMap
): ToolRunDisplayItem[] {
  const items = new Map<string, ToolRunDisplayItem>();

  for (const record of parent.evalDelegations ?? []) {
    items.set(record.delegation_id, {
      key: `tool-run-${record.delegation_id}`,
      type: "tool_run",
      toolCallId: record.delegation_id,
      conversationId: parent.conversationId,
      name: "delegation",
      args: {
        analysis_id: record.analysis_id,
        agent_type: record.agent_type,
        session_id: record.session_id,
        message: record.message,
      },
      result: record.result == null ? undefined : JSON.stringify(record.result),
      attachments: record.attachments,
      completed: record.result != null,
      interrupted: record.result == null,
    });
  }

  for (const run of Object.values(subagentRuns)) {
    if (run.parentToolCallId !== parent.toolCallId) continue;
    const existing = items.get(run.delegationId);
    const completed = run.status !== "running";
    items.set(run.delegationId, {
      key: `tool-run-${run.delegationId}`,
      type: "tool_run",
      toolCallId: run.delegationId,
      conversationId: parent.conversationId,
      name: "delegation",
      args: existing?.args ?? {
        analysis_id: run.analysisId,
        agent_type: run.agentType,
        session_id: run.sessionId,
        message: run.instruction ?? "",
      },
      result: existing?.result,
      completed: existing?.completed === true || completed,
      interrupted: run.status === "cancelled" || run.status === "interrupted",
    });
  }

  return [...items.values()];
}

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

export function formatToolResult(result: string): string {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    return result;
  }
}

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
  failureReasons: string[];
}

export function parseDelegationResult(result: string | undefined): DelegationResultPayload | null {
  if (result === undefined) return null;
  try {
    const payload: unknown = JSON.parse(result);
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return null;

    const status =
      "status" in payload && typeof payload.status === "string" ? payload.status : null;
    const content =
      "content" in payload && typeof payload.content === "string" ? payload.content : null;
    const failureReasons =
      "failure_reasons" in payload && Array.isArray(payload.failure_reasons)
        ? [
            ...new Set(
              payload.failure_reasons.filter(
                (reason): reason is string => typeof reason === "string" && reason.trim().length > 0
              )
            ),
          ]
        : [];

    return { status, content, failureReasons };
  } catch {
    return null;
  }
}

export function isToolResultFailure(result: string | undefined): boolean {
  const status = getToolResultStatus(result);
  return status === "error" || status === "failed";
}

export function resolveDelegationRunStatus(
  result: string | undefined,
  completed: boolean,
  interrupted: boolean,
  activityStatus: SubagentRunStatus | undefined
): SubagentRunStatus {
  const resultStatus = getToolResultStatus(result);
  if (resultStatus === "error" || resultStatus === "failed") return "failed";
  if (resultStatus === "completed" || resultStatus === "needs_repair") return resultStatus;
  if (interrupted) return "interrupted";
  if (activityStatus !== undefined) return activityStatus;
  return completed ? "completed" : "running";
}

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
