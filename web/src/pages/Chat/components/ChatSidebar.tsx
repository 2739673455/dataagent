import {
  Check,
  KeyRound,
  LogOut,
  MessageSquare,
  Pencil,
  Plus,
  Settings,
  Trash2,
  User,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import type { UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ROUTES } from "@/config/settings";
import { cn } from "@/lib/utils";
import type { ConversationResponse } from "@/types";

interface ChatSidebarProps {
  conversations: ConversationResponse[];
  activeConversationId: string | null;
  onCreate: () => void;
  onDelete: (conversationId: string) => void;
  onRename: (conversationId: string, title: string) => Promise<void>;
}

interface ChatUserFooterProps {
  user: UserResponse | null;
  onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  onLogout: () => void;
}

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

export function ChatSidebar({
  conversations,
  activeConversationId,
  onCreate,
  onDelete,
  onRename,
}: ChatSidebarProps) {
  const [editingConversationId, setEditingConversationId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isRenaming, setIsRenaming] = useState(false);

  const submitRename = async (event: React.FormEvent, conversationId: string) => {
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
      await onRename(conversationId, title);
      setEditingConversationId(null);
      toast.success("会话标题已更新");
    } catch (error) {
      toast.error(getApiErrorMessage(error, "会话重命名失败"));
    } finally {
      setIsRenaming(false);
    }
  };

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
            const timeStr = formatConversationTime(conversation.update_at);

            return (
              <div
                key={conversation.conversation_id}
                className={cn(
                  "group relative flex items-center justify-between rounded border px-3 py-2 transition-colors",
                  isActive
                    ? "border-[#1e2024] bg-[#1e2024] text-[#ffffff] shadow-xs"
                    : "border-transparent text-[#52525b] hover:bg-[#dfdfda] hover:text-[#18181b]"
                )}
              >
                {editingConversationId === conversation.conversation_id ? (
                  <form
                    className="flex min-w-0 flex-1 items-center gap-1"
                    onSubmit={(event) => void submitRename(event, conversation.conversation_id)}
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
                      onClick={() => setEditingConversationId(null)}
                      className="rounded p-1 text-rose-400 hover:bg-[#2d3139] disabled:opacity-40"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </form>
                ) : (
                  <>
                    <Link
                      to={ROUTES.chatConversation(conversation.conversation_id)}
                      className="flex min-w-0 flex-1 items-center gap-2.5"
                    >
                      <MessageSquare
                        className={cn(
                          "h-4 w-4 shrink-0",
                          isActive ? "text-[#d4d4ce]" : "text-[#71717a]"
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
                              isActive ? "text-[#a1a1aa]" : "text-[#8e8e93]"
                            )}
                          >
                            {timeStr}
                          </p>
                        )}
                      </div>
                    </Link>
                    <button
                      type="button"
                      title="重命名会话"
                      className="ml-1 shrink-0 rounded p-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                      onClick={() => {
                        setEditingConversationId(conversation.conversation_id);
                        setEditingTitle(conversation.title);
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
          })}

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

export function ChatUserFooter({ user, onChangePassword, onLogout }: ChatUserFooterProps) {
  const [isPasswordOpen, setIsPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const submitPasswordChange = async (event: React.FormEvent) => {
    event.preventDefault();
    if (newPassword.length < 6) {
      toast.error("新密码至少需要 6 个字符");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error("两次输入的新密码不一致");
      return;
    }
    setIsChangingPassword(true);
    try {
      await onChangePassword(currentPassword, newPassword);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "密码修改失败"));
    } finally {
      setIsChangingPassword(false);
    }
  };

  return (
    <div className="p-3 bg-[#e4e4df] h-full flex flex-col justify-center">
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

      <div className={cn("grid gap-1.5", user?.is_admin ? "grid-cols-3" : "grid-cols-2")}>
        {user?.is_admin && (
          <Button
            asChild
            variant="outline"
            size="sm"
            className="w-full border-[#d4d4ce] bg-[#ffffff] px-1.5 text-xs text-[#27272a] hover:bg-[#deded8]"
          >
            <Link to={ROUTES.admin} title="管理后台">
              <Settings className="h-3.5 w-3.5 shrink-0" />
              <span>后台</span>
            </Link>
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="w-full border-[#d4d4ce] bg-[#ffffff] px-1.5 text-xs text-[#27272a] hover:bg-[#deded8]"
          onClick={() => setIsPasswordOpen(true)}
          title="修改密码"
        >
          <KeyRound className="h-3.5 w-3.5 shrink-0" />
          <span>密码</span>
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-full border-[#d4d4ce] bg-[#ffffff] px-1.5 text-xs text-[#71717a] hover:bg-[#deded8] hover:text-[#dc2626]"
          onClick={onLogout}
          title="退出登录"
        >
          <LogOut className="h-3.5 w-3.5 shrink-0" />
          <span>退出</span>
        </Button>
      </div>

      {isPasswordOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="change-password-title"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target && !isChangingPassword) {
              setIsPasswordOpen(false);
            }
          }}
        >
          <form
            className="w-full max-w-sm rounded border border-[#d4d4ce] bg-white p-5 shadow-xl"
            onSubmit={(event) => void submitPasswordChange(event)}
          >
            <h2 id="change-password-title" className="mb-4 text-base font-bold text-[#18181b]">
              修改密码
            </h2>
            <label className="mb-3 block text-xs text-[#52525b]">
              当前密码
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                className="mt-1 h-9 w-full rounded border border-[#d4d4ce] px-3 text-sm"
                required
              />
            </label>
            <label className="mb-3 block text-xs text-[#52525b]">
              新密码
              <input
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                className="mt-1 h-9 w-full rounded border border-[#d4d4ce] px-3 text-sm"
                required
              />
            </label>
            <label className="mb-5 block text-xs text-[#52525b]">
              确认新密码
              <input
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className="mt-1 h-9 w-full rounded border border-[#d4d4ce] px-3 text-sm"
                required
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={isChangingPassword}
                onClick={() => setIsPasswordOpen(false)}
              >
                取消
              </Button>
              <Button type="submit" disabled={isChangingPassword}>
                {isChangingPassword ? "提交中..." : "确认修改"}
              </Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
