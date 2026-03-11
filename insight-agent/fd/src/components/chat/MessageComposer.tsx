import { SendHorizonal } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface MessageComposerProps {
	disabled?: boolean;
	onSubmit: (value: string) => Promise<void> | void;
}

export function MessageComposer({
	disabled = false,
	onSubmit,
}: MessageComposerProps) {
	const [value, setValue] = useState("");

	const handleSubmit = async () => {
		const next = value.trim();
		if (!next || disabled) return;
		setValue("");
		await onSubmit(next);
	};

	return (
		<div className="glass-panel rounded-[1.5rem] p-4">
			<Textarea
				placeholder="输入一个问题，Agent 会结合工具和历史上下文继续工作"
				value={value}
				onChange={(event) => setValue(event.target.value)}
				onKeyDown={(event) => {
					if (event.key === "Enter" && !event.shiftKey) {
						event.preventDefault();
						void handleSubmit();
					}
				}}
				disabled={disabled}
				className="min-h-28 resize-none border-none bg-transparent shadow-none focus-visible:ring-0"
			/>
			<div className="mt-3 flex items-center justify-between gap-3">
				<p className="text-xs text-muted-foreground">
					Enter 发送，Shift + Enter 换行
				</p>
				<Button
					onClick={() => void handleSubmit()}
					disabled={disabled || !value.trim()}
				>
					<SendHorizonal className="h-4 w-4" />
					发送
				</Button>
			</div>
		</div>
	);
}
