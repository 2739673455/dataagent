import { ChevronLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { authApi, buildAuthorizeUrl, clearAccessToken, getAccessToken, useAuthStore } from "@/auth";
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
function collectReturnedHtmlAttachments(messages: MessageSchema[]): Attachment[] {
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

function getHtmlPreviewCacheKey(conversationId: number, attachmentPath: string) {
  return `${conversationId}:${attachmentPath}`;
}

// 当前 token 不可用时统一回到认证中心
function redirectToAuth(returnTo?: string) {
  const target = returnTo || `${window.location.pathname}${window.location.search}`;
  void buildAuthorizeUrl(target).then((url) => window.location.replace(url));
}

export default function ChatPage() {
  // 路由参数决定当前选中的会话，store 负责会话列表、消息和连接状态
  const navigate = useNavigate();
  const params = useParams();
  const conversations = useChatStore((state) => state.conversations);
  const messagesByConversation = useChatStore((state) => state.messagesByConversation);
  const isLoadingMessages = useChatStore((state) => state.isLoadingMessages);
  const loadConversations = useChatStore((state) => state.loadConversations);
  const createConversation = useChatStore((state) => state.createConversation);
  const deleteConversation = useChatStore((state) => state.deleteConversation);
  const loadMessages = useChatStore((state) => state.loadMessages);
  const streamingConversations = useChatStore((state) => state.streamingConversations);
  const markStreaming = useChatStore((state) => state.markStreaming);
  const unmarkStreaming = useChatStore((state) => state.unmarkStreaming);
  const ensureConversation = useChatStore((state) => state.ensureConversation);
  const appendMessage = useChatStore((state) => state.appendMessage);
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const user = useAuthStore((state) => state.user);

  // 记录哪些会话的 WebSocket 当前是 open 状态
  const [openSocketIds, setOpenSocketIds] = useState<Set<number>>(new Set());

  // socketsRef: 每个对话独立维护 WebSocket，切换会话时不关闭旧连接
  const socketsRef = useRef<Map<number, WebSocket>>(new Map());
  const idleTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
  const closingSocketsRef = useRef<Set<number>>(new Set());
  const routeConversationIdRef = useRef<number | null>(null);
  const pendingMessageRef = useRef<PendingMessageState | null>(null);
  const messageViewportRef = useRef<HTMLDivElement | null>(null);
  const attachmentsRef = useRef<Attachment[]>([]);

  // 辅助函数：关闭指定会话的 socket
  const closeSocket = useCallback((conversationId: number) => {
    closingSocketsRef.current.add(conversationId);
    const socket = socketsRef.current.get(conversationId);
    if (socket) {
      socket.close();
      socketsRef.current.delete(conversationId);
    }
    const timer = idleTimersRef.current.get(conversationId);
    if (timer) {
      clearTimeout(timer);
      idleTimersRef.current.delete(conversationId);
    }
    setOpenSocketIds((prev) => {
      const next = new Set(prev);
      next.delete(conversationId);
      return next;
    });
  }, []);

  // 辅助函数：启动空闲断开定时器（agent 结束后 5s 关闭连接）
  const startIdleTimer = useCallback(
    (conversationId: number) => {
      const existing = idleTimersRef.current.get(conversationId);
      if (existing) clearTimeout(existing);
      const timer = setTimeout(() => {
        closeSocket(conversationId);
      }, 5_000);
      idleTimersRef.current.set(conversationId, timer);
    },
    [closeSocket]
  );

  // 辅助函数：取消空闲定时器
  const cancelIdleTimer = useCallback((conversationId: number) => {
    const timer = idleTimersRef.current.get(conversationId);
    if (timer) {
      clearTimeout(timer);
      idleTimersRef.current.delete(conversationId);
    }
  }, []);
  const htmlPreviewUrlsRef = useRef<Record<string, string>>({});

  // draftConversationId 用于“尚未进入正式路由但已提前上传附件”的草稿会话
  const [draftConversationId, setDraftConversationId] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);
  const [isHtmlSidebarOpen, setIsHtmlSidebarOpen] = useState(true);
  const [activeHtmlPath, setActiveHtmlPath] = useState<string | null>(null);
  const [htmlPreviewUrls, setHtmlPreviewUrls] = useState<Record<string, string>>({});

  // URL 中的 conversationId 非法时按未选中会话处理
  const routeConversationId = (() => {
    const raw = params.conversationId;
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isNaN(parsed) ? null : parsed;
  })();

  const isStreaming =
    routeConversationId != null && streamingConversations.has(routeConversationId);

  // 同步 routeConversationId 到 ref，供 socket 回调闭包内读取最新值
  useEffect(() => {
    routeConversationIdRef.current = routeConversationId;
  }, [routeConversationId]);

  // 根据当前会话的 socket 是否存活推导连接状态
  const connectionState: "idle" | "connecting" | "open" | "closed" = routeConversationId
    ? openSocketIds.has(routeConversationId)
      ? "open"
      : "closed"
    : "idle";

  const currentMessages = routeConversationId
    ? (messagesByConversation[routeConversationId] ?? [])
    : [];
  const currentMessageCount = currentMessages.length;
  const returnedHtmlAttachments = useMemo(
    () => collectReturnedHtmlAttachments(currentMessages),
    [currentMessages]
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
      if (current && returnedHtmlAttachments.some((item) => item.f_path === current)) {
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
      activeHtmlAttachment.f_path
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
          new Blob([response.data], { type: "text/html;charset=utf-8" })
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
  }, [activeHtmlAttachment, htmlPreviewUrls, isHtmlSidebarOpen, routeConversationId]);

  const activeHtmlPreviewUrl =
    routeConversationId && activeHtmlAttachment
      ? htmlPreviewUrls[getHtmlPreviewCacheKey(routeConversationId, activeHtmlAttachment.f_path)]
      : undefined;

  // 首次渲染出历史消息后直接滚到最底部
  useEffect(() => {
    if (!routeConversationId || isLoadingMessages) return;
    if (currentMessageCount < 1) return;

    const frameId = window.requestAnimationFrame(() => {
      scrollToBottom("auto");
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [currentMessageCount, isLoadingMessages, routeConversationId, scrollToBottom]);

  // 每个会话独立维护 WebSocket，切换会话时不关闭旧连接
  useEffect(() => {
    const token = getAccessToken();
    if (!routeConversationId || !token) return;

    const conversationId = routeConversationId;

    // 取消该会话的空闲定时器
    cancelIdleTimer(conversationId);

    // 如果已有活跃连接则直接复用
    const existingSocket = socketsRef.current.get(conversationId);
    if (
      existingSocket &&
      (existingSocket.readyState === WebSocket.OPEN ||
        existingSocket.readyState === WebSocket.CONNECTING)
    ) {
      return () => {
        const socket = socketsRef.current.get(conversationId);
        if (
          socket &&
          socket.readyState === WebSocket.OPEN &&
          !useChatStore.getState().streamingConversations.has(conversationId)
        ) {
          startIdleTimer(conversationId);
        }
      };
    }

    let cancelled = false;

    const connectSocket = async () => {
      try {
        const response = await chatApi.createWebSocketToken();
        if (cancelled) return;

        const socket = chatApi.buildChatSocket(conversationId, response.data.websocket_token);
        socketsRef.current.set(conversationId, socket);

        socket.onopen = () => {
          closingSocketsRef.current.delete(conversationId);
          setOpenSocketIds((prev) => new Set([...prev, conversationId]));
          // 若用户在 socket 建连期间已切走，且该会话未在生成，启动空闲定时器
          if (
            routeConversationIdRef.current !== conversationId &&
            !useChatStore.getState().streamingConversations.has(conversationId)
          ) {
            startIdleTimer(conversationId);
          }

          // 新建会话时，首条消息会先暂存在 ref，待连接建立后补发
          if (pendingMessageRef.current?.conversationId === conversationId) {
            socket.send(
              chatApi.serializeChatRequest({
                message: pendingMessageRef.current.message,
              })
            );
            pendingMessageRef.current = null;
          }
        };

        socket.onmessage = (event) => {
          const payload = JSON.parse(event.data) as
            | WebSocketMessageResponse
            | WebSocketErrorResponse;

          if (payload.type === "error") {
            unmarkStreaming(conversationId);
            toast.error(payload.content);
            return;
          }

          appendMessage(conversationId, payload.message);
          if (payload.message.finish_reason === "stop") {
            unmarkStreaming(conversationId);
            void loadConversations();

            // 后台会话：agent 结束后启动空闲定时器；当前会话保持连接
            if (routeConversationIdRef.current !== conversationId) {
              startIdleTimer(conversationId);
            }
          }
        };

        socket.onclose = (event) => {
          const isIntentional = closingSocketsRef.current.has(conversationId);
          closingSocketsRef.current.delete(conversationId);
          socketsRef.current.delete(conversationId);
          cancelIdleTimer(conversationId);
          setOpenSocketIds((prev) => {
            const next = new Set(prev);
            next.delete(conversationId);
            return next;
          });

          if (event.code === 4401) {
            redirectToAuth();
            return;
          }

          if (event.code === 4404) {
            toast.error("对话不存在或无权限访问");
          }

          if (!isIntentional && event.code !== 1000 && event.code !== 1005) {
            toast.error("聊天连接已断开");
          }
        };

        socket.onerror = () => {
          if (closingSocketsRef.current.has(conversationId)) return;
          unmarkStreaming(conversationId);
          toast.error("聊天连接异常");
        };
      } catch {
        if (cancelled) return;
        setOpenSocketIds((prev) => {
          const next = new Set(prev);
          next.delete(conversationId);
          return next;
        });
        unmarkStreaming(conversationId);
        toast.error("聊天连接初始化失败");
      }
    };

    void connectSocket();

    return () => {
      cancelled = true;
      // 切换离开时，若该会话未在生成中，5s 后断开连接
      const socket = socketsRef.current.get(conversationId);
      if (
        socket &&
        socket.readyState === WebSocket.OPEN &&
        !useChatStore.getState().streamingConversations.has(conversationId)
      ) {
        startIdleTimer(conversationId);
      }
    };
  }, [
    appendMessage,
    cancelIdleTimer,
    loadConversations,
    routeConversationId,
    startIdleTimer,
    unmarkStreaming,
  ]);

  // 页面退出时取消所有正在生成的 agent，卸载时关闭所有连接
  useEffect(() => {
    const handleBeforeUnload = () => {
      for (const socket of socketsRef.current.values()) {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "cancel" }));
        }
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      for (const [conversationId] of socketsRef.current) {
        closeSocket(conversationId);
      }
    };
  }, [closeSocket]);

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

  // 停止生成：仅发送 cancel 信号，不断开 WebSocket，让后端在下一个 chunk 边界终止 agent
  const handleStop = () => {
    if (routeConversationId != null) {
      unmarkStreaming(routeConversationId);
    }
    pendingMessageRef.current = null;
    const sock =
      routeConversationId != null ? socketsRef.current.get(routeConversationId) : undefined;
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ type: "cancel" }));
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
        const response = await chatApi.uploadAttachment(nextConversationId, file);
        nextAttachments.push({
          ...response.data.attachment,
          preview_url: isImageFile(file.name) ? URL.createObjectURL(file) : undefined,
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
        const target = current.find((attachment) => attachment.f_path === attachmentName);
        if (target?.preview_url) {
          URL.revokeObjectURL(target.preview_url);
        }
        return current.filter((attachment) => attachment.f_path !== attachmentName);
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
      markStreaming(conversationId);
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
      markStreaming(conversationId);
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
    const socket = socketsRef.current.get(conversationId);
    if (!conversationId || !socket || socket.readyState !== WebSocket.OPEN) {
      toast.error("连接尚未建立，请稍后重试");
      return;
    }

    appendMessage(conversationId, userMessage);
    cancelIdleTimer(conversationId);
    markStreaming(conversationId);
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
          onDelete={(conversationId) => void handleDeleteConversation(conversationId)}
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
                  disabled={connectionState !== "open" && Boolean(routeConversationId)}
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
                  title={isHtmlSidebarOpen ? "收起 HTML 侧栏" : "展开 HTML 侧栏"}
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
