import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { chatApi } from "@/features/chat/api";
import { getApiErrorMessage } from "@/api/errors";
import { getAccessToken } from "@/auth/index";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import { useChatStore } from "@/features/chat/store";
import type { Attachment } from "@/features/chat/types";

/** 根据文件扩展名判断上传附件是否需要本地图片预览。 */
function isImageFile(name: string) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

/** 管理草稿会话、附件上传删除和本地预览资源的生命周期。 */
export function useChatDraft(routeConversationId: string | null, onRedirectToAuth: () => void) {
  const loadConversations = useChatStore((state) => state.loadConversations);
  const draftConversationIdRef = useRef<string | null>(null);
  const attachmentsRef = useRef<Attachment[]>([]);

  const [draftConversationId, setDraftConversationId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  // 编辑器持有附件预览 URL，清空附件时统一释放。
  const releaseAttachments = useCallback(() => {
    for (const attachment of attachmentsRef.current) {
      if (attachment.preview_url) URL.revokeObjectURL(attachment.preview_url);
    }
    attachmentsRef.current = [];
    setAttachments([]);
  }, []);

  const abandonDraftConversation = useCallback(() => {
    const conversationId = draftConversationIdRef.current;
    if (!conversationId) return;
    draftConversationIdRef.current = null;
    setDraftConversationId(null);
    void chatApi.deleteDraftConversation(conversationId).catch(() => {
      // 服务端 TTL 会回收网络异常时遗留的草稿
    });
  }, []);

  // 进入具体会话时回收编辑器草稿和附件预览
  useEffect(() => {
    if (!routeConversationId) return;
    abandonDraftConversation();
    releaseAttachments();
  }, [abandonDraftConversation, releaseAttachments, routeConversationId]);

  const handleAttachmentsSelected = async (files: File[]) => {
    const generation = sessionLifecycle.current();
    const token = getAccessToken();
    if (!token) {
      onRedirectToAuth();
      return;
    }

    setIsUploadingAttachments(true);
    try {
      let nextConversationId = routeConversationId ?? draftConversationId;
      if (!nextConversationId) {
        const response = await chatApi.createConversation(true);
        if (!sessionLifecycle.isCurrent(generation)) return;
        nextConversationId = response.data.conversation_id;
        draftConversationIdRef.current = nextConversationId;
        setDraftConversationId(nextConversationId);
        void loadConversations();
      }
      const nextAttachments: Attachment[] = [];
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
    } catch (error) {
      toast.error(getApiErrorMessage(error, "附件上传失败"));
    } finally {
      setIsUploadingAttachments(false);
    }
  };

  const handleRemoveAttachment = async (attachmentName: string) => {
    const targetConversationId = routeConversationId ?? draftConversationId;
    if (!targetConversationId) return;

    try {
      await chatApi.deleteAttachment(targetConversationId, attachmentName);
      setAttachments((current) => {
        const target = current.find((attachment) => attachment.f_path === attachmentName);
        if (target?.preview_url) {
          URL.revokeObjectURL(target.preview_url);
        }
        return current.filter((attachment) => attachment.f_path !== attachmentName);
      });
    } catch (error) {
      toast.error(getApiErrorMessage(error, "附件删除失败"));
    }
  };

  const clearAttachments = useCallback(() => {
    abandonDraftConversation();
    releaseAttachments();
  }, [abandonDraftConversation, releaseAttachments]);

  // 发送消息后，草稿转为正式会话；清除草稿归属以结束自动回收。
  const consumeDraft = () => {
    draftConversationIdRef.current = null;
    setDraftConversationId(null);
  };
  useEffect(
    () => () => {
      abandonDraftConversation();
      for (const attachment of attachmentsRef.current) {
        if (attachment.preview_url) URL.revokeObjectURL(attachment.preview_url);
      }
    },
    [abandonDraftConversation]
  );
  return {
    draftConversationId,
    attachments,
    isUploadingAttachments,
    handleAttachmentsSelected,
    handleRemoveAttachment,
    clearAttachments,
    consumeDraft,
    releaseAttachments,
  };
}
