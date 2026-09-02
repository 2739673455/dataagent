import { create } from "zustand";
import { chatApi } from "@/api/chat";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import type {
  ConversationResponse,
  MessageDeltaEvent,
  MessageResponse,
  SubagentMessageDeltaEvent,
  SubagentMessageEvent,
  SubagentRun,
  SubagentRunIdentity,
  SubagentStatusEvent,
  SubagentThinkingEvent,
  ThinkingEvent,
} from "@/types";

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
  renameConversation: (conversationId: string, title: string) => Promise<void>;
  loadMessages: (conversationId: string) => Promise<MessageResponse[]>;
  syncMessages: (conversationId: string) => Promise<MessageResponse[]>;
  ensureConversation: (conversation: ConversationResponse) => void;
  appendMessage: (conversationId: string, message: MessageResponse) => void;
  appendThinking: (conversationId: string, event: ThinkingEvent) => void;
  appendMessageDelta: (conversationId: string, event: MessageDeltaEvent) => void;
  appendSubagentMessage: (conversationId: string, event: SubagentMessageEvent) => void;
  appendSubagentMessageDelta: (conversationId: string, event: SubagentMessageDeltaEvent) => void;
  appendSubagentThinking: (conversationId: string, event: SubagentThinkingEvent) => void;
  updateSubagentStatus: (conversationId: string, event: SubagentStatusEvent) => void;
  loadSubagentMessages: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  interruptRunningSubagents: (conversationId: string) => void;
  markStreaming: (conversationId: string) => void;
  finishStreaming: (
    conversationId: string,
    outcome: "complete" | "interrupted"
  ) => void;
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

function createSubagentRun(
  identity: SubagentRunIdentity,
  status: SubagentRun["status"] = "running"
): SubagentRun {
  return {
    ...identity,
    status,
    messages: [],
    historyLoaded: false,
    historyLoading: false,
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

export const useChatStore = create<ChatState>()((set, get) => ({
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

  renameConversation: async (conversationId, title) => {
    const generation = sessionLifecycle.current();
    const normalizedTitle = title.trim();
    const previous = useChatStore
      .getState()
      .conversations.find((conversation) => conversation.conversation_id === conversationId);
    set((state) => ({
      conversations: state.conversations.map((conversation) =>
        conversation.conversation_id === conversationId
          ? { ...conversation, title: normalizedTitle }
          : conversation
      ),
    }));
    try {
      await chatApi.updateConversation(conversationId, normalizedTitle);
    } catch (error) {
      if (previous && sessionLifecycle.isCurrent(generation)) {
        set((state) => ({
          conversations: state.conversations.map((conversation) =>
            conversation.conversation_id === conversationId ? previous : conversation
          ),
        }));
      }
      throw error;
    }
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

  appendSubagentMessage: (conversationId, event) =>
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
      const identity: SubagentRunIdentity = {
        delegationId: event.delegation_id,
        analysisId: event.analysis_id,
        agentType: event.agent_type,
        sessionId: event.session_id,
        parentToolCallId: event.parent_tool_call_id,
        instruction: event.instruction,
      };
      const current = conversationRuns[event.delegation_id] ?? createSubagentRun(identity);
      const messages = upsertMessage(current.messages, event.message);
      if (messages === current.messages) return state;
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: {
            ...conversationRuns,
            [event.delegation_id]: {
              ...current,
              ...identity,
              messages,
            },
          },
        },
      };
    }),

  appendSubagentMessageDelta: (conversationId, event) =>
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
      const identity: SubagentRunIdentity = {
        delegationId: event.delegation_id,
        analysisId: event.analysis_id,
        agentType: event.agent_type,
        sessionId: event.session_id,
        parentToolCallId: event.parent_tool_call_id,
        instruction: event.instruction,
      };
      const current = conversationRuns[event.delegation_id] ?? createSubagentRun(identity);
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: {
            ...conversationRuns,
            [event.delegation_id]: {
              ...current,
              ...identity,
              messages: appendTextDelta(
                current.messages,
                event.message_id,
                event.delta,
                event.reset
              ),
            },
          },
        },
      };
    }),

  appendSubagentThinking: (conversationId, event) =>
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
      const identity: SubagentRunIdentity = {
        delegationId: event.delegation_id,
        analysisId: event.analysis_id,
        agentType: event.agent_type,
        sessionId: event.session_id,
        parentToolCallId: event.parent_tool_call_id,
        instruction: event.instruction,
      };
      const current = conversationRuns[event.delegation_id] ?? createSubagentRun(identity);
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: {
            ...conversationRuns,
            [event.delegation_id]: {
              ...current,
              ...identity,
              messages: appendThinkingDelta(
                current.messages,
                event.message_id,
                event.delta,
                event.reset
              ),
            },
          },
        },
      };
    }),

  updateSubagentStatus: (conversationId, event) =>
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
      const identity: SubagentRunIdentity = {
        delegationId: event.delegation_id,
        analysisId: event.analysis_id,
        agentType: event.agent_type,
        sessionId: event.session_id,
        parentToolCallId: event.parent_tool_call_id,
        instruction: event.instruction,
      };
      const current = conversationRuns[event.delegation_id] ?? createSubagentRun(identity);
      const thinkingStatus =
        event.status === "completed" || event.status === "needs_repair"
          ? "complete"
          : event.status === "running"
            ? null
            : "interrupted";
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: {
            ...conversationRuns,
            [event.delegation_id]: {
              ...current,
              ...identity,
              status: event.status,
              messages:
                thinkingStatus === null
                  ? current.messages
                  : settleThinking(current.messages, thinkingStatus),
              historyLoaded: event.status === "running" ? current.historyLoaded : true,
            },
          },
        },
      };
    }),

  loadSubagentMessages: async (conversationId, identity) => {
    const existing = get().subagentRunsByConversation[conversationId]?.[identity.delegationId];
    if (existing?.historyLoaded) return existing.messages;
    if (existing?.historyLoading) return existing.messages;
    const generation = sessionLifecycle.current();
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
      const current =
        conversationRuns[identity.delegationId] ?? createSubagentRun(identity, "interrupted");
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: {
            ...conversationRuns,
            [identity.delegationId]: { ...current, ...identity, historyLoading: true },
          },
        },
      };
    });
    try {
      const response = await chatApi.getSubagentMessages(conversationId, identity);
      const messages = response.data.messages;
      if (sessionLifecycle.isCurrent(generation)) {
        set((state) => {
          const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
          const current =
            conversationRuns[identity.delegationId] ?? createSubagentRun(identity, "interrupted");
          let merged = [...current.messages];
          for (const message of messages) {
            merged = upsertMessage(merged, message);
          }
          return {
            subagentRunsByConversation: {
              ...state.subagentRunsByConversation,
              [conversationId]: {
                ...conversationRuns,
                [identity.delegationId]: {
                  ...current,
                  ...identity,
                  status: response.data.status,
                  messages: merged,
                  historyLoaded: true,
                  historyLoading: false,
                },
              },
            },
          };
        });
      }
      return messages;
    } catch (error) {
      if (sessionLifecycle.isCurrent(generation)) {
        set((state) => {
          const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
          const current = conversationRuns[identity.delegationId];
          if (!current) return state;
          return {
            subagentRunsByConversation: {
              ...state.subagentRunsByConversation,
              [conversationId]: {
                ...conversationRuns,
                [identity.delegationId]: { ...current, historyLoading: false },
              },
            },
          };
        });
      }
      throw error;
    }
  },

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
              messages: settleThinking(run.messages, "interrupted"),
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
