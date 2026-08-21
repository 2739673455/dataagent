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

export interface AttachmentReference {
  f_path: string;
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

interface ToolCallPart {
  type: "tool_call";
  tool_call_id: string;
  name: string;
  args: Record<string, unknown>;
}

interface ToolResultPart {
  type: "tool_result";
  tool_call_id: string;
  name: string;
  content: string;
}

export type UserMessagePart = TextContent | ImageContent;
export type MessagePart = UserMessagePart | ToolCallPart | ToolResultPart;

type MessageRole = "user" | "assistant" | "tool" | "system";
type FinishReason = string;

export interface UserMessageRequest {
  parts: UserMessagePart[];
  attachments?: AttachmentReference[] | null;
}

export interface MessageResponse {
  message_id?: string | null;
  role: MessageRole;
  parts: MessagePart[];
  attachments?: Attachment[] | null;
  finish_reason?: FinishReason | null;
}

export interface MessageListResponse {
  messages: MessageResponse[];
}

export interface UploadAttachmentResponse {
  attachment: Attachment;
}

export interface ChatStreamRequest {
  conversation_id: string;
  message: UserMessageRequest;
}

interface ChatStreamMessageEvent {
  type: "message";
  message: MessageResponse;
}

interface ChatStreamErrorEvent {
  type: "error";
  content: string;
}

interface ChatStreamDoneEvent {
  type: "done";
}

export type ChatStreamEvent = ChatStreamMessageEvent | ChatStreamErrorEvent | ChatStreamDoneEvent;
