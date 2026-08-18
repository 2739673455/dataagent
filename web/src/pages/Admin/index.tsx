import { ArrowLeft, Database, RefreshCw, ShieldCheck, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse } from "@/api/admin";
import type { UserResponse } from "@/auth";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";

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

export default function AdminPage() {
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [policies, setPolicies] = useState<Record<string, unknown>[]>([]);
  const [tableName, setTableName] = useState("");
  const [columns, setColumns] = useState("");
  const [policyName, setPolicyName] = useState("");
  const [policyTable, setPolicyTable] = useState("");
  const [predicate, setPredicate] = useState("");
  const [policyType, setPolicyType] = useState<"RESTRICTIVE" | "PERMISSIVE">("RESTRICTIVE");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [loadedRoles, loadedUsers] = await Promise.all([
        adminApi.listRoles(),
        adminApi.listUsers(),
      ]);
      setRoles(loadedRoles);
      setUsers(loadedUsers);
      setSelectedRole((current) => current || loadedRoles[0]?.name || "");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selectedRole) {
      setPolicies([]);
      return;
    }
    void adminApi
      .listRowPolicies(selectedRole)
      .then(setPolicies)
      .catch((error) => toast.error(errorMessage(error)));
  }, [selectedRole]);

  const selectedRoleStatus = useMemo(
    () => roles.find((role) => role.name === selectedRole),
    [roles, selectedRole]
  );

  const updateUser = (updated: UserResponse) => {
    setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
  };

  const mutate = async (operation: () => Promise<void>, message: string) => {
    setBusy(true);
    try {
      await operation();
      toast.success(message);
      if (selectedRole) {
        setPolicies(await adminApi.listRowPolicies(selectedRole));
      }
      setRoles(await adminApi.listRoles());
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen px-5 py-8 lg:px-10">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.26em] text-cyan-800">
              DataAgent Administration
            </p>
            <h1 className="mt-2 text-3xl font-semibold text-slate-900">Doris 权限管理</h1>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" disabled={busy} onClick={() => void load()}>
              <RefreshCw className="h-4 w-4" /> 刷新
            </Button>
            <Button asChild variant="outline">
              <Link to={ROUTES.chat}>
                <ArrowLeft className="h-4 w-4" /> 返回分析
              </Link>
            </Button>
          </div>
        </header>

        <section className="rounded-3xl border border-stone-200 bg-white/90 p-6 shadow-sm">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Users className="h-5 w-5 text-cyan-800" /> 用户绑定
          </h2>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b text-slate-500">
                <tr>
                  <th className="px-3 py-3">用户</th>
                  <th className="px-3 py-3">邮箱</th>
                  <th className="px-3 py-3">唯一 Doris 角色</th>
                  <th className="px-3 py-3">平台管理员</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-b border-stone-100">
                    <td className="px-3 py-3 font-medium">{user.username}</td>
                    <td className="px-3 py-3 text-slate-500">{user.email}</td>
                    <td className="px-3 py-3">
                      <select
                        className="h-10 rounded-xl border border-stone-300 bg-white px-3"
                        value={user.doris_role}
                        disabled={busy}
                        onChange={(event) => {
                          const role = event.target.value;
                          void adminApi
                            .setUserRole(user.id, role)
                            .then(updateUser)
                            .catch((error) => toast.error(errorMessage(error)));
                        }}
                      >
                        {roles.map((role) => (
                          <option key={role.name} value={role.name}>
                            {role.name}
                            {role.is_default ? "（缺省）" : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-3">
                      <input
                        type="checkbox"
                        checked={user.is_admin}
                        disabled={busy}
                        onChange={(event) => {
                          void adminApi
                            .setAdministrator(user.id, event.target.checked)
                            .then(updateUser)
                            .catch((error) => toast.error(errorMessage(error)));
                        }}
                        className="h-5 w-5 accent-cyan-800"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-3xl border border-stone-200 bg-white/90 p-6 shadow-sm">
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <Database className="h-5 w-5 text-cyan-800" /> 表与列权限
            </h2>
            <label className="mt-5 block text-sm font-medium">
              Doris 角色
              <select
                value={selectedRole}
                onChange={(event) => setSelectedRole(event.target.value)}
                className="mt-2 h-11 w-full rounded-xl border border-stone-300 bg-white px-3"
              >
                {roles.map((role) => (
                  <option key={role.name} value={role.name}>
                    {role.name}
                  </option>
                ))}
              </select>
            </label>
            <p className="mt-3 text-xs text-slate-500">
              {selectedRoleStatus?.exists_in_doris ? "Doris 角色已就绪" : "Doris 中未找到该角色"}
            </p>
            <label className="mt-4 block text-sm font-medium">
              表名
              <input
                value={tableName}
                onChange={(event) => setTableName(event.target.value)}
                placeholder="留空表示当前数据库"
                className="mt-2 h-11 w-full rounded-xl border border-stone-300 px-3"
              />
            </label>
            <label className="mt-4 block text-sm font-medium">
              字段
              <input
                value={columns}
                onChange={(event) => setColumns(event.target.value)}
                placeholder="逗号分隔；留空表示整表"
                className="mt-2 h-11 w-full rounded-xl border border-stone-300 px-3"
              />
            </label>
            <div className="mt-5 flex gap-3">
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
              >
                授权
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
              >
                回收
              </Button>
            </div>
          </div>

          <div className="rounded-3xl border border-stone-200 bg-white/90 p-6 shadow-sm">
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <ShieldCheck className="h-5 w-5 text-cyan-800" /> 行级策略
            </h2>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <input
                value={policyName}
                onChange={(event) => setPolicyName(event.target.value)}
                placeholder="策略名"
                className="h-11 rounded-xl border border-stone-300 px-3"
              />
              <input
                value={policyTable}
                onChange={(event) => setPolicyTable(event.target.value)}
                placeholder="表名"
                className="h-11 rounded-xl border border-stone-300 px-3"
              />
            </div>
            <select
              value={policyType}
              onChange={(event) =>
                setPolicyType(event.target.value as "RESTRICTIVE" | "PERMISSIVE")
              }
              className="mt-4 h-11 w-full rounded-xl border border-stone-300 bg-white px-3"
            >
              <option value="RESTRICTIVE">RESTRICTIVE</option>
              <option value="PERMISSIVE">PERMISSIVE</option>
            </select>
            <textarea
              value={predicate}
              onChange={(event) => setPredicate(event.target.value)}
              placeholder="例如 region = 'east' AND tenant_id = 42"
              className="mt-4 min-h-28 w-full rounded-xl border border-stone-300 p-3 font-mono text-sm"
            />
            <div className="mt-4 flex gap-3">
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
              >
                创建策略
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
              >
                删除策略
              </Button>
            </div>
            <div className="mt-5 max-h-52 space-y-2 overflow-auto">
              {policies.map((policy) => (
                <pre
                  key={`${selectedRole}-${JSON.stringify(policy)}`}
                  className="overflow-auto rounded-xl bg-slate-950 p-3 text-xs text-slate-100"
                >
                  {JSON.stringify(policy, null, 2)}
                </pre>
              ))}
              {!policies.length && <p className="text-sm text-slate-500">暂无行策略</p>}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
