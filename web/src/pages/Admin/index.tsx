import { ArrowLeft, RefreshCw, Shield, Users, Database } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  adminApi,
  type AssetGrantResponse,
  type DorisRoleResponse,
  type UserListResponse,
} from "@/api/admin";
import { useAuthStore, type UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";
import { MetadataManagement } from "./components/MetadataManagement";

function errorMessage(error: unknown): string {
  return (
    (error as { response?: { data?: { detail?: string; title?: string } } }).response?.data
      ?.detail ?? "操作失败，请检查 Doris 权限配置"
  );
}

function splitColumns(value: string): string[] {
  return value
    .split(",")
    .map((column) => column.trim())
    .filter(Boolean);
}

const USER_PAGE_SIZE = 50;

export default function AdminPage() {
  const currentUser = useAuthStore((state) => state.user);
  const [activeTab, setActiveTab] = useState<"metadata" | "users" | "roles">("metadata");
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [policies, setPolicies] = useState<Record<string, unknown>[]>([]);
  const [grants, setGrants] = useState<AssetGrantResponse[]>([]);
  const [userOffset, setUserOffset] = useState(0);
  const [userTotal, setUserTotal] = useState(0);
  const [userHasMore, setUserHasMore] = useState(false);
  const [newRole, setNewRole] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newQueryUser, setNewQueryUser] = useState("");
  const [newWorkloadGroup, setNewWorkloadGroup] = useState("normal");
  const [tableName, setTableName] = useState("");
  const [columns, setColumns] = useState("");
  const [policyName, setPolicyName] = useState("");
  const [policyTable, setPolicyTable] = useState("");
  const [predicate, setPredicate] = useState("");
  const [policyType, setPolicyType] = useState<"RESTRICTIVE" | "PERMISSIVE">("RESTRICTIVE");
  const [busy, setBusy] = useState(false);

  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState("");
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false);

  const [discoveredRoles, setDiscoveredRoles] = useState<
    {
      name: string;
      is_attached: boolean;
      description: string | null;
      query_user: string | null;
      workload_group: string | null;
    }[]
  >([]);
  const [discovering, setDiscovering] = useState(false);
  const [attachRoleName, setAttachRoleName] = useState("");
  const [attachDescription, setAttachDescription] = useState("");
  const [attachWorkloadGroup, setAttachWorkloadGroup] = useState("normal");

  const applyUserPage = useCallback((page: UserListResponse) => {
    setUsers(page.users);
    setUserTotal(page.total);
    setUserHasMore(page.has_more);
  }, []);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [loadedRoles, userPage] = await Promise.all([
        adminApi.listRoles(),
        adminApi.listUsers(USER_PAGE_SIZE, userOffset),
      ]);
      setRoles(loadedRoles);
      applyUserPage(userPage);
      setSelectedRole((current) =>
        loadedRoles.some((role) => role.name === current) ? current : loadedRoles[0]?.name || ""
      );
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [applyUserPage, userOffset]);

  const scanDorisRoles = async () => {
    setDiscovering(true);
    try {
      const discovered = await adminApi.discoverRoles();
      setDiscoveredRoles(discovered);
      toast.success(`扫描完成，发现 ${discovered.length} 个 Doris 角色`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setDiscovering(false);
    }
  };

  const attachRole = async () => {
    if (!attachRoleName.trim() || !attachDescription.trim()) return;
    setBusy(true);
    try {
      const attached = await adminApi.attachRole({
        role: attachRoleName.trim(),
        description: attachDescription.trim(),
        workload_group: attachWorkloadGroup.trim() || "normal",
        is_default: roles.length === 0,
      });
      const loadedRoles = await adminApi.listRoles();
      setRoles(loadedRoles);
      setSelectedRole(attached.name);
      setAttachRoleName("");
      setAttachDescription("");
      setDiscoveredRoles((prev) =>
        prev.map((r) => (r.name === attached.name ? { ...r, is_attached: true } : r))
      );
      toast.success(`Doris 角色 ${attached.name} 接入成功，已自动配置代理查询用户`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selectedRole) {
      setPolicies([]);
      setGrants([]);
      return;
    }
    void Promise.all([
      adminApi.listRowPolicies(selectedRole),
      adminApi.listSelectGrants(selectedRole),
    ])
      .then(([loadedPolicies, loadedGrants]) => {
        setPolicies(loadedPolicies);
        setGrants(loadedGrants);
      })
      .catch((error) => toast.error(errorMessage(error)));
  }, [selectedRole]);

  const selectedRoleStatus = useMemo(
    () => roles.find((role) => role.name === selectedRole),
    [roles, selectedRole]
  );

  const grantTables = useMemo(() => {
    const grouped = new Map<string, { allColumns: boolean; columns: string[] }>();
    for (const grant of grants) {
      if (!grant.table_name) continue;
      const entry = grouped.get(grant.table_name) ?? { allColumns: false, columns: [] };
      if (grant.scope === "table") entry.allColumns = true;
      if (grant.column_name) entry.columns.push(grant.column_name);
      grouped.set(grant.table_name, entry);
    }
    return [...grouped.entries()]
      .map(([name, entry]) => ({
        name,
        allColumns: entry.allColumns,
        columns: [...new Set(entry.columns)].sort(),
      }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [grants]);
  const databaseGrant = grants.find((grant) => grant.scope === "database");
  const grantDatabase = grants[0]?.database_name;
  const grantDataSource = grants[0]?.data_source;

  const updateUser = (updated: UserResponse) => {
    setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
  };

  const handleCreateUser = async () => {
    if (!newUsername.trim() || !newEmail.trim() || !newPassword.trim()) return;
    setBusy(true);
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
      const page = await adminApi.listUsers(USER_PAGE_SIZE, 0);
      setUserOffset(0);
      applyUserPage(page);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
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

  const mutate = async (
    operation: () => Promise<void>,
    message: string,
    refreshPolicies = true
  ) => {
    setBusy(true);
    try {
      await operation();
      toast.success(message);
      if (refreshPolicies && selectedRole) {
        const [loadedPolicies, loadedGrants] = await Promise.all([
          adminApi.listRowPolicies(selectedRole),
          adminApi.listSelectGrants(selectedRole),
        ]);
        setPolicies(loadedPolicies);
        setGrants(loadedGrants);
      }
      const loadedRoles = await adminApi.listRoles();
      setRoles(loadedRoles);
      setSelectedRole((current) =>
        loadedRoles.some((role) => role.name === current) ? current : loadedRoles[0]?.name || ""
      );
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const createRole = async () => {
    setBusy(true);
    try {
      const created = await adminApi.createRole({
        role: newRole.trim(),
        description: newDescription.trim(),
        query_user: newQueryUser.trim(),
        workload_group: newWorkloadGroup.trim(),
        is_default: roles.length === 0,
      });
      const loadedRoles = await adminApi.listRoles();
      setRoles(loadedRoles);
      setSelectedRole(created.name);
      setNewRole("");
      setNewDescription("");
      setNewQueryUser("");
      toast.success("Doris 角色和查询身份已创建");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#f4f4f0] p-4 font-mono text-[#1e2024] md:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        {/* 顶部控制台标题 */}
        <header className="flex flex-wrap items-center justify-between gap-4 rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
          <div>
            <h1 className="text-xl font-bold text-[#18181b]">管理中心</h1>
          </div>
          <div className="flex gap-2.5">
            <Button asChild variant="default" size="sm" className="text-sm">
              <Link to={ROUTES.chat}>
                <ArrowLeft className="h-4 w-4 mr-1.5" />
                返回对话
              </Link>
            </Button>
          </div>
        </header>

        {/* 模块 Tab 切换导航 */}
        <div className="flex border-b border-[#d4d4ce] bg-[#ffffff] rounded p-1.5 gap-2 text-sm shadow-xs">
          <button
            type="button"
            onClick={() => setActiveTab("metadata")}
            className={`flex items-center gap-1.5 px-4 py-2 rounded text-sm font-medium transition-colors ${
              activeTab === "metadata"
                ? "bg-[#1e2024] text-[#ffffff]"
                : "text-[#52525b] hover:bg-[#ebebe6] hover:text-[#18181b]"
            }`}
          >
            <Database className="h-4 w-4" />
            <span>元数据管理</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("users")}
            className={`flex items-center gap-1.5 px-4 py-2 rounded text-sm font-medium transition-colors ${
              activeTab === "users"
                ? "bg-[#1e2024] text-[#ffffff]"
                : "text-[#52525b] hover:bg-[#ebebe6] hover:text-[#18181b]"
            }`}
          >
            <Users className="h-4 w-4" />
            <span>用户账号管理</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("roles")}
            className={`flex items-center gap-1.5 px-4 py-2 rounded text-sm font-medium transition-colors ${
              activeTab === "roles"
                ? "bg-[#1e2024] text-[#ffffff]"
                : "text-[#52525b] hover:bg-[#ebebe6] hover:text-[#18181b]"
            }`}
          >
            <Shield className="h-4 w-4" />
            <span>Doris 角色管理</span>
          </button>
        </div>

        {/* 1. Doris 角色与权限管理 Tab */}
        {activeTab === "roles" && (
          <div className="space-y-6">
            {/* Doris 角色与查询身份管理 */}
            <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
              <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#e5e5df] pb-3">
                <h2 className="text-base font-bold text-[#18181b]">Doris 角色与查询身份</h2>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={discovering}
                  onClick={() => void scanDorisRoles()}
                  className="text-sm"
                >
                  <RefreshCw className={`h-4 w-4 mr-1.5 ${discovering ? "animate-spin" : ""}`} />
                  扫描原生角色
                </Button>
              </div>

              {discoveredRoles.length > 0 && (
                <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-4 text-xs">
                  <h3 className="font-semibold text-[#18181b]">
                    扫描发现的原生角色（未托管角色可直接接入管理）：
                  </h3>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2 md:grid-cols-3">
                    {discoveredRoles.map((role) => (
                      <div
                        key={role.name}
                        className="flex flex-col justify-between rounded border border-[#d4d4ce] bg-[#ffffff] p-3 shadow-xs"
                      >
                        <div>
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-[#18181b]">{role.name}</span>
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                role.is_attached
                                  ? "bg-[#ebebe6] text-[#27272a]"
                                  : "bg-[#fef3c7] text-[#92400e]"
                              }`}
                            >
                              {role.is_attached ? "已托管" : "未托管"}
                            </span>
                          </div>
                          {role.description && (
                            <p className="mt-1 text-xs text-[#71717a]">{role.description}</p>
                          )}
                        </div>
                        {!role.is_attached && (
                          <div className="mt-3 space-y-1.5">
                            <input
                              value={attachRoleName === role.name ? attachDescription : ""}
                              onChange={(e) => {
                                setAttachRoleName(role.name);
                                setAttachDescription(e.target.value);
                              }}
                              placeholder="角色描述（必填）"
                              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                            />
                            <input
                              value={attachRoleName === role.name ? attachWorkloadGroup : "normal"}
                              onChange={(e) => {
                                setAttachRoleName(role.name);
                                setAttachWorkloadGroup(e.target.value);
                              }}
                              placeholder="Workload Group（默认：normal）"
                              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                            />
                            <Button
                              size="sm"
                              className="w-full text-xs"
                              disabled={
                                busy || attachRoleName !== role.name || !attachDescription.trim()
                              }
                              onClick={() => void attachRole()}
                            >
                              接入并管理
                            </Button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 新增角色输入行 */}
              <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-3 text-sm">
                <p className="mb-2 font-medium text-[#71717a]">创建新 Doris 角色</p>
                <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4">
                  <input
                    value={newRole}
                    onChange={(event) => setNewRole(event.target.value)}
                    placeholder="角色名称（如：finance_analyst）"
                    className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                  />
                  <input
                    value={newQueryUser}
                    onChange={(event) => setNewQueryUser(event.target.value)}
                    placeholder="查询用户名（如：finance_query_user）"
                    className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                  />
                  <input
                    value={newWorkloadGroup}
                    onChange={(event) => setNewWorkloadGroup(event.target.value)}
                    placeholder="资源组（默认：normal）"
                    className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                  />
                  <input
                    value={newDescription}
                    onChange={(event) => setNewDescription(event.target.value)}
                    placeholder="角色说明描述"
                    className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                  />
                </div>
                <div className="mt-2.5 flex justify-end">
                  <Button
                    size="sm"
                    disabled={
                      busy ||
                      !newRole.trim() ||
                      !newQueryUser.trim() ||
                      !newWorkloadGroup.trim() ||
                      !newDescription.trim()
                    }
                    onClick={() => void createRole()}
                  >
                    创建角色
                  </Button>
                </div>
              </div>

              {/* 角色列表表格 */}
              <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
                <table className="w-full min-w-[760px] text-left text-sm font-mono">
                  <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                    <tr>
                      <th className="px-3.5 py-2.5">角色名称 / 描述</th>
                      <th className="px-3.5 py-2.5">查询代理用户</th>
                      <th className="px-3.5 py-2.5">资源组</th>
                      <th className="px-3.5 py-2.5">状态</th>
                      <th className="px-3.5 py-2.5 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roles.map((role) => (
                      <tr key={role.name} className="border-b border-[#f0f0eb] hover:bg-[#fafaf8]">
                        <td className="px-3.5 py-2.5">
                          <div className="font-semibold text-[#18181b]">{role.name}</div>
                          <div className="text-xs text-[#71717a]">{role.description}</div>
                        </td>
                        <td className="px-3.5 py-2.5 font-mono text-[#27272a]">
                          {role.query_user}
                        </td>
                        <td className="px-3.5 py-2.5 text-[#71717a]">{role.workload_group}</td>
                        <td className="px-3.5 py-2.5">
                          <span
                            className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                              role.is_default
                                ? "bg-[#1e2024] text-[#ffffff]"
                                : role.is_active
                                  ? "bg-[#ebebe6] text-[#27272a]"
                                  : "bg-[#fee2e2] text-[#991b1b]"
                            }`}
                          >
                            {role.is_default ? "默认角色" : role.is_active ? "正常" : "禁用"}
                          </span>
                        </td>
                        <td className="px-3.5 py-2.5 text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy || role.is_default || !role.is_active}
                              onClick={() =>
                                void mutate(
                                  () => adminApi.setDefaultRole(role.name).then(() => undefined),
                                  "默认角色已更新",
                                  false
                                )
                              }
                              className="h-8 px-2.5 text-xs"
                            >
                              设为默认
                            </Button>
                            <Button
                              variant="destructive"
                              size="sm"
                              disabled={busy || role.is_default}
                              onClick={() =>
                                void mutate(
                                  () => adminApi.deleteRole(role.name),
                                  "Doris 角色已删除",
                                  false
                                )
                              }
                              className="h-8 px-2.5 text-xs"
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {/* 权限与行级策略配置分栏 */}
            <section className="grid gap-6 lg:grid-cols-2">
              {/* 表与列权限 */}
              <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
                <h2 className="border-b border-[#e5e5df] pb-3 text-base font-bold text-[#18181b]">
                  表与列数据权限 (SELECT)
                </h2>

                <div className="mt-4 space-y-3.5 text-sm">
                  <div>
                    <label htmlFor="permission-role" className="block text-xs text-[#52525b] mb-1">
                      目标 Doris 角色：
                    </label>
                    <select
                      id="permission-role"
                      value={selectedRole}
                      onChange={(event) => setSelectedRole(event.target.value)}
                      className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
                    >
                      {roles.map((role) => (
                        <option key={role.name} value={role.name}>
                          {role.name}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-xs text-[#71717a]">
                      {selectedRoleStatus?.exists_in_doris
                        ? "✓ Doris 原生角色已生效"
                        : "⚠ 角色未在 Doris 元数据中找到"}
                    </p>
                  </div>

                  <div className="rounded border border-[#d4d4ce] bg-[#fafaf8] p-3">
                    <div className="flex items-center justify-between text-xs font-semibold text-[#18181b]">
                      <span>当前 SELECT 授权</span>
                      <span className="font-normal text-[#71717a]">{grants.length} 条投影</span>
                    </div>
                    {grants.length ? (
                      <div className="mt-2 border-l-2 border-[#1e2024] pl-3 text-xs">
                        <div className="font-semibold text-[#18181b]">
                          数据库 {grantDataSource}.{grantDatabase}
                          {databaseGrant && (
                            <span className="ml-2 rounded bg-[#e8e8e4] px-1.5 py-0.5 text-[10px] text-[#1e2024]">
                              全库 SELECT
                            </span>
                          )}
                        </div>
                        {grantTables.map((table) => (
                          <div key={table.name} className="mt-2 border-l border-[#d4d4ce] pl-3">
                            <div className="font-medium text-[#27272a]">
                              表 {table.name}
                              {table.allColumns && (
                                <span className="ml-2 rounded bg-[#e5e5df] px-1.5 py-0.5 text-[10px]">
                                  全部列
                                </span>
                              )}
                            </div>
                            {table.columns.length > 0 && (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {table.columns.map((column) => (
                                  <span
                                    key={column}
                                    className="rounded border border-[#d4d4ce] bg-white px-1.5 py-0.5 text-[10px] text-[#52525b]"
                                  >
                                    列 {column}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-2 text-xs text-[#71717a]">当前角色没有 SELECT 授权</p>
                    )}
                  </div>

                  <div>
                    <label htmlFor="permission-table" className="block text-xs text-[#52525b] mb-1">
                      目标数据表（留空表示当前数据库全部表）：
                    </label>
                    <input
                      id="permission-table"
                      value={tableName}
                      onChange={(event) => setTableName(event.target.value)}
                      placeholder="如：ods_order_detail"
                      className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="permission-columns"
                      className="block text-xs text-[#52525b] mb-1"
                    >
                      目标字段列（逗号分隔，留空表示全部列）：
                    </label>
                    <input
                      id="permission-columns"
                      value={columns}
                      onChange={(event) => setColumns(event.target.value)}
                      placeholder="如：order_id, user_id, amount"
                      className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                    />
                  </div>

                  <div className="flex gap-2.5 pt-2">
                    <Button
                      disabled={busy || !selectedRole}
                      onClick={() =>
                        void mutate(
                          () =>
                            adminApi.grantSelect(selectedRole, {
                              table_name: tableName.trim() || null,
                              columns: splitColumns(columns),
                            }),
                          "SELECT 权限已授予"
                        )
                      }
                      className="flex-1 text-sm"
                    >
                      授予 SELECT 权限
                    </Button>
                    <Button
                      variant="destructive"
                      disabled={busy || !selectedRole}
                      onClick={() =>
                        void mutate(
                          () =>
                            adminApi.revokeSelect(selectedRole, {
                              table_name: tableName.trim() || null,
                              columns: splitColumns(columns),
                            }),
                          "SELECT 权限已回收"
                        )
                      }
                      className="flex-1 text-sm"
                    >
                      回收 SELECT 权限
                    </Button>
                  </div>
                </div>
              </div>

              {/* 行级策略 */}
              <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
                <h2 className="border-b border-[#e5e5df] pb-3 text-base font-bold text-[#18181b]">
                  行级数据过滤策略 (RLS)
                </h2>

                <div className="mt-4 space-y-3.5 text-sm">
                  <div className="grid gap-2.5 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="row-policy-name"
                        className="block text-xs text-[#52525b] mb-1"
                      >
                        策略名称：
                      </label>
                      <input
                        id="row-policy-name"
                        value={policyName}
                        onChange={(event) => setPolicyName(event.target.value)}
                        placeholder="如：region_east_filter"
                        className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                      />
                    </div>
                    <div>
                      <label
                        htmlFor="row-policy-table"
                        className="block text-xs text-[#52525b] mb-1"
                      >
                        目标数据表：
                      </label>
                      <input
                        id="row-policy-table"
                        value={policyTable}
                        onChange={(event) => setPolicyTable(event.target.value)}
                        placeholder="如：ods_order_detail"
                        className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                      />
                    </div>
                  </div>

                  <div>
                    <label htmlFor="row-policy-type" className="block text-xs text-[#52525b] mb-1">
                      策略组合类型：
                    </label>
                    <select
                      id="row-policy-type"
                      value={policyType}
                      onChange={(event) =>
                        setPolicyType(event.target.value as "RESTRICTIVE" | "PERMISSIVE")
                      }
                      className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
                    >
                      <option value="RESTRICTIVE">RESTRICTIVE (限制性 AND 组合)</option>
                      <option value="PERMISSIVE">PERMISSIVE (兼容性 OR 组合)</option>
                    </select>
                  </div>

                  <div>
                    <label
                      htmlFor="row-policy-predicate"
                      className="block text-xs text-[#52525b] mb-1"
                    >
                      过滤表达式 (SQL WHERE 条件)：
                    </label>
                    <textarea
                      id="row-policy-predicate"
                      value={predicate}
                      onChange={(event) => setPredicate(event.target.value)}
                      placeholder="如：region = 'east' AND tenant_id = 42"
                      className="min-h-20 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] p-3 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                    />
                  </div>

                  <div className="flex gap-2.5">
                    <Button
                      disabled={busy || !selectedRole || !policyName || !policyTable || !predicate}
                      onClick={() =>
                        void mutate(
                          () =>
                            adminApi.createRowPolicy(selectedRole, {
                              policy_name: policyName,
                              table_name: policyTable,
                              policy_type: policyType,
                              predicate,
                            }),
                          "行策略已创建"
                        )
                      }
                      className="flex-1 text-sm"
                    >
                      创建行策略
                    </Button>
                    <Button
                      variant="destructive"
                      disabled={busy || !selectedRole || !policyName || !policyTable}
                      onClick={() =>
                        void mutate(
                          () => adminApi.dropRowPolicy(selectedRole, policyName, policyTable),
                          "行策略已删除"
                        )
                      }
                      className="flex-1 text-sm"
                    >
                      删除行策略
                    </Button>
                  </div>

                  <div className="mt-4">
                    <p className="mb-1 text-xs font-semibold text-[#71717a]">
                      当前角色生效的行策略：
                    </p>
                    <div className="max-h-40 space-y-1.5 overflow-auto rounded border border-[#d4d4ce] bg-[#fafaf8] p-2">
                      {policies.map((policy) => (
                        <pre
                          key={`${selectedRole}-${JSON.stringify(policy)}`}
                          className="overflow-auto rounded bg-[#ffffff] border border-[#e5e5df] p-2 text-xs text-[#27272a]"
                        >
                          {JSON.stringify(policy, null, 2)}
                        </pre>
                      ))}
                      {!policies.length && (
                        <p className="py-2 text-center text-xs text-[#71717a]">
                          暂无定义的行级安全策略
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* 2. 用户账号管理 Tab */}
        {activeTab === "users" && (
          <div className="space-y-6">
            {/* 用户账号与角色绑定 */}
            <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
              <h2 className="border-b border-[#e5e5df] pb-3 text-base font-bold text-[#18181b]">
                用户账号与角色绑定
              </h2>

              {/* 新增用户输入行 */}
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
                      !newUsername.trim() ||
                      !newEmail.trim() ||
                      !newPassword.trim() ||
                      newPassword.length < 6
                    }
                    onClick={() => void handleCreateUser()}
                  >
                    创建用户
                  </Button>
                </div>
              </div>

              <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
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
        )}

        {/* 3. 元数据管理 Tab */}
        {activeTab === "metadata" && <MetadataManagement />}
      </div>
    </main>
  );
}
