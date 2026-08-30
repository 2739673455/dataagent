import { Edit2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { type ColumnInfo, metaApi, type ValueIndexSyncRequestMode } from "@/api/meta";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { Button } from "@/components/ui/button";
import { ColumnCreateDialog, ColumnEditDialog, ValueIndexStatus } from "./ColumnDialogs";
import { splitCsv } from "./utils";

interface ColumnSectionProps {
  selectedTable: string | null;
  columns: ColumnInfo[];
  selectedColumnNames: string[];
  onToggleSelectColumn: (colName: string) => void;
  onSelectAllColumns: (colNames: string[]) => void;
  loadingColumns: boolean;
  syncing: string | null;
  onSyncColumnIndexes: () => Promise<void>;
  onSyncColumnValues: (mode: ValueIndexSyncRequestMode) => Promise<void>;
  onReloadColumns: (tableName: string) => Promise<void>;
}

export function ColumnSection({
  selectedTable,
  columns,
  selectedColumnNames,
  onToggleSelectColumn,
  onSelectAllColumns,
  loadingColumns,
  syncing,
  onSyncColumnIndexes,
  onSyncColumnValues,
  onReloadColumns,
}: ColumnSectionProps) {
  const [isCreatingColumn, setIsCreatingColumn] = useState(false);
  const [newColName, setNewColName] = useState("");
  const [newColDesc, setNewColDesc] = useState("");
  const [newColAlias, setNewColAlias] = useState("");
  const [newColIndexValues, setNewColIndexValues] = useState(false);
  const [newColRefTable, setNewColRefTable] = useState("");
  const [newColRefColumn, setNewColRefColumn] = useState("");
  const [editingColumn, setEditingColumn] = useState<ColumnInfo | null>(null);
  const [editColDesc, setEditColDesc] = useState("");
  const [editColAlias, setEditColAlias] = useState("");
  const [editColIndexValues, setEditColIndexValues] = useState(false);
  const [editColRefTable, setEditColRefTable] = useState("");
  const [editColRefColumn, setEditColRefColumn] = useState("");
  const [savingColumn, setSavingColumn] = useState(false);
  const [deletingColumn, setDeletingColumn] = useState<string | null>(null);
  const [isBatchDeleting, setIsBatchDeleting] = useState(false);

  const handleBatchDeleteColumns = async () => {
    if (!selectedTable || selectedColumnNames.length === 0) return;
    const confirmed = window.confirm(
      `确认批量删除表 ${selectedTable} 下选中的 ${selectedColumnNames.length} 个字段吗？\n这将同时删除对应的取值索引及语义索引。`
    );
    if (!confirmed) return;
    setIsBatchDeleting(true);
    try {
      await metaApi.deleteColumns(
        selectedColumnNames.map((colName) => ({
          t_name: selectedTable,
          c_name: colName,
        }))
      );
      toast.success(`已成功删除 ${selectedColumnNames.length} 个字段`);
      onSelectAllColumns([]);
      await onReloadColumns(selectedTable);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "批量删除字段失败"));
    } finally {
      setIsBatchDeleting(false);
    }
  };

  const handleCreateColumn = async () => {
    if (!selectedTable || !newColName.trim() || !newColDesc.trim()) {
      toast.error("字段名称和业务描述不能为空");
      return;
    }
    setSavingColumn(true);
    try {
      await metaApi.upsertColumn(selectedTable, newColName.trim(), {
        description: newColDesc.trim(),
        alias: splitCsv(newColAlias),
        index_values: newColIndexValues,
        reference_t_name: newColRefTable.trim() || undefined,
        reference_c_name: newColRefColumn.trim() || undefined,
      });
      toast.success(`字段 ${newColName.trim()} 添加成功`);
      setIsCreatingColumn(false);
      await onReloadColumns(selectedTable);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "添加字段失败"));
    } finally {
      setSavingColumn(false);
    }
  };

  const handleSaveColumn = async () => {
    if (!selectedTable || !editingColumn) return;
    setSavingColumn(true);
    try {
      await metaApi.upsertColumn(selectedTable, editingColumn.name, {
        description: editColDesc.trim(),
        alias: splitCsv(editColAlias),
        index_values: editColIndexValues,
        reference_t_name: editColRefTable.trim() || undefined,
        reference_c_name: editColRefColumn.trim() || undefined,
      });
      toast.success(`字段 ${editingColumn.name} 更新成功`);
      setEditingColumn(null);
      await onReloadColumns(selectedTable);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "更新字段失败"));
    } finally {
      setSavingColumn(false);
    }
  };

  const handleDeleteColumn = async (colName: string) => {
    if (!selectedTable) return;
    if (!window.confirm(`确定删除字段 ${colName} 吗？此操作不可逆。`)) return;
    setDeletingColumn(colName);
    try {
      await metaApi.deleteColumns([{ t_name: selectedTable, c_name: colName }]);
      toast.success(`字段 ${colName} 已删除`);
      await onReloadColumns(selectedTable);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "删除字段失败"));
    } finally {
      setDeletingColumn(null);
    }
  };

  return (
    <section
      id="section-columns"
      className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3 shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
            <span>
              表字段元数据[{selectedTable || "未选择"}]({columns.length})
            </span>
            {loadingColumns && <DotMatrixLoader className="ml-1 text-[#71717a]" />}
          </h2>
          {selectedColumnNames.length > 0 && (
            <span className="rounded bg-[#ebebe6] px-2 py-0.5 text-xs text-[#52525b] font-mono">
              已选 {selectedColumnNames.length} 字段
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selectedTable && (
            <>
              <Button
                variant="outline"
                size="sm"
                disabled={syncing !== null || selectedColumnNames.length === 0}
                onClick={() => void onSyncColumnIndexes()}
                className="h-7 text-xs"
                title={
                  selectedColumnNames.length === 0
                    ? "请先勾选需要同步语义索引的字段"
                    : `同步已选 ${selectedColumnNames.length} 个字段语义索引`
                }
              >
                {syncing === "col_semantic" ? (
                  <DotMatrixLoader className="mr-1" />
                ) : (
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                )}
                {selectedColumnNames.length > 0
                  ? `同步语义索引 (${selectedColumnNames.length})`
                  : "同步语义索引"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={syncing !== null || selectedColumnNames.length === 0}
                onClick={() => void onSyncColumnValues("full")}
                className="h-7 text-xs"
                title={
                  selectedColumnNames.length === 0
                    ? "请先勾选需要全量同步取值索引的字段"
                    : `全量替换已选 ${selectedColumnNames.length} 个字段的取值索引`
                }
              >
                {syncing === "col_values_full" ? (
                  <DotMatrixLoader className="mr-1" />
                ) : (
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                )}
                {selectedColumnNames.length > 0
                  ? `全量同步取值索引 (${selectedColumnNames.length})`
                  : "全量同步取值索引"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={syncing !== null || selectedColumnNames.length === 0}
                onClick={() => void onSyncColumnValues("incremental")}
                className="h-7 text-xs"
                title={
                  selectedColumnNames.length === 0
                    ? "请先勾选需要增量同步取值索引的字段"
                    : `按水位增量同步已选 ${selectedColumnNames.length} 个字段，字段需要先完成全量同步`
                }
              >
                {syncing === "col_values_incremental" ? (
                  <DotMatrixLoader className="mr-1" />
                ) : (
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                )}
                {selectedColumnNames.length > 0
                  ? `增量同步取值索引 (${selectedColumnNames.length})`
                  : "增量同步取值索引"}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={syncing !== null || isBatchDeleting || selectedColumnNames.length === 0}
                onClick={() => void handleBatchDeleteColumns()}
                className="h-7 text-xs"
                title={
                  selectedColumnNames.length === 0
                    ? "请先勾选需要删除的字段"
                    : `批量删除已选 ${selectedColumnNames.length} 个字段`
                }
              >
                <Trash2 className="h-3 w-3 mr-1" />
                {isBatchDeleting
                  ? "删除中..."
                  : selectedColumnNames.length > 0
                    ? `批量删除 (${selectedColumnNames.length})`
                    : "批量删除"}
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setEditingColumn(null);
                  setIsCreatingColumn(true);
                  setNewColName("");
                  setNewColDesc("");
                  setNewColAlias("");
                  setNewColIndexValues(false);
                  setNewColRefTable("");
                  setNewColRefColumn("");
                }}
                className="h-7 px-2 text-xs"
                title={`为表 ${selectedTable} 添加字段元数据`}
              >
                <Plus className="h-3 w-3 mr-1" />
                添加字段
              </Button>
            </>
          )}
        </div>
      </div>

      <ColumnCreateDialog
        isOpen={isCreatingColumn}
        newColAlias={newColAlias}
        newColDesc={newColDesc}
        newColIndexValues={newColIndexValues}
        newColName={newColName}
        newColRefColumn={newColRefColumn}
        newColRefTable={newColRefTable}
        onClose={() => setIsCreatingColumn(false)}
        onSubmit={handleCreateColumn}
        savingColumn={savingColumn}
        selectedTable={selectedTable}
        setNewColAlias={setNewColAlias}
        setNewColDesc={setNewColDesc}
        setNewColIndexValues={setNewColIndexValues}
        setNewColName={setNewColName}
        setNewColRefColumn={setNewColRefColumn}
        setNewColRefTable={setNewColRefTable}
      />

      <ColumnEditDialog
        editColAlias={editColAlias}
        editColDesc={editColDesc}
        editColIndexValues={editColIndexValues}
        editColRefColumn={editColRefColumn}
        editColRefTable={editColRefTable}
        editingColumn={editingColumn}
        onClose={() => setEditingColumn(null)}
        onSubmit={handleSaveColumn}
        savingColumn={savingColumn}
        setEditColAlias={setEditColAlias}
        setEditColDesc={setEditColDesc}
        setEditColIndexValues={setEditColIndexValues}
        setEditColRefColumn={setEditColRefColumn}
        setEditColRefTable={setEditColRefTable}
      />

      <div className="mt-4 rounded border border-[#d4d4ce]">
        {!selectedTable ? (
          <div className="py-12 text-center text-xs text-[#71717a]">
            请在上方选择一个数据表以查看和配置其字段元数据
          </div>
        ) : columns.length === 0 ? (
          <div className="py-12 text-center text-xs text-[#71717a]">
            该表暂无字段元数据，可点击右上角“添加字段”进行创建
          </div>
        ) : (
          <div className="max-h-[410px] overflow-auto">
            <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[18%]" />
                <col className="w-[28%]" />
                <col className="w-[18%]" />
                <col className="w-[16%]" />
                <col className="w-[140px]" />
                <col className="w-[84px]" />
              </colgroup>
              <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                <tr>
                  <th className="w-[44px] px-3.5 py-2.5 bg-[#f4f4f0] text-center">
                    <input
                      type="checkbox"
                      aria-label="全选字段"
                      checked={columns.length > 0 && selectedColumnNames.length === columns.length}
                      ref={(el) => {
                        if (el) {
                          el.indeterminate =
                            selectedColumnNames.length > 0 &&
                            selectedColumnNames.length < columns.length;
                        }
                      }}
                      onChange={(e) => {
                        if (e.target.checked) {
                          onSelectAllColumns(columns.map((c) => c.name));
                        } else {
                          onSelectAllColumns([]);
                        }
                      }}
                      className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                    />
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    字段名称
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    业务描述
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    同义别名
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    引用关系
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    取值索引
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap text-right bg-[#f4f4f0]">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#f0f0eb]">
                {columns.map((col) => {
                  const isChecked = selectedColumnNames.includes(col.name);
                  return (
                    <tr
                      key={col.name}
                      className={`hover:bg-[#fafaf8] transition-colors ${
                        isChecked ? "bg-[#fafaf8]" : ""
                      }`}
                    >
                      <td className="px-3.5 py-2.5 align-middle text-center">
                        <input
                          type="checkbox"
                          aria-label={`选择字段 ${col.name}`}
                          checked={isChecked}
                          onChange={() => onToggleSelectColumn(col.name)}
                          className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                        />
                      </td>
                      <td className="px-3.5 py-2.5 align-middle font-semibold text-[#18181b]">
                        <span className="truncate block" title={col.name}>
                          {col.name}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-[#71717a] text-xs break-words">
                        <span className="line-clamp-2 leading-relaxed" title={col.description}>
                          {col.description}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-xs">
                        {col.alias?.length ? (
                          <div className="flex flex-wrap gap-1">
                            {col.alias.map((a) => (
                              <span
                                key={a}
                                className="rounded bg-[#f0f0eb] px-1.5 py-0.5 text-[10px] text-[#52525b]"
                              >
                                {a}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-[#a1a1aa]">-</span>
                        )}
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-xs text-[#71717a]">
                        {col.reference_t_name ? (
                          <span
                            className="font-mono text-[11px] truncate block"
                            title={`${col.reference_t_name}.${col.reference_c_name || ""}`}
                          >
                            {col.reference_t_name}.{col.reference_c_name}
                          </span>
                        ) : (
                          <span className="text-[#a1a1aa]">-</span>
                        )}
                      </td>
                      <td className="px-3.5 py-2.5 align-middle">
                        <ValueIndexStatus column={col} />
                      </td>
                      <td className="px-3.5 py-2.5 align-middle text-right whitespace-nowrap">
                        <div className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setIsCreatingColumn(false);
                              setEditingColumn(col);
                              setEditColDesc(col.description);
                              setEditColAlias(col.alias?.join(", ") || "");
                              setEditColIndexValues(col.index_values);
                              setEditColRefTable(col.reference_t_name || "");
                              setEditColRefColumn(col.reference_c_name || "");
                            }}
                            className="h-7 px-2 text-xs"
                            title={`编辑字段 ${col.name}`}
                          >
                            <Edit2 className="h-3 w-3" />
                            <span className="sr-only">编辑字段 {col.name}</span>
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={deletingColumn === col.name}
                            onClick={() => void handleDeleteColumn(col.name)}
                            className="h-7 px-2 text-xs"
                            title={`删除字段 ${col.name}`}
                          >
                            <Trash2 className="h-3 w-3" />
                            <span className="sr-only">删除字段 {col.name}</span>
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
