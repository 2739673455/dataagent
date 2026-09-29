import { create } from "zustand";
import { chatApi } from "@/features/chat/api";
import { sessionLifecycle } from "@/identity/sessionLifecycle";
import type {
  ConversationResponse,
  MessageDeltaEvent,
  MessageResponse,
  SubagentRun,
  SubagentStatusEvent,
  ThinkingEvent,
} from "@/features/chat/types";

type MessageState = Record<string, MessageResponse[]>;
type SubagentRunState = Record<string, Record<string, SubagentRun>>;

interface ChatState {
  conversations: ConversationResponse[];
  messagesByConversation: MessageState;
  subagentRunsByConversation: SubagentRunState;
  isLoadingMessages: boolean;
  streamingConversations: Set<string>;
  loadConversations: () => Promise<ConversationResponse[]>;
  createConversation: (initialMessage: string) => Promise<ConversationResponse | null>;
  deleteConversation: (conversationId: string) => Promise<boolean>;
  loadMessages: (conversationId: string) => Promise<MessageResponse[]>;
  syncMessages: (conversationId: string) => Promise<MessageResponse[]>;
  ensureConversation: (conversation: ConversationResponse) => void;
  appendMessage: (conversationId: string, message: MessageResponse) => void;
  appendThinking: (conversationId: string, event: ThinkingEvent) => void;
  appendMessageDelta: (conversationId: string, event: MessageDeltaEvent) => void;
  updateSubagentStatus: (conversationId: string, event: SubagentStatusEvent) => void;
  interruptRunningSubagents: (conversationId: string) => void;
  markStreaming: (conversationId: string) => void;
  finishStreaming: (conversationId: string, outcome: "complete" | "interrupted") => void;
  reset: () => void;
}

function emptyChatState() {
  return {
    conversations: [],
    messagesByConversation: {},
    subagentRunsByConversation: {},
    isLoadingMessages: false,
    streamingConversations: new Set<string>(),
  };
}

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

function mergeMessageSnapshot(
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

function upsertMessage(messages: MessageResponse[], message: MessageResponse): MessageResponse[] {
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

function appendThinkingDelta(
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

function appendTextDelta(
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

function settleThinking(
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

export const useChatStore = create<ChatState>()((set) => ({
  ...emptyChatState(),

  loadConversations: async () => {
    const generation = sessionLifecycle.current();
    const response = await chatApi.listConversations();
    const conversations = response.data.conversations;
    if (sessionLifecycle.isCurrent(generation)) set({ conversations });
    return conversations;
  },

  createConversation: async (initialMessage) => {
    const generation = sessionLifecycle.current();
    const response = await chatApi.createConversation(false, initialMessage);
    const conversation = response.data;
    if (!sessionLifecycle.isCurrent(generation)) return null;
    set((state) => ({
      conversations: [conversation, ...state.conversations],
      messagesByConversation: {
        ...state.messagesByConversation,
        [conversation.conversation_id]: [],
      },
    }));
    return conversation;
  },

  deleteConversation: async (conversationId) => {
    const generation = sessionLifecycle.current();
    await chatApi.deleteConversations([conversationId]);
    if (!sessionLifecycle.isCurrent(generation)) return false;
    set((state) => {
      const nextMessages = { ...state.messagesByConversation };
      const nextSubagentRuns = { ...state.subagentRunsByConversation };
      delete nextMessages[conversationId];
      delete nextSubagentRuns[conversationId];
      return {
        conversations: state.conversations.filter(
          (conversation) => conversation.conversation_id !== conversationId
        ),
        messagesByConversation: nextMessages,
        subagentRunsByConversation: nextSubagentRuns,
      };
    });
    return true;
  },

  loadMessages: async (conversationId) => {
    const generation = sessionLifecycle.current();
    set({ isLoadingMessages: true });
    try {
      const response = await chatApi.getMessages(conversationId);
      const messages = response.data.messages;
      if (sessionLifecycle.isCurrent(generation)) {
        set((state) => {
          return {
            messagesByConversation: {
              ...state.messagesByConversation,
              [conversationId]: mergeMessageSnapshot(
                messages,
                state.messagesByConversation[conversationId] ?? []
              ),
            },
          };
        });
      }
      return messages;
    } finally {
      if (sessionLifecycle.isCurrent(generation)) set({ isLoadingMessages: false });
    }
  },

  syncMessages: async (conversationId) => {
    const generation = sessionLifecycle.current();
    const response = await chatApi.getMessages(conversationId);
    const messages = response.data.messages;
    if (sessionLifecycle.isCurrent(generation)) {
      set((state) => ({
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: messages,
        },
      }));
    }
    return messages;
  },

  ensureConversation: (conversation) =>
    set((state) => {
      const exists = state.conversations.some(
        (item) => item.conversation_id === conversation.conversation_id
      );
      if (exists) {
        return state;
      }
      return {
        conversations: [conversation, ...state.conversations],
      };
    }),

  appendMessage: (conversationId, message) =>
    set((state) => {
      const current = state.messagesByConversation[conversationId] ?? [];
      const messages = upsertMessage(current, message);
      if (messages === current) return state;
      return {
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: messages,
        },
      };
    }),

  appendThinking: (conversationId, event) =>
    set((state) => {
      const current = state.messagesByConversation[conversationId] ?? [];
      return {
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: appendThinkingDelta(
            current,
            event.message_id,
            event.delta,
            event.reset
          ),
        },
      };
    }),

  appendMessageDelta: (conversationId, event) =>
    set((state) => {
      const current = state.messagesByConversation[conversationId] ?? [];
      return {
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: appendTextDelta(current, event.message_id, event.delta, event.reset),
        },
      };
    }),

  updateSubagentStatus: (conversationId, event) =>
    set((state) => ({
      subagentRunsByConversation: {
        ...state.subagentRunsByConversation,
        [conversationId]: {
          ...state.subagentRunsByConversation[conversationId],
          [event.delegation_id]: {
            delegationId: event.delegation_id,
            agentType: event.agent_type,
            status: event.status,
          },
        },
      },
    })),

  interruptRunningSubagents: (conversationId) =>
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId];
      if (!conversationRuns) return state;
      let changed = false;
      const nextRuns = Object.fromEntries(
        Object.entries(conversationRuns).map(([delegationId, run]) => {
          if (run.status !== "running") return [delegationId, run];
          changed = true;
          return [
            delegationId,
            {
              ...run,
              status: "interrupted" as const,
            },
          ];
        })
      );
      if (!changed) return state;
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: nextRuns,
        },
      };
    }),

  markStreaming: (conversationId) =>
    set((state) => ({
      conversations: state.conversations.map((conversation) =>
        conversation.conversation_id === conversationId
          ? { ...conversation, running: true }
          : conversation
      ),
      streamingConversations: new Set([...state.streamingConversations, conversationId]),
    })),

  finishStreaming: (conversationId, outcome) =>
    set((state) => {
      const next = new Set(state.streamingConversations);
      next.delete(conversationId);
      return {
        conversations: state.conversations.map((conversation) =>
          conversation.conversation_id === conversationId
            ? { ...conversation, running: false }
            : conversation
        ),
        streamingConversations: next,
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: settleThinking(
            state.messagesByConversation[conversationId] ?? [],
            outcome
          ),
        },
      };
    }),

  reset: () => set(emptyChatState()),
}));

const unsubscribeSessionReset = sessionLifecycle.subscribeReset(() => {
  useChatStore.getState().reset();
});

if (import.meta.hot) import.meta.hot.dispose(unsubscribeSessionReset);
