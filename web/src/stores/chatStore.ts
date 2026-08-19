import { create } from "zustand";
import { chatApi } from "@/api/chat";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import type { ConversationResponse, MessageSchema } from "@/types";

type MessageState = Record<string, MessageSchema[]>;

interface ChatState {
  conversations: ConversationResponse[];
  messagesByConversation: MessageState;
  isLoadingMessages: boolean;
  streamingConversations: Set<string>;
  loadConversations: () => Promise<ConversationResponse[]>;
  createConversation: (initialMessage: string) => Promise<ConversationResponse | null>;
  deleteConversation: (conversationId: string) => Promise<boolean>;
  loadMessages: (conversationId: string) => Promise<MessageSchema[]>;
  ensureConversation: (conversation: ConversationResponse) => void;
  appendMessage: (conversationId: string, message: MessageSchema) => void;
  markStreaming: (conversationId: string) => void;
  unmarkStreaming: (conversationId: string) => void;
  reset: () => void;
}

function emptyChatState() {
  return {
    conversations: [],
    messagesByConversation: {},
    isLoadingMessages: false,
    streamingConversations: new Set<string>(),
  };
}

export const useChatStore = create<ChatState>()((set, _get) => ({
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
    const response = await chatApi.createConversation(0, initialMessage);
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
      delete nextMessages[conversationId];
      return {
        conversations: state.conversations.filter(
          (conversation) => conversation.conversation_id !== conversationId
        ),
        messagesByConversation: nextMessages,
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
