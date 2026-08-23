import { useState } from "react";
import { toast } from "sonner";
import { adminApi, type DorisRoleResponse } from "@/api/admin";
import { Button } from "@/components/ui/button";

function errorMessage(error: unknown): string {
  return (
    (error as { response?: { data?: { detail?: string; title?: string } } }).response?.data
      ?.detail ?? "创建角色失败"
  );
}

interface DorisRoleCreateCardProps {
  rolesCount: number;
  busy: boolean;
  onRoleCreated: (role: DorisRoleResponse) => void;
}

export function DorisRoleCreateCard({ rolesCount, busy, onRoleCreated }: DorisRoleCreateCardProps) {
  const [newRole, setNewRole] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newQueryUser, setNewQueryUser] = useState("");
  const [newWorkloadGroup, setNewWorkloadGroup] = useState("normal");
  const [submitting, setSubmitting] = useState(false);

  const handleCreateRole = async () => {
    if (!newRole.trim() || !newDescription.trim() || !newQueryUser.trim()) return;
    setSubmitting(true);
    try {
      const created = await adminApi.createRole({
        role: newRole.trim(),
        description: newDescription.trim(),
        query_user: newQueryUser.trim(),
        workload_group: newWorkloadGroup.trim() || "normal",
        is_default: rolesCount === 0,
      });
      setNewRole("");
      setNewDescription("");
      setNewQueryUser("");
      setNewWorkloadGroup("normal");
      toast.success("Doris 角色和查询身份已创建");
      onRoleCreated(created);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-3 text-sm">
      <p className="mb-2 font-medium text-[#71717a]">创建新 Doris 角色与查询身份</p>
      <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4">
        <input
          value={newRole}
          onChange={(event) => setNewRole(event.target.value)}
          placeholder="角色标识 (如 data_analyst)"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
        <input
          value={newQueryUser}
          onChange={(event) => setNewQueryUser(event.target.value)}
          placeholder="查询用户 (如 q_analyst)"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
        <input
          value={newDescription}
          onChange={(event) => setNewDescription(event.target.value)}
          placeholder="角色业务描述"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
        <input
          value={newWorkloadGroup}
          onChange={(event) => setNewWorkloadGroup(event.target.value)}
          placeholder="资源工作组 (默认 normal)"
          className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
        />
      </div>
      <div className="mt-2.5 flex justify-end">
        <Button
          size="sm"
          disabled={
            busy || submitting || !newRole.trim() || !newDescription.trim() || !newQueryUser.trim()
          }
          onClick={() => void handleCreateRole()}
        >
          {submitting ? "创建中..." : "创建角色与身份"}
        </Button>
      </div>
    </div>
  );
}
