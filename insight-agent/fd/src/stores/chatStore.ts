import { create } from "zustand";
import { chatApi } from "@/api/chat";
import type { ConversationResponse, MessageSchema } from "@/types";

type MessageState = Record<number, MessageSchema[]>;

interface ChatState {
	conversations: ConversationResponse[];
	messagesByConversation: MessageState;
	activeConversationId: number | null;
	isLoadingConversations: boolean;
	isLoadingMessages: boolean;
	connectionState: "idle" | "connecting" | "open" | "closed";
	setConnectionState: (state: ChatState["connectionState"]) => void;
	setActiveConversationId: (conversationId: number | null) => void;
	loadConversations: () => Promise<ConversationResponse[]>;
	createConversation: () => Promise<ConversationResponse>;
	deleteConversation: (conversationId: number) => Promise<void>;
	loadMessages: (conversationId: number) => Promise<MessageSchema[]>;
	appendMessage: (conversationId: number, message: MessageSchema) => void;
	replaceMessages: (conversationId: number, messages: MessageSchema[]) => void;
}

export const useChatStore = create<ChatState>()((set, get) => ({
	conversations: [],
	messagesByConversation: {},
	activeConversationId: null,
	isLoadingConversations: false,
	isLoadingMessages: false,
	connectionState: "idle",

	setConnectionState: (connectionState) => set({ connectionState }),

	setActiveConversationId: (activeConversationId) =>
		set({ activeConversationId }),

	loadConversations: async () => {
		set({ isLoadingConversations: true });
		try {
			const response = await chatApi.listConversations();
			const conversations = response.data.conversations;
			set({ conversations });
			return conversations;
		} finally {
			set({ isLoadingConversations: false });
		}
	},

	createConversation: async () => {
		const response = await chatApi.createConversation();
		const conversation = response.data;
		set((state) => ({
			conversations: [conversation, ...state.conversations],
			activeConversationId: conversation.conversation_id,
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
					(conversation) => conversation.conversation_id !== conversationId,
				),
				activeConversationId:
					state.activeConversationId === conversationId
						? null
						: state.activeConversationId,
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

	appendMessage: (conversationId, message) =>
		set((state) => ({
			messagesByConversation: {
				...state.messagesByConversation,
				[conversationId]: [
					...(state.messagesByConversation[conversationId] ?? []),
					message,
				],
			},
		})),

	replaceMessages: (conversationId, messages) =>
		set((state) => ({
			messagesByConversation: {
				...state.messagesByConversation,
				[conversationId]: messages,
			},
		})),
}));
