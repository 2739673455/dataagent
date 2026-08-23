import { useState } from "react";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse } from "@/api/admin";
import { Button } from "@/components/ui/button";

function errorMessage(error: unknown): string {
  return (
    (error as { response?: { data?: { detail?: string; title?: string } } }).response?.data
      ?.detail ?? "创建用户失败"
  );
}

interface UserCreateCardProps {
  roles: DorisRoleResponse[];
  busy: boolean;
  onUserCreated: () => void;
}

export function UserCreateCard({ roles, busy, onUserCreated }: UserCreateCardProps) {
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
      toast.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-3 text-sm">
      <p className="mb-2 font-medium text-[#71717a]">创建新用户账号</p>
      <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-5">
        <input
          value={newUsername}
          onChange={(event) => setNewUsername(event.target.value)}
          placeholder="用户名 (3-64位)"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
        <input
          value={newEmail}
          onChange={(event) => setNewEmail(event.target.value)}
          type="email"
          placeholder="邮箱地址 (如 user@company.com)"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
        <input
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
          type="password"
          placeholder="初始密码 (最少6位)"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
        <select
          value={newUserRole}
          onChange={(event) => setNewUserRole(event.target.value)}
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-sm text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
        >
          <option value="">[ 默认 Doris 角色 ]</option>
          {roles
            .filter((role) => role.is_active)
            .map((role) => (
              <option key={role.name} value={role.name}>
                {role.name} {role.is_default ? "(默认)" : ""}
              </option>
            ))}
        </select>
        <div className="flex items-center gap-2 px-1">
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-[#52525b]">
            <input
              type="checkbox"
              checked={newUserIsAdmin}
              onChange={(event) => setNewUserIsAdmin(event.target.checked)}
              className="h-4 w-4 rounded accent-[#1e2024]"
            />
            <span>管理员</span>
          </label>
        </div>
      </div>
      <div className="mt-2.5 flex justify-end">
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
        >
          {submitting ? "创建中..." : "创建用户"}
        </Button>
      </div>
    </div>
  );
}
