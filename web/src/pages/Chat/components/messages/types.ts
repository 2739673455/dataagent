import type {
  Attachment,
  EvalDelegationResponse,
  MessagePart,
  SubagentRun,
  SubagentRunIdentity,
} from "@/types";

export type MessageDisplayItem = {
  key: string;
  type: "message";
  message: {
    key: string;
    conversationId?: string | null;
    messageId?: string;
    role: "user" | "assistant" | "system" | "tool";
    parts: MessagePart[];
    createdAt?: string | null;
    attachments?: Attachment[] | null;
  };
};

export type ToolRunDisplayItem = {
  key: string;
  type: "tool_run";
  toolCallId: string;
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  completed: boolean;
  interrupted?: boolean;
  attachments?: Attachment[] | null;
  conversationId?: string | null;
  createdAt?: string | null;
  evalDelegations?: EvalDelegationResponse[] | null;
};

export type DisplayItem = MessageDisplayItem | ToolRunDisplayItem;

export type ChatTurn = {
  turnId: string;
  userItem: MessageDisplayItem | null;
  intermediateItems: DisplayItem[];
  finalItem: MessageDisplayItem | null;
};

export type { SubagentRunIdentity };

export type SubagentRunMap = Record<string, SubagentRun>;

export type UserMessageNavigationItem = {
  key: string;
  createdAt: string | null;
  preview: string;
};
