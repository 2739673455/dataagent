import { Check, Database, Edit2, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { type TableInfo, type TableRole, metaApi } from "@/api/meta";
import { Button } from "@/components/ui/button";
import { extractErrorMessage } from "./utils";

interface TableSectionProps {
  tables: TableInfo[];
  selectedTable: string | null;
  onSelectTable: (tableName: string) => void;
  selectedTableNames: string[];
  onToggleSelectTable: (tableName: string) => void;
  onSelectAllTables: (tableNames: string[]) => void;
  loadingCatalog: boolean;
  syncing: string | null;
  onSyncTableIndexes: () => Promise<void>;
  onSyncTableValues: () => Promise<void>;
  onReloadCatalog: () => Promise<void>;
}

export function TableSection({
  tables,
  selectedTable,
  onSelectTable,
  selectedTableNames,
  onToggleSelectTable,
  onSelectAllTables,
  loadingCatalog,
  syncing,
  onSyncTableIndexes,
  onSyncTableValues,
  onReloadCatalog,
}: TableSectionProps) {
  const [sourceTables, setSourceTables] = useState<string[]>([]);
  const [loadingSourceTables, setLoadingSourceTables] = useState(false);
  const [isCreatingTable, setIsCreatingTable] = useState(false);
  const [newTableName, setNewTableName] = useState("");
  const [newTableRole, setNewTableRole] = useState<TableRole>("fact");
  const [newTableDesc, setNewTableDesc] = useState("");
  const [isTableDropdownOpen, setIsTableDropdownOpen] = useState(false);
  const [editingTable, setEditingTable] = useState<TableInfo | null>(null);
  const [editTableRole, setEditTableRole] = useState<TableRole>("fact");
  const [editTableDesc, setEditTableDesc] = useState("");
  const [savingTable, setSavingTable] = useState(false);
  const [deletingTable, setDeletingTable] = useState<string | null>(null);
  const [isBatchDeleting, setIsBatchDeleting] = useState(false);

  const handleBatchDeleteTables = async () => {
    if (selectedTableNames.length === 0) return;
    const confirmed = window.confirm(
      `确认批量删除选中的 ${selectedTableNames.length} 个数据表吗？\n这将同时删除这些表下的所有字段元数据及关联索引。`
    );
    if (!confirmed) return;
    setIsBatchDeleting(true);
    try {
      await metaApi.deleteTables(selectedTableNames);
      toast.success(`已成功删除 ${selectedTableNames.length} 个数据表`);
      onSelectAllTables([]);
      await onReloadCatalog();
    } catch (error) {
      toast.error(extractErrorMessage(error, "批量删除数据表失败"));
    } finally {
      setIsBatchDeleting(false);
    }
  };

  const existingTableNames = useMemo(() => new Set(tables.map((t) => t.name)), [tables]);

  const filteredSourceTables = useMemo(() => {
    if (!newTableName.trim()) return sourceTables;
    const query = newTableName.toLowerCase().trim();
    return sourceTables.filter((t) => t.toLowerCase().includes(query));
  }, [sourceTables, newTableName]);

  const handleOpenCreateTable = async () => {
    setIsCreatingTable(true);
    setEditingTable(null);
    setNewTableName("");
    setNewTableRole("fact");
    setNewTableDesc("");
    setIsTableDropdownOpen(false);

    if (sourceTables.length === 0) {
      setLoadingSourceTables(true);
      try {
        const rawTables = await metaApi.listSourceTables();
        setSourceTables(rawTables);
      } catch (error) {
        toast.error(extractErrorMessage(error, "获取 Doris 物理表列表失败"));
      } finally {
        setLoadingSourceTables(false);
      }
    }
  };

  const handleCreateTable = async () => {
    if (!newTableName.trim() || !newTableDesc.trim()) {
      toast.error("表名称和业务描述不能为空");
      return;
    }
    setSavingTable(true);
    try {
      await metaApi.upsertTable(newTableName.trim(), {
        role: newTableRole,
        description: newTableDesc.trim(),
      });
      toast.success(`数据表 ${newTableName.trim()} 添加成功`);
      setIsCreatingTable(false);
      await onReloadCatalog();
      onSelectTable(newTableName.trim());
    } catch (error) {
      toast.error(extractErrorMessage(error, "添加数据表失败"));
    } finally {
      setSavingTable(false);
    }
  };

  const handleSaveTable = async () => {
    if (!editingTable) return;
    setSavingTable(true);
    try {
      await metaApi.upsertTable(editingTable.name, {
        role: editTableRole,
        description: editTableDesc.trim(),
      });
      toast.success(`数据表 ${editingTable.name} 更新成功`);
      setEditingTable(null);
      await onReloadCatalog();
    } catch (error) {
      toast.error(extractErrorMessage(error, "更新数据表失败"));
    } finally {
      setSavingTable(false);
    }
  };

  const handleDeleteTable = async (table: TableInfo) => {
    if (!window.confirm(`确定删除数据表 ${table.name} 及其所有字段吗？此操作不可逆。`)) return;
    setDeletingTable(table.name);
    try {
      await metaApi.deleteTables([table.name]);
      toast.success(`数据表 ${table.name} 已删除`);
      await onReloadCatalog();
    } catch (error) {
      toast.error(extractErrorMessage(error, "删除数据表失败"));
    } finally {
      setDeletingTable(null);
    }
  };

  return (
    <section
      id="section-tables"
      className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3 shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
            <Database className="h-4 w-4 text-[#52525b]" />
            <span>数据表元数据 ({tables.length})</span>
            {loadingCatalog && <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />}
          </h2>
          {selectedTableNames.length > 0 && (
            <span className="rounded bg-[#ebebe6] px-2 py-0.5 text-xs text-[#52525b] font-mono">
              已选 {selectedTableNames.length} 表
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={syncing !== null || selectedTableNames.length === 0}
            onClick={() => void onSyncTableIndexes()}
            className="h-7 text-xs"
            title={
              selectedTableNames.length === 0
                ? "请先勾选需要同步语义索引的数据表"
                : `同步已选 ${selectedTableNames.length} 张表的全部字段语义索引`
            }
          >
            <RefreshCw
              className={`h-3.5 w-3.5 mr-1 ${syncing === "table_semantic" ? "animate-spin" : ""}`}
            />
            {selectedTableNames.length > 0
              ? `同步表字段语义索引 (${selectedTableNames.length})`
              : "同步表字段语义索引"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={syncing !== null || selectedTableNames.length === 0}
            onClick={() => void onSyncTableValues()}
            className="h-7 text-xs"
            title={
              selectedTableNames.length === 0
                ? "请先勾选需要同步取值索引的数据表"
                : `同步已选 ${selectedTableNames.length} 张表的全部字段取值索引`
            }
          >
            <RefreshCw
              className={`h-3.5 w-3.5 mr-1 ${syncing === "table_values" ? "animate-spin" : ""}`}
            />
            {selectedTableNames.length > 0
              ? `同步表取值索引 (${selectedTableNames.length})`
              : "同步表取值索引"}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={syncing !== null || isBatchDeleting || selectedTableNames.length === 0}
            onClick={() => void handleBatchDeleteTables()}
            className="h-7 text-xs"
            title={
              selectedTableNames.length === 0
                ? "请先勾选需要删除的数据表"
                : `批量删除已选 ${selectedTableNames.length} 个数据表`
            }
          >
            <Trash2 className="h-3 w-3 mr-1" />
            {isBatchDeleting
              ? "删除中..."
              : selectedTableNames.length > 0
                ? `批量删除 (${selectedTableNames.length})`
                : "批量删除"}
          </Button>
          <Button
            size="sm"
            onClick={() => void handleOpenCreateTable()}
            className="h-7 px-2 text-xs"
            title="添加数据表元数据"
          >
            <Plus className="h-3 w-3 mr-1" />
            添加表
          </Button>
        </div>
      </div>

      {isCreatingTable && (
        <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
          <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
            <span>添加数据表元数据</span>
            <button
              type="button"
              onClick={() => setIsCreatingTable(false)}
              className="text-[#71717a] hover:text-[#18181b]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-3">
            <div className="relative">
              <div className="flex items-center justify-between mb-1">
                <label
                  htmlFor="metadata-new-table-name"
                  className="text-xs font-medium text-[#71717a]"
                >
                  源表名称
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
                  value={newTableName}
                  onFocus={() => setIsTableDropdownOpen(true)}
                  onBlur={() => {
                    setTimeout(() => setIsTableDropdownOpen(false), 200);
                  }}
                  onChange={(e) => {
                    const val = e.target.value;
                    setNewTableName(val);
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
                            setNewTableName(t);
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
            <div>
              <label
                htmlFor="metadata-new-table-role"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                表角色
              </label>
              <select
                id="metadata-new-table-role"
                value={newTableRole}
                onChange={(e) => setNewTableRole(e.target.value as TableRole)}
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
                value={newTableDesc}
                onChange={(e) => setNewTableDesc(e.target.value)}
                placeholder="数据表的业务用途与口径说明"
                rows={2}
                className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setIsCreatingTable(false)}
                className="h-7 text-xs"
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={savingTable || !newTableName.trim() || !newTableDesc.trim()}
                onClick={() => void handleCreateTable()}
                className="h-7 text-xs"
              >
                <Check className="h-3.5 w-3.5 mr-1" />
                {savingTable ? "正在创建..." : "确认添加表"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {editingTable && (
        <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
          <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
            <span>编辑表元数据: {editingTable.name}</span>
            <button
              type="button"
              onClick={() => setEditingTable(null)}
              className="text-[#71717a] hover:text-[#18181b]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-3">
            <div>
              <label
                htmlFor="metadata-table-role"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                表角色
              </label>
              <select
                id="metadata-table-role"
                value={editTableRole}
                onChange={(e) => setEditTableRole(e.target.value as TableRole)}
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] focus:border-[#1e2024] focus:outline-none"
              >
                <option value="fact">事实表</option>
                <option value="dim">维度表</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="metadata-table-description"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                业务描述
              </label>
              <textarea
                id="metadata-table-description"
                value={editTableDesc}
                onChange={(e) => setEditTableDesc(e.target.value)}
                placeholder="数据表的业务用途与口径说明"
                rows={2}
                className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditingTable(null)}
                className="h-7 text-xs"
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={savingTable || !editTableDesc.trim()}
                onClick={() => void handleSaveTable()}
                className="h-7 text-xs"
              >
                <Check className="h-3.5 w-3.5 mr-1" />
                {savingTable ? "保存中..." : "保存表元数据"}
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className="mt-4 rounded border border-[#d4d4ce]">
        {tables.length === 0 ? (
          <div className="py-12 text-center text-xs text-[#71717a]">暂无数据表</div>
        ) : (
          <div className="max-h-[410px] overflow-auto">
            <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[28%]" />
                <col className="w-[130px]" />
                <col className="w-[50%]" />
                <col className="w-[84px]" />
              </colgroup>
              <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                <tr>
                  <th className="w-[44px] px-3.5 py-2.5 bg-[#f4f4f0] text-center">
                    <input
                      type="checkbox"
                      aria-label="全选数据表"
                      checked={tables.length > 0 && selectedTableNames.length === tables.length}
                      ref={(el) => {
                        if (el) {
                          el.indeterminate =
                            selectedTableNames.length > 0 &&
                            selectedTableNames.length < tables.length;
                        }
                      }}
                      onChange={(e) => {
                        if (e.target.checked) {
                          onSelectAllTables(tables.map((t) => t.name));
                        } else {
                          onSelectAllTables([]);
                        }
                      }}
                      className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                    />
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    表名称
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    表角色
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    业务描述
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap text-right bg-[#f4f4f0]">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#f0f0eb]">
                {tables.map((table) => {
                  const isSelected = table.name === selectedTable;
                  const isChecked = selectedTableNames.includes(table.name);
                  return (
                    <tr
                      key={table.name}
                      onClick={() => onSelectTable(table.name)}
                      className={`transition-colors cursor-pointer ${
                        isSelected
                          ? "bg-[#1e2024] text-[#ffffff]"
                          : "hover:bg-[#fafaf8] text-[#1e2024]"
                      }`}
                    >
                      <td className="px-3.5 py-2.5 align-middle text-center">
                        <input
                          type="checkbox"
                          aria-label={`选择数据表 ${table.name}`}
                          checked={isChecked}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => onToggleSelectTable(table.name)}
                          className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                        />
                      </td>
                      <td className="px-3.5 py-2.5 align-middle">
                        <span
                          className={`font-semibold text-xs truncate block ${
                            isSelected ? "text-[#ffffff]" : "text-[#18181b]"
                          }`}
                          title={table.name}
                        >
                          {table.name}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 align-middle">
                        <span
                          className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap ${
                            isSelected
                              ? "bg-[#2d3139] text-[#ffffff]"
                              : "bg-[#e5e5df] text-[#52525b]"
                          }`}
                        >
                          {table.role === "fact" ? "事实表 (fact)" : "维度表 (dim)"}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-xs break-words">
                        <span
                          className={`line-clamp-2 leading-relaxed ${
                            isSelected ? "text-[#d4d4ce]" : "text-[#71717a]"
                          }`}
                          title={table.description || "暂无表描述"}
                        >
                          {table.description || "-"}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setIsCreatingTable(false);
                              setEditingTable(table);
                              setEditTableRole(table.role);
                              setEditTableDesc(table.description);
                            }}
                            className={`h-7 px-2 text-xs ${
                              isSelected
                                ? "bg-transparent text-white border-white/40 hover:bg-white/15 hover:text-white"
                                : ""
                            }`}
                            title={`编辑数据表 ${table.name}`}
                          >
                            <Edit2 className="h-3 w-3" />
                            <span className="sr-only">编辑数据表 {table.name}</span>
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={deletingTable === table.name}
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleDeleteTable(table);
                            }}
                            className="h-7 px-2 text-xs"
                            title={`删除数据表 ${table.name}`}
                          >
                            <Trash2 className="h-3 w-3" />
                            <span className="sr-only">删除数据表 {table.name}</span>
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
