import { ChevronLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import {
	authApi,
	buildAuthorizeUrl,
	clearAccessToken,
	getAccessToken,
	useAuthStore,
} from "@/auth";
import { ROUTES } from "@/config/settings";
import { createLocalTimestamp } from "@/lib/message";
import { getAttachmentName } from "@/lib/utils";
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

// 基于文件名判断是否需要图片预览
function isImageFile(name: string) {
	return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

// 返回的 HTML 附件会在右侧栏内嵌预览
function isHtmlFile(name: string) {
	return /\.(html?)$/i.test(name);
}

// 从助手消息里收集可预览的 HTML 附件，并按路径去重
function collectReturnedHtmlAttachments(
	messages: MessageSchema[],
): Attachment[] {
	const unique = new Map<string, Attachment>();

	for (const message of messages) {
		if (message.role === "user" || !message.attachments?.length) continue;

		for (const attachment of message.attachments) {
			if (isHtmlFile(attachment.f_path) && !unique.has(attachment.f_path)) {
				unique.set(attachment.f_path, attachment);
			}
		}
	}

	return Array.from(unique.values());
}

function getHtmlPreviewCacheKey(
	conversationId: number,
	attachmentPath: string,
) {
	return `${conversationId}:${attachmentPath}`;
}

// 当前 token 不可用时统一回到认证中心
function redirectToAuth(returnTo?: string) {
	const target =
		returnTo || `${window.location.pathname}${window.location.search}`;
	void buildAuthorizeUrl(target).then((url) =>
		window.location.replace(url),
	);
}

export default function ChatPage() {
	// 路由参数决定当前选中的会话，store 负责会话列表、消息和连接状态
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
	const clearAuth = useAuthStore((state) => state.clearAuth);
	const user = useAuthStore((state) => state.user);

	// socketRef 保存当前对话的实时连接，pendingMessageRef 用于在建连完成后补发首条消息
	const socketRef = useRef<WebSocket | null>(null);
	const pendingMessageRef = useRef<PendingMessageState | null>(null);
	const isClosingSocketRef = useRef(false);
	const messageViewportRef = useRef<HTMLDivElement | null>(null);
	const attachmentsRef = useRef<Attachment[]>([]);
	const htmlPreviewUrlsRef = useRef<Record<string, string>>({});

	// draftConversationId 用于“尚未进入正式路由但已提前上传附件”的草稿会话
	const [draftConversationId, setDraftConversationId] = useState<number | null>(
		null,
	);
	const [attachments, setAttachments] = useState<Attachment[]>([]);
	const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);
	const [isStreaming, setIsStreaming] = useState(false);
	const [socketVersion, setSocketVersion] = useState(0);
	const [isHtmlSidebarOpen, setIsHtmlSidebarOpen] = useState(true);
	const [activeHtmlPath, setActiveHtmlPath] = useState<string | null>(null);
	const [htmlPreviewUrls, setHtmlPreviewUrls] = useState<
		Record<string, string>
	>({});

	// URL 中的 conversationId 非法时按未选中会话处理
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
	const returnedHtmlAttachments = useMemo(
		() => collectReturnedHtmlAttachments(currentMessages),
		[currentMessages],
	);
	const activeHtmlAttachment =
		returnedHtmlAttachments.find((item) => item.f_path === activeHtmlPath) ??
		returnedHtmlAttachments[0] ??
		null;

	// 在卸载阶段读取最新附件列表，需要把状态同步进 ref
	useEffect(() => {
		attachmentsRef.current = attachments;
	}, [attachments]);

	// HTML 预览 URL 由 createObjectURL 生成，也需要在卸载时统一回收
	useEffect(() => {
		htmlPreviewUrlsRef.current = htmlPreviewUrls;
	}, [htmlPreviewUrls]);

	// 新消息到达后将消息区滚到底部
	const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
		const viewport = messageViewportRef.current;
		if (!viewport) return;

		viewport.scrollTo({
			top: viewport.scrollHeight,
			behavior,
		});
	}, []);

	// 页面初始化时加载会话列表
	useEffect(() => {
		void loadConversations();
	}, [loadConversations]);

	// 切换到具体会话时按需加载历史消息
	useEffect(() => {
		if (!routeConversationId) return;
		if (messagesByConversation[routeConversationId] === undefined) {
			void loadMessages(routeConversationId);
		}
	}, [loadMessages, messagesByConversation, routeConversationId]);

	// 路由切到正式会话后，草稿态附件不再保留在页面级状态里
	useEffect(() => {
		if (!routeConversationId) return;
		setDraftConversationId(null);
		setAttachments([]);
	}, [routeConversationId]);

	// 当前消息里一旦出现 HTML 结果，自动展开侧栏并选中可预览文件
	useEffect(() => {
		if (returnedHtmlAttachments.length === 0) {
			setActiveHtmlPath(null);
			return;
		}

		setIsHtmlSidebarOpen(true);
		setActiveHtmlPath((current) => {
			if (
				current &&
				returnedHtmlAttachments.some((item) => item.f_path === current)
			) {
				return current;
			}
			return returnedHtmlAttachments[0].f_path;
		});
	}, [returnedHtmlAttachments]);

	// 按需拉取 HTML 附件内容并缓存成 object URL，避免重复请求
	useEffect(() => {
		if (!routeConversationId || !isHtmlSidebarOpen || !activeHtmlAttachment) {
			return;
		}
		const previewCacheKey = getHtmlPreviewCacheKey(
			routeConversationId,
			activeHtmlAttachment.f_path,
		);
		if (htmlPreviewUrls[previewCacheKey]) {
			return;
		}

		let objectUrl: string | null = null;
		let cancelled = false;

		void chatApi
			.fetchAttachmentFile(routeConversationId, activeHtmlAttachment.f_path)
			.then((response) => {
				if (cancelled) return;
				objectUrl = URL.createObjectURL(
					new Blob([response.data], { type: "text/html;charset=utf-8" }),
				);
				setHtmlPreviewUrls((current) => ({
					...current,
					[previewCacheKey]: objectUrl as string,
				}));
			})
			.catch(() => {
				if (cancelled) return;
				toast.error(`HTML 预览加载失败：${getAttachmentName(activeHtmlAttachment.f_path)}`);
			});

		return () => {
			cancelled = true;
		};
	}, [
		activeHtmlAttachment,
		htmlPreviewUrls,
		isHtmlSidebarOpen,
		routeConversationId,
	]);

	const activeHtmlPreviewUrl =
		routeConversationId && activeHtmlAttachment
			? htmlPreviewUrls[
					getHtmlPreviewCacheKey(routeConversationId, activeHtmlAttachment.f_path)
				]
			: undefined;

	// 首次渲染出历史消息后直接滚到最底部
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

	// 进入具体会话后建立 websocket，并在连接断开或会话切换时清理
	useEffect(() => {
		void socketVersion;

		const token = getAccessToken();
		if (!routeConversationId || !token) return;

		let cancelled = false;

		const connectSocket = async () => {
			try {
				setConnectionState("connecting");
				// 先用 HTTP 接口申请一次性 websocket token，再换成实时连接
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

					// 新建会话时，首条消息会先暂存在 ref，待连接建立后补发
					if (
						pendingMessageRef.current?.conversationId === routeConversationId
					) {
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

					// 服务端主动返回错误时直接停止流式状态并提示用户
					if (payload.type === "error") {
						setIsStreaming(false);
						toast.error(payload.content);
						return;
					}

					appendMessage(routeConversationId, payload.message);
					if (payload.message.finish_reason === "stop") {
						setIsStreaming(false);
						// 助手完成回复后刷新会话列表，让标题和更新时间同步
						void loadConversations();
					}
				};

				socket.onclose = (event) => {
					setConnectionState("closed");
					socketRef.current = null;
					const isIntentionalClose = isClosingSocketRef.current;
					isClosingSocketRef.current = false;

					// 后端标记未授权时直接回到认证中心
					if (event.code === 4401) {
						redirectToAuth();
						return;
					}

					// 会话被删除或无权限访问时，不再继续保持流式状态
					if (event.code === 4404) {
						setIsStreaming(false);
						toast.error("对话不存在或无权限访问");
					}

					// 除主动关闭和正常关闭外，其余都视为异常断连
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

		// 退出网页时通知后端停止 agent（beforeunload 不触发于 SPA 路由切换）
		const handleBeforeUnload = () => {
			if (
				socketRef.current &&
				socketRef.current.readyState === WebSocket.OPEN
			) {
				socketRef.current.send(JSON.stringify({ type: "cancel" }));
			}
		};
		window.addEventListener("beforeunload", handleBeforeUnload);

		// 会话切换或组件卸载时关闭旧连接
		return () => {
			window.removeEventListener("beforeunload", handleBeforeUnload);
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

	// 新建对话按钮只重置当前页面态，不直接向后端发消息
	const handleCreateConversation = () => {
		pendingMessageRef.current = null;
		for (const attachment of attachments) {
			if (attachment.preview_url) {
				URL.revokeObjectURL(attachment.preview_url);
			}
		}
		setAttachments([]);
		setDraftConversationId(null);
		navigate(ROUTES.chat);
	};

	// 删除当前会话后，如果用户正停留在该会话页，则回到空白聊天页
	const handleDeleteConversation = async (conversationId: number) => {
		await deleteConversation(conversationId);
		if (routeConversationId === conversationId) {
			navigate(ROUTES.chat);
		}
		toast.success("对话已删除");
	};

	// "停止生成" 先发取消信号再关闭 websocket，通知后端停止 agent
	const handleStop = () => {
		setIsStreaming(false);
		pendingMessageRef.current = null;
		if (socketRef.current) {
			socketRef.current.send(JSON.stringify({ type: "cancel" }));
			isClosingSocketRef.current = true;
			socketRef.current.close(1000);
			socketRef.current = null;
			setSocketVersion((value) => value + 1);
		}
	};

	// 上传附件前需要确保已有可归属的会话，没有则先创建草稿会话
	const handleAttachmentsSelected = async (files: File[]) => {
		const token = getAccessToken();
		if (!token) {
			redirectToAuth();
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
			// 逐个上传并在前端补充本地预览 URL
			for (const file of files) {
				const response = await chatApi.uploadAttachment(
					nextConversationId,
					file,
				);
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
			// 无论成功失败都结束上传态，避免输入区一直被锁住
			setIsUploadingAttachments(false);
		}
	};

	// 删除附件时同时回收已创建的本地 object URL
	const handleRemoveAttachment = async (attachmentName: string) => {
		const targetConversationId = routeConversationId ?? draftConversationId;
		if (!targetConversationId) {
			return;
		}

		try {
			await chatApi.deleteAttachment(targetConversationId, attachmentName);
			setAttachments((current) => {
				const target = current.find(
					(attachment) => attachment.f_path === attachmentName,
				);
				if (target?.preview_url) {
					URL.revokeObjectURL(target.preview_url);
				}
				return current.filter(
					(attachment) => attachment.f_path !== attachmentName,
				);
			});
		} catch {
			toast.error("附件删除失败");
		}
	};

	// 发送消息时要兼容三种情况：新会话首条消息、草稿会话首条消息、已建立连接的既有会话
	const handleSend = async (value: string) => {
		const token = getAccessToken();
		if (!token) {
			redirectToAuth();
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
			// 完全新对话：先创建正式会话，再导航到对应路由
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
			navigate(ROUTES.chatConversation(conversationId));
			return;
		}

		if (!routeConversationId) {
			// 已有草稿会话但还没进入路由：先把本地消息入队，再导航
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
			navigate(ROUTES.chatConversation(conversationId));
			return;
		}

		// 既有会话必须等 websocket 已经打开后才能发送
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

	// 页面卸载时统一回收所有图片和 HTML 预览用的 object URL
	useEffect(() => {
		return () => {
			for (const attachment of attachmentsRef.current) {
				if (attachment.preview_url) {
					URL.revokeObjectURL(attachment.preview_url);
				}
			}
			for (const url of Object.values(htmlPreviewUrlsRef.current)) {
				URL.revokeObjectURL(url);
			}
		};
	}, []);

	// 点击消息里的 HTML 附件时展开右侧栏并切到对应预览
	const handleOpenHtmlAttachment = useCallback((attachment: Attachment) => {
		setActiveHtmlPath(attachment.f_path);
		setIsHtmlSidebarOpen(true);
	}, []);

	return (
		<div
			className="min-h-screen h-[100dvh] overflow-hidden bg-[#fefdfa]"
			style={{ fontFeatureSettings: '"cv11", "ss01"' }}
		>
			{/* 左侧为会话列表，右侧为聊天主区域；当返回 HTML 结果时再展开附加预览栏 */}
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
						const token = getAccessToken();
						void authApi
							.logout(token ?? "")
							.catch(() => undefined)
							.finally(() => {
								clearAccessToken();
								clearAuth();
								redirectToAuth(ROUTES.chat);
							});
					}}
				/>

				<div className="flex h-full min-h-0 bg-[#fefdfa]">
					<div className="flex min-w-0 flex-1 flex-col bg-[#fefdfa]">
						<ChatMessages
							conversationId={routeConversationId}
							conversationSelected={Boolean(routeConversationId)}
							isLoading={isLoadingMessages}
							messages={currentMessages}
							onOpenHtmlAttachment={handleOpenHtmlAttachment}
							viewportRef={messageViewportRef}
						/>
						<div className="sticky bottom-0 z-10 w-full shrink-0 bg-[#fefdfa] pb-6 pt-0">
							<div className="mx-auto w-[70%] min-w-[320px] max-w-[1120px]">
								{/* 已有会话但 websocket 尚未打开时，输入区先禁用，避免消息丢失 */}
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
					{returnedHtmlAttachments.length > 0 ? (
						<div
							className={`border-l border-slate-200 bg-white/80 backdrop-blur transition-all duration-300 ${
								isHtmlSidebarOpen ? "w-[min(42vw,560px)]" : "w-10"
							}`}
						>
							<div className="flex h-full min-h-0">
								{/* 侧栏折叠按钮始终保留，方便快速收起预览区 */}
								<button
									type="button"
									onClick={() => setIsHtmlSidebarOpen((value) => !value)}
									className="flex w-10 shrink-0 items-center justify-center border-r border-slate-200 bg-white/90 text-slate-500 transition hover:bg-slate-50 hover:text-slate-800"
									title={
										isHtmlSidebarOpen ? "收起 HTML 侧栏" : "展开 HTML 侧栏"
									}
								>
									<ChevronLeft
										className={`h-6 w-6 transition-transform duration-300 ${
											isHtmlSidebarOpen ? "rotate-180" : "rotate-0"
										}`}
									/>
								</button>
								<div
									className={`flex min-w-0 flex-1 min-h-0 flex-col overflow-hidden transition-opacity duration-200 ${
										isHtmlSidebarOpen
											? "delay-150 opacity-100"
											: "pointer-events-none delay-0 opacity-0"
									}`}
								>
									{/* 顶部 tab 按返回顺序展示所有可预览的 HTML 附件 */}
									<div className="flex gap-2 overflow-x-auto border-b border-slate-200 px-3 py-2">
										{returnedHtmlAttachments.map((attachment) => (
											<button
												key={attachment.f_path}
												type="button"
												onClick={() => setActiveHtmlPath(attachment.f_path)}
												className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
													activeHtmlAttachment?.f_path === attachment.f_path
														? "bg-slate-900 text-white"
														: "bg-slate-100 text-slate-600 hover:bg-slate-200"
												}`}
											>
												{getAttachmentName(attachment.f_path)}
											</button>
										))}
									</div>
									<div className="min-h-0 flex-1 bg-slate-50">
										{activeHtmlAttachment ? (
											activeHtmlPreviewUrl ? (
												<iframe
													title={getAttachmentName(activeHtmlAttachment.f_path)}
													src={activeHtmlPreviewUrl}
													className="h-full w-full border-0 bg-white"
												/>
											) : (
												<div className="flex h-full items-center justify-center text-sm text-slate-500">
													正在加载 HTML 预览...
												</div>
											)
										) : (
											<div className="flex h-full items-center justify-center text-sm text-slate-500">
												暂无可预览的 HTML 文件
											</div>
										)}
									</div>
								</div>
							</div>
						</div>
					) : null}
				</div>
			</div>
		</div>
	);
}
