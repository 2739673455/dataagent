import { LogOut, MessageSquare, Plus, Settings, Trash2, User } from "lucide-react";
import { Link } from "react-router-dom";
import type { UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ROUTES } from "@/config/settings";
import { cn } from "@/lib/utils";
import type { ConversationResponse } from "@/types";

interface ChatSidebarProps {
  conversations: ConversationResponse[];
  activeConversationId: string | null;
  user: UserResponse | null;
  onCreate: () => void;
  onDelete: (conversationId: string) => void;
  onLogout: () => void;
}

function formatCliTime(isoString: string): string {
  try {
    const date = new Date(isoString);
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    const h = String(date.getHours()).padStart(2, "0");
    const min = String(date.getMinutes()).padStart(2, "0");
    return `${m}/${d} ${h}:${min}`;
  } catch {
    return "";
  }
}

export function ChatSidebar({
  conversations,
  activeConversationId,
  user,
  onCreate,
  onDelete,
  onLogout,
}: ChatSidebarProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden border-r border-[#d4d4ce] bg-[#ebebe6] font-mono text-[#27272a]">
      {/* 顶部会话控制栏 */}
      <div className="p-3">
        <div className="mb-2 flex items-center justify-between px-1 text-sm text-[#71717a]">
          <span className="font-medium text-[#27272a]">会话列表</span>
          <span className="rounded bg-[#deded8] px-1.5 py-0.5 text-xs text-[#52525b]">
            {conversations.length}
          </span>
        </div>
        <Button
          variant="outline"
          className="w-full justify-start gap-2 border-[#d4d4ce] bg-[#ffffff] text-sm text-[#1e2024] hover:bg-[#deded8]"
          onClick={onCreate}
        >
          <Plus className="h-4 w-4" />
          <span>新建会话</span>
        </Button>
      </div>

      <Separator className="bg-[#d4d4ce]" />

      {/* 会话列表区域 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <div className="space-y-1">
          {conversations.map((conversation) => {
            const isActive = conversation.conversation_id === activeConversationId;
            const timeStr = formatCliTime(conversation.update_at);

            return (
              <div
                key={conversation.conversation_id}
                className={cn(
                  "group relative flex items-center justify-between rounded border px-3 py-2 transition-colors",
                  isActive
                    ? "border-[#1e3a8a] bg-[#1e3a8a] text-[#ffffff] shadow-xs"
                    : "border-transparent text-[#52525b] hover:bg-[#dfdfda] hover:text-[#18181b]"
                )}
              >
                <Link
                  to={ROUTES.chatConversation(conversation.conversation_id)}
                  className="flex min-w-0 flex-1 items-center gap-2.5"
                >
                  <MessageSquare
                    className={cn(
                      "h-4 w-4 shrink-0",
                      isActive ? "text-[#93c5fd]" : "text-[#71717a]"
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        "truncate text-sm",
                        isActive ? "font-medium text-[#ffffff]" : "font-normal"
                      )}
                    >
                      {conversation.title || "新会话"}
                    </p>
                    {timeStr && (
                      <p
                        className={cn(
                          "text-xs",
                          isActive ? "text-[#bfdbfe]" : "text-[#8e8e93]"
                        )}
                      >
                        {timeStr}
                      </p>
                    )}
                  </div>
                </Link>

                <button
                  type="button"
                  title="删除会话"
                  className={cn(
                    "ml-1 shrink-0 rounded p-1 opacity-0 transition group-hover:opacity-100",
                    isActive
                      ? "text-[#bfdbfe] hover:bg-[#1e40af] hover:text-[#ffffff]"
                      : "text-[#8e8e93] hover:bg-[#deded8] hover:text-[#dc2626]"
                  )}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onDelete(conversation.conversation_id);
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            );
          })}

          {!conversations.length && (
            <div className="p-4 text-center text-sm text-[#8e8e93]">
              <p>暂无活跃会话</p>
            </div>
          )}
        </div>
      </div>

      <Separator className="bg-[#d4d4ce]" />

      {/* 底部当前登录操作员状态 */}
      <div className="p-3 bg-[#e4e4df]">
        <div className="mb-2.5 flex items-start gap-2.5 rounded border border-[#d4d4ce] bg-[#ffffff] p-2.5 text-xs">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-[#ebebe6] text-[#27272a]">
            <User className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between">
              <p className="truncate font-semibold text-sm text-[#18181b]">
                {user?.username || "访客"}
              </p>
              {user?.is_admin && (
                <span className="rounded bg-[#27272a] px-1.5 py-0.5 text-[10px] font-bold text-[#ffffff]">
                  管理员
                </span>
              )}
            </div>
            <p className="truncate text-xs text-[#71717a]">
              {user?.doris_role ? `Doris: ${user.doris_role}` : "未分配数据角色"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {user?.is_admin && (
            <Button
              asChild
              variant="outline"
              size="sm"
              className="flex-1 border-[#d4d4ce] bg-[#ffffff] text-xs text-[#27272a] hover:bg-[#deded8]"
            >
              <Link to={ROUTES.admin} title="管理后台">
                <Settings className="h-3.5 w-3.5 mr-1" />
                管理后台
              </Link>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className={cn(
              "border-[#d4d4ce] bg-[#ffffff] text-xs text-[#71717a] hover:bg-[#deded8] hover:text-[#dc2626]",
              !user?.is_admin && "w-full"
            )}
            onClick={onLogout}
            title="退出登录"
          >
            <LogOut className="h-3 w-3 mr-1" />
            退出
          </Button>
        </div>
      </div>
    </div>
  );
}
