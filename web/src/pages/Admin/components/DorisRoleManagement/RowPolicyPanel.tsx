import { Shield, Trash2 } from "lucide-react";
import { useState } from "react";
import { adminApi, type RowPolicyResponse } from "@/api/admin";
import { Button } from "@/components/ui/button";

interface RowPolicyPanelProps {
  selectedRole: string;
  policies: RowPolicyResponse[];
  busy: boolean;
  onMutate: (operation: () => Promise<void>, message: string) => Promise<boolean>;
}

export function RowPolicyPanel({ selectedRole, policies, busy, onMutate }: RowPolicyPanelProps) {
  const [policyName, setPolicyName] = useState("");
  const [policyTable, setPolicyTable] = useState("");
  const [predicate, setPredicate] = useState("");
  const [policyType, setPolicyType] = useState<"RESTRICTIVE" | "PERMISSIVE">("RESTRICTIVE");

  const createPolicy = async () => {
    const created = await onMutate(
      () =>
        adminApi.createRowPolicy(selectedRole, {
          policy_name: policyName,
          table_name: policyTable,
          policy_type: policyType,
          predicate,
        }),
      "行策略已创建"
    );
    if (!created) {
      return;
    }
    setPolicyName("");
    setPolicyTable("");
    setPredicate("");
    setPolicyType("RESTRICTIVE");
  };

  return (
    <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3 shrink-0">
        <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
          <Shield className="h-4 w-4 text-[#52525b]" />
          <span>
            行级数据过滤策略 (RLS)
            {selectedRole ? ` [${selectedRole}]` : ""} ({policies.length})
          </span>
        </h2>
      </div>

      <div className="mt-4 space-y-3.5 text-sm">
        <div className="grid gap-2.5 sm:grid-cols-2">
          <div>
            <label htmlFor="row-policy-name" className="block text-xs text-[#52525b] mb-1">
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
            <label htmlFor="row-policy-table" className="block text-xs text-[#52525b] mb-1">
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
            onChange={(event) => setPolicyType(event.target.value as "RESTRICTIVE" | "PERMISSIVE")}
            className="h-9 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 text-sm text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
          >
            <option value="RESTRICTIVE">RESTRICTIVE (限制性 AND 组合)</option>
            <option value="PERMISSIVE">PERMISSIVE (许可性 OR 组合)</option>
          </select>
        </div>

        <div>
          <label htmlFor="row-policy-predicate" className="block text-xs text-[#52525b] mb-1">
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

        <Button
          disabled={busy || !selectedRole || !policyName || !policyTable || !predicate}
          onClick={() => void createPolicy()}
          className="w-full text-sm"
        >
          创建行策略
        </Button>

        <div className="rounded border border-[#d4d4ce] bg-[#fafaf8] p-3">
          <div className="flex items-center justify-between text-xs font-semibold text-[#18181b]">
            <span>当前行策略</span>
            <span className="font-normal text-[#71717a]">{policies.length} 条策略</span>
          </div>
          {policies.length ? (
            <div className="mt-2 max-h-64 space-y-1.5 overflow-auto">
              {policies.map((policy) => (
                <div
                  key={`${selectedRole}-${policy.catalog_name}-${policy.database_name}-${policy.table_name}-${policy.policy_name}`}
                  className="flex items-start justify-between gap-3 rounded border border-[#e5e5df] bg-[#ffffff] p-2.5 text-xs text-[#27272a]"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-semibold">{policy.policy_name}</span>
                      <span className="rounded bg-[#ecece7] px-1.5 py-0.5 text-[11px] text-[#52525b]">
                        {policy.policy_type}
                      </span>
                    </div>
                    <p
                      className="truncate text-[#71717a]"
                      title={`${policy.catalog_name}.${policy.database_name}.${policy.table_name}`}
                    >
                      {policy.catalog_name}.{policy.database_name}.{policy.table_name}
                    </p>
                    <code className="block whitespace-pre-wrap break-words rounded bg-[#f4f4f0] px-2 py-1 text-[#3f3f46]">
                      {policy.predicate}
                    </code>
                  </div>
                  <Button
                    variant="destructive"
                    size="icon"
                    disabled={busy || !selectedRole}
                    onClick={() =>
                      void onMutate(
                        () =>
                          adminApi.dropRowPolicy(
                            selectedRole,
                            policy.policy_name,
                            policy.table_name
                          ),
                        "行策略已删除"
                      )
                    }
                    className="h-7 w-7 shrink-0"
                    title={`删除行策略 ${policy.policy_name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    <span className="sr-only">删除行策略 {policy.policy_name}</span>
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-xs text-[#71717a]">当前角色没有行策略</p>
          )}
        </div>
      </div>
    </div>
  );
}
