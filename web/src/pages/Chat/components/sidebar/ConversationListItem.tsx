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
        "group relative flex items-center justify-between rounded border px-3 py-2 transition-colors",
        isActive
          ? "border-[#1e2024] bg-[#1e2024] text-[#ffffff] shadow-xs"
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
                ? "border-[#52525b] bg-[#2d3139] text-white"
                : "border-[#d4d4ce] bg-white text-[#18181b]"
            )}
          />
          <button
            type="submit"
            disabled={isRenaming || !editingTitle.trim()}
            className="rounded p-1 text-emerald-400 hover:bg-[#2d3139] disabled:opacity-40"
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
            className="rounded p-1 text-rose-400 hover:bg-[#2d3139] disabled:opacity-40"
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
                  isActive ? "font-medium text-[#ffffff]" : "font-normal"
                )}
              >
                {fullTitle}
              </p>
            </div>
            {timeStr && (
              <p className={cn("text-xs", isActive ? "text-[#a1a1aa]" : "text-[#8e8e93]")}>
                {timeStr}
              </p>
            )}
          </Link>
          <button
            type="button"
            title="重命名会话"
            className="ml-1 shrink-0 rounded p-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
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
            className="ml-1 shrink-0 p-1 text-[#dc2626] opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            onClick={() => onDelete(conversation.conversation_id)}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </>
      )}
    </div>
  );
}
