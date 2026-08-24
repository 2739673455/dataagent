import { Plus, RefreshCw, Shield, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { adminApi, type AssetGrantResponse, type DorisRoleResponse } from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { Button } from "@/components/ui/button";
import { AssetPermissionPanel } from "./AssetPermissionPanel";
import { DorisRoleCreateCard } from "./DorisRoleCreateCard";
import { RowPolicyPanel } from "./RowPolicyPanel";

export function DorisRoleManagement() {
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [policies, setPolicies] = useState<Record<string, unknown>[]>([]);
  const [grants, setGrants] = useState<AssetGrantResponse[]>([]);
  const [busy, setBusy] = useState(false);
  const [isCreatingRole, setIsCreatingRole] = useState(false);
  const [workloadGroups, setWorkloadGroups] = useState<string[]>([]);

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
  const [attachWorkloadGroup, setAttachWorkloadGroup] = useState("");

  const defaultWorkloadGroup = workloadGroups.includes("normal")
    ? "normal"
    : workloadGroups[0] || "";

  const loadRoles = useCallback(async () => {
    setBusy(true);
    try {
      const [loadedRoles, loadedWorkloadGroups] = await Promise.all([
        adminApi.listRoles(),
        adminApi.listWorkloadGroups(),
      ]);
      setRoles(loadedRoles);
      setWorkloadGroups(loadedWorkloadGroups);
      setAttachWorkloadGroup((current) =>
        loadedWorkloadGroups.includes(current)
          ? current
          : loadedWorkloadGroups.includes("normal")
            ? "normal"
            : loadedWorkloadGroups[0] || ""
      );
      setSelectedRole((current) =>
        loadedRoles.some((role) => role.name === current) ? current : loadedRoles[0]?.name || ""
      );
    } catch (error) {
      toast.error(getApiErrorMessage(error, "加载 Doris 角色失败"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void loadRoles();
  }, [loadRoles]);

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
      .catch((error) => toast.error(getApiErrorMessage(error, "加载角色权限失败")));
  }, [selectedRole]);

  const scanDorisRoles = async () => {
    setDiscovering(true);
    try {
      const discovered = await adminApi.discoverRoles();
      setDiscoveredRoles(discovered);
      toast.success(`扫描完成，发现 ${discovered.length} 个 Doris 角色`);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "扫描 Doris 角色失败"));
    } finally {
      setDiscovering(false);
    }
  };

  const attachRole = async () => {
    if (!attachRoleName.trim() || !attachDescription.trim() || !attachWorkloadGroup) return;
    setBusy(true);
    try {
      const attached = await adminApi.attachRole({
        role: attachRoleName.trim(),
        description: attachDescription.trim(),
        workload_group: attachWorkloadGroup,
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
      toast.error(getApiErrorMessage(error, "接入 Doris 角色失败"));
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
      toast.error(getApiErrorMessage(error, "更新 Doris 权限失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Doris 角色与查询身份管理 */}
      <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3 shrink-0">
          <div className="flex items-center gap-2">
            <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
              <Shield className="h-4 w-4 text-[#52525b]" />
              <span>Doris 角色与查询身份 ({roles.length})</span>
              {busy && <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={discovering}
              onClick={() => void scanDorisRoles()}
              className="h-7 px-2 text-xs"
              title="扫描原生角色"
            >
              <RefreshCw className={`h-3.5 w-3.5 mr-1 ${discovering ? "animate-spin" : ""}`} />
              扫描原生角色
            </Button>
            <Button
              size="sm"
              disabled={busy || workloadGroups.length === 0}
              onClick={() => setIsCreatingRole((prev) => !prev)}
              className="h-7 px-2 text-xs"
              title="添加 Doris 角色"
            >
              <Plus className="h-3 w-3 mr-1" />
              添加角色
            </Button>
          </div>
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
                  className="flex flex-col justify-between rounded border border-[#d4d4ce] bg-[#ffffff] p-3 shadow-2xs"
                >
                  <div>
                    <div className="flex items-center justify-between gap-1 font-semibold text-[#18181b]">
                      <span>{role.name}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] ${
                          role.is_attached
                            ? "bg-[#e5e5df] text-[#52525b]"
                            : "bg-[#1e2024] text-[#ffffff]"
                        }`}
                      >
                        {role.is_attached ? "已托管" : "未托管"}
                      </span>
                    </div>
                    {role.query_user && (
                      <p className="mt-1 text-[#71717a]">查询用户：{role.query_user}</p>
                    )}
                    {role.description && (
                      <p className="mt-0.5 text-[#52525b]">{role.description}</p>
                    )}
                  </div>
                  {!role.is_attached && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="mt-3 h-7 w-full text-xs"
                      onClick={() => {
                        setAttachRoleName(role.name);
                        setAttachDescription(role.description || `原生 Doris 角色 ${role.name}`);
                        setAttachWorkloadGroup(
                          role.workload_group && workloadGroups.includes(role.workload_group)
                            ? role.workload_group
                            : defaultWorkloadGroup
                        );
                      }}
                    >
                      选择并接入
                    </Button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {attachRoleName && (
          <div className="mt-4 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm">
            <h3 className="font-semibold text-[#18181b]">
              快速接入原生 Doris 角色：{attachRoleName}
            </h3>
            <p className="mt-1 text-[#71717a]">
              系统将为此角色自动创建专属查询用户并配置代理身份。
            </p>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <input
                value={attachDescription}
                onChange={(event) => setAttachDescription(event.target.value)}
                placeholder="角色业务描述 *"
                className="h-8 rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
              <select
                value={attachWorkloadGroup}
                onChange={(event) => setAttachWorkloadGroup(event.target.value)}
                className="h-8 rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              >
                {workloadGroups.length === 0 && <option value="">暂无可用工作组</option>}
                {workloadGroups.map((workloadGroup) => (
                  <option key={workloadGroup} value={workloadGroup}>
                    {workloadGroup}
                  </option>
                ))}
              </select>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={busy || !attachDescription.trim() || !attachWorkloadGroup}
                  onClick={() => void attachRole()}
                  className="h-8 flex-1 text-xs bg-[#1e2024] text-white"
                >
                  确认接入
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setAttachRoleName("")}
                  className="h-8 text-xs"
                >
                  取消
                </Button>
              </div>
            </div>
          </div>
        )}

        {isCreatingRole && (
          <DorisRoleCreateCard
            rolesCount={roles.length}
            busy={busy}
            workloadGroups={workloadGroups}
            defaultWorkloadGroup={defaultWorkloadGroup}
            onCancel={() => setIsCreatingRole(false)}
            onRoleCreated={(createdRole) => {
              setIsCreatingRole(false);
              setRoles((prev) => [...prev, createdRole]);
              setSelectedRole(createdRole.name);
            }}
          />
        )}

        {roles.length === 0 ? (
          <div className="mt-4 rounded border border-[#d4d4ce] bg-[#ffffff] py-12 text-center text-sm text-[#71717a]">
            暂无 Doris 角色
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto overflow-y-hidden rounded border border-[#d4d4ce]">
            <table className="w-full min-w-[760px] text-left text-sm font-mono">
              <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                <tr>
                  <th className="px-3.5 py-2.5">角色名称</th>
                  <th className="px-3.5 py-2.5">对应查询用户</th>
                  <th className="px-3.5 py-2.5">工作组</th>
                  <th className="px-3.5 py-2.5">状态</th>
                  <th className="px-3.5 py-2.5 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {roles.map((role) => (
                  <tr
                    key={role.name}
                    onClick={() => setSelectedRole(role.name)}
                    className={`border-b border-[#f0f0eb] transition-colors cursor-pointer ${
                      role.name === selectedRole
                        ? "bg-[#1e2024] text-[#ffffff]"
                        : "hover:bg-[#fafaf8]"
                    }`}
                  >
                    <td className="px-3.5 py-2.5 font-semibold">
                      {role.name}
                      {role.is_default && (
                        <span
                          className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${
                            role.name === selectedRole
                              ? "bg-[#ffffff] text-[#1e2024]"
                              : "bg-[#1e2024] text-[#ffffff]"
                          }`}
                        >
                          默认角色
                        </span>
                      )}
                    </td>
                    <td
                      className={`px-3.5 py-2.5 ${
                        role.name === selectedRole ? "text-[#deded8]" : "text-[#71717a]"
                      }`}
                    >
                      {role.query_user}
                    </td>
                    <td
                      className={`px-3.5 py-2.5 ${
                        role.name === selectedRole ? "text-[#deded8]" : "text-[#71717a]"
                      }`}
                    >
                      {role.workload_group}
                    </td>
                    <td className="px-3.5 py-2.5">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] ${
                          role.is_active
                            ? role.name === selectedRole
                              ? "bg-[#ffffff] text-[#1e2024]"
                              : "bg-[#1e2024] text-[#ffffff]"
                            : "bg-[#e5e5df] text-[#71717a]"
                        }`}
                      >
                        {role.is_active ? "已开启" : "未开启"}
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5 text-right whitespace-nowrap">
                      <div className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy || role.is_default || !role.is_active}
                          onClick={(e) => {
                            e.stopPropagation();
                            void mutate(
                              () => adminApi.setDefaultRole(role.name).then(() => undefined),
                              "默认角色已更新",
                              false
                            );
                          }}
                          className={`h-7 px-2 text-xs ${
                            role.name === selectedRole
                              ? "bg-transparent text-white border-white/40 hover:bg-white/15 hover:text-white"
                              : ""
                          }`}
                        >
                          设为默认
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={busy || role.is_default}
                          onClick={(e) => {
                            e.stopPropagation();
                            void mutate(
                              () => adminApi.deleteRole(role.name),
                              "Doris 角色已删除",
                              false
                            );
                          }}
                          className="h-7 px-2 text-xs"
                          title={`删除角色 ${role.name}`}
                        >
                          <Trash2 className="h-3 w-3" />
                          <span className="sr-only">删除角色 {role.name}</span>
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 权限与行级策略配置分栏 */}
      <section className="grid gap-6 lg:grid-cols-2">
        <AssetPermissionPanel
          roles={roles}
          selectedRole={selectedRole}
          onSelectRole={setSelectedRole}
          grants={grants}
          busy={busy}
          onMutate={mutate}
        />
        <RowPolicyPanel
          selectedRole={selectedRole}
          policies={policies}
          busy={busy}
          onMutate={mutate}
        />
      </section>
    </div>
  );
}
