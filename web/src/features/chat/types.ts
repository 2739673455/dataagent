import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type ConversationResponse = ApiSchemas["ConversationResponse"];
export type ConversationListResponse = ApiSchemas["ConversationListResponse"];

export type Attachment = ApiSchemas["Attachment"];

export type TextContent = ApiSchemas["TextContent"];
export type ThinkingContent = ApiSchemas["ThinkingContent"];
export type MessagePart = ApiSchemas["MessageResponse"]["parts"][number];
export type UserMessageRequest = ApiSchemas["UserMessageRequest"];

export type MessageResponse = ApiSchemas["MessageResponse"];

export type MessageListResponse = ApiSchemas["MessageListResponse"];

export type ChatStreamRequest = ApiSchemas["ChatStreamRequest"];
export type ConversationRunStatusResponse = ApiSchemas["ConversationRunStatusResponse"];

export type ChatStreamEvent = ApiSchemas["ChatStreamEvent"];
export type AgentType = ApiSchemas["AgentType"];

export type SubagentStatusEvent = Extract<ChatStreamEvent, { type: "subagent_status" }>;
export type ThinkingEvent = Extract<ChatStreamEvent, { type: "thinking" }>;
export type MessageDeltaEvent = Extract<ChatStreamEvent, { type: "message_delta" }>;
export type SubagentRunStatus = SubagentStatusEvent["status"] | "interrupted";

export interface SubagentRunIdentity {
  delegationId: string;
  agentType: AgentType;
}

export interface SubagentRun extends SubagentRunIdentity {
  status: SubagentRunStatus;
}
