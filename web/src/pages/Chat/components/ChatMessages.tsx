import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  Eye,
  FileText,
  Loader2,
  Sparkles,
  Square,
  Wrench,
} from "lucide-react";
import type { RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi } from "@/api/chat";
import { cn, getAttachmentName } from "@/lib/utils";
import type {
  AgentType,
  Attachment,
  ImageContent,
  MessagePart,
  MessageResponse,
  SubagentRun,
  SubagentRunIdentity,
  TextContent,
} from "@/types";

export type MessageDisplayItem = {
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

export type ToolRunDisplayItem = {
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

export type DisplayItem = MessageDisplayItem | ToolRunDisplayItem;

export type ChatTurn = {
  key: string;
  userItem: MessageDisplayItem | null;
  intermediateItems: DisplayItem[];
  finalItem: MessageDisplayItem | null;
};

type SubagentRunMap = Record<string, SubagentRun>;

type UserMessageNavigationItem = {
  key: string;
  createdAt: string | null;
  preview: string;
};

const TOOL_ARGS_PREVIEW_MAX_LENGTH = 120;

const AGENT_CONFIG: Record<
  AgentType,
  {
    name: string;
    roleName: string;
    title: string;
    badgeBg: string;
    badgeText: string;
    border: string;
    bg: string;
  }
> = {
  explorer: {
    name: "Explorer",
    roleName: "探索者",
    title: "Explorer 探索者",
    badgeBg: "bg-[#e0f2fe]",
    badgeText: "text-[#0369a1]",
    border: "border-[#bae6fd]",
    bg: "bg-[#f0f9ff]",
  },
  analyst: {
    name: "Analyst",
    roleName: "分析师",
    title: "Analyst 分析师",
    badgeBg: "bg-[#ede9fe]",
    badgeText: "text-[#6d28d9]",
    border: "border-[#ddd6fe]",
    bg: "bg-[#f5f3ff]",
  },
  reviewer: {
    name: "Reviewer",
    roleName: "审查员",
    title: "Reviewer 审查员",
    badgeBg: "bg-[#fef3c7]",
    badgeText: "text-[#b45309]",
    border: "border-[#fde68a]",
    bg: "bg-[#fffbeb]",
  },
  visualizer: {
    name: "Visualizer",
    roleName: "可视化专家",
    title: "Visualizer 可视化专家",
    badgeBg: "bg-[#d1fae5]",
    badgeText: "text-[#047857]",
    border: "border-[#a7f3d0]",
    bg: "bg-[#ecfdf5]",
  },
};

const AGENT_TYPES = new Set<AgentType>(["explorer", "analyst", "reviewer", "visualizer"]);

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
  second: "2-digit",
  hourCycle: "h23",
});

function formatMessageTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(
    messageTimeFormatter.formatToParts(date).map((part) => [part.type, part.value])
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

function getUserMessagePreview(message: MessageDisplayItem["message"]): string {
  const content = message.parts
    .map((part) => (part.type === "text" ? part.text : "[图片]"))
    .join("\n")
    .trim();
  if (content) return content;

  const attachmentNames = message.attachments?.map((attachment) =>
    getAttachmentName(attachment.f_path)
  );
  return attachmentNames?.length ? `[附件] ${attachmentNames.join("、")}` : "空消息";
}

function findActiveUserMessageKey(viewport: HTMLDivElement): string | null {
  const messageElements = viewport.querySelectorAll<HTMLElement>("[data-user-message-key]");
  if (messageElements.length === 0) return null;

  const viewportRect = viewport.getBoundingClientRect();
  const activationLine = viewportRect.top + viewportRect.height / 2;
  let activeKey = messageElements[0]?.dataset.userMessageKey ?? null;

  for (const element of messageElements) {
    if (element.getBoundingClientRect().top > activationLine) break;
    activeKey = element.dataset.userMessageKey ?? activeKey;
  }
  return activeKey;
}

function UserMessageQuickNavigation({
  activeKey,
  items,
  onNavigate,
}: {
  activeKey: string | null;
  items: UserMessageNavigationItem[];
  onNavigate: (key: string) => void;
}) {
  return (
    <nav
      aria-label="用户消息快速导航"
      className="absolute left-3 top-1/2 z-20 hidden -translate-y-1/2 xl:block"
    >
      <div className="flex flex-col gap-1.5 py-2">
        {items.map((item, index) => {
          const tooltipPosition =
            index < 2
              ? "top-0"
              : index >= items.length - 2
                ? "bottom-0"
                : "top-1/2 -translate-y-1/2";
          return (
            <div key={item.key} className="group relative flex h-2.5 items-center">
              <button
                type="button"
                aria-label={`跳转到用户消息：${item.preview}`}
                onClick={() => onNavigate(item.key)}
                className="flex h-2.5 w-10 items-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#71717a]"
              >
                <span
                  className={cn(
                    "block h-0.5 rounded-full transition-all duration-150 group-hover:h-1 group-hover:w-10",
                    activeKey === item.key ? "w-10 bg-[#18181b]" : "w-8 bg-[#a1a1aa]"
                  )}
                />
              </button>
              <div
                role="tooltip"
                className={cn(
                  "pointer-events-none invisible absolute left-full z-30 ml-3 w-72 rounded border border-[#d4d4ce] bg-[#ffffff] p-3 text-left opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100",
                  tooltipPosition
                )}
              >
                <div className="mb-1.5 text-[11px] text-[#71717a]">
                  {formatMessageTime(item.createdAt) ?? "时间未记录"}
                </div>
                <div className="max-h-24 overflow-hidden whitespace-pre-wrap break-words text-xs leading-5 text-[#27272a]">
                  {item.preview}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </nav>
  );
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

export function buildDisplayItems(
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

export function groupDisplayItemsIntoTurns(displayItems: DisplayItem[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let currentUserItem: MessageDisplayItem | null = null;
  let currentAssistantItems: DisplayItem[] = [];

  const flushTurn = () => {
    if (!currentUserItem && currentAssistantItems.length === 0) return;

    let finalItem: MessageDisplayItem | null = null;
    let intermediateItems: DisplayItem[] = [];

    if (currentAssistantItems.length > 0) {
      const lastItem = currentAssistantItems[currentAssistantItems.length - 1];
      if (lastItem.type === "message" && lastItem.message.role === "assistant") {
        finalItem = lastItem;
        intermediateItems = currentAssistantItems.slice(0, currentAssistantItems.length - 1);
      } else {
        intermediateItems = [...currentAssistantItems];
      }
    }

    const key =
      currentUserItem?.key ??
      (finalItem?.key || intermediateItems[0]?.key || `turn-${turns.length}`);

    turns.push({
      key,
      userItem: currentUserItem,
      intermediateItems,
      finalItem,
    });

    currentUserItem = null;
    currentAssistantItems = [];
  };

  for (const item of displayItems) {
    if (item.type === "message" && item.message.role === "user") {
      flushTurn();
      currentUserItem = item;
    } else {
      currentAssistantItems.push(item);
    }
  }

  flushTurn();
  return turns;
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

function getSubagentRunIdentity(item: ToolRunDisplayItem): SubagentRunIdentity | null {
  if (item.name !== "delegation" || !item.args) return null;
  const analysisId = item.args.analysis_id;
  const agentType = item.args.agent_type;
  const sessionId = item.args.session_id;
  if (
    typeof analysisId !== "string" ||
    typeof agentType !== "string" ||
    !AGENT_TYPES.has(agentType as AgentType) ||
    typeof sessionId !== "string"
  ) {
    return null;
  }
  return {
    delegationId: item.toolCallId,
    analysisId,
    agentType: agentType as AgentType,
    sessionId,
  };
}

function getSubagentStatusLabel(status: SubagentRun["status"]): string {
  switch (status) {
    case "running":
      return "运行中";
    case "completed":
      return "已完成";
    case "needs_repair":
      return "待修补";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    case "interrupted":
      return "已中断";
  }
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
  assistantName = "DataAgent",
  message,
  onOpenPreviewAttachment,
  username,
}: {
  assistantName?: string;
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
      <div className="my-2.5 font-mono">
        <div
          className={cn(
            "rounded border p-3.5 shadow-xs",
            isUser ? "border-[#d4d4ce] bg-[#eaeae5]" : "border-[#d4d4ce] bg-[#ffffff]"
          )}
        >
          {/* 消息来源标识 */}
          <div className="mb-2 flex items-center justify-between border-b border-[#e5e5df] pb-1.5 text-xs">
            <span className="font-semibold text-[#18181b]">
              {isUser ? username : assistantName}
            </span>
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

/**
 * 普通工具调用的紧凑条目组件
 */
function GenericToolRunBar({
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
    <div className="my-1.5 font-mono text-xs">
      <div className="rounded border border-[#d4d4ce] bg-[#ffffff] shadow-xs">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
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
        <div className="mt-1.5 flex flex-wrap gap-2 px-1">
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

/**
 * Specialist 内部工具调用的折叠组件
 */
function SubagentInternalProcessCollapse({
  items,
  isStreaming,
  onOpenPreviewAttachment,
}: {
  items: DisplayItem[];
  isStreaming: boolean;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="my-2 rounded border border-[#e5e5df] bg-[#fcfcfb]">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs transition hover:bg-[#f4f4f0]"
      >
        <div className="flex items-center gap-2 text-[#52525b]">
          <div className="flex h-4 w-4 items-center justify-center rounded bg-[#ebebe6]">
            {isStreaming ? (
              <Loader2 className="h-2.5 w-2.5 animate-spin text-[#18181b]" />
            ) : (
              <Wrench className="h-2.5 w-2.5 text-[#71717a]" />
            )}
          </div>
          <span className="font-medium text-[11px]">
            Specialist 执行与工具调用 ({items.length} 步)
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-[#71717a]">
          <span>{isOpen ? "收起" : "展开详情"}</span>
          <ChevronDown
            className={cn("h-3 w-3 transition-transform duration-150", isOpen && "rotate-180")}
          />
        </div>
      </button>

      {isOpen && (
        <div className="space-y-1.5 border-t border-[#e5e5df] bg-[#fafaf8] p-2.5">
          {items.map((item) =>
            item.type === "message" ? (
              <div
                key={item.key}
                className="rounded border border-[#e5e5df] bg-[#ffffff] p-2 text-xs text-[#3f3f46]"
              >
                {item.message.parts.map((part) => (
                  <PartView key={getMessagePartKey(part)} part={part} renderMarkdown={true} />
                ))}
              </div>
            ) : (
              <GenericToolRunBar
                key={item.key}
                item={item}
                onOpenPreviewAttachment={onOpenPreviewAttachment}
              />
            )
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 专业 Agent 委派 (Delegation) 的专属展示卡片
 * 内部同样遵循：若有最终结果，则突出展示最终结果并将内部工具调用折叠
 */
function DelegationToolRunBar({
  item,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  subagentRun,
}: {
  item: ToolRunDisplayItem;
  loadSubagentMessages?: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  subagentRun?: SubagentRun;
}) {
  const identity = getSubagentRunIdentity(item);
  const [isCardOpen, setIsCardOpen] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const instruction = typeof item.args?.message === "string" ? item.args.message : null;

  if (!identity) {
    return <GenericToolRunBar item={item} onOpenPreviewAttachment={onOpenPreviewAttachment} />;
  }

  const agentConfig = AGENT_CONFIG[identity.agentType];
  const runStatus =
    subagentRun?.status ??
    (item.completed ? "completed" : item.interrupted ? "interrupted" : "running");
  const isRunning = runStatus === "running";

  const loadHistory = () => {
    if (!item.conversationId || !loadSubagentMessages) return;
    setHistoryError(null);
    void loadSubagentMessages(item.conversationId, identity).catch(() => {
      setHistoryError("加载 Specialist 工作详情失败，点击重试");
    });
  };

  // 解析 subagentRun 的消息流
  const subagentDisplayItems = subagentRun
    ? buildDisplayItems(item.conversationId ?? null, subagentRun.messages, isRunning)
    : [];

  let subagentFinalItem: MessageDisplayItem | null = null;
  let subagentIntermediateItems: DisplayItem[] = [];

  if (subagentDisplayItems.length > 0) {
    const lastItem = subagentDisplayItems[subagentDisplayItems.length - 1];
    if (lastItem.type === "message" && lastItem.message.role === "assistant") {
      subagentFinalItem = lastItem;
      subagentIntermediateItems = subagentDisplayItems.slice(0, subagentDisplayItems.length - 1);
    } else {
      subagentIntermediateItems = subagentDisplayItems;
    }
  }

  // 尝试从 item.result 解析结构化委派输出（当无 message 流或作为兜底展示）
  const parsedDelegationResult = (() => {
    if (!item.result) return null;
    try {
      const parsed = JSON.parse(item.result);
      if (typeof parsed === "object" && parsed !== null) {
        return parsed as {
          summary?: string;
          findings?: string[];
          limitations?: string[];
          status?: string;
        };
      }
    } catch {
      // 非 JSON 字符串
    }
    return null;
  })();

  return (
    <div className="my-2.5 font-mono text-xs">
      <div
        className={cn(
          "rounded-md border shadow-xs overflow-hidden",
          agentConfig.border,
          "bg-[#ffffff]"
        )}
      >
        {/* 卡片头部 */}
        <div className="flex items-center justify-between gap-2 border-b border-[#e5e5df] bg-[#fafaf8] px-3.5 py-2">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div
              className={cn(
                "flex h-6 px-2 shrink-0 items-center justify-center gap-1 rounded text-xs font-semibold",
                agentConfig.badgeBg,
                agentConfig.badgeText
              )}
            >
              <Bot className="h-3.5 w-3.5" />
              <span>{agentConfig.title}</span>
            </div>
            <span className="truncate text-[#71717a] text-[11px]" title={identity.sessionId}>
              #{identity.sessionId}
            </span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <span
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium",
                runStatus === "completed"
                  ? "bg-[#e6f4ea] text-[#137333] border border-[#ceead6]"
                  : runStatus === "running"
                    ? "bg-[#e0f2fe] text-[#0369a1] border border-[#bae6fd]"
                    : runStatus === "needs_repair"
                      ? "bg-[#fef7e0] text-[#b06000] border border-[#feefc3]"
                      : runStatus === "failed"
                        ? "bg-[#fce8e6] text-[#c5221f] border border-[#fad2cf]"
                        : "bg-[#f0f0ec] text-[#71717a]"
              )}
            >
              {isRunning ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : null}
              {runStatus === "completed" ? <Check className="h-2.5 w-2.5" /> : null}
              <span>{getSubagentStatusLabel(runStatus)}</span>
            </span>
            <button
              type="button"
              onClick={() => setIsCardOpen(!isCardOpen)}
              className="rounded p-1 text-[#71717a] hover:bg-[#ebebe6] transition"
              title={isCardOpen ? "折叠卡片" : "展开卡片"}
            >
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 transition-transform duration-150",
                  !isCardOpen && "-rotate-90"
                )}
              />
            </button>
          </div>
        </div>

        {/* 卡片主体 */}
        {isCardOpen && (
          <div className="p-3 space-y-2.5">
            {/* 委派任务目标 */}
            {instruction && (
              <div className="rounded border border-[#e5e5df] bg-[#f8f8f6] p-2.5 text-xs text-[#52525b] leading-relaxed">
                <span className="font-semibold text-[#27272a] mr-1.5">🎯 目标:</span>
                {instruction}
              </div>
            )}

            {/* Specialist 内部流式或历史内容 */}
            {subagentRun && subagentRun.messages.length > 0 ? (
              <div className="space-y-2">
                {/* 内部中间工具与过程（若已有最终消息则折叠） */}
                {subagentIntermediateItems.length > 0 && (
                  <SubagentInternalProcessCollapse
                    items={subagentIntermediateItems}
                    isStreaming={isRunning}
                    onOpenPreviewAttachment={onOpenPreviewAttachment}
                  />
                )}

                {/* 内部最终结论消息（直接展示） */}
                {subagentFinalItem ? (
                  <div className="rounded border border-[#e5e5df] bg-[#ffffff] p-3 shadow-xs">
                    <div className="mb-1.5 flex items-center justify-between border-b border-[#e5e5df] pb-1 text-[11px] text-[#71717a]">
                      <span className="font-semibold text-[#18181b]">
                        {agentConfig.title} 结论输出
                      </span>
                      {subagentFinalItem.message.createdAt ? (
                        <span>{formatMessageTime(subagentFinalItem.message.createdAt)}</span>
                      ) : null}
                    </div>
                    <div className="space-y-1.5">
                      {subagentFinalItem.message.parts.map((part) => (
                        <PartView key={getMessagePartKey(part)} part={part} renderMarkdown={true} />
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : isRunning ? (
              <div className="flex items-center gap-2 rounded border border-[#e5e5df] bg-[#ffffff] px-3 py-2 text-[#71717a]">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-[#18181b]" />
                <span>Specialist 正在执行分析与计算...</span>
              </div>
            ) : parsedDelegationResult ? (
              /* 如果尚未拉取历史消息，但有结构化返回结果，展示摘要与结论 */
              <div className="space-y-2">
                {parsedDelegationResult.summary && (
                  <div className="rounded border border-[#e5e5df] bg-[#ffffff] p-3 text-xs leading-relaxed text-[#27272a]">
                    <p className="font-semibold text-[#18181b] mb-1">分析总结</p>
                    <p className="whitespace-pre-wrap">{parsedDelegationResult.summary}</p>
                  </div>
                )}
                {parsedDelegationResult.findings && parsedDelegationResult.findings.length > 0 && (
                  <div className="rounded border border-[#e5e5df] bg-[#ffffff] p-3 text-xs text-[#27272a]">
                    <p className="font-semibold text-[#18181b] mb-1.5">核心发现</p>
                    <ul className="list-disc pl-4 space-y-1 text-[#3f3f46]">
                      {parsedDelegationResult.findings.map((finding) => (
                        <li key={finding}>{finding}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {item.conversationId && loadSubagentMessages && !subagentRun?.historyLoaded && (
                  <button
                    type="button"
                    onClick={loadHistory}
                    disabled={subagentRun?.historyLoading}
                    className="flex items-center gap-1.5 text-[11px] text-[#52525b] hover:text-[#18181b] underline underline-offset-2"
                  >
                    {subagentRun?.historyLoading ? (
                      <>
                        <Loader2 className="h-3 w-3 animate-spin" />
                        <span>正在加载完整工作记录...</span>
                      </>
                    ) : (
                      <>
                        <ChevronRight className="h-3 w-3" />
                        <span>查看 Specialist 完整执行过程</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            ) : (
              item.conversationId &&
              loadSubagentMessages && (
                <button
                  type="button"
                  onClick={loadHistory}
                  disabled={subagentRun?.historyLoading}
                  className="flex items-center gap-1.5 text-[11px] text-[#52525b] hover:text-[#18181b] underline underline-offset-2"
                >
                  {subagentRun?.historyLoading ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span>正在加载工作详情...</span>
                    </>
                  ) : (
                    <>
                      <ChevronRight className="h-3 w-3" />
                      <span>展开 Specialist 工作详情</span>
                    </>
                  )}
                </button>
              )
            )}

            {historyError && (
              <button
                type="button"
                onClick={loadHistory}
                className="text-left text-[11px] text-[#b91c1c] underline underline-offset-2"
              >
                {historyError}
              </button>
            )}

            {/* 产物附件 */}
            {(item.attachments?.length ?? 0) > 0 && (
              <div className="pt-1 flex flex-wrap gap-2">
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
        )}
      </div>
    </div>
  );
}

/**
 * 主会话中，当产生最终结果后，将所有中间消息和工具调用折叠展示的容器组件
 */
function ExecutionProcessCollapse({
  hasFinalItem,
  isStreaming,
  items,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  subagentRuns,
  username,
}: {
  hasFinalItem: boolean;
  isStreaming: boolean;
  items: DisplayItem[];
  loadSubagentMessages?: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  subagentRuns: SubagentRunMap;
  username: string;
}) {
  const [userToggledOpen, setUserToggledOpen] = useState<boolean | null>(null);

  // 默认规则：若已有最终结果则收起，否则（执行中或未出最终结果）展开
  const isOpen = userToggledOpen ?? !hasFinalItem;

  // 统计工具数量与主要委派信息作为摘要
  const toolCount = items.filter((i) => i.type === "tool_run").length;

  return (
    <div className="my-2 font-mono text-xs">
      <div className="rounded-md border border-[#d4d4ce] bg-[#ffffff] shadow-xs overflow-hidden">
        {/* 折叠栏触发器 */}
        <button
          type="button"
          onClick={() => setUserToggledOpen(!isOpen)}
          className="flex w-full items-center justify-between gap-3 px-3.5 py-2 text-left transition hover:bg-[#fafaf8]"
        >
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-[#ebebe6]">
              {isStreaming && !hasFinalItem ? (
                <Loader2 className="h-3 w-3 animate-spin text-[#18181b]" />
              ) : (
                <Sparkles className="h-3 w-3 text-[#52525b]" />
              )}
            </div>
            <span className="font-semibold text-[#18181b]">
              {isStreaming && !hasFinalItem ? "正在执行分析与工具调用..." : "思考与工具调用过程"}
            </span>
            <span className="rounded bg-[#f0f0eb] px-1.5 py-0.5 text-[10px] text-[#52525b]">
              共 {items.length} 步{toolCount > 0 ? ` · ${toolCount} 个工具` : ""}
            </span>
          </div>

          <div className="flex items-center gap-2 shrink-0 text-[#71717a]">
            <span className="text-[11px]">{isOpen ? "收起过程" : "展开过程"}</span>
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-150",
                isOpen && "rotate-180"
              )}
            />
          </div>
        </button>

        {/* 展开后的中间消息与工具列表 */}
        {isOpen && (
          <div className="space-y-2 border-t border-[#e5e5df] bg-[#fafaf8] p-3">
            {items.map((item) => {
              if (item.type === "message") {
                return (
                  <MessageBubble
                    key={item.key}
                    assistantName="DataAgent"
                    message={item.message}
                    onOpenPreviewAttachment={onOpenPreviewAttachment}
                    username={username}
                  />
                );
              }

              if (item.name === "delegation") {
                return (
                  <DelegationToolRunBar
                    key={item.key}
                    item={item}
                    loadSubagentMessages={loadSubagentMessages}
                    onOpenPreviewAttachment={onOpenPreviewAttachment}
                    subagentRun={subagentRuns[item.toolCallId]}
                  />
                );
              }

              return (
                <GenericToolRunBar
                  key={item.key}
                  item={item}
                  onOpenPreviewAttachment={onOpenPreviewAttachment}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

interface ChatMessagesProps {
  conversationId: string | null;
  conversationSelected: boolean;
  isLoading: boolean;
  isStreaming: boolean;
  messages: MessageResponse[];
  subagentRuns: SubagentRunMap;
  loadSubagentMessages: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
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
  subagentRuns,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  username,
  viewportRef,
}: ChatMessagesProps) {
  const displayItems = buildDisplayItems(conversationId, messages, isStreaming);
  const turns = groupDisplayItemsIntoTurns(displayItems);
  const messageElementsRef = useRef(new Map<string, HTMLDivElement>());
  const [activeUserMessageKey, setActiveUserMessageKey] = useState<string | null>(null);

  const userMessageNavigationItems = turns.flatMap<UserMessageNavigationItem>((turn) =>
    turn.userItem
      ? [
          {
            key: turn.userItem.key,
            createdAt: turn.userItem.message.createdAt ?? null,
            preview: getUserMessagePreview(turn.userItem.message),
          },
        ]
      : []
  );

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!conversationId || isLoading || userMessageNavigationItems.length === 0 || !viewport) {
      setActiveUserMessageKey(null);
      return;
    }

    let animationFrame: number | null = null;
    const updateActiveMessage = () => {
      animationFrame = null;
      setActiveUserMessageKey(findActiveUserMessageKey(viewport));
    };
    const scheduleUpdate = () => {
      if (animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(updateActiveMessage);
    };

    scheduleUpdate();
    viewport.addEventListener("scroll", scheduleUpdate, { passive: true });
    window.addEventListener("resize", scheduleUpdate);
    return () => {
      viewport.removeEventListener("scroll", scheduleUpdate);
      window.removeEventListener("resize", scheduleUpdate);
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    };
  }, [conversationId, isLoading, userMessageNavigationItems.length, viewportRef]);

  const navigateToUserMessage = (key: string) => {
    const element = messageElementsRef.current.get(key);
    if (!element) return;
    setActiveUserMessageKey(key);
    element.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[#f4f4f0] font-mono text-[#1e2024]">
      {conversationSelected && !isLoading && userMessageNavigationItems.length > 0 ? (
        <UserMessageQuickNavigation
          activeKey={activeUserMessageKey}
          items={userMessageNavigationItems}
          onNavigate={navigateToUserMessage}
        />
      ) : null}
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
          <div className="mx-auto w-full max-w-4xl space-y-3">
            {turns.map((turn) => {
              const isTurnStreaming = isStreaming && turn.finalItem === null;
              return (
                <div key={turn.key} className="space-y-1">
                  {turn.userItem && (
                    <div
                      ref={(element) => {
                        if (element) {
                          messageElementsRef.current.set(turn.userItem!.key, element);
                        } else {
                          messageElementsRef.current.delete(turn.userItem!.key);
                        }
                      }}
                      data-user-message-key={turn.userItem.key}
                      className="scroll-mt-4"
                    >
                      <MessageBubble
                        message={turn.userItem.message}
                        onOpenPreviewAttachment={onOpenPreviewAttachment}
                        username={username}
                      />
                    </div>
                  )}

                  {turn.intermediateItems.length > 0 && (
                    <ExecutionProcessCollapse
                      hasFinalItem={turn.finalItem !== null}
                      isStreaming={isTurnStreaming}
                      items={turn.intermediateItems}
                      loadSubagentMessages={loadSubagentMessages}
                      onOpenPreviewAttachment={onOpenPreviewAttachment}
                      subagentRuns={subagentRuns}
                      username={username}
                    />
                  )}

                  {turn.finalItem && (
                    <MessageBubble
                      assistantName="DataAgent"
                      message={turn.finalItem.message}
                      onOpenPreviewAttachment={onOpenPreviewAttachment}
                      username={username}
                    />
                  )}

                  {turn.userItem &&
                    turn.intermediateItems.length === 0 &&
                    !turn.finalItem &&
                    isTurnStreaming && (
                      <div className="my-2 flex items-center gap-2 rounded border border-[#d4d4ce] bg-[#ffffff] px-3.5 py-2.5 text-xs text-[#52525b] shadow-xs font-mono">
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-[#18181b]" />
                        <span>DataAgent 正在思考并规划...</span>
                      </div>
                    )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
