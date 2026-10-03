import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { getAccessToken } from "@/auth/index";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import { useChatStore } from "@/features/chat/store";
import { useChatDraft } from "@/features/chat/hooks/useChatDraft";
import { useConversationRun } from "@/features/chat/hooks/useConversationRun";
import type { MessageResponse, UserMessageRequest } from "@/features/chat/types";

/** 组合草稿与执行控制，完成用户消息发送和会话导航。 */
export function useChatStream({
  onNavigateToConversation,
  onRedirectToAuth,
  routeConversationId,
}: {
  onNavigateToConversation: (conversationId: string) => void;
  onRedirectToAuth: (returnTo?: string) => void;
  routeConversationId: string | null;
}) {
  const ensureConversation = useChatStore((state) => state.ensureConversation);
  const appendMessage = useChatStore((state) => state.appendMessage);
  const createConversation = useChatStore((state) => state.createConversation);
  const draft = useChatDraft(routeConversationId, onRedirectToAuth);
  const { draftConversationId, attachments, consumeDraft, releaseAttachments } = draft;
  const {
    isStreaming,
    runStream,
    markStreaming,
    handleStop,
    handleResume,
    abortConversationStream,
  } = useConversationRun(routeConversationId);

  const handleSend = async (value: string): Promise<boolean> => {
    const generation = sessionLifecycle.current();
    const token = getAccessToken();
    if (!token) {
      onRedirectToAuth();
      return false;
    }

    try {
      const requestMessage: UserMessageRequest = {
        parts: value ? [{ type: "text", text: value }] : [],
        attachments:
          attachments.length > 0
            ? attachments.map((attachment) => ({ f_path: attachment.f_path }))
            : undefined,
      };
      // 编辑器负责释放本地 Blob；已发送消息保留服务端附件引用，用于加载缩略图。
      const messageAttachments = attachments.map((attachment) => ({
        f_path: attachment.f_path,
        media_type: attachment.media_type,
        description: attachment.description,
      }));
      const userMessage: MessageResponse = {
        message_id: crypto.randomUUID(),
        created_at: new Date().toISOString(),
        role: "user",
        parts: requestMessage.parts,
        attachments: messageAttachments.length > 0 ? messageAttachments : undefined,
      };

      let conversationId = routeConversationId ?? draftConversationId;
      if (!conversationId) {
        const conversation = await createConversation(value);
        if (!conversation || !sessionLifecycle.isCurrent(generation)) return false;
        conversationId = conversation.conversation_id;
      } else if (!routeConversationId) {
        consumeDraft();
        ensureConversation({
          conversation_id: conversationId,
          title: value.trim().slice(0, 64) || "新对话",
          update_at: new Date().toISOString(),
          running: false,
        });
      }

      appendMessage(conversationId, userMessage);
      markStreaming(conversationId);
      releaseAttachments();
      if (routeConversationId !== conversationId) {
        onNavigateToConversation(conversationId);
      }
      runStream(conversationId, { type: "start", message: requestMessage });
      return true;
    } catch (error) {
      if (sessionLifecycle.isCurrent(generation)) {
        toast.error(getApiErrorMessage(error, "发送消息失败"));
      }
      return false;
    }
  };

  return {
    isStreaming,
    attachments,
    isUploadingAttachments: draft.isUploadingAttachments,
    handleAttachmentsSelected: draft.handleAttachmentsSelected,
    handleRemoveAttachment: draft.handleRemoveAttachment,
    handleSend,
    handleResume,
    handleStop,
    abortConversationStream,
    clearAttachments: draft.clearAttachments,
  };
}
