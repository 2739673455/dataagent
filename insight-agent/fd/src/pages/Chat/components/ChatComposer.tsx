import { SendHorizonal, Square } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface ChatComposerProps {
	disabled?: boolean;
	isStreaming?: boolean;
	onStop: () => void;
	onSubmit: (value: string) => Promise<void> | void;
}

export function ChatComposer({
	disabled = false,
	isStreaming = false,
	onStop,
	onSubmit,
}: ChatComposerProps) {
	const [value, setValue] = useState("");
	const textareaRef = useRef<HTMLTextAreaElement | null>(null);

	const resizeTextarea = () => {
		const textarea = textareaRef.current;
		if (!textarea) return;

		textarea.style.height = "0px";
		textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.3)}px`;
	};

	const handleSubmit = async () => {
		const next = value.trim();
		if (!next || disabled) return;
		setValue("");
		requestAnimationFrame(resizeTextarea);
		await onSubmit(next);
	};

	return (
		<div className="relative">
			<div className="overflow-hidden rounded-[2rem] border border-slate-300 bg-white shadow-[0_-36px_72px_-6px_rgba(255,255,255,1)] transition-all focus-within:border-slate-300 focus-within:shadow-[0_-36px_72px_-6px_rgba(255,255,255,1)]">
				<Textarea
					ref={textareaRef}
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
					disabled={disabled}
					className="min-h-[88px] max-h-[30vh] flex-1 resize-none overflow-y-auto rounded-none border-none bg-white px-5 pb-3 pt-5 text-[15px] text-slate-800 shadow-none placeholder:text-slate-500 focus-visible:ring-0"
				/>
				<div className="flex items-center justify-end bg-white px-3 pb-2 pt-1">
					<Button
						onClick={() => {
							if (isStreaming) {
								onStop();
								return;
							}
							void handleSubmit();
						}}
						disabled={isStreaming ? false : disabled || !value.trim()}
						variant="ghost"
						className={cn(
							"h-9 w-9 rounded-full border-none p-0 shadow-none transition-all active:scale-95",
							isStreaming
								? "bg-red-500 text-white hover:bg-red-600"
								: "bg-transparent text-slate-500 hover:bg-slate-200/80 hover:text-slate-700",
							disabled && !isStreaming ? "text-slate-400" : "",
						)}
					>
						{isStreaming ? (
							<Square className="h-3.5 w-3.5 fill-current" />
						) : (
							<SendHorizonal className="h-5 w-5" />
						)}
					</Button>
				</div>
			</div>
		</div>
	);
}
