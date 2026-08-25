import { Edit2, Plus, RefreshCw, Search, Trash2, Users, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse, type UserListResponse } from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { useAuthStore, type UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "../AdminEditorDialog";
import { UserCreateDialog } from "./UserCreateDialog";

const USER_PAGE_SIZE = 50;

export function UserManagement() {
  const currentUser = useAuthStore((state) => state.user);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [userOffset, setUserOffset] = useState(0);
  const [userTotal, setUserTotal] = useState(0);
  const [userHasMore, setUserHasMore] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [isCreatingUser, setIsCreatingUser] = useState(false);
  const [editingUser, setEditingUser] = useState<UserResponse | null>(null);
  const [editUsername, setEditUsername] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editRole, setEditRole] = useState("");
  const [editIsAdmin, setEditIsAdmin] = useState(false);
  const [editPassword, setEditPassword] = useState("");
  const [editConfirmPassword, setEditConfirmPassword] = useState("");
  const [savingUser, setSavingUser] = useState(false);

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
        adminApi.listUsers(USER_PAGE_SIZE, userOffset, searchQuery),
      ]);
      setRoles(loadedRoles);
      applyUserPage(userPage);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "加载用户管理数据失败"));
    } finally {
      setBusy(false);
    }
  }, [applyUserPage, searchQuery, userOffset]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const updateUser = (updated: UserResponse) => {
    setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
  };

  const handleOpenEdit = (user: UserResponse) => {
    setEditingUser(user);
    setEditUsername(user.username);
    setEditEmail(user.email);
    setEditRole(user.doris_role ?? "");
    setEditIsAdmin(user.is_admin);
    setEditPassword("");
    setEditConfirmPassword("");
  };

  const handleSaveUser = async () => {
    if (!editingUser) return;
    const trimmedUsername = editUsername.trim();
    const trimmedEmail = editEmail.trim();
    if (!trimmedUsername) {
      toast.error("用户名不能为空");
      return;
    }
    if (!trimmedEmail) {
      toast.error("邮箱地址不能为空");
      return;
    }
    if (editPassword) {
      if (editPassword.length < 6) {
        toast.error("新密码长度不能少于 6 位");
        return;
      }
      if (editPassword !== editConfirmPassword) {
        toast.error("两次输入的密码不一致");
        return;
      }
    }

    setSavingUser(true);
    try {
      const updated = await adminApi.updateUser(editingUser.id, {
        username: trimmedUsername !== editingUser.username ? trimmedUsername : undefined,
        email: trimmedEmail !== editingUser.email ? trimmedEmail : undefined,
        password: editPassword ? editPassword : undefined,
        doris_role: editRole !== (editingUser.doris_role ?? "") ? editRole || null : undefined,
        is_admin: editIsAdmin !== editingUser.is_admin ? editIsAdmin : undefined,
      });
      updateUser(updated);
      toast.success(`用户 "${updated.username}" 信息已更新`);
      setEditingUser(null);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "更新用户信息失败"));
    } finally {
      setSavingUser(false);
    }
  };

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    setUserOffset(0);
  };

  const handleDeleteUser = async (user: UserResponse) => {
    if (!window.confirm(`确定要删除用户 "${user.username}" 吗？此操作不可恢复。`)) return;
    setBusy(true);
    try {
      await adminApi.deleteUser(user.id);
      toast.success(`用户 ${user.username} 已删除`);
      const nextOffset = users.length === 1 ? Math.max(0, userOffset - USER_PAGE_SIZE) : userOffset;
      const page = await adminApi.listUsers(USER_PAGE_SIZE, nextOffset, searchQuery);
      setUserOffset(nextOffset);
      applyUserPage(page);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "删除用户失败"));
    } finally {
      setBusy(false);
    }
  };

  const reloadFirstPage = async () => {
    const page = await adminApi.listUsers(USER_PAGE_SIZE, 0, searchQuery);
    setUserOffset(0);
    applyUserPage(page);
  };

  return (
    <div className="space-y-6">
      {/* 用户账号与角色绑定 */}
      <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3 shrink-0">
          <div className="flex items-center gap-2">
            <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
              <Users className="h-4 w-4 text-[#52525b]" />
              <span>用户账号与角色绑定 ({userTotal})</span>
              {busy && <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative w-56 sm:w-64">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#a1a1aa] pointer-events-none" />
              <input
                id="user-search-query"
                type="text"
                value={searchQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
                placeholder="搜索用户名或邮箱"
                className="h-7 w-full rounded border border-[#d4d4ce] bg-[#ffffff] pl-8 pr-7 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => handleSearchChange("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[#a1a1aa] hover:text-[#1e2024]"
                  title="清空搜索"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <Button
              size="sm"
              onClick={() => setIsCreatingUser((prev) => !prev)}
              className="h-7 px-2 text-xs"
              title="添加新用户账号"
            >
              <Plus className="h-3 w-3 mr-1" />
              添加用户
            </Button>
          </div>
        </div>

        {isCreatingUser && (
          <UserCreateDialog
            roles={roles}
            busy={busy}
            onCancel={() => setIsCreatingUser(false)}
            onUserCreated={() => {
              setIsCreatingUser(false);
              void reloadFirstPage();
            }}
          />
        )}

        {users.length === 0 ? (
          <div className="mt-4 rounded border border-[#d4d4ce] bg-[#ffffff] py-12 text-center text-sm text-[#71717a]">
            {searchQuery.trim() ? `未找到与 "${searchQuery.trim()}" 匹配的用户` : "暂无用户账号"}
          </div>
        ) : (
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
                      {user.doris_role ? (
                        <span className="rounded bg-[#f4f4f0] px-2 py-0.5 text-xs text-[#27272a] font-mono">
                          {user.doris_role}
                        </span>
                      ) : (
                        <span className="text-xs text-[#a1a1aa]">[ 未分配 ]</span>
                      )}
                    </td>
                    <td className="px-3.5 py-2.5">
                      <span
                        className={`inline-block rounded px-2 py-0.5 text-xs ${
                          user.is_admin ? "bg-[#1e2024] text-white" : "bg-[#f4f4f0] text-[#52525b]"
                        }`}
                      >
                        {user.is_admin ? "管理员" : "普通用户"}
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5 text-right whitespace-nowrap">
                      <div className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() => handleOpenEdit(user)}
                          className="h-7 px-2 text-xs"
                          title={`编辑用户 ${user.username}`}
                        >
                          <Edit2 className="h-3 w-3" />
                          <span className="sr-only">编辑用户 {user.username}</span>
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={busy || user.id === currentUser?.id}
                          onClick={() => void handleDeleteUser(user)}
                          className="h-7 px-2 text-xs"
                          title={`删除用户 ${user.username}`}
                        >
                          <Trash2 className="h-3 w-3" />
                          <span className="sr-only">删除用户 {user.username}</span>
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

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
              className="h-7 text-xs"
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
              className="h-7 text-xs"
            >
              下一页
            </Button>
          </div>
        </div>
      </section>

      {/* 编辑用户信息弹窗 */}
      {editingUser && (
        <AdminEditorDialog
          ariaLabel={`编辑用户 ${editingUser.username}`}
          onClose={() => setEditingUser(null)}
          title={
            <>
              编辑用户: <span className="font-mono text-[#52525b]">{editingUser.username}</span>
            </>
          }
        >
          <div className="space-y-3">
            <div>
              <label
                htmlFor="admin-edit-username"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                用户名
              </label>
              <input
                id="admin-edit-username"
                type="text"
                value={editUsername}
                onChange={(e) => setEditUsername(e.target.value)}
                placeholder="请输入用户名"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div>
              <label
                htmlFor="admin-edit-email"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                邮箱地址
              </label>
              <input
                id="admin-edit-email"
                type="email"
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
                placeholder="请输入邮箱地址"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div>
              <label
                htmlFor="admin-edit-role"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                关联 Doris 角色
              </label>
              <select
                id="admin-edit-role"
                value={editRole}
                onChange={(e) => setEditRole(e.target.value)}
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
              >
                <option value="">[ 未分配 ]</option>
                {roles.map((role) => (
                  <option key={role.name} value={role.name}>
                    {role.name} {role.is_default ? "(默认)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center">
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[#52525b]">
                <input
                  type="checkbox"
                  checked={editIsAdmin}
                  disabled={editingUser.id === currentUser?.id}
                  onChange={(e) => setEditIsAdmin(e.target.checked)}
                  className="h-4 w-4 rounded accent-[#1e2024]"
                />
                <span>
                  设为管理员 {editingUser.id === currentUser?.id && "(当前登录账号不可取消管理员)"}
                </span>
              </label>
            </div>
            <div>
              <label
                htmlFor="admin-edit-password"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                重置密码 (可选)
              </label>
              <input
                id="admin-edit-password"
                type="password"
                autoComplete="new-password"
                value={editPassword}
                onChange={(e) => setEditPassword(e.target.value)}
                placeholder="留空则保持原密码不变"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            {editPassword && (
              <div>
                <label
                  htmlFor="admin-edit-confirm-password"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  确认新密码
                </label>
                <input
                  id="admin-edit-confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={editConfirmPassword}
                  onChange={(e) => setEditConfirmPassword(e.target.value)}
                  placeholder="请再次输入新密码"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
            )}
          </div>
          <AdminDialogActions>
            <AdminDialogCancelButton disabled={savingUser} onClick={() => setEditingUser(null)}>
              取消
            </AdminDialogCancelButton>
            <AdminDialogPrimaryButton
              disabled={
                savingUser ||
                !editUsername.trim() ||
                !editEmail.trim() ||
                (!!editPassword && (!editConfirmPassword || editPassword !== editConfirmPassword))
              }
              onClick={() => void handleSaveUser()}
            >
              {savingUser ? "保存中..." : "确认保存"}
            </AdminDialogPrimaryButton>
          </AdminDialogActions>
        </AdminEditorDialog>
      )}
    </div>
  );
}
