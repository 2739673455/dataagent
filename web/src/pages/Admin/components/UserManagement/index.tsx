import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse, type UserListResponse } from "@/api/admin";
import { useAuthStore, type UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import { UserCreateCard } from "./UserCreateCard";

function errorMessage(error: unknown): string {
  return (
    (error as { response?: { data?: { detail?: string; title?: string } } }).response?.data
      ?.detail ?? "操作失败，请检查用户管理配置"
  );
}

const USER_PAGE_SIZE = 50;

export function UserManagement() {
  const currentUser = useAuthStore((state) => state.user);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [userOffset, setUserOffset] = useState(0);
  const [userTotal, setUserTotal] = useState(0);
  const [userHasMore, setUserHasMore] = useState(false);
  const [busy, setBusy] = useState(false);

  const applyUserPage = useCallback((page: UserListResponse) => {
    setUsers(page.users);
    setUserTotal(page.total);
    setUserHasMore(page.has_more);
  }, []);

  const loadData = useCallback(async () => {
    setBusy(true);
    try {
      const [loadedRoles, userPage] = await Promise.all([
        adminApi.listRoles(),
        adminApi.listUsers(USER_PAGE_SIZE, userOffset),
      ]);
      setRoles(loadedRoles);
      applyUserPage(userPage);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [applyUserPage, userOffset]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const updateUser = (updated: UserResponse) => {
    setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
  };

  const handleDeleteUser = async (user: UserResponse) => {
    if (!window.confirm(`确定要删除用户 "${user.username}" 吗？此操作不可恢复。`)) return;
    setBusy(true);
    try {
      await adminApi.deleteUser(user.id);
      toast.success(`用户 ${user.username} 已删除`);
      const nextOffset = users.length === 1 ? Math.max(0, userOffset - USER_PAGE_SIZE) : userOffset;
      const page = await adminApi.listUsers(USER_PAGE_SIZE, nextOffset);
      setUserOffset(nextOffset);
      applyUserPage(page);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const reloadFirstPage = async () => {
    const page = await adminApi.listUsers(USER_PAGE_SIZE, 0);
    setUserOffset(0);
    applyUserPage(page);
  };

  return (
    <div className="space-y-6">
      {/* 用户账号与角色绑定 */}
      <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
        <h2 className="border-b border-[#e5e5df] pb-3 text-base font-bold text-[#18181b]">
          用户账号与角色绑定
        </h2>

        <UserCreateCard roles={roles} busy={busy} onUserCreated={() => void reloadFirstPage()} />

        <div className="mt-4 overflow-x-auto overflow-y-hidden rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[760px] text-left text-sm font-mono">
            <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-3.5 py-2.5">用户名</th>
                <th className="px-3.5 py-2.5">邮箱地址</th>
                <th className="px-3.5 py-2.5">关联 Doris 角色</th>
                <th className="px-3.5 py-2.5">管理员权限</th>
                <th className="px-3.5 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-b border-[#f0f0eb] hover:bg-[#fafaf8]">
                  <td className="px-3.5 py-2.5 font-semibold text-[#18181b]">
                    {user.username}
                    {user.id === currentUser?.id && (
                      <span className="ml-1.5 rounded bg-[#ebebe6] px-1.5 py-0.5 text-[10px] text-[#52525b]">
                        当前账号
                      </span>
                    )}
                  </td>
                  <td className="px-3.5 py-2.5 text-[#71717a]">{user.email}</td>
                  <td className="px-3.5 py-2.5">
                    <select
                      className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-sm text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
                      value={user.doris_role ?? ""}
                      disabled={busy}
                      onChange={(event) => {
                        const role = event.target.value;
                        void adminApi
                          .setUserRole(user.id, role)
                          .then(updateUser)
                          .catch((error) => toast.error(errorMessage(error)));
                      }}
                    >
                      {!user.doris_role && <option value="">[ 未分配 ]</option>}
                      {roles
                        .filter((role) => role.is_active)
                        .map((role) => (
                          <option key={role.name} value={role.name}>
                            {role.name} {role.is_default ? "(默认)" : ""}
                          </option>
                        ))}
                    </select>
                  </td>
                  <td className="px-3.5 py-2.5">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={user.is_admin}
                        disabled={busy || user.id === currentUser?.id}
                        onChange={(event) => {
                          void adminApi
                            .setAdministrator(user.id, event.target.checked)
                            .then(updateUser)
                            .catch((error) => toast.error(errorMessage(error)));
                        }}
                        className="h-4 w-4 rounded accent-[#1e2024]"
                      />
                      <span className="text-xs text-[#71717a]">
                        {user.is_admin ? "管理员" : "标准用户"}
                      </span>
                    </label>
                  </td>
                  <td className="px-3.5 py-2.5 text-right">
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={busy || user.id === currentUser?.id}
                      onClick={() => void handleDeleteUser(user)}
                      className="h-8 px-2.5 text-xs"
                    >
                      删除
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-[#71717a]">
          <span>
            共 {userTotal} 位用户，当前显示 {userTotal ? userOffset + 1 : 0}–
            {Math.min(userOffset + users.length, userTotal)}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={busy || userOffset === 0}
              onClick={() => setUserOffset(Math.max(0, userOffset - USER_PAGE_SIZE))}
              className="h-8 text-xs"
            >
              上一页
            </Button>
            <span className="min-w-20 text-center text-[#52525b]">
              第 {Math.floor(userOffset / USER_PAGE_SIZE) + 1} /{" "}
              {Math.max(1, Math.ceil(userTotal / USER_PAGE_SIZE))} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !userHasMore}
              onClick={() => setUserOffset(userOffset + USER_PAGE_SIZE)}
              className="h-8 text-xs"
            >
              下一页
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
