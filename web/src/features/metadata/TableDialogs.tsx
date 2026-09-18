import { useState } from "react";
import { Search } from "lucide-react";
import type { TableRole } from "@/features/metadata/api";
import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "@/components/AdminEditorDialog";

export interface TableDraft {
  mode: "create" | "edit";
  name: string;
  role: TableRole;
  description: string;
  cursorColumn: string;
}

export function TableEditorDialog({
  draft,
  onChange,
  onClose,
  onSubmit,
  saving,
  existingTableNames,
  sourceTables,
  loadingSourceTables,
}: {
  draft: TableDraft;
  onChange: (draft: TableDraft) => void;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  saving: boolean;
  existingTableNames: Set<string>;
  sourceTables: string[];
  loadingSourceTables: boolean;
}) {
  const creating = draft.mode === "create";
  const [isTableDropdownOpen, setIsTableDropdownOpen] = useState(false);
  const query = draft.name.toLowerCase().trim();
  const filteredSourceTables = sourceTables.filter((name) => name.toLowerCase().includes(query));
  return (
    <AdminEditorDialog
      ariaLabel={creating ? "添加数据表" : `编辑表元数据 ${draft.name}`}
      onClose={onClose}
      title={creating ? "添加数据表（从 Doris 物理表）" : `编辑表元数据: ${draft.name}`}
    >
      <div className="space-y-3">
        {creating && (
          <div className="relative">
            <div className="flex items-center justify-between mb-1">
              <label
                htmlFor="metadata-new-table-name"
                className="text-xs font-medium text-[#71717a]"
              >
                表名称
              </label>
              {loadingSourceTables ? (
                <span className="text-[10px] text-[#71717a] animate-pulse">
                  正在检索 Doris 物理表...
                </span>
              ) : sourceTables.length > 0 ? (
                <span className="text-[10px] text-[#71717a]">
                  共 {sourceTables.length} 张物理表
                </span>
              ) : null}
            </div>
            <div className="relative">
              <input
                id="metadata-new-table-name"
                type="text"
                autoComplete="off"
                value={draft.name}
                onFocus={() => setIsTableDropdownOpen(true)}
                onBlur={() => {
                  setTimeout(() => setIsTableDropdownOpen(false), 200);
                }}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...draft, name: val });
                  setIsTableDropdownOpen(true);
                }}
                placeholder="输入并从 Doris 物理表中检索"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
              <Search className="absolute right-2 top-2 h-4 w-4 text-[#a1a1aa] pointer-events-none" />
            </div>

            {isTableDropdownOpen && sourceTables.length > 0 && (
              <div className="absolute left-0 right-0 z-50 mt-1 max-h-48 overflow-y-auto rounded border border-[#d4d4ce] bg-[#ffffff] py-1 shadow-lg">
                {filteredSourceTables.length > 0 ? (
                  filteredSourceTables.map((t) => {
                    const isManaged = existingTableNames.has(t);
                    return (
                      <button
                        key={t}
                        type="button"
                        disabled={isManaged}
                        onMouseDown={() => {
                          onChange({ ...draft, name: t });
                          setIsTableDropdownOpen(false);
                        }}
                        className={`flex w-full items-center justify-between px-3 py-1.5 text-xs transition-colors ${
                          isManaged
                            ? "cursor-not-allowed bg-transparent text-[#a1a1aa] opacity-50"
                            : "cursor-pointer text-[#1e2024] hover:bg-[#ebebe6]"
                        }`}
                      >
                        <span className="font-mono">{t}</span>
                        {isManaged && <span className="text-[10px] text-[#71717a]">已纳管</span>}
                      </button>
                    );
                  })
                ) : (
                  <div className="px-2 py-2 text-center text-[#71717a]">
                    无匹配物理表，直接回车使用当前输入
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <div>
          <label
            htmlFor="metadata-new-table-role"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            表角色
          </label>
          <select
            id="metadata-new-table-role"
            value={draft.role}
            onChange={(e) => onChange({ ...draft, role: e.target.value as TableRole })}
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
          >
            <option value="fact">事实表</option>
            <option value="dim">维度表</option>
          </select>
        </div>
        <div>
          <label
            htmlFor="metadata-new-table-description"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            业务描述
          </label>
          <textarea
            id="metadata-new-table-description"
            value={draft.description}
            onChange={(e) => onChange({ ...draft, description: e.target.value })}
            placeholder="数据表的业务用途与口径说明"
            rows={2}
            className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="metadata-new-table-cursor-column"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            增量游标字段
          </label>
          <input
            id="metadata-new-table-cursor-column"
            value={draft.cursorColumn}
            onChange={(event) => onChange({ ...draft, cursorColumn: event.target.value })}
            placeholder="如：dw_update_time"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 font-mono text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
          <p className="mt-1 text-[10px] leading-relaxed text-[#71717a]">
            留空时仅支持全量同步；系统以该字段最大值记录取值索引同步水位
          </p>
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={saving || !draft.name.trim() || !draft.description.trim()}
            onClick={() => void onSubmit()}
          >
            {saving ? "保存中..." : creating ? "确认添加表" : "保存表元数据"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}
