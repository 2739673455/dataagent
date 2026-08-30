import { Check, ChevronRight, Copy } from "lucide-react";
import type React from "react";
import { createContext, useContext, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { cn } from "@/lib/utils";
import type { MessagePart, ThinkingContent } from "@/types";

const IsInsidePreContext = createContext(false);

export function CodeBlock({ children }: { children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);

  const extractText = (node: React.ReactNode): string => {
    if (typeof node === "string") return node;
    if (typeof node === "number") return String(node);
    if (Array.isArray(node)) return node.map(extractText).join("");
    if (node && typeof node === "object" && "props" in node) {
      return extractText((node as { props: { children?: React.ReactNode } }).props.children);
    }
    return "";
  };

  const rawText = extractText(children).replace(/\n$/, "");

  const handleCopy = async () => {
    if (!rawText) return;
    try {
      await navigator.clipboard.writeText(rawText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 忽略复制失败
    }
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
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed text-[#1e2024]">{children}</pre>
    </div>
  );
}

export function MarkdownPre({ children }: { children?: React.ReactNode }) {
  return (
    <IsInsidePreContext.Provider value={true}>
      <CodeBlock>{children}</CodeBlock>
    </IsInsidePreContext.Provider>
  );
}

export function MarkdownCode({
  className,
  children,
  ...props
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  const isInsidePre = useContext(IsInsidePreContext);
  if (isInsidePre) {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  }
  return (
    <code
      className="rounded border border-[#d4d4ce] bg-[#f0f0eb] px-1.5 py-0.5 text-xs text-[#18181b]"
      {...props}
    >
      {children}
    </code>
  );
}

export function MarkdownText({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={cn(
        "font-mono text-sm leading-relaxed text-[#1e2024] [&>*:last-child]:mb-0",
        className
      )}
    >
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
          p: ({ children }) => <p className="mb-2 last:mb-0 whitespace-pre-wrap">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-2 last:mb-0 list-disc space-y-1 pl-4 text-[#3f3f46]">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2 last:mb-0 list-decimal space-y-1 pl-4 text-[#3f3f46]">
              {children}
            </ol>
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
          pre: MarkdownPre,
          code: MarkdownCode,
          table: ({ children }) => (
            <div className="my-2.5 overflow-x-auto rounded border border-[#d4d4ce] bg-[#ffffff]">
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
            <blockquote className="my-2 last:mb-0 border-l-2 border-[#52525b] bg-[#fafaf8] pl-3 py-1 italic text-[#52525b]">
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

function ThinkingView({ part }: { part: ThinkingContent }) {
  const [open, setOpen] = useState(part.status === "streaming");

  useEffect(() => {
    setOpen(part.status === "streaming");
  }, [part.status]);

  const label =
    part.status === "streaming"
      ? "思考中"
      : part.status === "interrupted"
        ? "思考已中断"
        : "思考过程";

  return (
    <div className="my-1 text-[#71717a]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1.5 py-0.5 text-xs transition-colors hover:text-[#3f3f46]"
      >
        <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-90")} />
        {part.status === "streaming" ? (
          <DotMatrixLoader className="text-[#71717a]" label="模型正在思考" />
        ) : null}
        <span>{label}</span>
      </button>
      {open ? (
        <div className="ml-[7px] mt-1 border-l border-[#d4d4ce] pl-4">
          <MarkdownText
            text={part.text}
            className="text-[13px] text-[#6f6f78] [&_*]:!text-[#6f6f78] [&_h1]:!text-sm [&_h2]:!text-[13px] [&_h3]:!text-[13px]"
          />
        </div>
      ) : null}
    </div>
  );
}

export function PartView({
  part,
  onPreview,
  renderMarkdown = false,
  isUser = false,
}: {
  part: MessagePart;
  onPreview?: (src: string, alt: string) => void;
  renderMarkdown?: boolean;
  isUser?: boolean;
}) {
  if (part.type === "text") {
    const textContent = part.text.trimEnd();
    return renderMarkdown ? (
      <MarkdownText text={textContent} />
    ) : (
      <div
        className={cn(
          "font-mono text-sm leading-relaxed",
          isUser ? "text-[#2563eb]" : "text-[#1e2024]"
        )}
      >
        <p className="whitespace-pre-wrap leading-relaxed">{textContent}</p>
      </div>
    );
  }

  if (part.type === "image_url") {
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

  if (part.type === "thinking") {
    return <ThinkingView part={part} />;
  }

  return null;
}
