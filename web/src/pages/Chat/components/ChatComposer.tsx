import { ArrowUp, Paperclip, RotateCcw, Square } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Attachment } from "@/types";
import { AttachmentChip } from "./messages/AttachmentChip";

interface ChatComposerProps {
  attachments?: Attachment[];
  disabled?: boolean;
  isUploading?: boolean;
  isStreaming?: boolean;
  canResume?: boolean;
  onAttachmentsSelected: (files: File[]) => Promise<void> | void;
  onRemoveAttachment: (attachmentName: string) => void;
  onResume: () => void;
  onStop: () => void;
  onSubmit: (value: string) => Promise<boolean>;
}

export function ChatComposer({
  attachments = [],
  disabled = false,
  isUploading = false,
  isStreaming = false,
  canResume = false,
  onAttachmentsSelected,
  onRemoveAttachment,
  onResume,
  onStop,
  onSubmit,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const resizeTextarea = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 40), window.innerHeight * 0.35)}px`;
  };

  const handleSubmit = async () => {
    const next = value.trim();
    if ((!next && attachments.length === 0) || disabled || isUploading || isSubmitting) return;
    setIsSubmitting(true);
    try {
      if (!(await onSubmit(next))) return;
      setValue("");
      requestAnimationFrame(resizeTextarea);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative font-mono">
      <div className="overflow-hidden rounded border border-[#d4d4ce] bg-[#ffffff] shadow-xs transition-colors focus-within:border-[#1e2024]">
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(event) => {
            if (event.target.files && event.target.files.length > 0) {
              void onAttachmentsSelected(Array.from(event.target.files));
            }
            event.target.value = "";
          }}
        />

        {attachments.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 border-b border-[#e5e5df] bg-[#fafaf8] px-3 py-2">
            {attachments.map((attachment) => (
              <AttachmentChip
                key={attachment.f_path}
                attachment={attachment}
                isUser={false}
                onRemove={() => onRemoveAttachment(attachment.f_path)}
              />
            ))}
          </div>
        ) : null}

        <div className="flex items-start gap-2 px-4 pt-3">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              requestAnimationFrame(resizeTextarea);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSubmit();
              }
            }}
            disabled={disabled || isUploading || isSubmitting}
            className="min-h-[44px] max-h-[35vh] flex-1 resize-none bg-transparent font-mono text-sm leading-relaxed text-[#1e2024] placeholder:text-[#a1a1aa] focus:outline-none focus:ring-0 disabled:opacity-40"
          />
        </div>

        <div className="flex items-center justify-between border-t border-[#f0f0eb] bg-[#fafaf8] px-3.5 py-2 text-xs text-[#71717a]">
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={disabled || isUploading || isStreaming || isSubmitting}
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs text-[#52525b] transition hover:bg-[#ebebe6] hover:text-[#18181b] disabled:opacity-40"
              title="添加附件"
            >
              <Paperclip className="h-4 w-4" />
              <span>添加附件</span>
            </button>
            <span className="hidden text-xs text-[#a1a1aa] sm:inline">
              回车发送 / Shift+回车换行
            </span>
          </div>

          <div>
            {isStreaming ? (
              <Button
                size="sm"
                variant="destructive"
                className="gap-1.5 px-3.5 text-xs"
                onClick={onStop}
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                <span>停止</span>
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                {canResume ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 px-3.5 text-xs"
                    disabled={disabled || isUploading || isSubmitting}
                    onClick={onResume}
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    <span>继续执行</span>
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="default"
                  className="gap-1.5 px-3.5 text-xs"
                  disabled={
                    disabled ||
                    isUploading ||
                    isSubmitting ||
                    (!value.trim() && attachments.length === 0)
                  }
                  onClick={() => void handleSubmit()}
                >
                  <ArrowUp className="h-4 w-4" />
                  <span>{isSubmitting ? "发送中" : "发送"}</span>
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
