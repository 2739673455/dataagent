import { Loader2, LogOut, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { ConversationSidebar } from "@/components/chat/ConversationSidebar";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { MessageComposer } from "@/components/chat/MessageComposer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getMessagePreview } from "@/lib/message";
import { redirectToAuthorize } from "@/lib/redirect";
import { getAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import type {
	MessageSchema,
	WebSocketErrorResponse,
	WebSocketMessageResponse,
} from "@/types";

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
	const isLoadingConversations = useChatStore(
		(state) => state.isLoadingConversations,
	);
	const isLoadingMessages = useChatStore((state) => state.isLoadingMessages);
	const loadConversations = useChatStore((state) => state.loadConversations);
	const createConversation = useChatStore((state) => state.createConversation);
	const loadMessages = useChatStore((state) => state.loadMessages);
	const appendMessage = useChatStore((state) => state.appendMessage);
	const setConnectionState = useChatStore((state) => state.setConnectionState);
	const setActiveConversationId = useChatStore(
		(state) => state.setActiveConversationId,
	);
	const logout = useAuthStore((state) => state.logout);
	const user = useAuthStore((state) => state.user);
	const socketRef = useRef<WebSocket | null>(null);

	const routeConversationId = useMemo(() => {
		const raw = params.conversationId;
		if (!raw) return null;
		const parsed = Number(raw);
		return Number.isNaN(parsed) ? null : parsed;
	}, [params.conversationId]);

	const currentMessages = activeConversationId
		? (messagesByConversation[activeConversationId] ?? [])
		: [];

	const conversationPreviewMap = useMemo(() => {
		return Object.fromEntries(
			Object.entries(messagesByConversation).map(([key, value]) => {
				const latest = value.at(-1);
				return [Number(key), latest ? getMessagePreview(latest) : ""];
			}),
		) as Record<number, string>;
	}, [messagesByConversation]);

	useEffect(() => {
		void loadConversations();
	}, [loadConversations]);

	useEffect(() => {
		if (!conversations.length || routeConversationId) return;
		navigate(`/chat/${conversations[0].conversation_id}`, { replace: true });
	}, [conversations, navigate, routeConversationId]);

	useEffect(() => {
		if (!routeConversationId) return;
		setActiveConversationId(routeConversationId);
		void loadMessages(routeConversationId);
	}, [loadMessages, routeConversationId, setActiveConversationId]);

	useEffect(() => {
		const token = getAccessToken();
		if (!activeConversationId || !token) return;

		setConnectionState("connecting");
		const socket = chatApi.buildChatSocket(activeConversationId, token);
		socketRef.current = socket;

		socket.onopen = () => {
			setConnectionState("open");
		};

		socket.onmessage = (event) => {
			const payload = JSON.parse(event.data) as
				| WebSocketMessageResponse
				| WebSocketErrorResponse;

			if (payload.type === "error") {
				toast.error(payload.content);
				return;
			}

			appendMessage(activeConversationId, payload.message);
		};

		socket.onclose = (event) => {
			setConnectionState("closed");
			socketRef.current = null;

			if (event.code === 4401) {
				redirectToAuthorize(
					`${window.location.pathname}${window.location.search}`,
				);
				return;
			}

			if (event.code === 4404) {
				toast.error("对话不存在或无权限访问");
			}
		};

		socket.onerror = () => {
			toast.error("聊天连接异常");
		};

		return () => {
			socket.close();
			socketRef.current = null;
		};
	}, [activeConversationId, appendMessage, setConnectionState]);

	const handleCreateConversation = async () => {
		const conversation = await createConversation();
		navigate(`/chat/${conversation.conversation_id}`);
	};

	const handleSend = async (value: string) => {
		const token = getAccessToken();
		if (!token) {
			redirectToAuthorize(
				`${window.location.pathname}${window.location.search}`,
			);
			return;
		}

		let conversationId = activeConversationId;
		if (!conversationId) {
			const conversation = await createConversation();
			conversationId = conversation.conversation_id;
			navigate(`/chat/${conversationId}`);
		}

		const socket = socketRef.current;
		if (!conversationId || !socket || socket.readyState !== WebSocket.OPEN) {
			toast.error("连接尚未建立，请稍后重试");
			return;
		}

		const userMessage: MessageSchema = {
			role: "user",
			parts: [{ type: "text", text: value }],
			timestamp: new Date().toISOString(),
		};

		appendMessage(conversationId, userMessage);
		socket.send(chatApi.serializeChatRequest({ message: userMessage }));
	};

	return (
		<div className="min-h-screen p-4 sm:p-6">
			<div className="mx-auto grid max-w-[1600px] gap-4 chat-grid">
				<ConversationSidebar
					conversations={conversations}
					activeConversationId={routeConversationId}
					currentPreview={conversationPreviewMap}
					isLoading={isLoadingConversations}
					onRefresh={() => void loadConversations()}
					onCreate={() => void handleCreateConversation()}
				/>

				<Card className="flex h-[calc(100vh-3rem)] flex-col overflow-hidden">
					<div className="flex items-center justify-between gap-4 p-6">
						<div>
							<Badge variant="accent" className="mb-3">
								<Sparkles className="mr-1 h-3.5 w-3.5" />
								工具增强会话
							</Badge>
							<h1 className="text-2xl font-semibold tracking-tight">
								Insight Agent
							</h1>
							<p className="mt-1 text-sm text-muted-foreground">
								{user ? `${user.username} · ${user.email}` : "正在建立身份状态"}
							</p>
						</div>
						<div className="flex items-center gap-3">
							<Badge variant={connectionState === "open" ? "default" : "muted"}>
								{connectionState === "open"
									? "ws connected"
									: connectionState === "connecting"
										? "ws connecting"
										: "ws closed"}
							</Badge>
							<Button
								variant="outline"
								onClick={() => {
									void logout().then(() => redirectToAuthorize("/chat"));
								}}
							>
								<LogOut className="h-4 w-4" />
								退出
							</Button>
						</div>
					</div>
					<Separator />
					<div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
						{!routeConversationId ? (
							<div className="flex h-full items-center justify-center">
								<div className="max-w-xl text-center">
									<Badge variant="accent" className="mb-4">
										新会话
									</Badge>
									<h2 className="text-3xl font-semibold tracking-tight">
										从一个问题开始
									</h2>
									<p className="mt-4 text-sm leading-7 text-muted-foreground">
										这里会展示用户消息、模型回复和工具执行结果
									</p>
									<Button
										className="mt-6"
										onClick={() => void handleCreateConversation()}
									>
										创建对话
									</Button>
								</div>
							</div>
						) : isLoadingMessages ? (
							<div className="flex h-full items-center justify-center">
								<Loader2 className="h-8 w-8 animate-spin text-primary" />
							</div>
						) : (
							<div className="space-y-4">
								{currentMessages.map((message, index) => (
									<MessageBubble
										key={`${message.message_id ?? "draft"}-${index}`}
										message={message}
									/>
								))}
								{!currentMessages.length && (
									<div className="rounded-[1.5rem] border border-dashed border-border bg-white/30 px-6 py-10 text-center text-sm text-muted-foreground">
										当前对话还没有消息
									</div>
								)}
							</div>
						)}
					</div>
					<Separator />
					<div className="p-4 sm:p-6">
						<MessageComposer
							disabled={
								connectionState !== "open" && Boolean(routeConversationId)
							}
							onSubmit={handleSend}
						/>
					</div>
				</Card>
			</div>
		</div>
	);
}
