import { KeyRound, LogOut, Settings, User } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import type { UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";
import { cn } from "@/lib/utils";

export interface ChatUserFooterProps {
  user: UserResponse | null;
  onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  onLogout: () => void;
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
