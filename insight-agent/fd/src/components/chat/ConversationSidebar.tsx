import { MessageSquareMore, Plus, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { formatDateTime } from "@/lib/message";
import { cn } from "@/lib/utils";
import type { ConversationResponse } from "@/types";

interface ConversationSidebarProps {
	conversations: ConversationResponse[];
	activeConversationId: number | null;
	currentPreview: Record<number, string>;
	isLoading: boolean;
	onRefresh: () => void;
	onCreate: () => void;
}

export function ConversationSidebar({
	conversations,
	activeConversationId,
	currentPreview,
	isLoading,
	onRefresh,
	onCreate,
}: ConversationSidebarProps) {
	return (
		<Card className="flex h-[calc(100vh-3rem)] flex-col overflow-hidden">
			<CardHeader className="space-y-4">
				<div className="flex items-start justify-between gap-4">
					<div>
						<Badge variant="accent" className="mb-3">
							Insight Agent
						</Badge>
						<CardTitle>会话面板</CardTitle>
					</div>
					<Button size="icon" variant="outline" onClick={onRefresh}>
						<RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} />
					</Button>
				</div>
				<Button
					variant="accent"
					className="w-full justify-start"
					onClick={onCreate}
				>
					<Plus className="h-4 w-4" />
					新建对话
				</Button>
			</CardHeader>
			<Separator />
			<CardContent className="min-h-0 flex-1 overflow-y-auto p-3">
				<div className="space-y-2">
					{conversations.map((conversation) => {
						const isActive =
							conversation.conversation_id === activeConversationId;
						return (
							<Link
								key={conversation.conversation_id}
								to={`/chat/${conversation.conversation_id}`}
								className={cn(
									"block rounded-[1.25rem] border px-4 py-4 transition-all",
									isActive
										? "border-primary/30 bg-primary/10 shadow-sm"
										: "border-transparent bg-white/40 hover:border-border hover:bg-white/75",
								)}
							>
								<div className="mb-2 flex items-center gap-2">
									<MessageSquareMore className="h-4 w-4 text-primary" />
									<p className="line-clamp-1 text-sm font-semibold">
										{conversation.title}
									</p>
								</div>
								<p className="line-clamp-2 min-h-10 text-xs text-muted-foreground">
									{currentPreview[conversation.conversation_id] ||
										"等待新的问题"}
								</p>
								<p className="mt-3 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
									{formatDateTime(conversation.update_at)}
								</p>
							</Link>
						);
					})}
					{!conversations.length && (
						<div className="rounded-[1.25rem] border border-dashed border-border bg-white/35 px-4 py-8 text-center text-sm text-muted-foreground">
							暂无历史对话
						</div>
					)}
				</div>
			</CardContent>
		</Card>
	);
}
