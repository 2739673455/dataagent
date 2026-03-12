import type {
  MessagePart,
  MessageSchema,
  TextContent,
  ToolResultPart,
} from "@/types";

export function getMessagePreview(message: MessageSchema) {
  const textPart = message.parts.find(
    (part): part is TextContent => part.type === "text"
  );
  if (textPart?.text) {
    return textPart.text;
  }

  const toolPart = message.parts.find(
    (part): part is ToolResultPart => part.type === "tool_result"
  );
  if (toolPart?.content) {
    return `${toolPart.name}: ${toolPart.content}`;
  }

  return "暂无内容";
}

export function getMessageText(parts: MessagePart[]) {
  return parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();
}

export function formatDateTime(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function createLocalTimestamp(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  const second = String(date.getSeconds()).padStart(2, "0");
  const millisecond = String(date.getMilliseconds()).padStart(3, "0");
  return `${year}-${month}-${day}T${hour}:${minute}:${second}.${millisecond}`;
}
