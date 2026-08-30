import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { getApiErrorMessage } from "@/api/errors";
import { sanitizeHtmlForPreview } from "@/lib/htmlPreview";
import { getAttachmentName } from "@/lib/utils";
import type { Attachment, InteractiveTableArtifact, MessageResponse } from "@/types";
import { parseInteractiveTableArtifact } from "../components/InteractiveTablePreview";

function isHtmlAttachment(attachment: Attachment) {
  return attachment.media_type === "text/html" || /\.(html?)$/i.test(attachment.f_path);
}

function isInteractiveTableAttachment(attachment: Attachment) {
  return (
    attachment.media_type === "application/vnd.dataagent.table+json" ||
    /\.table\.json$/i.test(attachment.f_path)
  );
}

function collectReturnedPreviewAttachments(messages: MessageResponse[]): Attachment[] {
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

export function useChatPreview(
  routeConversationId: string | null,
  currentMessages: MessageResponse[]
) {
  const [isPreviewSidebarOpen, setIsPreviewSidebarOpen] = useState(true);
  const [activePreviewPath, setActivePreviewPath] = useState<string | null>(null);
  const [htmlPreviewUrls, setHtmlPreviewUrls] = useState<Record<string, string>>({});
  const [tableArtifacts, setTableArtifacts] = useState<Record<string, InteractiveTableArtifact>>(
    {}
  );
  const htmlPreviewUrlsRef = useRef<Record<string, string>>({});

  const returnedPreviewAttachments = useMemo(
    () => collectReturnedPreviewAttachments(currentMessages),
    [currentMessages]
  );

  const activePreviewAttachment =
    returnedPreviewAttachments.find((item) => item.f_path === activePreviewPath) ??
    returnedPreviewAttachments[0] ??
    null;

  useEffect(() => {
    htmlPreviewUrlsRef.current = htmlPreviewUrls;
  }, [htmlPreviewUrls]);

  // 新增可预览产物时自动展开
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

  // 按需拉取 HTML 附件
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
      .catch((error) => {
        if (cancelled) return;
        toast.error(
          getApiErrorMessage(
            error,
            `HTML 预览加载失败：${getAttachmentName(activePreviewAttachment.f_path)}`
          )
        );
      });

    return () => {
      cancelled = true;
    };
  }, [activePreviewAttachment, htmlPreviewUrls, isPreviewSidebarOpen, routeConversationId]);

  // 按需拉取交互表格附件
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
      .catch((error) => {
        if (cancelled) return;
        toast.error(
          getApiErrorMessage(
            error,
            `表格预览加载失败：${getAttachmentName(activePreviewAttachment.f_path)}`
          )
        );
      });

    return () => {
      cancelled = true;
    };
  }, [activePreviewAttachment, isPreviewSidebarOpen, routeConversationId, tableArtifacts]);

  // 卸载时回收 Object URL
  useEffect(() => {
    return () => {
      for (const url of Object.values(htmlPreviewUrlsRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  const handleOpenPreviewAttachment = useCallback((attachment: Attachment) => {
    setActivePreviewPath(attachment.f_path);
    setIsPreviewSidebarOpen(true);
  }, []);

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

  return {
    isPreviewSidebarOpen,
    setIsPreviewSidebarOpen,
    activePreviewPath,
    setActivePreviewPath,
    returnedPreviewAttachments,
    activePreviewAttachment,
    activeHtmlPreviewUrl,
    activeTableArtifact,
    handleOpenPreviewAttachment,
  };
}
