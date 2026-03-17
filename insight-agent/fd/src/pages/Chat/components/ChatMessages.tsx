import {
	ChevronDown,
	Download,
	Eye,
	FileImage,
	FileText,
	Loader2,
	Wrench,
} from "lucide-react";
import type { RefObject } from "react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi } from "@/api/chat";
import { cn } from "@/lib/utils";
import type {
	Attachment,
	ImageContent,
	MessagePart,
	MessageSchema,
	TextContent,
} from "@/types";

type MessageDisplayItem = {
	key: string;
	type: "message";
	message: {
		key: string;
		conversationId?: number | null;
		role: MessageSchema["role"];
		attachments?: Attachment[] | null;
		parts: Array<TextContent | ImageContent>;
	};
};

type ToolRunDisplayItem = {
	key: string;
	type: "tool_run";
	toolCallId: string;
	conversationId?: number | null;
	name: string;
	args?: Record<string, unknown>;
	result?: string;
	attachments?: Attachment[] | null;
	completed: boolean;
};

type DisplayItem = MessageDisplayItem | ToolRunDisplayItem;
const TOOL_ARGS_PREVIEW_MAX_LENGTH = 96;

function ImagePreview({
	alt,
	onClose,
	src,
}: {
	alt: string;
	onClose: () => void;
	src: string;
}) {
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
		document.body,
	);
}

function getMessageKey(message: MessageSchema) {
	if (message.message_id != null) {
		return `message-${message.message_id}`;
	}

	return `message-${message.timestamp ?? "draft"}-${message.role}-${JSON.stringify(message.parts)}`;
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

function isImageAttachment(name: string) {
	return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

function isHtmlAttachment(name: string) {
	return /\.(html?)$/i.test(name);
}

function buildDisplayItems(
	conversationId: number | null,
	messages: MessageSchema[],
): DisplayItem[] {
	const items: DisplayItem[] = [];
	const toolRuns = new Map<string, ToolRunDisplayItem>();

	for (const message of messages) {
		const regularParts: Array<TextContent | ImageContent> = [];
		const toolParts: Array<
			Extract<MessagePart, { type: "tool_call" | "tool_result" }>
		> = [];

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

	const preview = entries
		.map(([key, value]) => `${key}=${formatToolArgValue(value)}`)
		.join(", ");

	if (preview.length <= TOOL_ARGS_PREVIEW_MAX_LENGTH) {
		return preview;
	}

	return `${preview.slice(0, TOOL_ARGS_PREVIEW_MAX_LENGTH).trimEnd()}...`;
}

function MarkdownText({ text }: { text: string }) {
	return (
		<div className="text-[15px] tracking-wide opacity-95">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				components={{
					h1: ({ children }) => (
						<h1 className="mt-1 text-xl font-semibold text-slate-900">
							{children}
						</h1>
					),
					h2: ({ children }) => (
						<h2 className="mt-1 text-lg font-semibold text-slate-900">
							{children}
						</h2>
					),
					h3: ({ children }) => (
						<h3 className="mt-1 text-base font-semibold text-slate-900">
							{children}
						</h3>
					),
					p: ({ children }) => (
						<p className="whitespace-pre-wrap leading-relaxed">{children}</p>
					),
					ul: ({ children }) => (
						<ul className="list-disc space-y-1 pl-5">{children}</ul>
					),
					ol: ({ children }) => (
						<ol className="list-decimal space-y-1 pl-5">{children}</ol>
					),
					li: ({ children }) => <li>{children}</li>,
					a: ({ href, children }) => (
						<a
							href={href}
							target="_blank"
							rel="noreferrer"
							className="text-sky-700 underline underline-offset-2"
						>
							{children}
						</a>
					),
					code: ({ className, children, ...props }) => {
						const isBlock = Boolean(className);
						if (isBlock) {
							return (
								<code className={className} {...props}>
									{children}
								</code>
							);
						}

						return (
							<code
								className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.95em] text-slate-800"
								{...props}
							>
								{children}
							</code>
						);
					},
					pre: ({ children }) => (
						<pre className="overflow-x-auto rounded-[1rem] bg-slate-100 px-4 py-3 text-sm text-slate-700">
							{children}
						</pre>
					),
					table: ({ children }) => (
						<div className="overflow-x-auto rounded-[1rem] border border-slate-200 bg-white">
							<table className="min-w-full border-collapse text-left text-sm text-slate-700">
								{children}
							</table>
						</div>
					),
					thead: ({ children }) => (
						<thead className="bg-slate-50">{children}</thead>
					),
					th: ({ children }) => (
						<th className="border-b border-slate-200 px-4 py-2.5 font-semibold text-slate-900">
							{children}
						</th>
					),
					tbody: ({ children }) => <tbody>{children}</tbody>,
					tr: ({ children }) => (
						<tr className="border-t border-slate-200 align-top">{children}</tr>
					),
					td: ({ children }) => <td className="px-4 py-2.5">{children}</td>,
					blockquote: ({ children }) => (
						<blockquote className="border-l-4 border-slate-300 pl-4 italic text-slate-600">
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
			<div className="text-[15px] tracking-wide opacity-95">
				<p className="whitespace-pre-wrap leading-relaxed">{part.text}</p>
			</div>
		);
	}

	return (
		<button
			type="button"
			onClick={() => onPreview?.(part.image_url, "message asset")}
			className="mt-2 overflow-hidden rounded-[1rem]"
		>
			<img
				src={part.image_url}
				alt="message asset"
				className="max-h-80 rounded-[1rem] border border-white/80 object-cover shadow-[0_12px_30px_-10px_rgba(15,23,42,0.18)]"
			/>
		</button>
	);
}

function ToolRunBar({
	item,
	onOpenHtmlAttachment,
}: {
	item: ToolRunDisplayItem;
	onOpenHtmlAttachment?: (attachment: Attachment) => void;
}) {
	const [isOpen, setIsOpen] = useState(false);
	const argsPreview = getToolArgsPreview(item.args);
	const hasAttachments = item.completed && (item.attachments?.length ?? 0) > 0;

	return (
		<div className={cn(hasAttachments ? "space-y-1.5" : "space-y-0")}>
			<div className="flex w-full justify-start">
				<div
					className={cn(
						"w-full max-w-[88%] overflow-hidden rounded-[1.25rem]",
						item.completed ? "bg-green-200" : "bg-slate-200",
					)}
				>
					<button
						type="button"
						onClick={() => setIsOpen((value) => !value)}
						className={cn(
							"flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm",
							"text-slate-700",
						)}
					>
						<div
							className={cn(
								"relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
								item.completed
									? "bg-green-300 text-green-800"
									: "bg-transparent text-white",
							)}
						>
							{item.completed ? (
								<Wrench className="h-3.5 w-3.5" />
							) : (
								<span className="h-5 w-5 rounded-full bg-indigo-300 animate-tool-dot-breathe" />
							)}
						</div>
						<div className="min-w-0 flex-1">
							<p
								className={cn("truncate text-sm font-medium", "text-slate-800")}
							>
								{item.name}
								{argsPreview ? (
									<span className={cn("ml-2 font-normal", "text-slate-500")}>
										{argsPreview}
									</span>
								) : null}
							</p>
						</div>
						<ChevronDown
							className={cn(
								"h-4 w-4 shrink-0 transition-transform",
								isOpen ? "rotate-180" : "",
								"text-slate-500",
							)}
						/>
					</button>
					{isOpen ? (
						<div
							className={cn(
								"space-y-3 px-3.5 pb-3.5 pt-2.5",
								item.completed ? "bg-green-200" : "bg-slate-200",
							)}
						>
							{item.args !== undefined ? (
								<div className="space-y-2">
									<p
										className={cn(
											"text-xs font-medium uppercase tracking-[0.18em]",
											"text-slate-600",
										)}
									>
										参数
									</p>
									<pre
										className={cn(
											"overflow-x-auto whitespace-pre-wrap rounded-[1rem] px-3 py-2.5 text-xs",
											item.completed
												? "bg-green-100 text-green-950"
												: "bg-white/80 text-slate-700",
										)}
									>
										{JSON.stringify(item.args, null, 2)}
									</pre>
								</div>
							) : null}
							{item.result !== undefined ? (
								<div className="space-y-2">
									<p
										className={cn(
											"text-xs font-medium uppercase tracking-[0.18em]",
											"text-slate-600",
										)}
									>
										结果
									</p>
									<pre
										className={cn(
											"overflow-x-auto whitespace-pre-wrap rounded-[1rem] px-3 py-2.5 text-xs",
											item.completed
												? "bg-green-100 text-green-950"
												: "bg-white/80 text-slate-700",
										)}
									>
										{item.result}
									</pre>
								</div>
							) : null}
						</div>
					) : null}
				</div>
			</div>
			{hasAttachments ? (
				<div className="flex w-full justify-start">
					<div className="max-w-[88%] rounded-[1.5rem] bg-transparent px-4 py-0.5 text-slate-800">
						<div className="flex flex-wrap gap-2">
							{(item.attachments ?? []).map((attachment) => (
								<AttachmentChip
									key={attachment.path}
									attachment={attachment}
									conversationId={item.conversationId}
									isUser={false}
									onOpenHtmlAttachment={onOpenHtmlAttachment}
								/>
							))}
						</div>
					</div>
				</div>
			) : null}
		</div>
	);
}

function AttachmentPreview({
	attachment,
	conversationId,
	onPreview,
}: {
	attachment: Attachment;
	conversationId?: number | null;
	onPreview?: (src: string, alt: string) => void;
}) {
	const [imageUrl, setImageUrl] = useState<string | null>(null);

	useEffect(() => {
		if (!conversationId || !isImageAttachment(attachment.path)) {
			return;
		}

		let objectUrl: string | null = null;
		let cancelled = false;

		void chatApi
			.fetchAttachmentFile(conversationId, attachment.path)
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
			if (objectUrl) {
				URL.revokeObjectURL(objectUrl);
			}
		};
	}, [attachment.path, conversationId]);

	return (
		<div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-md bg-slate-200">
			{imageUrl ? (
				<button
					type="button"
					onClick={() => onPreview?.(imageUrl, attachment.raw_name)}
					className="h-full w-full"
				>
					<img
						src={imageUrl}
						alt={attachment.raw_name}
						className="h-full w-full object-cover"
					/>
				</button>
			) : isImageAttachment(attachment.path) ? (
				<FileImage className="h-4 w-4 text-slate-500" />
			) : (
				<FileText className="h-4 w-4 text-slate-600" />
			)}
		</div>
	);
}

function AttachmentChip({
	attachment,
	conversationId,
	isUser,
	onPreview,
	onOpenHtmlAttachment,
}: {
	attachment: Attachment;
	conversationId?: number | null;
	isUser: boolean;
	onPreview?: (src: string, alt: string) => void;
	onOpenHtmlAttachment?: (attachment: Attachment) => void;
}) {
	const [isDownloading, setIsDownloading] = useState(false);
	const isHtml = isHtmlAttachment(attachment.path);

	const handleDownload = async () => {
		if (!conversationId || isDownloading) return;

		try {
			setIsDownloading(true);
			const response = await chatApi.fetchAttachmentFile(
				conversationId,
				attachment.path,
			);
			const objectUrl = URL.createObjectURL(response.data);
			const link = document.createElement("a");
			link.href = objectUrl;
			link.download = attachment.raw_name;
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
				"flex items-center gap-2 rounded-[1rem] px-2.5 py-2 text-xs",
				isUser ? "bg-white/60 text-slate-700" : "bg-slate-100 text-slate-600",
			)}
		>
			<AttachmentPreview
				attachment={attachment}
				conversationId={conversationId}
				onPreview={onPreview}
			/>
			<span className="truncate" title={attachment.raw_name}>
				{attachment.raw_name}
			</span>
			{isHtml ? (
				<button
					type="button"
					onClick={() => onOpenHtmlAttachment?.(attachment)}
					className={cn(
						"flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition",
						isUser ? "hover:bg-slate-800/10" : "hover:bg-slate-800/5",
					)}
					title="预览 HTML"
				>
					<Eye className="h-3.5 w-3.5" />
				</button>
			) : null}
			{conversationId ? (
				<button
					type="button"
					onClick={handleDownload}
					disabled={isDownloading}
					className={cn(
						"flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition",
						isUser ? "hover:bg-slate-800/10" : "hover:bg-slate-800/5",
						isDownloading ? "cursor-wait opacity-60" : "",
					)}
					title="下载附件"
				>
					{isDownloading ? (
						<Loader2 className="h-3.5 w-3.5 animate-spin text-slate-700" />
					) : (
						<Download className="h-3.5 w-3.5" />
					)}
				</button>
			) : null}
		</div>
	);
}

function MessageBubble({
	message,
	onOpenHtmlAttachment,
}: {
	message: MessageDisplayItem["message"];
	onOpenHtmlAttachment?: (attachment: Attachment) => void;
}) {
	const isUser = message.role === "user";
	const [previewImage, setPreviewImage] = useState<{
		src: string;
		alt: string;
	} | null>(null);

	return (
		<>
			<div
				className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
			>
				<div
					className={cn(
						"relative max-w-[88%] transition-all duration-300",
						isUser
							? "rounded-[1.75rem] bg-[#dde3ec] px-4 py-3 text-slate-800"
							: "rounded-[1.75rem] bg-transparent px-4 py-3 text-slate-800",
					)}
				>
					<div className="space-y-3">
						{message.attachments?.length ? (
							<div className="flex flex-wrap gap-2">
								{message.attachments.map((attachment) => (
									<AttachmentChip
										key={attachment.path}
										attachment={attachment}
										conversationId={message.conversationId}
										isUser={isUser}
										onPreview={(src, alt) => setPreviewImage({ src, alt })}
										onOpenHtmlAttachment={onOpenHtmlAttachment}
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
			{previewImage ? (
				<ImagePreview
					src={previewImage.src}
					alt={previewImage.alt}
					onClose={() => setPreviewImage(null)}
				/>
			) : null}
		</>
	);
}

interface ChatMessagesProps {
	conversationId: number | null;
	conversationSelected: boolean;
	isLoading: boolean;
	messages: MessageSchema[];
	onOpenHtmlAttachment?: (attachment: Attachment) => void;
	viewportRef: RefObject<HTMLDivElement | null>;
}

export function ChatMessages({
	conversationId,
	conversationSelected,
	isLoading,
	messages,
	onOpenHtmlAttachment,
	viewportRef,
}: ChatMessagesProps) {
	const displayItems = buildDisplayItems(conversationId, messages);

	return (
		<div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-none border-0 bg-[#fefdfa] shadow-none">
			<div
				ref={viewportRef}
				className="min-h-0 flex-1 overflow-y-auto bg-[#fefdfa] pb-10 pt-6"
			>
				{!conversationSelected ? (
					<div className="flex h-full items-center justify-center">
						<p className="text-base font-medium tracking-[0.18em] text-slate-400">
							创建新对话
						</p>
					</div>
				) : isLoading ? (
					<div className="flex h-full items-center justify-center">
						<div className="flex h-16 w-16 items-center justify-center">
							<Loader2 className="h-7 w-7 animate-spin text-slate-700" />
						</div>
					</div>
				) : (
					<div className="mx-auto w-[60%] min-w-[320px] max-w-[960px] space-y-2">
						{displayItems.map((item) =>
							item.type === "message" ? (
								<MessageBubble
									key={item.key}
									message={item.message}
									onOpenHtmlAttachment={onOpenHtmlAttachment}
								/>
							) : (
								<ToolRunBar
									key={item.key}
									item={item}
									onOpenHtmlAttachment={onOpenHtmlAttachment}
								/>
							),
						)}
					</div>
				)}
			</div>
		</div>
	);
}
