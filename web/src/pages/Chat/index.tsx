import { useCallback, useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { changePassword, logoutUser, redirectToLogin, useAuthStore } from "@/auth";
import { ROUTES } from "@/config/settings";
import { useChatStore } from "@/stores/chatStore";
import { ChatComposer } from "./components/ChatComposer";
import { ChatMessages } from "./components/ChatMessages";
import { ChatSidebar, ChatUserFooter } from "./components/ChatSidebar";
import { PreviewSidebar } from "./components/PreviewSidebar";
import { useChatPreview } from "./hooks/useChatPreview";
import { useChatStream } from "./hooks/useChatStream";

export default function ChatPage() {
  const navigate = useNavigate();
  const params = useParams();

  const conversations = useChatStore((state) => state.conversations);
  const messagesByConversation = useChatStore((state) => state.messagesByConversation);
  const subagentRunsByConversation = useChatStore((state) => state.subagentRunsByConversation);
  const isLoadingMessages = useChatStore((state) => state.isLoadingMessages);
  const loadConversations = useChatStore((state) => state.loadConversations);
  const deleteConversation = useChatStore((state) => state.deleteConversation);
  const renameConversation = useChatStore((state) => state.renameConversation);
  const loadMessages = useChatStore((state) => state.loadMessages);
  const loadSubagentMessages = useChatStore((state) => state.loadSubagentMessages);
  const user = useAuthStore((state) => state.user);

  const messageViewportRef = useRef<HTMLDivElement | null>(null);

  // 校验有效 UUID 格式的 conversationId
  const routeConversationId = (() => {
    const raw = params.conversationId;
    if (!raw) return null;
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(raw)
      ? raw
      : null;
  })();

  const currentMessages = routeConversationId
    ? (messagesByConversation[routeConversationId] ?? [])
    : [];
  const currentSubagentRuns = routeConversationId
    ? (subagentRunsByConversation[routeConversationId] ?? {})
    : {};
  const currentMessageCount = currentMessages.length;

  const {
    isStreaming,
    attachments,
    isUploadingAttachments,
    handleAttachmentsSelected,
    handleRemoveAttachment,
    handleSend,
    handleStop,
    abortConversationStream,
    clearAttachments,
  } = useChatStream({
    routeConversationId,
    onNavigateToConversation: (id) => navigate(ROUTES.chatConversation(id)),
    onRedirectToAuth: (returnTo) => redirectToLogin(returnTo),
  });

  const {
    isPreviewSidebarOpen,
    setIsPreviewSidebarOpen,
    activePreviewAttachment,
    activeHtmlPreviewUrl,
    activeTableArtifact,
    returnedPreviewAttachments,
    setActivePreviewPath,
    handleOpenPreviewAttachment,
  } = useChatPreview(routeConversationId, currentMessages);

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
    void loadConversations().catch((error) => {
      toast.error(getApiErrorMessage(error, "加载会话列表失败"));
    });
  }, [loadConversations]);

  // 切换到具体会话时按需加载历史消息
  useEffect(() => {
    if (!routeConversationId) return;
    if (messagesByConversation[routeConversationId] === undefined) {
      void loadMessages(routeConversationId).catch((error) => {
        toast.error(getApiErrorMessage(error, "加载历史消息失败"));
      });
    }
  }, [loadMessages, messagesByConversation, routeConversationId]);

  // 首次渲染历史消息时直接滚到底部
  useEffect(() => {
    if (!routeConversationId || isLoadingMessages) return;
    if (currentMessageCount < 1) return;

    const frameId = window.requestAnimationFrame(() => {
      scrollToBottom("auto");
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [currentMessageCount, isLoadingMessages, routeConversationId, scrollToBottom]);

  // 新建对话
  const handleCreateConversation = () => {
    clearAttachments();
    navigate(ROUTES.chat);
  };

  // 删除当前会话
  const handleDeleteConversation = async (conversationId: string) => {
    abortConversationStream(conversationId);
    try {
      if (!(await deleteConversation(conversationId))) return;
      if (routeConversationId === conversationId) {
        navigate(ROUTES.chat);
      }
      toast.success("对话已删除");
    } catch (error) {
      toast.error(getApiErrorMessage(error, "删除对话失败"));
    }
  };

  return (
    <div className="flex h-[100dvh] min-h-screen flex-col overflow-hidden bg-[#f4f4f0] font-mono text-[#1e2024]">
      {/* 顶部全局标题栏 */}
      <header className="flex h-11 shrink-0 select-none items-center justify-between border-b border-[#d4d4ce] bg-[#ffffff] px-4 text-sm">
        <div className="flex items-center gap-3">
          <span className="font-bold text-[#18181b] text-base">DataAgent</span>
        </div>

        <div className="flex items-center gap-3 text-xs">
          {isStreaming ? (
            <span className="font-medium text-[#18181b]">处理中</span>
          ) : (
            <span className="text-[#71717a]">就绪</span>
          )}
        </div>
      </header>

      {/* 主工作区分栏 */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* 左侧会话管理器 */}
        <div className="w-64 shrink-0 overflow-hidden hidden md:block">
          <ChatSidebar
            conversations={conversations}
            activeConversationId={routeConversationId}
            onCreate={handleCreateConversation}
            onDelete={(conversationId) => void handleDeleteConversation(conversationId)}
            onRename={renameConversation}
          />
        </div>

        {/* 中间聊天主执行区 */}
        <div className="flex min-w-0 flex-1 flex-col bg-[#f4f4f0]">
          <ChatMessages
            conversationId={routeConversationId}
            conversationSelected={Boolean(routeConversationId)}
            isLoading={isLoadingMessages}
            isStreaming={isStreaming}
            messages={currentMessages}
            subagentRuns={currentSubagentRuns}
            loadSubagentMessages={loadSubagentMessages}
            onOpenPreviewAttachment={handleOpenPreviewAttachment}
            viewportRef={messageViewportRef}
          />
        </div>

        {/* 右侧产物预览分栏 */}
        <PreviewSidebar
          activeHtmlPreviewUrl={activeHtmlPreviewUrl}
          activePreviewAttachment={activePreviewAttachment}
          activeTableArtifact={activeTableArtifact}
          isOpen={isPreviewSidebarOpen}
          onSelectAttachmentPath={setActivePreviewPath}
          onToggleOpen={() => setIsPreviewSidebarOpen((v) => !v)}
          previewAttachments={returnedPreviewAttachments}
        />
      </div>

      {/* 底部统一操作栏 */}
      <div className="flex shrink-0 border-t border-[#d4d4ce]">
        <div className="w-64 shrink-0 border-r border-[#d4d4ce] hidden md:block">
          <ChatUserFooter
            user={user}
            onChangePassword={async (currentPassword, newPassword) => {
              await changePassword(currentPassword, newPassword);
              toast.success("密码已修改，请重新登录");
              redirectToLogin(ROUTES.chat);
            }}
            onLogout={() => {
              void logoutUser().finally(() => redirectToLogin(ROUTES.chat));
            }}
          />
        </div>

        <div className="flex min-w-0 flex-1 items-center bg-[#f4f4f0] p-3">
          <div className="mx-auto w-full max-w-4xl">
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
    </div>
  );
}
