import { useEffect, useState } from "react";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse } from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "../AdminEditorDialog";

interface DorisRoleCreateDialogProps {
  rolesCount: number;
  busy: boolean;
  workloadGroups: string[];
  defaultWorkloadGroup: string;
  onCancel: () => void;
  onRoleCreated: (role: DorisRoleResponse) => void;
}

export function DorisRoleCreateDialog({
  rolesCount,
  busy,
  workloadGroups,
  defaultWorkloadGroup,
  onCancel,
  onRoleCreated,
}: DorisRoleCreateDialogProps) {
  const [newRole, setNewRole] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newQueryUser, setNewQueryUser] = useState("");
  const [newWorkloadGroup, setNewWorkloadGroup] = useState(defaultWorkloadGroup);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!workloadGroups.includes(newWorkloadGroup)) {
      setNewWorkloadGroup(defaultWorkloadGroup);
    }
  }, [defaultWorkloadGroup, newWorkloadGroup, workloadGroups]);

  const handleCreateRole = async () => {
    if (!newRole.trim() || !newDescription.trim() || !newQueryUser.trim() || !newWorkloadGroup) {
      return;
    }
    setSubmitting(true);
    try {
      const created = await adminApi.createRole({
        role: newRole.trim(),
        description: newDescription.trim(),
        query_user: newQueryUser.trim(),
        workload_group: newWorkloadGroup,
        is_default: rolesCount === 0,
      });
      setNewRole("");
      setNewDescription("");
      setNewQueryUser("");
      setNewWorkloadGroup(defaultWorkloadGroup);
      toast.success("Doris 角色和查询身份已创建");
      onRoleCreated(created);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "创建角色失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AdminEditorDialog
      ariaLabel="创建新 Doris 角色与查询身份"
      onClose={onCancel}
      title="创建新 Doris 角色与查询身份"
    >
      <div className="grid gap-3">
        <div>
          <label htmlFor="role-new-name" className="block text-xs font-medium text-[#71717a] mb-1">
            角色标识 *
          </label>
          <input
            id="role-new-name"
            value={newRole}
            onChange={(event) => setNewRole(event.target.value)}
            placeholder="如 data_analyst"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>

        <div>
          <label
            htmlFor="role-new-query-user"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            查询用户 *
          </label>
          <input
            id="role-new-query-user"
            value={newQueryUser}
            onChange={(event) => setNewQueryUser(event.target.value)}
            placeholder="如 q_analyst"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>

        <div>
          <label htmlFor="role-new-desc" className="block text-xs font-medium text-[#71717a] mb-1">
            角色业务描述 *
          </label>
          <input
            id="role-new-desc"
            value={newDescription}
            onChange={(event) => setNewDescription(event.target.value)}
            placeholder="角色业务描述"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>

        <div>
          <label
            htmlFor="role-new-workload"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            资源工作组 *
          </label>
          <select
            id="role-new-workload"
            value={newWorkloadGroup}
            onChange={(event) => setNewWorkloadGroup(event.target.value)}
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          >
            {workloadGroups.length === 0 && <option value="">暂无可用工作组</option>}
            {workloadGroups.map((workloadGroup) => (
              <option key={workloadGroup} value={workloadGroup}>
                {workloadGroup}
              </option>
            ))}
          </select>
        </div>
      </div>

      <AdminDialogActions>
        <AdminDialogCancelButton onClick={onCancel}>取消</AdminDialogCancelButton>
        <AdminDialogPrimaryButton
          disabled={
            busy ||
            submitting ||
            !newRole.trim() ||
            !newDescription.trim() ||
            !newQueryUser.trim() ||
            !newWorkloadGroup
          }
          onClick={() => void handleCreateRole()}
        >
          {submitting ? "创建中..." : "确认创建"}
        </AdminDialogPrimaryButton>
      </AdminDialogActions>
    </AdminEditorDialog>
  );
}
