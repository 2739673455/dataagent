import { Download, Eye, FileText, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { chatApi } from "@/api/chat";
import { cn, getAttachmentName } from "@/lib/utils";
import type { Attachment } from "@/types";
import {
  isHtmlAttachment,
  isImageAttachment,
  isInteractiveTableAttachment,
} from "./displayModel";

export function AttachmentPreview({
  attachment,
  conversationId,
  onPreview,
}: {
  attachment: Attachment;
  conversationId?: string | null;
  onPreview?: (src: string, alt: string) => void;
}) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!conversationId || !isImageAttachment(attachment.f_path)) return;

    let objectUrl: string | null = null;
    let cancelled = false;

    void chatApi
      .fetchAttachmentFile(conversationId, attachment.f_path)
      .then((response) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(response.data);
        setImageUrl(objectUrl);
      })
      .catch(() => {
        if (cancelled) return;
        setImageUrl(null);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment.f_path, conversationId]);

  return (
    <div className="flex shrink-0 items-center justify-center">
      {imageUrl ? (
        <button
          type="button"
          onClick={() => onPreview?.(imageUrl, getAttachmentName(attachment.f_path))}
          className="h-5 w-5 overflow-hidden rounded"
        >
          <img
            src={imageUrl}
            alt={getAttachmentName(attachment.f_path)}
            className="h-full w-full object-cover"
          />
        </button>
      ) : (
        <FileText className="h-3.5 w-3.5 text-[#71717a]" />
      )}
    </div>
  );
}

export function AttachmentChip({
  attachment,
  conversationId,
  isUser,
  onPreview,
  onOpenPreviewAttachment,
}: {
  attachment: Attachment;
  conversationId?: string | null;
  isUser: boolean;
  onPreview?: (src: string, alt: string) => void;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
}) {
  const [isDownloading, setIsDownloading] = useState(false);
  const isHtml = isHtmlAttachment(attachment.f_path);
  const isPreviewable = isHtml || isInteractiveTableAttachment(attachment);

  const handleDownload = async () => {
    if (!conversationId || isDownloading) return;
    try {
      setIsDownloading(true);
      const response = await chatApi.fetchAttachmentFile(conversationId, attachment.f_path);
      const objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = getAttachmentName(attachment.f_path);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded border px-2 py-1 font-mono text-[11px]",
        isUser
          ? "border-[#c4c4be] bg-[#f0f0eb] text-[#27272a]"
          : "border-[#d4d4ce] bg-[#ffffff] text-[#27272a]"
      )}
    >
      <AttachmentPreview
        attachment={attachment}
        conversationId={conversationId}
        onPreview={onPreview}
      />
      <span
        className="max-w-[180px] truncate text-[11px]"
        title={getAttachmentName(attachment.f_path)}
      >
        {getAttachmentName(attachment.f_path)}
      </span>

      {isPreviewable && (
        <button
          type="button"
          onClick={() => onOpenPreviewAttachment?.(attachment)}
          className="ml-1 rounded p-0.5 text-[#52525b] hover:bg-[#deded8] hover:text-[#18181b]"
          title={isHtml ? "预览 HTML 产物" : "预览交互表格"}
        >
          <Eye className="h-3 w-3" />
        </button>
      )}

      {conversationId && (
        <button
          type="button"
          onClick={() => void handleDownload()}
          disabled={isDownloading}
          className="rounded p-0.5 text-[#71717a] hover:bg-[#deded8] hover:text-[#18181b]"
          title="下载产物文件"
        >
          {isDownloading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Download className="h-3 w-3" />
          )}
        </button>
      )}
    </div>
  );
}
