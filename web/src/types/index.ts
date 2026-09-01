import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type ConversationResponse = ApiSchemas["ConversationResponse"];
export type ConversationListResponse = ApiSchemas["ConversationListResponse"];
export type AttachmentReference = ApiSchemas["AttachmentReference"];

export type Attachment = ApiSchemas["Attachment"] & {
  preview_url?: string;
};

export type TextContent = ApiSchemas["TextContent"];
export type ImageContent = ApiSchemas["ImageContent"];
export type ThinkingContent = ApiSchemas["ThinkingContent"];
export type UserMessagePart = ApiSchemas["UserMessageRequest"]["parts"][number];
export type MessagePart = ApiSchemas["MessageResponse"]["parts"][number];
export type EvalDelegationResponse = ApiSchemas["EvalDelegationResponse"];
export type UserMessageRequest = ApiSchemas["UserMessageRequest"];

export type MessageResponse = Omit<ApiSchemas["MessageResponse"], "attachments"> & {
  attachments?: Attachment[] | null;
};

export type MessageListResponse = Omit<ApiSchemas["MessageListResponse"], "messages"> & {
  messages: MessageResponse[];
};

export type UploadAttachmentResponse = Omit<
  ApiSchemas["UploadAttachmentResponse"],
  "attachment"
> & {
  attachment: Attachment;
};

export type ChatStreamRequest = ApiSchemas["ChatStreamRequest"];
export type ConversationRunStatusResponse = ApiSchemas["ConversationRunStatusResponse"];

export type ChatStreamEvent = ApiSchemas["ChatStreamEvent"];
export type AgentType = ApiSchemas["AgentType"];
export type SubagentMessageListResponse = Omit<
  ApiSchemas["SubagentMessageListResponse"],
  "messages"
> & {
  messages: MessageResponse[];
};
export type SubagentStatusEvent = Extract<ChatStreamEvent, { type: "subagent_status" }>;
export type SubagentMessageEvent = Extract<ChatStreamEvent, { type: "subagent_message" }>;
export type ThinkingEvent = Extract<ChatStreamEvent, { type: "thinking" }>;
export type MessageDeltaEvent = Extract<ChatStreamEvent, { type: "message_delta" }>;
export type SubagentThinkingEvent = Extract<ChatStreamEvent, { type: "subagent_thinking" }>;
export type SubagentMessageDeltaEvent = Extract<
  ChatStreamEvent,
  { type: "subagent_message_delta" }
>;
export type SubagentRunStatus = SubagentStatusEvent["status"] | "interrupted";

export interface SubagentRunIdentity {
  delegationId: string;
  analysisId: string;
  agentType: AgentType;
  sessionId: string;
  parentToolCallId?: string | null;
  instruction?: string | null;
}

export interface SubagentRun extends SubagentRunIdentity {
  status: SubagentRunStatus;
  messages: MessageResponse[];
  historyLoaded: boolean;
  historyLoading: boolean;
}
