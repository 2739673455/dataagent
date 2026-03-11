import {
	Bot,
	Hammer,
	Loader2,
	LogOut,
	MessageSquareMore,
	Plus,
	SendHorizonal,
	Square,
	Trash2,
	User2,
	Wrench,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { getAuthAppBaseUrl } from "@/lib/env";
import { formatDateTime } from "@/lib/message";
import { redirectToAuthorize } from "@/lib/redirect";
import { getAccessToken } from "@/lib/token";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import type {
	ConversationResponse,
	MessagePart,
	MessageSchema,
	UserResponse,
	WebSocketErrorResponse,
	WebSocketMessageResponse,
} from "@/types";

interface PendingMessageState {
	conversationId: number;
	message: MessageSchema;
}

interface ConversationSidebarProps {
	conversations: ConversationResponse[];
	activeConversationId: number | null;
	user: UserResponse | null;
	onCreate: () => void;
	onDelete: (conversationId: number) => void;
	onLogout: () => void;
}

function ConversationSidebar({
	conversations,
	activeConversationId,
	user,
	onCreate,
	onDelete,
	onLogout,
}: ConversationSidebarProps) {
	const profileUrl = new URL(`${getAuthAppBaseUrl()}/profile`);
	profileUrl.searchParams.set(
		"redirect_uri",
		`${window.location.origin}${window.location.pathname}${window.location.search}`,
	);

	return (
		<Card className="flex h-[calc(100vh-1.5rem)] flex-col overflow-hidden rounded-[2rem] border border-white/65 bg-white/68 shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur-2xl">
			<div className="px-4 pb-4 pt-5">
				<Button
					variant="default"
					className="w-full justify-center rounded-2xl border border-emerald-200/70 bg-[linear-gradient(135deg,rgba(236,253,245,0.96),rgba(220,252,231,0.92))] text-emerald-900 shadow-[0_14px_30px_rgba(5,150,105,0.12)] hover:bg-[linear-gradient(135deg,rgba(220,252,231,0.98),rgba(209,250,229,0.96))] hover:text-emerald-950"
					onClick={onCreate}
				>
					<Plus className="h-4 w-4" />
					新建对话
				</Button>
			</div>
			<Separator />
			<div className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
				<div className="space-y-2">
					{conversations.map((conversation) => {
						const isActive =
							conversation.conversation_id === activeConversationId;
						return (
							<div
								key={conversation.conversation_id}
								className={cn(
									"group relative flex items-center gap-2 rounded-2xl pr-2 transition-[background-color,border-color,box-shadow] duration-200",
									isActive
										? "border border-emerald-200/70 bg-[linear-gradient(135deg,rgba(236,253,245,0.82),rgba(220,252,231,0.64))] shadow-[0_12px_28px_-12px_rgba(5,150,105,0.22)] backdrop-blur-xl"
										: "border border-transparent bg-transparent hover:border-white/70 hover:bg-white/62 hover:shadow-[0_10px_25px_-10px_rgba(15,23,42,0.08)] hover:backdrop-blur-xl",
								)}
							>
								<Link
									to={`/chat/${conversation.conversation_id}`}
									className={cn(
										"relative flex min-w-0 flex-1 items-center gap-3 rounded-2xl px-4 py-3.5",
									)}
								>
									<MessageSquareMore
										className={cn(
											"h-4 w-4 shrink-0 transition-colors",
											isActive
												? "text-emerald-600"
												: "text-slate-400 group-hover:text-slate-600",
										)}
									/>
									<span
										className={cn(
											"line-clamp-1 min-w-0 text-sm font-medium transition-colors",
											isActive
												? "text-emerald-950"
												: "text-slate-500 group-hover:text-slate-700",
										)}
									>
										{conversation.title}
									</span>
								</Link>
								<Button
									variant="ghost"
									size="icon"
									className="h-8 w-8 shrink-0 rounded-full border-none bg-transparent text-slate-400 opacity-0 transition-all hover:bg-red-100/80 hover:text-red-600 group-hover:opacity-100"
									onClick={(event) => {
										event.preventDefault();
										event.stopPropagation();
										onDelete(conversation.conversation_id);
									}}
								>
									<Trash2 className="h-4 w-4" />
								</Button>
							</div>
						);
					})}
					{!conversations.length && (
						<div className="px-4 py-8 text-center text-sm text-muted-foreground">
							暂无历史对话
						</div>
					)}
				</div>
			</div>
			<Separator />
			<div className="flex items-center gap-2 p-3">
				<a
					href={profileUrl.toString()}
					className="group flex h-12 min-w-0 flex-1 items-center gap-3 rounded-full border border-transparent bg-transparent px-3 text-left transition-all duration-300 hover:border-stone-300 hover:bg-white/70 hover:shadow-[8px_8px_16px_rgba(201,197,190,0.35),-8px_-8px_16px_rgba(255,255,255,0.65)]"
				>
					<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-stone-600 text-white transition-colors group-hover:bg-stone-700">
						<User2 className="h-4 w-4" />
					</div>
					<div className="min-w-0">
						<p className="truncate text-sm font-medium text-stone-700">
							{user?.username || "未登录"}
						</p>
						<p className="truncate text-xs text-muted-foreground">
							{user?.email || "点击重新认证"}
						</p>
					</div>
				</a>
				<Button
					variant="ghost"
					size="icon"
					className="h-12 w-12 shrink-0 rounded-full border border-transparent bg-transparent text-red-500 transition-all duration-300 hover:border-red-300 hover:bg-red-100/90 hover:text-red-700 hover:shadow-[8px_8px_16px_rgba(254,202,202,0.45),-8px_-8px_16px_rgba(255,255,255,0.65)]"
					onClick={onLogout}
				>
					<LogOut className="h-4 w-4" />
				</Button>
			</div>
		</Card>
	);
}

function PartView({ part }: { part: MessagePart }) {
	if (part.type === "text") {
		return (
			<p className="whitespace-pre-wrap text-[15px] leading-relaxed tracking-wide opacity-95">
				{part.text}
			</p>
		);
	}

	if (part.type === "image_url") {
		return (
			<img
				src={part.image_url}
				alt="message asset"
				className="mt-2 max-h-80 rounded-[1.5rem] border border-white/80 object-cover shadow-[0_12px_30px_-10px_rgba(15,23,42,0.18)]"
			/>
		);
	}

	if (part.type === "tool_call") {
		return (
			<div className="rounded-[1.5rem] border border-slate-200/70 bg-slate-50/80 px-4 py-3 text-sm shadow-[inset_0_1px_0_rgba(255,255,255,0.9)]">
				<div className="mb-2 flex items-center gap-2 font-medium text-slate-800">
					<Hammer className="h-4 w-4" />
					调用工具 {part.name}
				</div>
				<pre className="overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
					{JSON.stringify(part.args, null, 2)}
				</pre>
			</div>
		);
	}

	return (
		<div className="rounded-[1.5rem] border border-amber-200/70 bg-[linear-gradient(180deg,rgba(255,251,235,0.95),rgba(254,243,199,0.9))] px-4 py-3 text-sm text-amber-950 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
			<div className="mb-2 flex items-center gap-2 font-medium">
				<Wrench className="h-4 w-4" />
				工具结果 {part.name}
			</div>
			<pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6">
				{part.content}
			</pre>
		</div>
	);
}

function MessageBubble({ message }: { message: MessageSchema }) {
	const isUser = message.role === "user";
	const isTool = message.role === "tool";

	return (
		<div
			className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
		>
			<div
				className={cn(
					"relative max-w-[85%] space-y-3 rounded-[2rem] px-6 py-4 transition-all duration-300 sm:max-w-[72%]",
					isUser
						? "rounded-tr-none bg-slate-900 text-slate-50 shadow-[0_15px_30px_-10px_rgba(15,23,42,0.3)]"
						: isTool
							? "border border-amber-200/80 bg-[linear-gradient(135deg,#fffaf0,#fef3c7)] text-amber-950 shadow-[0_10px_25px_-10px_rgba(120,53,15,0.12)]"
							: "rounded-tl-none border border-white/60 bg-white/80 text-slate-800 shadow-[0_10px_25px_-10px_rgba(0,0,0,0.05)] backdrop-blur-md",
				)}
			>
				<div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.2em]">
					{isUser ? <User2 className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
					<span>{message.role}</span>
					{message.timestamp ? (
						<Badge
							variant={isUser ? "accent" : "muted"}
							className="ml-auto border-none bg-black/5 text-current"
						>
							{formatDateTime(message.timestamp)}
						</Badge>
					) : null}
				</div>
				<div className="space-y-3">
					{message.parts.map((part) => (
						<PartView key={getMessagePartKey(part)} part={part} />
					))}
				</div>
			</div>
		</div>
	);
}

function MessageComposer({
	disabled = false,
	isStreaming = false,
	onStop,
	onSubmit,
}: {
	disabled?: boolean;
	isStreaming?: boolean;
	onStop: () => void;
	onSubmit: (value: string) => Promise<void> | void;
}) {
	const [value, setValue] = useState("");
	const textareaRef = useRef<HTMLTextAreaElement | null>(null);

	const resizeTextarea = () => {
		const textarea = textareaRef.current;
		if (!textarea) return;

		textarea.style.height = "0px";
		textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.3)}px`;
	};

	const handleSubmit = async () => {
		const next = value.trim();
		if (!next || disabled) return;
		setValue("");
		requestAnimationFrame(resizeTextarea);
		await onSubmit(next);
	};

	return (
		<div className="relative rounded-[2rem] border border-slate-200/80 bg-[rgb(237,241,245)] px-3 pb-1 pt-3 shadow-[0_22px_55px_-22px_rgba(15,23,42,0.2)] transition-all focus-within:border-slate-300 focus-within:shadow-[0_26px_65px_-24px_rgba(15,23,42,0.28)]">
			<Textarea
				ref={textareaRef}
				placeholder=""
				value={value}
				onChange={(event) => {
					setValue(event.target.value);
					requestAnimationFrame(resizeTextarea);
				}}
				onKeyDown={(event) => {
					if (event.key === "Enter" && !event.shiftKey) {
						event.preventDefault();
						void handleSubmit();
					}
				}}
				disabled={disabled}
				className="min-h-[80px] max-h-[30vh] flex-1 resize-none overflow-y-auto rounded-[1.5rem] border-none bg-transparent px-4 py-2 text-[15px] text-slate-800 shadow-none placeholder:text-slate-500 focus-visible:ring-0"
			/>
			<div className="mt-0 flex h-9 items-center justify-end px-1 pb-0">
				<Button
					onClick={() => {
						if (isStreaming) {
							onStop();
							return;
						}
						void handleSubmit();
					}}
					disabled={isStreaming ? false : disabled || !value.trim()}
					variant="ghost"
					className={cn(
						"h-9 w-9 border-none p-0 shadow-none transition-all active:scale-95",
						isStreaming
							? "rounded-md bg-red-500 text-white hover:bg-red-600"
							: "rounded-full bg-transparent text-slate-500 hover:bg-transparent hover:text-slate-700",
						disabled && !isStreaming ? "text-slate-400" : "",
					)}
				>
					{isStreaming ? (
						<Square className="h-3.5 w-3.5 fill-current" />
					) : (
						<SendHorizonal className="h-5 w-5" />
					)}
				</Button>
			</div>
		</div>
	);
}

function getMessageKey(message: MessageSchema) {
	if (message.message_id != null) {
		return `message-${message.message_id}`;
	}

	return `message-${message.timestamp ?? "draft"}-${message.role}-${JSON.stringify(message.parts)}`;
}

function getMessagePartKey(part: MessagePart) {
	switch (part.type) {
		case "text":
			return `text-${part.text}`;
		case "image_url":
			return `image-${part.image_url}`;
		case "tool_call":
			return `tool-call-${part.tool_call_id}-${part.name}`;
		case "tool_result":
			return `tool-result-${part.tool_call_id}-${part.name}-${part.content}`;
	}
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

	const scheduleStreamIdle = () => {
		if (streamIdleTimerRef.current !== null) {
			window.clearTimeout(streamIdleTimerRef.current);
		}
		streamIdleTimerRef.current = window.setTimeout(() => {
			setIsStreaming(false);
			streamIdleTimerRef.current = null;
		}, 900);
	};

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
	}, [activeConversationId, appendMessage, setConnectionState, socketVersion]);

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
			className="relative h-screen overflow-hidden bg-[linear-gradient(135deg,#f6f1e8_0%,#f8f6f0_48%,#edf4f3_100%)]"
			style={{ fontFeatureSettings: '"cv11", "ss01"' }}
		>
			<div className="pointer-events-none absolute inset-0 overflow-hidden">
				<div className="bg-noise absolute inset-0" />
				<div className="absolute -left-24 top-16 h-72 w-72 rounded-full bg-orange-200/35 blur-3xl" />
				<div className="absolute right-0 top-0 h-80 w-80 rounded-full bg-cyan-200/30 blur-3xl" />
				<div className="absolute bottom-0 right-1/4 h-64 w-64 rounded-full bg-emerald-200/25 blur-3xl" />
			</div>
			<div className="relative grid h-full chat-grid">
				<ConversationSidebar
					conversations={conversations}
					activeConversationId={routeConversationId}
					user={user}
					onCreate={handleCreateConversation}
					onDelete={(conversationId) => void handleDeleteConversation(conversationId)}
					onLogout={() => {
						void logout().then(() => redirectToAuthorize("/chat"));
					}}
				/>

				<Card className="flex h-[calc(100vh-1.5rem)] flex-col overflow-hidden rounded-[2rem] border border-white/65 bg-white/58 shadow-[0_24px_90px_rgba(15,23,42,0.08)] backdrop-blur-2xl">
					<div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
						{!routeConversationId ? (
							<div className="flex h-full items-center justify-center">
								<p className="text-base font-medium tracking-[0.18em] text-slate-400">
									创建新对话
								</p>
							</div>
						) : isLoadingMessages ? (
							<div className="flex h-full items-center justify-center">
								<div className="flex h-16 w-16 items-center justify-center rounded-full border border-white/80 bg-white/70 shadow-[inset_0_2px_12px_rgba(15,23,42,0.08),0_12px_32px_-16px_rgba(15,23,42,0.28)]">
									<Loader2 className="h-7 w-7 animate-spin text-primary" />
								</div>
							</div>
						) : (
							<div className="mx-auto w-full max-w-4xl space-y-5">
								{currentMessages.map((message) => (
									<MessageBubble
										key={getMessageKey(message)}
										message={message}
									/>
								))}
							</div>
						)}
					</div>
					<div className="bg-transparent px-5 py-5">
						<div className="mx-auto w-full max-w-4xl">
							<MessageComposer
								isStreaming={isStreaming}
								disabled={
									connectionState !== "open" && Boolean(routeConversationId)
								}
								onStop={handleStop}
								onSubmit={handleSend}
							/>
						</div>
					</div>
				</Card>
			</div>
		</div>
	);
}
