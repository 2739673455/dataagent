export interface ConversationResponse {
  conversation_id: string;
  title: string;
  update_at: string;
}

export interface ConversationListResponse {
  conversations: ConversationResponse[];
}

export interface Attachment {
  f_path: string;
  media_type?: string;
  description?: string;
  preview_url?: string;
}

export interface InteractiveTableArtifact {
  format: "dataagent-interactive-table-v1";
  source_path: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total_rows: number;
  truncated: boolean;
}

export interface TextContent {
  type: "text";
  text: string;
}

export interface ImageContent {
  type: "image_url";
  image_url: string;
}

export interface ToolCallPart {
  type: "tool_call";
  tool_call_id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResultPart {
  type: "tool_result";
  tool_call_id: string;
  name: string;
  content: string;
}

export type MessagePart = TextContent | ImageContent | ToolCallPart | ToolResultPart;

export type MessageRole = "user" | "assistant" | "tool" | "system";
export type FinishReason = string;

export interface MessageSchema {
  message_id?: string | null;
  role: MessageRole;
  parts: MessagePart[];
  attachments?: Attachment[] | null;
  finish_reason?: FinishReason | null;
}

export interface MessageListResponse {
  messages: MessageSchema[];
}

export interface UploadAttachmentResponse {
  attachment: Attachment;
}

export interface ChatStreamRequest {
  conversation_id: string;
  message: MessageSchema;
}

export interface ChatStreamMessageEvent {
  type: "message";
  message: MessageSchema;
}

export interface ChatStreamErrorEvent {
  type: "error";
  content: string;
}

export interface ChatStreamDoneEvent {
  type: "done";
}

export type ChatStreamEvent = ChatStreamMessageEvent | ChatStreamErrorEvent | ChatStreamDoneEvent;
