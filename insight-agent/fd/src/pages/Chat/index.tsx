import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { createLocalTimestamp } from "@/lib/message";
import { redirectToAuthorize } from "@/lib/redirect";
import { getAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import type {
	Attachment,
	MessageSchema,
	WebSocketErrorResponse,
	WebSocketMessageResponse,
} from "@/types";
import { ChatComposer } from "./components/ChatComposer";
import { ChatMessages } from "./components/ChatMessages";
import { ChatSidebar } from "./components/ChatSidebar";

interface PendingMessageState {
	conversationId: number;
	message: MessageSchema;
}

function isImageFile(name: string) {
	return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

export default function ChatPage() {
	const navigate = useNavigate();
	const params = useParams();
	const conversations = useChatStore((state) => state.conversations);
	const messagesByConversation = useChatStore(
		(state) => state.messagesByConversation,
	);
	const connectionState = useChatStore((state) => state.connectionState);
	const isLoadingMessages = useChatStore((state) => state.isLoadingMessages);
	const loadConversations = useChatStore((state) => state.loadConversations);
	const createConversation = useChatStore((state) => state.createConversation);
	const deleteConversation = useChatStore((state) => state.deleteConversation);
	const loadMessages = useChatStore((state) => state.loadMessages);
	const ensureConversation = useChatStore((state) => state.ensureConversation);
	const appendMessage = useChatStore((state) => state.appendMessage);
	const setConnectionState = useChatStore((state) => state.setConnectionState);
	const logout = useAuthStore((state) => state.logout);
	const user = useAuthStore((state) => state.user);
	const socketRef = useRef<WebSocket | null>(null);
	const pendingMessageRef = useRef<PendingMessageState | null>(null);
	const isClosingSocketRef = useRef(false);
	const messageViewportRef = useRef<HTMLDivElement | null>(null);
	const attachmentsRef = useRef<Attachment[]>([]);
	const [draftConversationId, setDraftConversationId] = useState<number | null>(
		null,
	);
	const [attachments, setAttachments] = useState<Attachment[]>([]);
	const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);
	const [isStreaming, setIsStreaming] = useState(false);
	const [socketVersion, setSocketVersion] = useState(0);

	const routeConversationId = (() => {
		const raw = params.conversationId;
		if (!raw) return null;
		const parsed = Number(raw);
		return Number.isNaN(parsed) ? null : parsed;
	})();

	const currentMessages = routeConversationId
		? (messagesByConversation[routeConversationId] ?? [])
		: [];
	const currentMessageCount = currentMessages.length;

	useEffect(() => {
		attachmentsRef.current = attachments;
	}, [attachments]);

	const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
		const viewport = messageViewportRef.current;
		if (!viewport) return;

		viewport.scrollTo({
			top: viewport.scrollHeight,
			behavior,
		});
	}, []);

	useEffect(() => {
		void loadConversations();
	}, [loadConversations]);

	useEffect(() => {
		if (!routeConversationId) return;
		if (messagesByConversation[routeConversationId] === undefined) {
			void loadMessages(routeConversationId);
		}
	}, [
		loadMessages,
		messagesByConversation,
		routeConversationId,
	]);

	useEffect(() => {
		if (!routeConversationId) return;
		setDraftConversationId(null);
		setAttachments([]);
	}, [routeConversationId]);

	useEffect(() => {
		if (!routeConversationId || isLoadingMessages) return;
		if (currentMessageCount < 1) return;

		const frameId = window.requestAnimationFrame(() => {
			scrollToBottom("auto");
		});

		return () => window.cancelAnimationFrame(frameId);
	}, [
		currentMessageCount,
		isLoadingMessages,
		routeConversationId,
		scrollToBottom,
	]);

	useEffect(() => {
		void socketVersion;

		const token = getAccessToken();
		if (!routeConversationId || !token) return;

		let cancelled = false;

		const connectSocket = async () => {
			try {
				setConnectionState("connecting");
				const response = await chatApi.createWebSocketToken();
				if (cancelled) return;

				const socket = chatApi.buildChatSocket(
					routeConversationId,
					response.data.websocket_token,
				);
				socketRef.current = socket;

				socket.onopen = () => {
					isClosingSocketRef.current = false;
					setConnectionState("open");

					if (pendingMessageRef.current?.conversationId === routeConversationId) {
						socket.send(
							chatApi.serializeChatRequest({
								message: pendingMessageRef.current.message,
							}),
						);
						pendingMessageRef.current = null;
					}
				};

				socket.onmessage = (event) => {
					const payload = JSON.parse(event.data) as
						| WebSocketMessageResponse
						| WebSocketErrorResponse;

					if (payload.type === "error") {
						setIsStreaming(false);
						toast.error(payload.content);
						return;
					}

					appendMessage(routeConversationId, payload.message);
					if (payload.message.finish_reason === "stop") {
						setIsStreaming(false);
						void loadConversations();
					}
				};

				socket.onclose = (event) => {
					setConnectionState("closed");
					socketRef.current = null;
					const isIntentionalClose = isClosingSocketRef.current;
					isClosingSocketRef.current = false;

					if (event.code === 4401) {
						redirectToAuthorize(
							`${window.location.pathname}${window.location.search}`,
						);
						return;
					}

					if (event.code === 4404) {
						setIsStreaming(false);
						toast.error("对话不存在或无权限访问");
					}

					if (
						!isIntentionalClose &&
						event.code !== 1000 &&
						event.code !== 1005
					) {
						setIsStreaming(false);
						toast.error("聊天连接已断开");
					}
				};

				socket.onerror = () => {
					if (isClosingSocketRef.current) return;
					setIsStreaming(false);
					toast.error("聊天连接异常");
				};
			} catch {
				if (cancelled) return;
				setConnectionState("closed");
				setIsStreaming(false);
				toast.error("聊天连接初始化失败");
			}
		};

		void connectSocket();

		return () => {
			cancelled = true;
			isClosingSocketRef.current = true;
			socketRef.current?.close();
			socketRef.current = null;
		};
	}, [
		appendMessage,
		loadConversations,
		routeConversationId,
		setConnectionState,
		socketVersion,
	]);

	const handleCreateConversation = () => {
		pendingMessageRef.current = null;
		for (const attachment of attachments) {
			if (attachment.preview_url) {
				URL.revokeObjectURL(attachment.preview_url);
			}
		}
		setAttachments([]);
		setDraftConversationId(null);
		navigate("/chat");
	};

	const handleDeleteConversation = async (conversationId: number) => {
		await deleteConversation(conversationId);
		if (routeConversationId === conversationId) {
			navigate("/chat");
		}
		toast.success("对话已删除");
	};

	const handleStop = () => {
		setIsStreaming(false);
		pendingMessageRef.current = null;
		if (socketRef.current) {
			isClosingSocketRef.current = true;
			socketRef.current.close(1000);
			socketRef.current = null;
			setSocketVersion((value) => value + 1);
		}
	};

	const handleAttachmentsSelected = async (files: File[]) => {
		const token = getAccessToken();
		if (!token) {
			redirectToAuthorize(
				`${window.location.pathname}${window.location.search}`,
			);
			return;
		}

		setIsUploadingAttachments(true);
		try {
			let nextConversationId = routeConversationId ?? draftConversationId;
			if (!nextConversationId) {
				const response = await chatApi.createConversation(1);
				nextConversationId = response.data.conversation_id;
				setDraftConversationId(nextConversationId);
				void loadConversations();
			}
			const nextAttachments: Attachment[] = [];
			for (const file of files) {
				const response = await chatApi.uploadAttachment(nextConversationId, file);
				nextAttachments.push({
					...response.data.attachment,
					preview_url: isImageFile(file.name)
						? URL.createObjectURL(file)
						: undefined,
				});
			}
			if (nextAttachments.length > 0) {
				setAttachments((current) => [...current, ...nextAttachments]);
			}
		} catch {
			toast.error("附件上传失败");
		} finally {
			setIsUploadingAttachments(false);
		}
	};

	const handleRemoveAttachment = async (attachmentName: string) => {
		const targetConversationId = routeConversationId ?? draftConversationId;
		if (!targetConversationId) {
			return;
		}

		try {
			await chatApi.deleteAttachment(targetConversationId, attachmentName);
			setAttachments((current) => {
				const target = current.find(
					(attachment) => attachment.path === attachmentName,
				);
				if (target?.preview_url) {
					URL.revokeObjectURL(target.preview_url);
				}
				return current.filter((attachment) => attachment.path !== attachmentName);
			});
		} catch {
			toast.error("附件删除失败");
		}
	};

	const handleSend = async (value: string) => {
		const token = getAccessToken();
		if (!token) {
			redirectToAuthorize(
				`${window.location.pathname}${window.location.search}`,
			);
			return;
		}

		const userMessage: MessageSchema = {
			role: "user",
			parts: value ? [{ type: "text", text: value }] : [],
			attachments: attachments.length > 0 ? attachments : undefined,
			timestamp: createLocalTimestamp(),
		};

		let conversationId = routeConversationId ?? draftConversationId;
		if (!conversationId) {
			const conversation = await createConversation();
			conversationId = conversation.conversation_id;
			pendingMessageRef.current = {
				conversationId,
				message: userMessage,
			};
			setIsStreaming(true);
			appendMessage(conversationId, userMessage);
			for (const attachment of attachments) {
				if (attachment.preview_url) {
					URL.revokeObjectURL(attachment.preview_url);
				}
			}
			setAttachments([]);
			navigate(`/chat/${conversationId}`);
			return;
		}

		if (!routeConversationId) {
			setDraftConversationId(null);
			ensureConversation({
				conversation_id: conversationId,
				title: "新对话",
				update_at: new Date().toISOString(),
			});
			pendingMessageRef.current = {
				conversationId,
				message: userMessage,
			};
			setIsStreaming(true);
			appendMessage(conversationId, userMessage);
			for (const attachment of attachments) {
				if (attachment.preview_url) {
					URL.revokeObjectURL(attachment.preview_url);
				}
			}
			setAttachments([]);
			navigate(`/chat/${conversationId}`);
			return;
		}

		const socket = socketRef.current;
		if (!conversationId || !socket || socket.readyState !== WebSocket.OPEN) {
			toast.error("连接尚未建立，请稍后重试");
			return;
		}

		appendMessage(conversationId, userMessage);
		setIsStreaming(true);
		for (const attachment of attachments) {
			if (attachment.preview_url) {
				URL.revokeObjectURL(attachment.preview_url);
			}
		}
		setAttachments([]);
		socket.send(chatApi.serializeChatRequest({ message: userMessage }));
	};

	useEffect(() => {
		return () => {
			for (const attachment of attachmentsRef.current) {
				if (attachment.preview_url) {
					URL.revokeObjectURL(attachment.preview_url);
				}
			}
		};
	}, []);

	return (
		<div
			className="min-h-screen h-[100dvh] overflow-hidden bg-[#fefdfa]"
			style={{ fontFeatureSettings: '"cv11", "ss01"' }}
		>
			<div className="grid h-full min-h-0 chat-grid">
				<ChatSidebar
					conversations={conversations}
					activeConversationId={routeConversationId}
					user={user}
					onCreate={handleCreateConversation}
					onDelete={(conversationId) =>
						void handleDeleteConversation(conversationId)
					}
					onLogout={() => {
						void logout().then(() => redirectToAuthorize("/chat"));
					}}
				/>

				<div className="flex h-full min-h-0 flex-col bg-[#fefdfa]">
					<ChatMessages
						conversationId={routeConversationId}
						conversationSelected={Boolean(routeConversationId)}
						isLoading={isLoadingMessages}
						messages={currentMessages}
						viewportRef={messageViewportRef}
					/>
					<div className="sticky bottom-0 z-10 w-full shrink-0 bg-[#fefdfa] pb-6 pt-0">
						<div className="mx-auto w-[70%] min-w-[320px] max-w-[1120px]">
							<ChatComposer
								attachments={attachments}
								isStreaming={isStreaming}
								isUploading={isUploadingAttachments}
								disabled={
									connectionState !== "open" && Boolean(routeConversationId)
								}
								onAttachmentsSelected={handleAttachmentsSelected}
								onRemoveAttachment={handleRemoveAttachment}
								onStop={handleStop}
								onSubmit={handleSend}
							/>
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}
