import { create } from "zustand";
import { chatApi } from "@/api/chat";
import type { Attachment, ConversationResponse, MessageSchema } from "@/types";

type MessageState = Record<number, MessageSchema[]>;
type AttachmentState = Record<number, Attachment[]>;

interface ChatState {
	conversations: ConversationResponse[];
	messagesByConversation: MessageState;
	attachmentsByConversation: AttachmentState;
	activeConversationId: number | null;
	draftConversationId: number | null;
	isLoadingConversations: boolean;
	isLoadingMessages: boolean;
	connectionState: "idle" | "connecting" | "open" | "closed";
	setConnectionState: (state: ChatState["connectionState"]) => void;
	setActiveConversationId: (conversationId: number | null) => void;
	setDraftConversationId: (conversationId: number | null) => void;
	loadConversations: () => Promise<ConversationResponse[]>;
	createConversation: () => Promise<ConversationResponse>;
	deleteConversation: (conversationId: number) => Promise<void>;
	loadMessages: (conversationId: number) => Promise<MessageSchema[]>;
	ensureConversation: (conversation: ConversationResponse) => void;
	appendMessage: (conversationId: number, message: MessageSchema) => void;
	replaceMessages: (conversationId: number, messages: MessageSchema[]) => void;
	appendAttachments: (conversationId: number, attachments: Attachment[]) => void;
	removeAttachment: (conversationId: number, attachmentName: string) => void;
	clearAttachments: (conversationId: number) => void;
}

export const useChatStore = create<ChatState>()((set, get) => ({
	conversations: [],
	messagesByConversation: {},
	attachmentsByConversation: {},
	activeConversationId: null,
	draftConversationId: null,
	isLoadingConversations: false,
	isLoadingMessages: false,
	connectionState: "idle",

	setConnectionState: (connectionState) => set({ connectionState }),

	setActiveConversationId: (activeConversationId) =>
		set({ activeConversationId }),

	setDraftConversationId: (draftConversationId) => set({ draftConversationId }),

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
			attachmentsByConversation: {
				...state.attachmentsByConversation,
				[conversation.conversation_id]: [],
			},
		}));
		return conversation;
	},

	deleteConversation: async (conversationId) => {
		await chatApi.deleteConversations([conversationId]);
		set((state) => {
			const nextMessages = { ...state.messagesByConversation };
			const nextAttachments = { ...state.attachmentsByConversation };
			delete nextMessages[conversationId];
			delete nextAttachments[conversationId];
			return {
				conversations: state.conversations.filter(
					(conversation) => conversation.conversation_id !== conversationId,
				),
				activeConversationId:
					state.activeConversationId === conversationId
						? null
						: state.activeConversationId,
				draftConversationId:
					state.draftConversationId === conversationId
						? null
						: state.draftConversationId,
				messagesByConversation: nextMessages,
				attachmentsByConversation: nextAttachments,
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
				(item) => item.conversation_id === conversation.conversation_id,
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

	appendAttachments: (conversationId, attachments) =>
		set((state) => {
			const nextByName = new Map(
				(state.attachmentsByConversation[conversationId] ?? []).map(
					(attachment) => [attachment.path, attachment],
				),
			);
			for (const attachment of attachments) {
				nextByName.set(attachment.path, attachment);
			}
			return {
				attachmentsByConversation: {
					...state.attachmentsByConversation,
					[conversationId]: [...nextByName.values()],
				},
			};
		}),

	removeAttachment: (conversationId, attachmentName) =>
		set((state) => ({
			attachmentsByConversation: {
				...state.attachmentsByConversation,
				[conversationId]: (state.attachmentsByConversation[conversationId] ?? [])
					.filter((attachment) => attachment.path !== attachmentName),
			},
		})),

	clearAttachments: (conversationId) =>
		set((state) => ({
			attachmentsByConversation: {
				...state.attachmentsByConversation,
				[conversationId]: [],
			},
		})),
}));
