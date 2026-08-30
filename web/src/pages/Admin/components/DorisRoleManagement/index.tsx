import { Plus, Shield, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  type AssetGrantResponse,
  adminApi,
  type DorisExistingRoleResponse,
  type DorisRoleResponse,
  type RowPolicyResponse,
} from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { Button } from "@/components/ui/button";
import { AssetPermissionPanel } from "./AssetPermissionPanel";
import { DorisRoleCreateDialog } from "./DorisRoleCreateDialog";
import { RowPolicyPanel } from "./RowPolicyPanel";

export function DorisRoleManagement() {
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [existingRoles, setExistingRoles] = useState<DorisExistingRoleResponse[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [policies, setPolicies] = useState<RowPolicyResponse[]>([]);
  const [grants, setGrants] = useState<AssetGrantResponse[]>([]);
  const [busy, setBusy] = useState(false);
  const [isCreatingRole, setIsCreatingRole] = useState(false);
  const [workloadGroups, setWorkloadGroups] = useState<string[]>([]);

  const defaultWorkloadGroup = workloadGroups.includes("normal")
    ? "normal"
    : workloadGroups[0] || "";

  const loadRoles = useCallback(async () => {
    setBusy(true);
    try {
      const [loadedRoles, loadedExistingRoles, loadedWorkloadGroups] = await Promise.all([
        adminApi.listRoles(),
        adminApi.listExistingRoles(),
        adminApi.listWorkloadGroups(),
      ]);
      setRoles(loadedRoles);
      setExistingRoles(loadedExistingRoles);
      setWorkloadGroups(loadedWorkloadGroups);
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

  const mutate = async (
    operation: () => Promise<void>,
    message: string,
    refreshPolicies = true
  ): Promise<boolean> => {
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
      const [loadedRoles, loadedExistingRoles] = await Promise.all([
        adminApi.listRoles(),
        adminApi.listExistingRoles(),
      ]);
      setRoles(loadedRoles);
      setExistingRoles(loadedExistingRoles);
      setSelectedRole((current) =>
        loadedRoles.some((role) => role.name === current) ? current : loadedRoles[0]?.name || ""
      );
      return true;
    } catch (error) {
      toast.error(getApiErrorMessage(error, "更新 Doris 权限失败"));
      return false;
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
              {busy && <DotMatrixLoader className="ml-1 text-[#71717a]" />}
            </h2>
            <span className="rounded bg-[#f0f0eb] px-2 py-0.5 text-xs text-[#71717a]">
              默认角色：{roles.find((role) => role.is_default)?.name || "未分配"}
            </span>
          </div>
          <div className="flex items-center gap-2">
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

        {isCreatingRole && (
          <DorisRoleCreateDialog
            busy={busy}
            existingRoles={existingRoles}
            workloadGroups={workloadGroups}
            defaultWorkloadGroup={defaultWorkloadGroup}
            onCancel={() => setIsCreatingRole(false)}
            onRoleCreated={(createdRole) => {
              setIsCreatingRole(false);
              setSelectedRole(createdRole.name);
              void loadRoles();
            }}
          />
        )}

        <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-3">
          <div className="mb-2">
            <h3 className="text-sm font-semibold text-[#18181b]">
              Doris 已有角色 ({existingRoles.length})
            </h3>
          </div>
          {existingRoles.length === 0 ? (
            <div className="rounded border border-[#e5e5df] bg-[#ffffff] py-5 text-center text-xs text-[#71717a]">
              Doris 中暂无显式角色
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {existingRoles.map((role) => (
                <div
                  key={role.name}
                  className="flex min-w-[220px] max-w-full flex-col gap-1.5 rounded border border-[#e5e5df] bg-[#ffffff] px-3 py-2 font-mono text-xs"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span
                      className="min-w-0 truncate font-semibold text-[#1e2024]"
                      title={role.name}
                    >
                      {role.name}
                    </span>
                    <span className="shrink-0 rounded bg-[#f0f0eb] px-1.5 py-0.5 text-[10px] text-[#71717a]">
                      {role.managed ? "平台已管理" : "仅 Doris"}
                    </span>
                  </div>
                  <div
                    className="truncate text-[10px] text-[#71717a]"
                    title={role.doris_users.length > 0 ? role.doris_users.join(", ") : "暂无用户"}
                  >
                    {role.doris_users.length > 0 ? role.doris_users.join(", ") : "暂无用户"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {roles.length === 0 ? (
          <div className="mt-4 rounded border border-[#d4d4ce] bg-[#ffffff] py-12 text-center text-sm text-[#71717a]">
            暂无平台管理的 Doris 角色
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto overflow-y-hidden rounded border border-[#d4d4ce]">
            <table className="w-full min-w-[760px] text-left text-sm font-mono">
              <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                <tr>
                  <th className="px-3.5 py-2.5">角色名称</th>
                  <th className="px-3.5 py-2.5">对应查询用户</th>
                  <th className="px-3.5 py-2.5">工作组</th>
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
                    <td className="px-3.5 py-2.5 text-right whitespace-nowrap">
                      <div className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={(e) => {
                            e.stopPropagation();
                            void mutate(
                              () =>
                                role.is_default
                                  ? adminApi.clearDefaultRole()
                                  : adminApi.setDefaultRole(role.name).then(() => undefined),
                              role.is_default ? "默认角色已清除" : "默认角色已更新",
                              false
                            );
                          }}
                          className={`h-7 px-2 text-xs ${
                            role.name === selectedRole
                              ? "bg-transparent text-white border-white/40 hover:bg-white/15 hover:text-white"
                              : ""
                          }`}
                        >
                          {role.is_default ? "取消默认" : "设为默认"}
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={busy}
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
          selectedRole={selectedRole}
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
