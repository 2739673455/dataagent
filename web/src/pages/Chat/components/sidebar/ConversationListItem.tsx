import { Check, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { ROUTES } from "@/config/settings";
import { cn } from "@/lib/utils";
import type { ConversationResponse } from "@/types";

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
  onRename,
}: {
  conversation: ConversationResponse;
  isActive: boolean;
  onDelete: (conversationId: string) => void;
  onRename: (conversationId: string, title: string) => Promise<void>;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editingTitle, setEditingTitle] = useState(conversation.title);
  const [isRenaming, setIsRenaming] = useState(false);

  const timeStr = formatConversationTime(conversation.update_at);
  const fullTitle = conversation.title || "新会话";

  const submitRename = async (event: React.FormEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const title = editingTitle.trim();
    if (!title) {
      toast.error("会话标题不能为空");
      return;
    }
    if (title.length > 64) {
      toast.error("会话标题不能超过 64 个字符");
      return;
    }
    setIsRenaming(true);
    try {
      await onRename(conversation.conversation_id, title);
      setIsEditing(false);
      toast.success("会话标题已更新");
    } catch (error) {
      toast.error(getApiErrorMessage(error, "会话重命名失败"));
    } finally {
      setIsRenaming(false);
    }
  };

  return (
    <div
      className={cn(
        "group relative flex items-center justify-between rounded border px-3 py-2 transition-all",
        isActive
          ? "border-[#3f3f46] bg-[#27272a] text-[#ffffff] shadow-xs"
          : "border-transparent text-[#52525b] hover:bg-[#dfdfda] hover:text-[#18181b]"
      )}
    >
      {isEditing ? (
        <form
          className="flex min-w-0 flex-1 items-center gap-1"
          onSubmit={(event) => void submitRename(event)}
        >
          <input
            maxLength={64}
            value={editingTitle}
            disabled={isRenaming}
            onChange={(event) => setEditingTitle(event.target.value)}
            className={cn(
              "h-7 min-w-0 flex-1 rounded border px-2 text-xs outline-none",
              isActive
                ? "border-[#52525b] bg-[#3f3f46] text-white"
                : "border-[#d4d4ce] bg-white text-[#18181b]"
            )}
          />
          <button
            type="submit"
            disabled={isRenaming || !editingTitle.trim()}
            className="rounded p-1 text-emerald-400 hover:bg-[#3f3f46] disabled:opacity-40"
          >
            <Check className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            disabled={isRenaming}
            onClick={() => {
              setIsEditing(false);
              setEditingTitle(conversation.title);
            }}
            className="rounded p-1 text-rose-400 hover:bg-[#3f3f46] disabled:opacity-40"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </form>
      ) : (
        <>
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
                className={cn(
                  "text-[11px] font-mono",
                  isActive ? "text-[#a1a1aa]" : "text-[#71717a]"
                )}
              >
                {timeStr}
              </p>
            )}
          </Link>
          <button
            type="button"
            title="重命名会话"
            className={cn(
              "ml-1 shrink-0 rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100",
              isActive
                ? "text-[#a1a1aa] hover:bg-[#3f3f46] hover:text-white"
                : "text-[#71717a] hover:bg-[#d4d4ce] hover:text-[#18181b]"
            )}
            onClick={() => {
              setEditingTitle(conversation.title);
              setIsEditing(true);
            }}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
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
        </>
      )}
    </div>
  );
}
