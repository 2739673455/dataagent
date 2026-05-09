import { ArrowUp, FileText, Plus, Square, X } from "lucide-react";
import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn, getAttachmentName } from "@/lib/utils";
import type { Attachment } from "@/types";

interface ChatComposerProps {
  attachments?: Attachment[];
  disabled?: boolean;
  isUploading?: boolean;
  isStreaming?: boolean;
  onAttachmentsSelected: (files: File[]) => Promise<void> | void;
  onRemoveAttachment: (attachmentName: string) => void;
  onStop: () => void;
  onSubmit: (value: string) => Promise<void> | void;
}

// 图片附件使用全屏浮层预览，避免在输入区内受尺寸限制
function ImagePreview({ alt, onClose, src }: { alt: string; onClose: () => void; src: string }) {
  return createPortal(
    <button
      type="button"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-6"
    >
      <img
        src={src}
        alt={alt}
        className="max-h-[88vh] max-w-[88vw] rounded-[1.25rem] object-contain shadow-2xl"
      />
    </button>,
    document.body
  );
}

export function ChatComposer({
  attachments = [],
  disabled = false,
  isUploading = false,
  isStreaming = false,
  onAttachmentsSelected,
  onRemoveAttachment,
  onStop,
  onSubmit,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const [previewImage, setPreviewImage] = useState<{
    src: string;
    alt: string;
  } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // 输入框高度随内容增长，但限制在视口高度的一部分之内
  const resizeTextarea = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.3)}px`;
  };

  // 发送时先清空输入框，再把修剪后的内容交给上层处理
  const handleSubmit = async () => {
    const next = value.trim();
    if ((!next && attachments.length === 0) || disabled || isUploading) return;
    setValue("");
    requestAnimationFrame(resizeTextarea);
    await onSubmit(next);
  };

  // 组件内只根据 preview_url 判断是否可直接显示图片缩略图
  const isImageAttachment = (attachment: Attachment) => Boolean(attachment.preview_url);

  const openPreview = (attachment: Attachment) => {
    if (!attachment.preview_url) return;
    setPreviewImage({
      src: attachment.preview_url,
      alt: getAttachmentName(attachment.f_path),
    });
  };

  return (
    <div className="relative">
      <div className="overflow-hidden rounded-[2rem] border border-slate-300 bg-white shadow-[0_-18px_36px_-12px_rgba(255,255,255,0.92)] transition-all focus-within:border-slate-300 focus-within:shadow-[0_-18px_36px_-12px_rgba(255,255,255,0.92)]">
        {/* 原生文件输入框隐藏，通过左下角按钮触发 */}
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
          /* 已选附件统一显示在输入框上方，可单独移除或预览 */
          <div className="flex flex-wrap gap-2 px-4 pt-4">
            {attachments.map((attachment) => (
              <div
                key={attachment.f_path}
                className="flex max-w-full items-center gap-3 rounded-[1.1rem] bg-slate-100 px-3 py-2 text-sm text-slate-700"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-slate-200">
                  {isImageAttachment(attachment) ? (
                    <button
                      type="button"
                      onClick={() => openPreview(attachment)}
                      className="h-full w-full"
                    >
                      <img
                        src={attachment.preview_url}
                        alt={getAttachmentName(attachment.f_path)}
                        className="h-full w-full object-cover"
                      />
                    </button>
                  ) : (
                    <FileText className="h-[18px] w-[18px] text-slate-600" />
                  )}
                </div>
                <span className="truncate">{getAttachmentName(attachment.f_path)}</span>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(attachment.f_path)}
                  className="rounded-full p-0.5 text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <Textarea
          ref={textareaRef}
          rows={1}
          placeholder=""
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
          disabled={disabled || isUploading}
          className="min-h-[52px] max-h-[30vh] flex-1 resize-none overflow-y-auto rounded-none border-none bg-white px-5 pb-2 pt-4 text-[15px] text-slate-800 shadow-none placeholder:text-slate-500 focus-visible:ring-0"
        />
        <div className="flex items-center justify-between bg-white px-3 pb-2 pt-0.5">
          {/* 左侧按钮负责选择附件，流式输出时不允许继续追加上传 */}
          <Button
            type="button"
            variant="ghost"
            disabled={disabled || isUploading || isStreaming}
            onClick={() => fileInputRef.current?.click()}
            className="h-9 w-9 rounded-full border-none bg-transparent p-0 text-slate-500 shadow-none transition-all hover:bg-slate-200/80 hover:text-slate-700 active:scale-95 disabled:text-slate-400"
          >
            <Plus className="h-5 w-5" />
          </Button>
          <Button
            /* 右侧按钮在普通状态下发送，在流式状态下转成停止生成 */
            onClick={() => {
              if (isStreaming) {
                onStop();
                return;
              }
              void handleSubmit();
            }}
            disabled={
              isStreaming
                ? false
                : disabled || isUploading || (!value.trim() && attachments.length === 0)
            }
            variant="ghost"
            className={cn(
              "h-9 w-9 border-none p-0 shadow-none transition-all active:scale-95",
              isStreaming
                ? "rounded-full bg-transparent text-red-500 hover:bg-red-100 hover:text-red-600"
                : "rounded-full bg-transparent text-slate-500 hover:bg-slate-200/80 hover:text-slate-700",
              disabled && !isStreaming ? "text-slate-400" : ""
            )}
          >
            {isStreaming ? (
              <Square className="h-4 w-4 fill-none stroke-current stroke-[2.75]" />
            ) : (
              <ArrowUp className="h-5 w-5" />
            )}
          </Button>
        </div>
      </div>
      {previewImage ? (
        /* 图片预览浮层挂到 document.body，避免被父级 overflow 裁切 */
        <ImagePreview
          src={previewImage.src}
          alt={previewImage.alt}
          onClose={() => setPreviewImage(null)}
        />
      ) : null}
    </div>
  );
}
