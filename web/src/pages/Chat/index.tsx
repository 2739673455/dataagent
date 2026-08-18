import { ChevronLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { getAccessToken, logoutUser, redirectToLogin, useAuthStore } from "@/auth";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import { ROUTES } from "@/config/settings";
import { getAttachmentName } from "@/lib/utils";
import { sanitizeHtmlForPreview } from "@/lib/htmlPreview";
import { useChatStore } from "@/stores/chatStore";
import type { Attachment, ChatStreamEvent, InteractiveTableArtifact, MessageSchema } from "@/types";
import { ChatComposer } from "./components/ChatComposer";
import { ChatMessages } from "./components/ChatMessages";
import { ChatSidebar } from "./components/ChatSidebar";
import {
  InteractiveTablePreview,
  parseInteractiveTableArtifact,
} from "./components/InteractiveTablePreview";

// 基于文件名判断是否需要图片预览
function isImageFile(name: string) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

// 返回的 HTML 与交互表格附件会在右侧栏预览
function isHtmlAttachment(attachment: Attachment) {
  return attachment.media_type === "text/html" || /\.(html?)$/i.test(attachment.f_path);
}

function isInteractiveTableAttachment(attachment: Attachment) {
  return (
    attachment.media_type === "application/vnd.dataagent.table+json" ||
    /\.table\.json$/i.test(attachment.f_path)
  );
}

// 从助手消息里收集可信预览附件，并按路径去重
function collectReturnedPreviewAttachments(messages: MessageSchema[]): Attachment[] {
  const unique = new Map<string, Attachment>();

  for (const message of messages) {
    if (message.role === "user" || !message.attachments?.length) continue;

    for (const attachment of message.attachments) {
      if (
        (isHtmlAttachment(attachment) || isInteractiveTableAttachment(attachment)) &&
        !unique.has(attachment.f_path)
      ) {
        unique.set(attachment.f_path, attachment);
      }
    }
  }

  return Array.from(unique.values());
}

function getPreviewCacheKey(conversationId: string, attachmentPath: string) {
  return `${conversationId}:${attachmentPath}`;
}

// 当前 token 不可用时统一回到登录页
function redirectToAuth(returnTo?: string) {
  redirectToLogin(returnTo);
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
  const user = useAuthStore((state) => state.user);

  // 每个会话独立持有 SSE 请求的取消控制器
  const streamControllersRef = useRef<Map<string, AbortController>>(new Map());
  const messageViewportRef = useRef<HTMLDivElement | null>(null);
  const attachmentsRef = useRef<Attachment[]>([]);
  const htmlPreviewUrlsRef = useRef<Record<string, string>>({});

  // draftConversationId 用于“尚未进入正式路由但已提前上传附件”的草稿会话
  const [draftConversationId, setDraftConversationId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);
  const [isPreviewSidebarOpen, setIsPreviewSidebarOpen] = useState(true);
  const [activePreviewPath, setActivePreviewPath] = useState<string | null>(null);
  const [htmlPreviewUrls, setHtmlPreviewUrls] = useState<Record<string, string>>({});
  const [tableArtifacts, setTableArtifacts] = useState<Record<string, InteractiveTableArtifact>>(
    {}
  );

  // URL 中的 conversationId 非法时按未选中会话处理
  const routeConversationId = (() => {
    const raw = params.conversationId;
    if (!raw) return null;
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(raw)
      ? raw
      : null;
  })();

  const isStreaming =
    routeConversationId != null && streamingConversations.has(routeConversationId);

  const currentMessages = routeConversationId
    ? (messagesByConversation[routeConversationId] ?? [])
    : [];
  const currentMessageCount = currentMessages.length;
  const returnedPreviewAttachments = useMemo(
    () => collectReturnedPreviewAttachments(currentMessages),
    [currentMessages]
  );
  const activePreviewAttachment =
    returnedPreviewAttachments.find((item) => item.f_path === activePreviewPath) ??
    returnedPreviewAttachments[0] ??
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

  // 当前消息里一旦出现可预览结果，自动展开侧栏并选中文件
  useEffect(() => {
    if (returnedPreviewAttachments.length === 0) {
      setActivePreviewPath(null);
      return;
    }

    setIsPreviewSidebarOpen(true);
    setActivePreviewPath((current) => {
      if (current && returnedPreviewAttachments.some((item) => item.f_path === current)) {
        return current;
      }
      return returnedPreviewAttachments[0].f_path;
    });
  }, [returnedPreviewAttachments]);

  // 按需拉取 HTML 附件内容并缓存成 object URL，避免重复请求
  useEffect(() => {
    if (
      !routeConversationId ||
      !isPreviewSidebarOpen ||
      !activePreviewAttachment ||
      !isHtmlAttachment(activePreviewAttachment)
    ) {
      return;
    }
    const previewCacheKey = getPreviewCacheKey(routeConversationId, activePreviewAttachment.f_path);
    if (htmlPreviewUrls[previewCacheKey]) {
      return;
    }

    let objectUrl: string | null = null;
    let cancelled = false;

    void chatApi
      .fetchAttachmentFile(routeConversationId, activePreviewAttachment.f_path)
      .then(async (response) => {
        if (cancelled) return;
        const source = await response.data.text();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(
          new Blob([sanitizeHtmlForPreview(source)], { type: "text/html;charset=utf-8" })
        );
        setHtmlPreviewUrls((current) => ({
          ...current,
          [previewCacheKey]: objectUrl as string,
        }));
      })
      .catch(() => {
        if (cancelled) return;
        toast.error(`HTML 预览加载失败：${getAttachmentName(activePreviewAttachment.f_path)}`);
      });

    return () => {
      cancelled = true;
    };
  }, [activePreviewAttachment, htmlPreviewUrls, isPreviewSidebarOpen, routeConversationId]);

  // 交互表格只解析确定性 worker 生成的有界 JSON 协议
  useEffect(() => {
    if (
      !routeConversationId ||
      !isPreviewSidebarOpen ||
      !activePreviewAttachment ||
      !isInteractiveTableAttachment(activePreviewAttachment)
    ) {
      return;
    }
    const previewCacheKey = getPreviewCacheKey(routeConversationId, activePreviewAttachment.f_path);
    if (tableArtifacts[previewCacheKey]) return;

    let cancelled = false;
    void chatApi
      .fetchAttachmentFile(routeConversationId, activePreviewAttachment.f_path)
      .then(async (response) => {
        const source = await response.data.text();
        if (cancelled) return;
        const artifact = parseInteractiveTableArtifact(JSON.parse(source));
        setTableArtifacts((current) => ({
          ...current,
          [previewCacheKey]: artifact,
        }));
      })
      .catch(() => {
        if (cancelled) return;
        toast.error(`表格预览加载失败：${getAttachmentName(activePreviewAttachment.f_path)}`);
      });

    return () => {
      cancelled = true;
    };
  }, [activePreviewAttachment, isPreviewSidebarOpen, routeConversationId, tableArtifacts]);

  const activeHtmlPreviewUrl =
    routeConversationId && activePreviewAttachment && isHtmlAttachment(activePreviewAttachment)
      ? htmlPreviewUrls[getPreviewCacheKey(routeConversationId, activePreviewAttachment.f_path)]
      : undefined;
  const activeTableArtifact =
    routeConversationId &&
    activePreviewAttachment &&
    isInteractiveTableAttachment(activePreviewAttachment)
      ? tableArtifacts[getPreviewCacheKey(routeConversationId, activePreviewAttachment.f_path)]
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

  const runStream = useCallback(
    (conversationId: string, message: MessageSchema) => {
      const generation = sessionLifecycle.current();
      streamControllersRef.current.get(conversationId)?.abort();
      const controller = new AbortController();
      streamControllersRef.current.set(conversationId, controller);

      const onEvent = (event: ChatStreamEvent) => {
        if (!sessionLifecycle.isCurrent(generation)) return;
        if (event.type === "message") {
          appendMessage(conversationId, event.message);
        } else if (event.type === "error") {
          toast.error(event.content);
        }
      };

      void chatApi
        .streamChat(conversationId, message, controller.signal, onEvent)
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (!sessionLifecycle.isCurrent(generation)) return;
          toast.error(error instanceof Error ? error.message : "聊天连接异常");
        })
        .finally(() => {
          if (streamControllersRef.current.get(conversationId) === controller) {
            streamControllersRef.current.delete(conversationId);
            if (sessionLifecycle.isCurrent(generation)) {
              unmarkStreaming(conversationId);
              void loadConversations();
            }
          }
        });
    },
    [appendMessage, loadConversations, unmarkStreaming]
  );

  // 页面卸载时取消全部正在运行的 SSE 请求
  useEffect(() => {
    const controllers = streamControllersRef.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
    };
  }, []);

  // 新建对话按钮只重置当前页面态，不直接向后端发消息
  const handleCreateConversation = () => {
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
  const handleDeleteConversation = async (conversationId: string) => {
    streamControllersRef.current.get(conversationId)?.abort();
    if (!(await deleteConversation(conversationId))) return;
    if (routeConversationId === conversationId) {
      navigate(ROUTES.chat);
    }
    toast.success("对话已删除");
  };

  // 停止生成会取消当前会话的 SSE 请求
  const handleStop = () => {
    if (!routeConversationId) return;
    streamControllersRef.current.get(routeConversationId)?.abort();
  };

  // 上传附件前需要确保已有可归属的会话，没有则先创建草稿会话
  const handleAttachmentsSelected = async (files: File[]) => {
    const generation = sessionLifecycle.current();
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
        if (!sessionLifecycle.isCurrent(generation)) return;
        nextConversationId = response.data.conversation_id;
        setDraftConversationId(nextConversationId);
        void loadConversations();
      }
      const nextAttachments: Attachment[] = [];
      // 逐个上传并在前端补充本地预览 URL
      for (const file of files) {
        const response = await chatApi.uploadAttachment(nextConversationId, file);
        if (!sessionLifecycle.isCurrent(generation)) return;
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

  // 发送消息时统一创建带 Bearer Token 的 SSE 请求
  const handleSend = async (value: string) => {
    const generation = sessionLifecycle.current();
    const token = getAccessToken();
    if (!token) {
      redirectToAuth();
      return;
    }

    const userMessage: MessageSchema = {
      message_id: crypto.randomUUID(),
      role: "user",
      parts: value ? [{ type: "text", text: value }] : [],
      attachments: attachments.length > 0 ? attachments : undefined,
    };

    let conversationId = routeConversationId ?? draftConversationId;
    if (!conversationId) {
      const conversation = await createConversation();
      if (!conversation || !sessionLifecycle.isCurrent(generation)) return;
      conversationId = conversation.conversation_id;
    } else if (!routeConversationId) {
      setDraftConversationId(null);
      ensureConversation({
        conversation_id: conversationId,
        title: "新对话",
        update_at: new Date().toISOString(),
      });
    }

    appendMessage(conversationId, userMessage);
    markStreaming(conversationId);
    for (const attachment of attachments) {
      if (attachment.preview_url) {
        URL.revokeObjectURL(attachment.preview_url);
      }
    }
    setAttachments([]);
    if (routeConversationId !== conversationId) {
      navigate(ROUTES.chatConversation(conversationId));
    }
    runStream(conversationId, userMessage);
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

  // 点击消息里的可信附件时展开右侧栏并切到对应预览
  const handleOpenPreviewAttachment = useCallback((attachment: Attachment) => {
    setActivePreviewPath(attachment.f_path);
    setIsPreviewSidebarOpen(true);
  }, []);

  return (
    <div
      className="min-h-screen h-[100dvh] overflow-hidden bg-[#fefdfa]"
      style={{ fontFeatureSettings: '"cv11", "ss01"' }}
    >
      {/* 左侧为会话列表，右侧为聊天主区域；返回展示产物时展开预览栏 */}
      <div className="grid h-full min-h-0 chat-grid">
        <ChatSidebar
          conversations={conversations}
          activeConversationId={routeConversationId}
          user={user}
          onCreate={handleCreateConversation}
          onDelete={(conversationId) => void handleDeleteConversation(conversationId)}
          onLogout={() => {
            void logoutUser().finally(() => redirectToAuth(ROUTES.chat));
          }}
        />

        <div className="flex h-full min-h-0 bg-[#fefdfa]">
          <div className="flex min-w-0 flex-1 flex-col bg-[#fefdfa]">
            <ChatMessages
              conversationId={routeConversationId}
              conversationSelected={Boolean(routeConversationId)}
              isLoading={isLoadingMessages}
              messages={currentMessages}
              onOpenPreviewAttachment={handleOpenPreviewAttachment}
              viewportRef={messageViewportRef}
            />
            <div className="sticky bottom-0 z-10 w-full shrink-0 bg-[#fefdfa] pb-6 pt-0">
              <div className="mx-auto w-[70%] min-w-[320px] max-w-[1120px]">
                <ChatComposer
                  attachments={attachments}
                  isStreaming={isStreaming}
                  isUploading={isUploadingAttachments}
                  onAttachmentsSelected={handleAttachmentsSelected}
                  onRemoveAttachment={handleRemoveAttachment}
                  onStop={handleStop}
                  onSubmit={handleSend}
                />
              </div>
            </div>
          </div>
          {returnedPreviewAttachments.length > 0 ? (
            <div
              className={`border-l border-slate-200 bg-white/80 backdrop-blur transition-all duration-300 ${
                isPreviewSidebarOpen ? "w-[min(50vw,760px)]" : "w-10"
              }`}
            >
              <div className="flex h-full min-h-0">
                {/* 侧栏折叠按钮始终保留，方便快速收起预览区 */}
                <button
                  type="button"
                  onClick={() => setIsPreviewSidebarOpen((value) => !value)}
                  className="flex w-10 shrink-0 items-center justify-center border-r border-slate-200 bg-white/90 text-slate-500 transition hover:bg-slate-50 hover:text-slate-800"
                  title={isPreviewSidebarOpen ? "收起预览侧栏" : "展开预览侧栏"}
                >
                  <ChevronLeft
                    className={`h-6 w-6 transition-transform duration-300 ${
                      isPreviewSidebarOpen ? "rotate-180" : "rotate-0"
                    }`}
                  />
                </button>
                <div
                  className={`flex min-w-0 flex-1 min-h-0 flex-col overflow-hidden transition-opacity duration-200 ${
                    isPreviewSidebarOpen
                      ? "delay-150 opacity-100"
                      : "pointer-events-none delay-0 opacity-0"
                  }`}
                >
                  {/* 顶部 tab 按返回顺序展示所有可预览附件 */}
                  <div className="flex gap-2 overflow-x-auto border-b border-slate-200 px-3 py-2">
                    {returnedPreviewAttachments.map((attachment) => (
                      <button
                        key={attachment.f_path}
                        type="button"
                        onClick={() => setActivePreviewPath(attachment.f_path)}
                        className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                          activePreviewAttachment?.f_path === attachment.f_path
                            ? "bg-slate-900 text-white"
                            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                        }`}
                      >
                        {getAttachmentName(attachment.f_path)}
                      </button>
                    ))}
                  </div>
                  <div className="min-h-0 flex-1 bg-slate-50">
                    {activePreviewAttachment ? (
                      activeHtmlPreviewUrl ? (
                        <iframe
                          title={getAttachmentName(activePreviewAttachment.f_path)}
                          src={activeHtmlPreviewUrl}
                          sandbox=""
                          referrerPolicy="no-referrer"
                          className="h-full w-full border-0 bg-white"
                        />
                      ) : activeTableArtifact ? (
                        <InteractiveTablePreview artifact={activeTableArtifact} />
                      ) : (
                        <div className="flex h-full items-center justify-center text-sm text-slate-500">
                          正在加载预览...
                        </div>
                      )
                    ) : (
                      <div className="flex h-full items-center justify-center text-sm text-slate-500">
                        暂无可预览文件
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
