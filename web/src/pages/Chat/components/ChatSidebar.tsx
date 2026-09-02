import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { ConversationResponse } from "@/types";
import { ChatUserFooter } from "./sidebar/ChatUserFooter";
import { ConversationListItem } from "./sidebar/ConversationListItem";

export { ChatUserFooter };

export interface ChatSidebarProps {
  conversations: ConversationResponse[];
  activeConversationId: string | null;
  onCreate: () => void;
  onDelete: (conversationId: string) => void;
  onRename: (conversationId: string, title: string) => Promise<void>;
}

export function ChatSidebar({
  conversations,
  activeConversationId,
  onCreate,
  onDelete,
  onRename,
}: ChatSidebarProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden border-r border-[#d4d4ce] bg-[#ebebe6] font-mono text-[#27272a]">
      {/* 顶部会话控制栏 */}
      <div className="p-3">
        <div className="mb-2 flex items-center justify-between px-1 text-sm text-[#71717a]">
          <span className="font-medium text-[#27272a]">会话列表</span>
          <span className="rounded bg-[#deded8] px-1.5 py-0.5 text-xs font-mono text-[#52525b]">
            {conversations.length}
          </span>
        </div>
        <Button
          variant="outline"
          className="w-full justify-start gap-2 rounded border-[#d4d4ce] bg-[#ffffff] text-sm font-medium text-[#1e2024] shadow-2xs transition-all hover:border-[#b8b8b0] hover:bg-[#f5f5f0] hover:shadow-xs active:scale-[0.99]"
          onClick={onCreate}
        >
          <Plus className="h-4 w-4 text-[#52525b]" />
          <span>新建会话</span>
        </Button>
      </div>

      <Separator className="bg-[#deded8]" />

      {/* 会话列表区域 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <div className="space-y-1">
          {conversations.map((conversation) => (
            <ConversationListItem
              key={conversation.conversation_id}
              conversation={conversation}
              isActive={conversation.conversation_id === activeConversationId}
              onDelete={onDelete}
              onRename={onRename}
            />
          ))}

          {!conversations.length && (
            <div className="p-4 text-center text-sm text-[#8e8e93]">
              <p>暂无活跃会话</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
