import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { redirectToAuthorize } from "@/lib/redirect";
import { getAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import type {
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

export default function ChatPage() {
	const navigate = useNavigate();
	const params = useParams();
	const activeConversationId = useChatStore(
		(state) => state.activeConversationId,
	);
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
	const appendMessage = useChatStore((state) => state.appendMessage);
	const setConnectionState = useChatStore((state) => state.setConnectionState);
	const setActiveConversationId = useChatStore(
		(state) => state.setActiveConversationId,
	);
	const logout = useAuthStore((state) => state.logout);
	const user = useAuthStore((state) => state.user);
	const socketRef = useRef<WebSocket | null>(null);
	const pendingMessageRef = useRef<PendingMessageState | null>(null);
	const isClosingSocketRef = useRef(false);
	const streamIdleTimerRef = useRef<number | null>(null);
	const messageViewportRef = useRef<HTMLDivElement | null>(null);
	const [isStreaming, setIsStreaming] = useState(false);
	const [socketVersion, setSocketVersion] = useState(0);

	const routeConversationId = (() => {
		const raw = params.conversationId;
		if (!raw) return null;
		const parsed = Number(raw);
		return Number.isNaN(parsed) ? null : parsed;
	})();

	const currentMessages = activeConversationId
		? (messagesByConversation[activeConversationId] ?? [])
		: [];
	const currentMessageCount = currentMessages.length;

	const scheduleStreamIdle = useCallback(() => {
		if (streamIdleTimerRef.current !== null) {
			window.clearTimeout(streamIdleTimerRef.current);
		}
		streamIdleTimerRef.current = window.setTimeout(() => {
			setIsStreaming(false);
			streamIdleTimerRef.current = null;
		}, 900);
	}, []);

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
		setActiveConversationId(routeConversationId);
		if (messagesByConversation[routeConversationId] === undefined) {
			void loadMessages(routeConversationId);
		}
	}, [
		loadMessages,
		messagesByConversation,
		routeConversationId,
		setActiveConversationId,
	]);

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
		if (!activeConversationId || !token) return;

		setConnectionState("connecting");
		const socket = chatApi.buildChatSocket(activeConversationId, token);
		socketRef.current = socket;

		socket.onopen = () => {
			isClosingSocketRef.current = false;
			setConnectionState("open");

			if (pendingMessageRef.current?.conversationId === activeConversationId) {
				socket.send(
					chatApi.serializeChatRequest({
						message: pendingMessageRef.current.message,
					}),
				);
				scheduleStreamIdle();
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

			scheduleStreamIdle();
			appendMessage(activeConversationId, payload.message);
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

			if (!isIntentionalClose && event.code !== 1000 && event.code !== 1005) {
				setIsStreaming(false);
				toast.error("聊天连接已断开");
			}
		};

		socket.onerror = () => {
			if (isClosingSocketRef.current) return;
			setIsStreaming(false);
			toast.error("聊天连接异常");
		};

		return () => {
			if (streamIdleTimerRef.current !== null) {
				window.clearTimeout(streamIdleTimerRef.current);
				streamIdleTimerRef.current = null;
			}
			isClosingSocketRef.current = true;
			socket.close();
			socketRef.current = null;
		};
	}, [
		activeConversationId,
		appendMessage,
		scheduleStreamIdle,
		setConnectionState,
		socketVersion,
	]);

	const handleCreateConversation = () => {
		pendingMessageRef.current = null;
		setActiveConversationId(null);
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
		if (streamIdleTimerRef.current !== null) {
			window.clearTimeout(streamIdleTimerRef.current);
			streamIdleTimerRef.current = null;
		}
		pendingMessageRef.current = null;
		if (socketRef.current) {
			isClosingSocketRef.current = true;
			socketRef.current.close(1000);
			socketRef.current = null;
			setSocketVersion((value) => value + 1);
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
			parts: [{ type: "text", text: value }],
			timestamp: new Date().toISOString(),
		};

		let conversationId = activeConversationId;
		if (!conversationId) {
			const conversation = await createConversation();
			conversationId = conversation.conversation_id;
			pendingMessageRef.current = {
				conversationId,
				message: userMessage,
			};
			setIsStreaming(true);
			scheduleStreamIdle();
			appendMessage(conversationId, userMessage);
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
		scheduleStreamIdle();
		socket.send(chatApi.serializeChatRequest({ message: userMessage }));
	};

	return (
		<div
			className="min-h-screen h-[100dvh] overflow-hidden bg-[#f7f7f7]"
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

				<div className="flex h-full min-h-0 flex-col">
					<ChatMessages
						conversationSelected={Boolean(routeConversationId)}
						isLoading={isLoadingMessages}
						messages={currentMessages}
						viewportRef={messageViewportRef}
					/>
					<div className="sticky bottom-0 z-10 w-full shrink-0 bg-white px-8 pb-4 pt-0">
						<ChatComposer
							isStreaming={isStreaming}
							disabled={
								connectionState !== "open" && Boolean(routeConversationId)
							}
							onStop={handleStop}
							onSubmit={handleSend}
						/>
					</div>
				</div>
			</div>
		</div>
	);
}
