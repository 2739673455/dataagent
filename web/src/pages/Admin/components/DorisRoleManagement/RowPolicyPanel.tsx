import { useState } from "react";
import { adminApi } from "@/api/admin";
import { Button } from "@/components/ui/button";

interface RowPolicyPanelProps {
  selectedRole: string;
  policies: Record<string, unknown>[];
  busy: boolean;
  onMutate: (operation: () => Promise<void>, message: string) => Promise<void>;
}

export function RowPolicyPanel({ selectedRole, policies, busy, onMutate }: RowPolicyPanelProps) {
  const [policyName, setPolicyName] = useState("");
  const [policyTable, setPolicyTable] = useState("");
  const [predicate, setPredicate] = useState("");
  const [policyType, setPolicyType] = useState<"RESTRICTIVE" | "PERMISSIVE">("RESTRICTIVE");

  return (
    <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
      <h2 className="border-b border-[#e5e5df] pb-3 text-base font-bold text-[#18181b]">
        行级数据过滤策略 (RLS)
      </h2>

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
            <option value="PERMISSIVE">PERMISSIVE (兼容性 OR 组合)</option>
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

        <div className="flex gap-2.5">
          <Button
            disabled={busy || !selectedRole || !policyName || !policyTable || !predicate}
            onClick={() =>
              void onMutate(
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
              void onMutate(
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
          <p className="mb-1 text-xs font-semibold text-[#71717a]">当前角色生效的行策略：</p>
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
              <p className="py-2 text-center text-xs text-[#71717a]">暂无定义的行级安全策略</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
