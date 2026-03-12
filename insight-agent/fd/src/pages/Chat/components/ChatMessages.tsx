import { Hammer, Loader2, Wrench } from "lucide-react";
import type { RefObject } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { MessagePart, MessageSchema } from "@/types";

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

function PartView({ part }: { part: MessagePart }) {
	if (part.type === "text") {
		return (
			<p className="whitespace-pre-wrap text-[15px] leading-relaxed tracking-wide opacity-95">
				{part.text}
			</p>
		);
	}

	if (part.type === "image_url") {
		return (
			<img
				src={part.image_url}
				alt="message asset"
				className="mt-2 max-h-80 border border-white/80 object-cover shadow-[0_12px_30px_-10px_rgba(15,23,42,0.18)]"
			/>
		);
	}

	if (part.type === "tool_call") {
	return (
		<div className="rounded-[1.25rem] border border-slate-200/70 bg-slate-50/90 px-4 py-3 text-sm shadow-[inset_0_1px_0_rgba(255,255,255,0.9)]">
			<div className="mb-2 flex items-center gap-2 font-medium text-slate-800">
				<Hammer className="h-4 w-4" />
				调用工具 {part.name}
				</div>
				<pre className="overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
					{JSON.stringify(part.args, null, 2)}
				</pre>
			</div>
		);
	}

	return (
		<div className="rounded-[1.25rem] border border-amber-200/70 bg-[linear-gradient(180deg,rgba(255,251,235,0.95),rgba(254,243,199,0.9))] px-4 py-3 text-sm text-amber-950 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
			<div className="mb-2 flex items-center gap-2 font-medium">
				<Wrench className="h-4 w-4" />
				工具结果 {part.name}
			</div>
			<pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6">
				{part.content}
			</pre>
		</div>
	);
}

function MessageBubble({ message }: { message: MessageSchema }) {
	const isUser = message.role === "user";
	const isTool = message.role === "tool";

	return (
		<div
			className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
		>
			<div
				className={cn(
					"relative max-w-[85%] rounded-[1.75rem] px-6 py-4 transition-all duration-300 sm:max-w-[72%]",
					isUser
						? "border border-[#cfd8e3] bg-[#dde3ec] text-slate-800"
						: isTool
							? "border border-amber-200/80 bg-[linear-gradient(135deg,#fffaf0,#fef3c7)] text-amber-950"
							: "border border-slate-200 bg-white text-slate-800",
				)}
			>
				<div className="space-y-3">
					{message.parts.map((part) => (
						<PartView key={getMessagePartKey(part)} part={part} />
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
	return (
		<Card className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-none border-y-0 border-r-0 border-l border-white/65 bg-[#f7f7f7] shadow-none backdrop-blur-none">
			<div
				ref={viewportRef}
				className="min-h-0 flex-1 overflow-y-auto px-8 pb-0 pt-6"
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
					<div className="w-full space-y-5">
						{messages.map((message) => (
							<MessageBubble key={getMessageKey(message)} message={message} />
						))}
					</div>
				)}
			</div>
		</Card>
	);
}
