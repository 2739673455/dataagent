import { useState } from "react";
import { cn } from "@/lib/utils";
import type { Attachment } from "@/types";
import { AttachmentChip } from "./AttachmentChip";
import { formatMessageTime, getMessagePartKey } from "./displayModel";
import { ImagePreview } from "./ImagePreview";
import { PartView } from "./MarkdownRenderer";
import type { MessageDisplayItem } from "./types";

export function MessageBubble({
  message,
  onOpenPreviewAttachment,
}: {
  message: MessageDisplayItem["message"];
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
}) {
  const isUser = message.role === "user";
  const createdAt = formatMessageTime(message.createdAt);
  const [previewImage, setPreviewImage] = useState<{
    src: string;
    alt: string;
  } | null>(null);
  const attachmentChips = message.attachments?.length ? (
    <div className="flex flex-wrap gap-1.5">
      {message.attachments.map((attachment) => (
        <AttachmentChip
          key={attachment.f_path}
          attachment={attachment}
          conversationId={message.conversationId}
          isUser={isUser}
          onPreview={(src, alt) => setPreviewImage({ src, alt })}
          onOpenPreviewAttachment={onOpenPreviewAttachment}
        />
      ))}
    </div>
  ) : null;

  return (
    <>
      <div className="my-2 font-mono">
        <div className="px-1 py-1">
          <div className="space-y-1.5">
            {isUser ? attachmentChips : null}

            {message.parts.map((part) => (
              <PartView
                key={getMessagePartKey(part)}
                part={part}
                onPreview={(src, alt) => setPreviewImage({ src, alt })}
                renderMarkdown={!isUser}
                isUser={isUser}
              />
            ))}

            {isUser ? null : attachmentChips}
          </div>

          {createdAt ? (
            <div className="mt-1.5 flex justify-start">
              <time className={cn("text-[11px]", isUser ? "text-[#3b82f6]" : "text-[#71717a]")}>
                {createdAt}
              </time>
            </div>
          ) : null}
        </div>
      </div>

      {previewImage && (
        <ImagePreview
          src={previewImage.src}
          alt={previewImage.alt}
          onClose={() => setPreviewImage(null)}
        />
      )}
    </>
  );
}
