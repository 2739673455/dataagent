import { Edit2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { metaApi, type TableInfo, type ValueIndexSyncRequestMode } from "@/api/meta";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { Button } from "@/components/ui/button";
import { TableEditorDialog, type TableDraft } from "./TableDialogs";

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
  onSyncTableValues: (mode: ValueIndexSyncRequestMode) => Promise<void>;
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
  const [editor, setEditor] = useState<TableDraft | null>(null);
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
      toast.error(getApiErrorMessage(error, "批量删除数据表失败"));
    } finally {
      setIsBatchDeleting(false);
    }
  };

  const existingTableNames = useMemo(() => new Set(tables.map((t) => t.name)), [tables]);

  const handleOpenCreateTable = async () => {
    setEditor({ mode: "create", name: "", role: "fact", description: "", cursorColumn: "" });

    if (sourceTables.length === 0) {
      setLoadingSourceTables(true);
      try {
        const rawTables = await metaApi.listSourceTables();
        setSourceTables(rawTables);
      } catch (error) {
        toast.error(getApiErrorMessage(error, "获取 Doris 物理表列表失败"));
      } finally {
        setLoadingSourceTables(false);
      }
    }
  };

  const handleSaveTable = async () => {
    if (!editor) return;
    const name = editor.name.trim();
    if (!name || !editor.description.trim()) {
      toast.error("表名称和业务描述不能为空");
      return;
    }
    setSavingTable(true);
    try {
      await metaApi.upsertTable(name, {
        role: editor.role,
        description: editor.description.trim(),
        value_index_cursor_column: editor.cursorColumn.trim() || null,
      });
      toast.success(`表 ${name} ${editor.mode === "create" ? "添加" : "更新"}成功`);
      setEditor(null);
      await onReloadCatalog();
      if (editor.mode === "create") onSelectTable(name);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "保存表元数据失败"));
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
      toast.error(getApiErrorMessage(error, "删除数据表失败"));
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
            <span>表元数据({tables.length})</span>
            {loadingCatalog && <DotMatrixLoader className="ml-1 text-[#71717a]" />}
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
            {syncing === "table_semantic" ? (
              <DotMatrixLoader className="mr-1" />
            ) : (
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
            )}
            {selectedTableNames.length > 0
              ? `同步语义索引 (${selectedTableNames.length})`
              : "同步语义索引"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={syncing !== null || selectedTableNames.length === 0}
            onClick={() => void onSyncTableValues("full")}
            className="h-7 text-xs"
            title={
              selectedTableNames.length === 0
                ? "请先勾选需要全量同步取值索引的数据表"
                : `全量替换已选 ${selectedTableNames.length} 张表的字段取值索引`
            }
          >
            {syncing === "table_values_full" ? (
              <DotMatrixLoader className="mr-1" />
            ) : (
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
            )}
            {selectedTableNames.length > 0
              ? `全量同步取值索引 (${selectedTableNames.length})`
              : "全量同步取值索引"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={syncing !== null || selectedTableNames.length === 0}
            onClick={() => void onSyncTableValues("incremental")}
            className="h-7 text-xs"
            title={
              selectedTableNames.length === 0
                ? "请先勾选需要增量同步取值索引的数据表"
                : `按水位增量同步已选 ${selectedTableNames.length} 张表，字段需要先完成全量同步`
            }
          >
            {syncing === "table_values_incremental" ? (
              <DotMatrixLoader className="mr-1" />
            ) : (
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
            )}
            {selectedTableNames.length > 0
              ? `增量同步取值索引 (${selectedTableNames.length})`
              : "增量同步取值索引"}
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

      {editor && (
        <TableEditorDialog
          draft={editor}
          onChange={setEditor}
          onClose={() => setEditor(null)}
          onSubmit={handleSaveTable}
          saving={savingTable}
          existingTableNames={existingTableNames}
          sourceTables={sourceTables}
          loadingSourceTables={loadingSourceTables}
        />
      )}

      <div className="mt-4 rounded border border-[#d4d4ce]">
        {tables.length === 0 ? (
          <div className="py-12 text-center text-xs text-[#71717a]">暂无数据表</div>
        ) : (
          <div className="max-h-[410px] overflow-auto">
            <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[24%]" />
                <col className="w-[130px]" />
                <col className="w-[40%]" />
                <col className="w-[18%]" />
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
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    增量游标字段
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
                      <td className="px-3.5 py-2.5 align-middle text-xs">
                        <span
                          className={`font-mono ${
                            isSelected ? "text-[#d4d4ce]" : "text-[#71717a]"
                          }`}
                          title={table.value_index_cursor_column || "未配置增量游标字段"}
                        >
                          {table.value_index_cursor_column || "-"}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditor({
                                mode: "edit",
                                name: table.name,
                                role: table.role,
                                description: table.description,
                                cursorColumn: table.value_index_cursor_column || "",
                              });
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
