import { useMemo, useState } from "react";
import { adminApi, type AssetGrantResponse, type DorisRoleResponse } from "@/api/admin";
import { Button } from "@/components/ui/button";

function splitColumns(value: string): string[] {
  return value
    .split(",")
    .map((column) => column.trim())
    .filter(Boolean);
}

interface AssetPermissionPanelProps {
  roles: DorisRoleResponse[];
  selectedRole: string;
  onSelectRole: (role: string) => void;
  grants: AssetGrantResponse[];
  busy: boolean;
  onMutate: (operation: () => Promise<void>, message: string) => Promise<void>;
}

export function AssetPermissionPanel({
  roles,
  selectedRole,
  onSelectRole,
  grants,
  busy,
  onMutate,
}: AssetPermissionPanelProps) {
  const [tableName, setTableName] = useState("");
  const [columns, setColumns] = useState("");

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

  return (
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
            onChange={(event) => onSelectRole(event.target.value)}
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
          <label htmlFor="permission-columns" className="block text-xs text-[#52525b] mb-1">
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
              void onMutate(
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
              void onMutate(
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
  );
}
