import {
  Download,
  Eye,
  File,
  FileArchive,
  FileCode2,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  Globe,
  X,
} from "lucide-react";
import { type ComponentType, useEffect, useState } from "react";
import { chatApi } from "@/api/chat";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { cn, getAttachmentName } from "@/lib/utils";
import type { Attachment } from "@/types";
import {
  type AttachmentFileType,
  getAttachmentFileType,
  isHtmlAttachment,
  isImageAttachment,
  isInteractiveTableAttachment,
} from "./displayModel";

export const FILE_TYPE_CONFIGS: Record<
  AttachmentFileType,
  {
    icon: ComponentType<{ className?: string }>;
    badgeClass: string;
    label: string;
  }
> = {
  table: {
    icon: FileSpreadsheet,
    badgeClass: "bg-emerald-50 text-emerald-600 border-emerald-200/80",
    label: "表格",
  },
  code: {
    icon: FileCode2,
    badgeClass: "bg-blue-50 text-blue-600 border-blue-200/80",
    label: "代码",
  },
  json: {
    icon: FileJson,
    badgeClass: "bg-amber-50 text-amber-600 border-amber-200/80",
    label: "数据",
  },
  markdown: {
    icon: FileText,
    badgeClass: "bg-indigo-50 text-indigo-600 border-indigo-200/80",
    label: "文档",
  },
  html: {
    icon: Globe,
    badgeClass: "bg-purple-50 text-purple-600 border-purple-200/80",
    label: "页面",
  },
  image: {
    icon: FileImage,
    badgeClass: "bg-rose-50 text-rose-600 border-rose-200/80",
    label: "图片",
  },
  archive: {
    icon: FileArchive,
    badgeClass: "bg-orange-50 text-orange-600 border-orange-200/80",
    label: "归档",
  },
  text: {
    icon: FileText,
    badgeClass: "bg-zinc-100 text-zinc-600 border-zinc-200/80",
    label: "文本",
  },
  generic: {
    icon: File,
    badgeClass: "bg-zinc-100 text-zinc-500 border-zinc-200/80",
    label: "文件",
  },
};

export function AttachmentIconBadge({
  attachment,
  conversationId,
  onPreview,
  size = "md",
}: {
  attachment: Attachment;
  conversationId?: string | null;
  onPreview?: (src: string, alt: string) => void;
  size?: "sm" | "md";
}) {
  const [imageUrl, setImageUrl] = useState<string | null>(attachment.preview_url ?? null);
  const fileType = getAttachmentFileType(attachment.f_path, attachment.media_type);
  const config = FILE_TYPE_CONFIGS[fileType];
  const IconComponent = config.icon;
  const isImage = isImageAttachment(attachment.f_path);

  useEffect(() => {
    if (attachment.preview_url) {
      setImageUrl(attachment.preview_url);
      return;
    }
    if (!conversationId || !isImage) return;

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
  }, [attachment.f_path, attachment.preview_url, conversationId, isImage]);

  const badgeSizeClass = size === "sm" ? "h-5 w-5" : "h-6 w-6";
  const iconSizeClass = size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5";

  if (imageUrl && isImage) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onPreview?.(imageUrl, getAttachmentName(attachment.f_path));
        }}
        className={cn(
          "shrink-0 overflow-hidden rounded-md border border-[#e4e4de] transition hover:opacity-85",
          badgeSizeClass
        )}
        title="预览图片"
      >
        <img
          src={imageUrl}
          alt={getAttachmentName(attachment.f_path)}
          className="h-full w-full object-cover"
        />
      </button>
    );
  }

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center rounded-md border",
        badgeSizeClass,
        config.badgeClass
      )}
      title={config.label}
    >
      <IconComponent className={iconSizeClass} />
    </div>
  );
}

export function AttachmentChip({
  attachment,
  conversationId,
  isUser,
  onPreview,
  onOpenPreviewAttachment,
  onRemove,
}: {
  attachment: Attachment;
  conversationId?: string | null;
  isUser: boolean;
  onPreview?: (src: string, alt: string) => void;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  onRemove?: () => void;
}) {
  const [isDownloading, setIsDownloading] = useState(false);
  const isHtml = isHtmlAttachment(attachment.f_path);
  const isTable = isInteractiveTableAttachment(attachment);
  const isPreviewable = isHtml || isTable;
  const canPreview = isPreviewable && onOpenPreviewAttachment !== undefined;
  const fileName = getAttachmentName(attachment.f_path);

  const handleDownload = async () => {
    if (!conversationId || isDownloading) return;
    try {
      setIsDownloading(true);
      const response = await chatApi.fetchAttachmentFile(conversationId, attachment.f_path);
      const objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } finally {
      setIsDownloading(false);
    }
  };

  const handlePrimaryClick = () => {
    if (canPreview) {
      onOpenPreviewAttachment(attachment);
    } else if (conversationId) {
      void handleDownload();
    }
  };

  return (
    <div
      className={cn(
        "group inline-flex items-center gap-1.5 rounded-lg border py-1 pl-1.5 pr-2 font-mono text-xs transition-all duration-150 select-none",
        isUser
          ? "border-[#d8d8d0] bg-[#f0f0eb] text-[#27272a] hover:border-[#b8b8b0] hover:bg-[#eaeae4]"
          : "border-[#e4e4de] bg-[#ffffff] text-[#27272a] shadow-[0_1px_2px_rgba(0,0,0,0.03)] hover:border-[#b8b8b0] hover:bg-[#fafaf8]"
      )}
    >
      <AttachmentIconBadge
        attachment={attachment}
        conversationId={conversationId}
        onPreview={onPreview}
        size="md"
      />

      {canPreview || conversationId ? (
        <button
          type="button"
          onClick={handlePrimaryClick}
          className="max-w-[220px] sm:max-w-[280px] truncate text-left text-[11.5px] font-medium text-[#27272a] transition-colors group-hover:text-[#09090b] hover:underline"
          title={
            canPreview ? `点击预览 ${fileName}` : conversationId ? `点击下载 ${fileName}` : fileName
          }
        >
          {fileName}
        </button>
      ) : (
        <span
          className="max-w-[220px] sm:max-w-[280px] truncate text-[11.5px] font-medium text-[#27272a]"
          title={fileName}
        >
          {fileName}
        </span>
      )}

      <div className="flex items-center gap-0.5 ml-0.5 shrink-0">
        {canPreview && (
          <button
            type="button"
            onClick={() => onOpenPreviewAttachment?.(attachment)}
            className="rounded p-0.5 text-[#71717a] transition hover:bg-[#ebebe5] hover:text-[#18181b]"
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
            className="rounded p-0.5 text-[#71717a] transition hover:bg-[#ebebe5] hover:text-[#18181b]"
            title="下载产物文件"
          >
            {isDownloading ? (
              <DotMatrixLoader label="正在下载" className="text-[#71717a]" />
            ) : (
              <Download className="h-3 w-3" />
            )}
          </button>
        )}

        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="rounded p-0.5 text-[#71717a] transition hover:bg-[#ebebe5] hover:text-[#dc2626]"
            title="移除附件"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}
