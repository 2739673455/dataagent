import { create } from "zustand";
import { chatApi } from "@/api/chat";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import type {
  ConversationResponse,
  MessageResponse,
  SubagentMessageEvent,
  SubagentRun,
  SubagentRunIdentity,
  SubagentStatusEvent,
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
  ensureConversation: (conversation: ConversationResponse) => void;
  appendMessage: (conversationId: string, message: MessageResponse) => void;
  appendSubagentMessage: (conversationId: string, event: SubagentMessageEvent) => void;
  updateSubagentStatus: (conversationId: string, event: SubagentStatusEvent) => void;
  loadSubagentMessages: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  interruptRunningSubagents: (conversationId: string) => void;
  markStreaming: (conversationId: string) => void;
  unmarkStreaming: (conversationId: string) => void;
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
  return (
    message.message_id != null &&
    messages.some((candidate) => candidate.message_id === message.message_id)
  );
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
        set((state) => ({
          messagesByConversation: {
            ...state.messagesByConversation,
            [conversationId]: messages,
          },
        }));
      }
      return messages;
    } finally {
      if (sessionLifecycle.isCurrent(generation)) set({ isLoadingMessages: false });
    }
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
    set((state) => ({
      messagesByConversation: {
        ...state.messagesByConversation,
        [conversationId]: [...(state.messagesByConversation[conversationId] ?? []), message],
      },
    })),

  appendSubagentMessage: (conversationId, event) =>
    set((state) => {
      const conversationRuns = state.subagentRunsByConversation[conversationId] ?? {};
      const identity: SubagentRunIdentity = {
        delegationId: event.delegation_id,
        analysisId: event.analysis_id,
        agentType: event.agent_type,
        sessionId: event.session_id,
      };
      const current = conversationRuns[event.delegation_id] ?? createSubagentRun(identity);
      if (messageAlreadyExists(current.messages, event.message)) return state;
      return {
        subagentRunsByConversation: {
          ...state.subagentRunsByConversation,
          [conversationId]: {
            ...conversationRuns,
            [event.delegation_id]: {
              ...current,
              ...identity,
              messages: [...current.messages, event.message],
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
              status: event.status,
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
        conversationRuns[identity.delegationId] ?? createSubagentRun(identity, "completed");
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
            conversationRuns[identity.delegationId] ?? createSubagentRun(identity, "completed");
          const merged = [...current.messages];
          for (const message of messages) {
            if (!messageAlreadyExists(merged, message)) merged.push(message);
          }
          return {
            subagentRunsByConversation: {
              ...state.subagentRunsByConversation,
              [conversationId]: {
                ...conversationRuns,
                [identity.delegationId]: {
                  ...current,
                  ...identity,
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
          return [delegationId, { ...run, status: "interrupted" as const }];
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
      streamingConversations: new Set([...state.streamingConversations, conversationId]),
    })),

  unmarkStreaming: (conversationId) =>
    set((state) => {
      const next = new Set(state.streamingConversations);
      next.delete(conversationId);
      return { streamingConversations: next };
    }),

  reset: () => set(emptyChatState()),
}));

const unsubscribeSessionReset = sessionLifecycle.subscribeReset(() => {
  useChatStore.getState().reset();
});

if (import.meta.hot) import.meta.hot.dispose(unsubscribeSessionReset);
