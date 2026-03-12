import { ChevronDown, Loader2, Wrench } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type {
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
		role: MessageSchema["role"];
		parts: Array<TextContent | ImageContent>;
	};
};

type ToolRunDisplayItem = {
	key: string;
	type: "tool_run";
	toolCallId: string;
	name: string;
	args?: Record<string, unknown>;
	result?: string;
	completed: boolean;
};

type DisplayItem = MessageDisplayItem | ToolRunDisplayItem;

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

function buildDisplayItems(messages: MessageSchema[]): DisplayItem[] {
	const items: DisplayItem[] = [];
	const toolRuns = new Map<string, ToolRunDisplayItem>();

	for (const message of messages) {
		const regularParts: Array<TextContent | ImageContent> = [];

		for (const part of message.parts) {
			if (part.type === "text" || part.type === "image_url") {
				regularParts.push(part);
				continue;
			}

			if (part.type === "tool_call") {
				const item: ToolRunDisplayItem = {
					key: `tool-run-${part.tool_call_id}`,
					type: "tool_run",
					toolCallId: part.tool_call_id,
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
				existing.completed = true;
				continue;
			}

			items.push({
				key: `tool-run-${part.tool_call_id}`,
				type: "tool_run",
				toolCallId: part.tool_call_id,
				name: part.name,
				result: part.content,
				completed: true,
			});
		}

		if (regularParts.length > 0) {
			items.push({
				key: getMessageKey(message),
				type: "message",
				message: {
					key: getMessageKey(message),
					role: message.role,
					parts: regularParts,
				},
			});
		}
	}

	return items;
}

function renderInlineMarkdown(text: string) {
	const nodes: ReactNode[] = [];
	const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
	let lastIndex = 0;

	for (const match of text.matchAll(pattern)) {
		const matched = match[0];
		const index = match.index ?? 0;
		if (index > lastIndex) {
			nodes.push(text.slice(lastIndex, index));
		}

		if (matched.startsWith("**") && matched.endsWith("**")) {
			nodes.push(
				<strong key={`${index}-bold`} className="font-semibold text-slate-900">
					{matched.slice(2, -2)}
				</strong>,
			);
		} else if (matched.startsWith("`") && matched.endsWith("`")) {
			nodes.push(
				<code
					key={`${index}-code`}
					className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.95em] text-slate-800"
				>
					{matched.slice(1, -1)}
				</code>,
			);
		} else {
			const linkMatch = matched.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
			if (linkMatch) {
				nodes.push(
					<a
						key={`${index}-link`}
						href={linkMatch[2]}
						target="_blank"
						rel="noreferrer"
						className="text-sky-700 underline underline-offset-2"
					>
						{linkMatch[1]}
					</a>,
				);
			}
		}

		lastIndex = index + matched.length;
	}

	if (lastIndex < text.length) {
		nodes.push(text.slice(lastIndex));
	}

	return nodes;
}

function createStableKey(prefix: string, value: string) {
	return `${prefix}-${value}`;
}

function MarkdownText({ text }: { text: string }) {
	const lines = text.split("\n");
	const blocks: ReactNode[] = [];
	let listItems: string[] = [];
	let orderedListItems: string[] = [];
	let codeLines: string[] = [];
	let inCodeBlock = false;

	const flushList = (key: string) => {
		if (listItems.length > 0) {
			blocks.push(
				<ul key={`ul-${key}`} className="list-disc space-y-1 pl-5">
					{listItems.map((item) => (
						<li key={createStableKey(`ul-item-${key}`, item)}>
							{renderInlineMarkdown(item)}
						</li>
					))}
				</ul>,
			);
			listItems = [];
		}
		if (orderedListItems.length > 0) {
			blocks.push(
				<ol key={`ol-${key}`} className="list-decimal space-y-1 pl-5">
					{orderedListItems.map((item) => (
						<li key={createStableKey(`ol-item-${key}`, item)}>
							{renderInlineMarkdown(item)}
						</li>
					))}
				</ol>,
			);
			orderedListItems = [];
		}
	};

	const flushCodeBlock = (key: string) => {
		if (codeLines.length > 0) {
			blocks.push(
				<pre
					key={`code-${key}`}
					className="overflow-x-auto rounded-[1rem] bg-slate-100 px-4 py-3 text-sm text-slate-700"
				>
					<code>{codeLines.join("\n")}</code>
				</pre>,
			);
			codeLines = [];
		}
	};

	lines.forEach((line, index) => {
		const trimmed = line.trim();

		if (trimmed.startsWith("```")) {
			if (inCodeBlock) {
				flushCodeBlock(String(index));
			} else {
				flushList(String(index));
			}
			inCodeBlock = !inCodeBlock;
			return;
		}

		if (inCodeBlock) {
			codeLines.push(line);
			return;
		}

		const unorderedMatch = line.match(/^\s*[-*]\s+(.*)$/);
		if (unorderedMatch) {
			flushCodeBlock(String(index));
			listItems.push(unorderedMatch[1]);
			return;
		}

		const orderedMatch = line.match(/^\s*\d+\.\s+(.*)$/);
		if (orderedMatch) {
			flushCodeBlock(String(index));
			orderedListItems.push(orderedMatch[1]);
			return;
		}

		flushList(String(index));
		flushCodeBlock(String(index));

		if (!trimmed) {
			return;
		}

		const headingMatch = line.match(/^(#{1,3})\s+(.*)$/);
		if (headingMatch) {
			const level = headingMatch[1].length;
			const className =
				level === 1
					? "text-xl font-semibold"
					: level === 2
						? "text-lg font-semibold"
						: "text-base font-semibold";
			blocks.push(
				<div
					key={createStableKey(`heading-${index}`, headingMatch[2])}
					className={cn("mt-1", className)}
				>
					{renderInlineMarkdown(headingMatch[2])}
				</div>,
			);
			return;
		}

		blocks.push(
			<p
				key={createStableKey(`p-${index}`, line)}
				className="whitespace-pre-wrap leading-relaxed"
			>
				{renderInlineMarkdown(line)}
			</p>,
		);
	});

	flushList("final");
	flushCodeBlock("final");

	return <div className="space-y-3">{blocks}</div>;
}

function PartView({
	part,
	renderMarkdown = false,
}: {
	part: TextContent | ImageContent;
	renderMarkdown?: boolean;
}) {
	if (part.type === "text") {
		return (
			<div className="text-[15px] tracking-wide opacity-95">
				{renderMarkdown ? (
					<MarkdownText text={part.text} />
				) : (
					<p className="whitespace-pre-wrap leading-relaxed">{part.text}</p>
				)}
			</div>
		);
	}

	return (
		<img
			src={part.image_url}
			alt="message asset"
			className="mt-2 max-h-80 rounded-[1rem] border border-white/80 object-cover shadow-[0_12px_30px_-10px_rgba(15,23,42,0.18)]"
		/>
	);
}

function ToolRunBar({ item }: { item: ToolRunDisplayItem }) {
	const [isOpen, setIsOpen] = useState(false);

	return (
		<div className="flex w-full justify-start">
			<div
				className={cn(
					"w-full max-w-[88%] overflow-hidden border",
					"rounded-[1.25rem]",
					item.completed
						? "border-emerald-300 bg-[linear-gradient(135deg,#ecfdf5,#d1fae5)]"
						: "border-slate-300 bg-[linear-gradient(135deg,#f8fafc,#e2e8f0)]",
				)}
			>
				<button
					type="button"
					onClick={() => setIsOpen((value) => !value)}
					className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-slate-700"
				>
					<div
						className={cn(
							"flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
							item.completed
								? "bg-emerald-600 text-white"
								: "bg-slate-700 text-white",
						)}
					>
						{item.completed ? (
							<Wrench className="h-3.5 w-3.5" />
						) : (
							<Loader2 className="h-3.5 w-3.5 animate-spin" />
						)}
					</div>
					<div className="min-w-0 flex-1">
						<p className="truncate text-sm font-medium text-slate-800">
							{item.name}
						</p>
						<p
							className={cn(
								"text-[11px] font-medium tracking-[0.08em] uppercase",
								item.completed ? "text-emerald-700" : "text-slate-500",
							)}
						>
							{item.completed ? "工具调用完成" : "工具调用中"}
						</p>
					</div>
					<ChevronDown
						className={cn(
							"h-4 w-4 shrink-0 transition-transform",
							isOpen ? "rotate-180" : "",
							item.completed ? "text-emerald-700" : "text-slate-500",
						)}
					/>
				</button>
				{isOpen ? (
					<div className="space-y-4 border-t border-white/80 bg-white px-4 pb-4 pt-3">
						{item.args !== undefined ? (
							<div className="space-y-2">
								<p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">
									参数
								</p>
								<pre className="overflow-x-auto whitespace-pre-wrap rounded-[1rem] bg-slate-50 px-4 py-3 text-xs text-slate-600">
									{JSON.stringify(item.args, null, 2)}
								</pre>
							</div>
						) : null}
						{item.result !== undefined ? (
							<div className="space-y-2">
								<p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">
									结果
								</p>
								<pre className="overflow-x-auto whitespace-pre-wrap rounded-[1rem] bg-emerald-50 px-4 py-3 text-xs text-emerald-950">
									{item.result}
								</pre>
							</div>
						) : null}
					</div>
				) : null}
			</div>
		</div>
	);
}

function MessageBubble({
	message,
}: {
	message: MessageDisplayItem["message"];
}) {
	const isUser = message.role === "user";

	return (
		<div
			className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
		>
			<div
				className={cn(
					"relative max-w-[88%] rounded-[1.75rem] px-6 py-4 transition-all duration-300",
					isUser
						? "border border-[#cfd8e3] bg-[#dde3ec] text-slate-800"
						: "border border-slate-200 bg-white text-slate-800",
				)}
			>
				<div className="space-y-3">
					{message.parts.map((part) => (
						<PartView
							key={getMessagePartKey(part)}
							part={part}
							renderMarkdown={!isUser}
						/>
					))}
				</div>
			</div>
		</div>
	);
}

interface ChatMessagesProps {
	conversationSelected: boolean;
	isLoading: boolean;
	messages: MessageSchema[];
	viewportRef: RefObject<HTMLDivElement | null>;
}

export function ChatMessages({
	conversationSelected,
	isLoading,
	messages,
	viewportRef,
}: ChatMessagesProps) {
	const displayItems = buildDisplayItems(messages);

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
						<div className="flex h-16 w-16 items-center justify-center border border-white/80 bg-white/70 shadow-[inset_0_2px_12px_rgba(15,23,42,0.08),0_12px_32px_-16px_rgba(15,23,42,0.28)]">
							<Loader2 className="h-7 w-7 animate-spin text-primary" />
						</div>
					</div>
				) : (
					<div className="mx-auto w-[60%] min-w-[320px] max-w-[960px] space-y-5">
						{displayItems.map((item) =>
							item.type === "message" ? (
								<MessageBubble key={item.key} message={item.message} />
							) : (
								<ToolRunBar key={item.key} item={item} />
							),
						)}
					</div>
				)}
			</div>
		</div>
	);
}
