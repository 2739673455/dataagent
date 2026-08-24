import { Check, ChevronDown, Copy, Download, Eye, FileText, Loader2, Square, Wrench } from "lucide-react";
import type { RefObject } from "react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi } from "@/api/chat";
import { cn, getAttachmentName } from "@/lib/utils";
import type { Attachment, ImageContent, MessagePart, MessageResponse, TextContent } from "@/types";

type MessageDisplayItem = {
  key: string;
  type: "message";
  message: {
    key: string;
    conversationId?: string | null;
    createdAt?: string | null;
    role: MessageResponse["role"];
    attachments?: Attachment[] | null;
    parts: Array<TextContent | ImageContent>;
  };
};

type ToolRunDisplayItem = {
  key: string;
  type: "tool_run";
  toolCallId: string;
  conversationId?: string | null;
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  attachments?: Attachment[] | null;
  completed: boolean;
  interrupted?: boolean;
};

type DisplayItem = MessageDisplayItem | ToolRunDisplayItem;
const TOOL_ARGS_PREVIEW_MAX_LENGTH = 120;

function ImagePreview({ alt, onClose, src }: { alt: string; onClose: () => void; src: string }) {
  return createPortal(
    <button
      type="button"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-xs"
    >
      <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-3 shadow-xl">
        <div className="mb-2 flex items-center justify-between border-b border-[#e5e5df] pb-1 text-xs text-[#71717a]">
          <span>图片预览: {alt}</span>
          <span className="text-[#27272a]">点击关闭</span>
        </div>
        <img src={src} alt={alt} className="max-h-[80vh] max-w-[85vw] rounded object-contain" />
      </div>
    </button>,
    document.body
  );
}

function getMessageKey(message: MessageResponse) {
  if (message.message_id != null) {
    return `message-${message.message_id}`;
  }
  return `message-draft-${message.role}-${JSON.stringify(message.parts)}`;
}

function getMessagePartKey(part: MessagePart) {
  switch (part.type) {
    case "text":
      return `text-${part.text}`;
    case "image_url":
      return `image-${part.image_url}`;
    case "tool_call":
      return `tool-call-${part.tool_call_id}-${part.name}`;
    case "tool_result":
      return `tool-result-${part.tool_call_id}-${part.name}-${part.content}`;
  }
}

const messageTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function formatMessageTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(
    messageTimeFormatter.formatToParts(date).map((part) => [part.type, part.value])
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function isImageAttachment(name: string) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

function isHtmlAttachment(name: string) {
  return /\.(html?)$/i.test(name);
}

function isInteractiveTableAttachment(attachment: Attachment) {
  return (
    attachment.media_type === "application/vnd.dataagent.table+json" ||
    /\.table\.json$/i.test(attachment.f_path)
  );
}

function buildDisplayItems(
  conversationId: string | null,
  messages: MessageResponse[],
  isStreaming: boolean
): DisplayItem[] {
  const items: DisplayItem[] = [];
  const toolRuns = new Map<string, ToolRunDisplayItem>();

  for (const message of messages) {
    const regularParts: Array<TextContent | ImageContent> = [];
    const toolParts: Array<Extract<MessagePart, { type: "tool_call" | "tool_result" }>> = [];

    for (const part of message.parts) {
      if (part.type === "text") {
        if (part.text.trim()) {
          regularParts.push(part);
        }
        continue;
      }

      if (part.type === "image_url") {
        regularParts.push(part);
        continue;
      }

      toolParts.push(part);
    }

    const shouldRenderAsStandaloneMessage =
      regularParts.length > 0 ||
      ((message.attachments?.length ?? 0) > 0 && message.role !== "tool");

    if (shouldRenderAsStandaloneMessage) {
      items.push({
        key: getMessageKey(message),
        type: "message",
        message: {
          key: getMessageKey(message),
          conversationId,
          createdAt: message.created_at,
          role: message.role,
          attachments: message.attachments,
          parts: regularParts,
        },
      });
    }

    for (const part of toolParts) {
      if (part.type === "tool_call") {
        const item: ToolRunDisplayItem = {
          key: `tool-run-${part.tool_call_id}`,
          type: "tool_run",
          toolCallId: part.tool_call_id,
          conversationId,
          name: part.name,
          args: part.args,
          completed: false,
        };
        toolRuns.set(part.tool_call_id, item);
        items.push(item);
        continue;
      }

      const existing = toolRuns.get(part.tool_call_id);
      if (existing) {
        existing.name = part.name || existing.name;
        existing.result = part.content;
        existing.attachments = message.attachments;
        existing.completed = true;
        continue;
      }

      items.push({
        key: `tool-run-${part.tool_call_id}`,
        type: "tool_run",
        toolCallId: part.tool_call_id,
        conversationId,
        name: part.name,
        result: part.content,
        attachments: message.attachments,
        completed: true,
      });
    }
  }

  // 会话不再生成时，将未配对的 tool_call 标记为已中断
  if (!isStreaming) {
    for (const run of toolRuns.values()) {
      if (!run.completed) {
        run.interrupted = true;
      }
    }
  }

  return items;
}

function formatToolArgValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return "[...]";
  }
  return "{...}";
}

function getToolArgsPreview(args?: Record<string, unknown>): string | null {
  if (!args) return null;
  const entries = Object.entries(args);
  if (entries.length === 0) return null;

  const preview = entries.map(([key, value]) => `${key}=${formatToolArgValue(value)}`).join(", ");
  if (preview.length <= TOOL_ARGS_PREVIEW_MAX_LENGTH) {
    return preview;
  }
  return `${preview.slice(0, TOOL_ARGS_PREVIEW_MAX_LENGTH).trimEnd()}...`;
}

function CodeBlock({ children, className }: { children: React.ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false);
  const text = String(children).replace(/\n$/, "");

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group relative my-3 overflow-hidden rounded border border-[#d4d4ce] bg-[#fafaf8]">
      <div className="flex items-center justify-between border-b border-[#e5e5df] bg-[#f4f4f0] px-3 py-1 text-xs text-[#71717a]">
        <span>代码片段</span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="flex items-center gap-1 text-[#52525b] transition hover:text-[#18181b]"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-[#16a34a]" />
              <span className="text-[#16a34a]">已复制</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed text-[#1e2024]">
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

function MarkdownText({ text }: { text: string }) {
  return (
    <div className="font-mono text-sm leading-relaxed text-[#1e2024]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-2 mt-4 text-base font-bold text-[#18181b] border-b border-[#e5e5df] pb-1">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-3 text-sm font-bold text-[#18181b]">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1.5 mt-2.5 text-sm font-semibold text-[#27272a]">{children}</h3>
          ),
          p: ({ children }) => <p className="mb-2.5 whitespace-pre-wrap">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-2.5 list-disc space-y-1 pl-4 text-[#3f3f46]">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2.5 list-decimal space-y-1 pl-4 text-[#3f3f46]">{children}</ol>
          ),
          li: ({ children }) => <li>{children}</li>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-[#18181b] underline underline-offset-2 hover:text-[#52525b]"
            >
              {children}
            </a>
          ),
          code: ({ className, children, ...props }) => {
            const isBlock = Boolean(className);
            if (isBlock) {
              return <CodeBlock className={className}>{children}</CodeBlock>;
            }
            return (
              <code
                className="rounded border border-[#d4d4ce] bg-[#f0f0eb] px-1.5 py-0.5 text-xs text-[#18181b]"
                {...props}
              >
                {children}
              </code>
            );
          },
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded border border-[#d4d4ce] bg-[#ffffff]">
              <table className="min-w-full border-collapse text-left text-xs sm:text-sm text-[#27272a]">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              {children}
            </thead>
          ),
          th: ({ children }) => (
            <th className="border-r border-[#e5e5df] px-3 py-1.5 font-medium last:border-r-0">
              {children}
            </th>
          ),
          tbody: ({ children }) => <tbody>{children}</tbody>,
          tr: ({ children }) => (
            <tr className="border-b border-[#f0f0eb] last:border-b-0 hover:bg-[#fafaf8]">
              {children}
            </tr>
          ),
          td: ({ children }) => (
            <td className="border-r border-[#f0f0eb] px-3 py-1.5 last:border-r-0">{children}</td>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-[#52525b] bg-[#fafaf8] pl-3 py-1 italic text-[#52525b]">
              {children}
            </blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function PartView({
  part,
  onPreview,
  renderMarkdown = false,
}: {
  part: TextContent | ImageContent;
  onPreview?: (src: string, alt: string) => void;
  renderMarkdown?: boolean;
}) {
  if (part.type === "text") {
    return renderMarkdown ? (
      <MarkdownText text={part.text} />
    ) : (
      <div className="font-mono text-xs text-[#1e2024]">
        <p className="whitespace-pre-wrap leading-relaxed">{part.text}</p>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onPreview?.(part.image_url, "asset")}
      className="mt-2 overflow-hidden rounded border border-[#d4d4ce] bg-[#ffffff] p-1"
    >
      <img src={part.image_url} alt="asset" className="max-h-72 rounded object-cover" />
    </button>
  );
}

function ToolRunBar({
  item,
  onOpenPreviewAttachment,
}: {
  item: ToolRunDisplayItem;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const argsPreview = getToolArgsPreview(item.args);
  const hasAttachments = item.completed && (item.attachments?.length ?? 0) > 0;

  return (
    <div className="my-2 font-mono text-xs">
      <div className="rounded border border-[#d4d4ce] bg-[#ffffff] shadow-xs">
        <button
          type="button"
          onClick={() => setIsOpen((value) => !value)}
          className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left transition hover:bg-[#fafaf8]"
        >
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-[#ebebe6]">
              {item.completed ? (
                <Wrench className="h-3 w-3 text-[#52525b]" />
              ) : item.interrupted ? (
                <Square className="h-3 w-3 text-[#a1a1aa]" />
              ) : (
                <Loader2 className="h-3 w-3 animate-spin text-[#1e2024]" />
              )}
            </div>
            <span className="font-medium text-[#18181b]">{item.name}</span>
            {argsPreview ? (
              <span className="truncate text-[#71717a] text-[11px]">{argsPreview}</span>
            ) : null}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium",
                item.completed
                  ? "bg-[#ebebe6] text-[#3f3f46]"
                  : item.interrupted
                    ? "bg-[#f0f0ec] text-[#a1a1aa]"
                    : "bg-[#deded8] text-[#18181b]"
              )}
            >
              {item.completed ? "已完成" : item.interrupted ? "已中断" : "执行中"}
            </span>
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 text-[#71717a] transition-transform",
                isOpen && "rotate-180"
              )}
            />
          </div>
        </button>

        {isOpen && (
          <div className="space-y-2 border-t border-[#e5e5df] bg-[#fafaf8] p-3 text-[11px]">
            {item.args !== undefined ? (
              <div className="space-y-1">
                <p className="font-medium text-[#71717a]">参数</p>
                <pre className="overflow-x-auto whitespace-pre-wrap rounded border border-[#e5e5df] bg-[#ffffff] p-2 text-[#3f3f46]">
                  {JSON.stringify(item.args, null, 2)}
                </pre>
              </div>
            ) : null}
            {item.result !== undefined ? (
              <div className="space-y-1">
                <p className="font-medium text-[#71717a]">输出</p>
                <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded border border-[#e5e5df] bg-[#ffffff] p-2 text-[#27272a]">
                  {item.result}
                </pre>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {hasAttachments && (
        <div className="mt-2 flex flex-wrap gap-2 px-1">
          {(item.attachments ?? []).map((attachment) => (
            <AttachmentChip
              key={attachment.f_path}
              attachment={attachment}
              conversationId={item.conversationId}
              isUser={false}
              onOpenPreviewAttachment={onOpenPreviewAttachment}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AttachmentPreview({
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
    <div className="flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden rounded border border-[#d4d4ce] bg-[#ffffff]">
      {imageUrl ? (
        <button
          type="button"
          onClick={() => onPreview?.(imageUrl, getAttachmentName(attachment.f_path))}
          className="h-full w-full"
        >
          <img
            src={imageUrl}
            alt={getAttachmentName(attachment.f_path)}
            className="h-full w-full object-cover"
          />
        </button>
      ) : (
        <FileText className="h-3 w-3 text-[#71717a]" />
      )}
    </div>
  );
}

function AttachmentChip({
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

function MessageBubble({
  message,
  onOpenPreviewAttachment,
  username,
}: {
  message: MessageDisplayItem["message"];
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  username: string;
}) {
  const isUser = message.role === "user";
  const createdAt = formatMessageTime(message.createdAt);
  const [previewImage, setPreviewImage] = useState<{
    src: string;
    alt: string;
  } | null>(null);

  return (
    <>
      <div className="my-3 font-mono">
        <div
          className={cn(
            "rounded border p-3.5 shadow-xs",
            isUser ? "border-[#d4d4ce] bg-[#eaeae5]" : "border-[#d4d4ce] bg-[#ffffff]"
          )}
        >
          {/* 消息来源标识 */}
          <div className="mb-2 flex items-center justify-between border-b border-[#e5e5df] pb-1.5 text-xs">
            <span className="font-semibold text-[#18181b]">{isUser ? username : "DataAgent"}</span>
            {createdAt ? <time className="text-[#71717a]">{createdAt}</time> : null}
          </div>

          <div className="space-y-2">
            {message.attachments?.length ? (
              <div className="flex flex-wrap gap-2">
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
            ) : null}

            {message.parts.map((part) => (
              <PartView
                key={getMessagePartKey(part)}
                part={part}
                onPreview={(src, alt) => setPreviewImage({ src, alt })}
                renderMarkdown={!isUser}
              />
            ))}
          </div>
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

interface ChatMessagesProps {
  conversationId: string | null;
  conversationSelected: boolean;
  isLoading: boolean;
  isStreaming: boolean;
  messages: MessageResponse[];
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  username: string;
  viewportRef: RefObject<HTMLDivElement | null>;
}

export function ChatMessages({
  conversationId,
  conversationSelected,
  isLoading,
  isStreaming,
  messages,
  onOpenPreviewAttachment,
  username,
  viewportRef,
}: ChatMessagesProps) {
  const displayItems = buildDisplayItems(conversationId, messages, isStreaming);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#f4f4f0] font-mono text-[#1e2024]">
      <div
        ref={viewportRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4 [scrollbar-gutter:stable_both-edges]"
      >
        {!conversationSelected ? (
          <div className="flex h-full flex-col items-center justify-center p-6 text-center">
            <div className="w-full max-w-lg space-y-2 text-left">
              <p className="text-xs font-medium text-[#71717a]">分析示例：</p>
              <div className="space-y-1.5">
                {[
                  "统计分析最近 30 天各个类目的 GMV 增长走势",
                  "按渠道拆解本月新客次日留存与客单价分布",
                  "查询订单退款率最高的 Top 10 商品与核心原因",
                ].map((example) => (
                  <div
                    key={example}
                    className="rounded border border-[#d4d4ce] bg-[#ffffff] px-3.5 py-2.5 text-xs text-[#52525b] shadow-xs"
                  >
                    {example}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : isLoading ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex items-center gap-2 rounded border border-[#d4d4ce] bg-[#ffffff] px-4 py-2 text-xs text-[#52525b] shadow-xs">
              <Loader2 className="h-4 w-4 animate-spin text-[#18181b]" />
              <span>正在获取会话消息...</span>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-4xl space-y-1">
            {displayItems.map((item) =>
              item.type === "message" ? (
                <MessageBubble
                  key={item.key}
                  message={item.message}
                  onOpenPreviewAttachment={onOpenPreviewAttachment}
                  username={username}
                />
              ) : (
                <ToolRunBar
                  key={item.key}
                  item={item}
                  onOpenPreviewAttachment={onOpenPreviewAttachment}
                />
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}
