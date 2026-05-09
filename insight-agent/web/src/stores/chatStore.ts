import { create } from "zustand";
import { chatApi } from "@/api/chat";
import type { ConversationResponse, MessageSchema } from "@/types";

type MessageState = Record<number, MessageSchema[]>;

interface ChatState {
  conversations: ConversationResponse[];
  messagesByConversation: MessageState;
  isLoadingMessages: boolean;
  connectionState: "idle" | "connecting" | "open" | "closed";
  streamingConversations: Set<number>;
  setConnectionState: (state: ChatState["connectionState"]) => void;
  loadConversations: () => Promise<ConversationResponse[]>;
  createConversation: () => Promise<ConversationResponse>;
  deleteConversation: (conversationId: number) => Promise<void>;
  loadMessages: (conversationId: number) => Promise<MessageSchema[]>;
  ensureConversation: (conversation: ConversationResponse) => void;
  appendMessage: (conversationId: number, message: MessageSchema) => void;
  markStreaming: (conversationId: number) => void;
  unmarkStreaming: (conversationId: number) => void;
}

export const useChatStore = create<ChatState>()((set, _get) => ({
  conversations: [],
  messagesByConversation: {},
  isLoadingMessages: false,
  connectionState: "idle",
  streamingConversations: new Set<number>(),

  setConnectionState: (connectionState) => set({ connectionState }),

  loadConversations: async () => {
    const response = await chatApi.listConversations();
    const conversations = response.data.conversations;
    set({ conversations });
    return conversations;
  },

  createConversation: async () => {
    const response = await chatApi.createConversation();
    const conversation = response.data;
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
    await chatApi.deleteConversations([conversationId]);
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
  },

  loadMessages: async (conversationId) => {
    set({ isLoadingMessages: true });
    try {
      const response = await chatApi.getMessages(conversationId);
      const messages = response.data.messages;
      set((state) => ({
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: messages,
        },
      }));
      return messages;
    } finally {
      set({ isLoadingMessages: false });
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
}));
