import { X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse } from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { Button } from "@/components/ui/button";

interface UserCreateCardProps {
  roles: DorisRoleResponse[];
  busy: boolean;
  onCancel: () => void;
  onUserCreated: () => void;
}

export function UserCreateCard({ roles, busy, onCancel, onUserCreated }: UserCreateCardProps) {
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState("");
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const handleCreateUser = async () => {
    if (!newUsername.trim() || !newEmail.trim() || !newPassword.trim()) return;
    setSubmitting(true);
    try {
      await adminApi.createUser({
        username: newUsername.trim(),
        email: newEmail.trim(),
        password: newPassword,
        doris_role: newUserRole || undefined,
        is_admin: newUserIsAdmin,
      });
      toast.success(`用户 ${newUsername.trim()} 创建成功`);
      setNewUsername("");
      setNewEmail("");
      setNewPassword("");
      setNewUserRole("");
      setNewUserIsAdmin(false);
      onUserCreated();
    } catch (error) {
      toast.error(getApiErrorMessage(error, "创建用户失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
      <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
        <span>添加新用户账号</span>
        <button
          type="button"
          onClick={onCancel}
          className="text-[#71717a] hover:text-[#18181b] cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-5">
        <div>
          <label
            htmlFor="user-new-username"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            用户名 *
          </label>
          <input
            id="user-new-username"
            value={newUsername}
            onChange={(event) => setNewUsername(event.target.value)}
            placeholder="3-64 位小写字母/数字"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>

        <div>
          <label htmlFor="user-new-email" className="block text-xs font-medium text-[#71717a] mb-1">
            邮箱地址 *
          </label>
          <input
            id="user-new-email"
            value={newEmail}
            onChange={(event) => setNewEmail(event.target.value)}
            type="email"
            placeholder="user@company.com"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>

        <div>
          <label
            htmlFor="user-new-password"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            初始密码 *
          </label>
          <input
            id="user-new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            type="password"
            placeholder="最少 6 位"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>

        <div>
          <label htmlFor="user-new-role" className="block text-xs font-medium text-[#71717a] mb-1">
            关联 Doris 角色
          </label>
          <select
            id="user-new-role"
            value={newUserRole}
            onChange={(event) => setNewUserRole(event.target.value)}
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
          >
            <option value="">[ 默认角色 ]</option>
            {roles
              .filter((role) => role.is_active)
              .map((role) => (
                <option key={role.name} value={role.name}>
                  {role.name} {role.is_default ? "(默认)" : ""}
                </option>
              ))}
          </select>
        </div>

        <div className="flex items-end pb-1.5">
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-[#52525b]">
            <input
              type="checkbox"
              checked={newUserIsAdmin}
              onChange={(event) => setNewUserIsAdmin(event.target.checked)}
              className="h-4 w-4 rounded accent-[#1e2024]"
            />
            <span>设为管理员</span>
          </label>
        </div>
      </div>

      <div className="mt-3 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel} className="h-7 px-2 text-xs">
          取消
        </Button>
        <Button
          size="sm"
          disabled={
            busy ||
            submitting ||
            !newUsername.trim() ||
            !newEmail.trim() ||
            !newPassword.trim() ||
            newPassword.length < 6
          }
          onClick={() => void handleCreateUser()}
          className="h-7 px-2 text-xs bg-[#1e2024] text-white hover:bg-[#2d3139]"
        >
          {submitting ? "创建中..." : "确认创建"}
        </Button>
      </div>
    </div>
  );
}
