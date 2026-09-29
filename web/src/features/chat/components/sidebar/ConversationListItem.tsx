import { Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { ROUTES } from "@/config/settings";
import { cn } from "@/lib/utils";
import type { ConversationResponse } from "@/features/chat/types";

function formatConversationTime(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "";

  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute}`;
}

export function ConversationListItem({
  conversation,
  isActive,
  onDelete,
}: {
  conversation: ConversationResponse;
  isActive: boolean;
  onDelete: (conversationId: string) => void;
}) {
  const timeStr = formatConversationTime(conversation.update_at);
  const fullTitle = conversation.title || "新会话";

  return (
    <div
      className={cn(
        "group relative flex items-center justify-between rounded border px-3 py-2 transition-all",
        isActive
          ? "border-[#3f3f46] bg-[#27272a] text-[#ffffff] shadow-xs"
          : "border-transparent text-[#52525b] hover:bg-[#dfdfda] hover:text-[#18181b]"
      )}
    >
      <Link
        to={ROUTES.chatConversation(conversation.conversation_id)}
        title={fullTitle}
        className="flex min-w-0 flex-1 flex-col"
      >
        <div className="flex min-w-0 items-center gap-1.5">
          {conversation.running && (
            <DotMatrixLoader
              label="对话正在运行"
              className={isActive ? "text-[#ffffff]" : "text-[#52525b]"}
            />
          )}
          <p
            className={cn(
              "min-w-0 flex-1 truncate text-sm",
              isActive ? "font-medium text-[#ffffff]" : "font-normal text-[#27272a]"
            )}
          >
            {fullTitle}
          </p>
        </div>
        {timeStr && (
          <p
            className={cn("text-[11px] font-mono", isActive ? "text-[#a1a1aa]" : "text-[#71717a]")}
          >
            {timeStr}
          </p>
        )}
      </Link>
      <button
        type="button"
        title="删除会话"
        className={cn(
          "ml-1 shrink-0 rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 text-rose-500",
          isActive
            ? "hover:bg-rose-950/50 hover:text-rose-400"
            : "hover:bg-rose-100 hover:text-rose-600"
        )}
        onClick={() => onDelete(conversation.conversation_id)}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
