import { Check, Edit2, Layers, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { type ColumnInfo, metaApi } from "@/api/meta";
import { Button } from "@/components/ui/button";
import { formatDateTime, splitCsv } from "./utils";

interface ColumnSectionProps {
  selectedTable: string | null;
  columns: ColumnInfo[];
  selectedColumnNames: string[];
  onToggleSelectColumn: (colName: string) => void;
  onSelectAllColumns: (colNames: string[]) => void;
  loadingColumns: boolean;
  syncing: string | null;
  onSyncColumnIndexes: () => Promise<void>;
  onSyncColumnValues: () => Promise<void>;
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
      `确认批量删除选中的 ${selectedColumnNames.length} 个字段吗？\n此操作将同时删除对应的语义索引与枚举取值索引。`
    );
    if (!confirmed) return;
    setIsBatchDeleting(true);
    try {
      await metaApi.deleteColumns(
        selectedColumnNames.map((cName) => ({
          t_name: selectedTable,
          c_name: cName,
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
            <Layers className="h-4 w-4 text-[#52525b]" />
            <span>
              表字段元数据 [{selectedTable || "未选择"}] ({columns.length})
            </span>
            {loadingColumns && <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />}
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
                <RefreshCw
                  className={`h-3.5 w-3.5 mr-1 ${syncing === "col_semantic" ? "animate-spin" : ""}`}
                />
                {selectedColumnNames.length > 0
                  ? `同步字段语义索引 (${selectedColumnNames.length})`
                  : "同步字段语义索引"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={syncing !== null || selectedColumnNames.length === 0}
                onClick={() => void onSyncColumnValues()}
                className="h-7 text-xs"
                title={
                  selectedColumnNames.length === 0
                    ? "请先勾选需要同步取值索引的字段"
                    : `同步已选 ${selectedColumnNames.length} 个字段枚举取值`
                }
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 mr-1 ${syncing === "col_values" ? "animate-spin" : ""}`}
                />
                {selectedColumnNames.length > 0
                  ? `同步取值索引 (${selectedColumnNames.length})`
                  : "同步取值索引"}
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

      {isCreatingColumn && (
        <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
          <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
            <span>添加字段元数据: {selectedTable}</span>
            <button
              type="button"
              onClick={() => setIsCreatingColumn(false)}
              className="text-[#71717a] hover:text-[#18181b]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-3">
            <div>
              <label
                htmlFor="new-col-name"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                字段名称
              </label>
              <input
                id="new-col-name"
                value={newColName}
                onChange={(e) => setNewColName(e.target.value)}
                placeholder="如：order_id"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div>
              <label
                htmlFor="new-col-desc"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                字段描述
              </label>
              <textarea
                id="new-col-desc"
                value={newColDesc}
                onChange={(e) => setNewColDesc(e.target.value)}
                placeholder="字段业务含义说明"
                rows={2}
                className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div>
              <label
                htmlFor="new-col-alias"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                同义别名（逗号分隔）
              </label>
              <input
                id="new-col-alias"
                value={newColAlias}
                onChange={(e) => setNewColAlias(e.target.value)}
                placeholder="别名1, 别名2"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label
                  htmlFor="new-col-ref-table"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  关联引用表
                </label>
                <input
                  id="new-col-ref-table"
                  value={newColRefTable}
                  onChange={(e) => setNewColRefTable(e.target.value)}
                  placeholder="如：dim_user"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="new-col-ref-column"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  关联引用列
                </label>
                <input
                  id="new-col-ref-column"
                  value={newColRefColumn}
                  onChange={(e) => setNewColRefColumn(e.target.value)}
                  placeholder="如：id"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
            </div>
            <div className="flex items-center gap-2 pt-1">
              <input
                type="checkbox"
                id="new-col-index-values"
                checked={newColIndexValues}
                onChange={(e) => setNewColIndexValues(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-[#d4d4ce] text-[#1e2024] focus:ring-[#1e2024]"
              />
              <label
                htmlFor="new-col-index-values"
                className="text-xs font-medium text-[#18181b] cursor-pointer"
              >
                开启取值索引
              </label>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setIsCreatingColumn(false)}
                className="h-7 text-xs"
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={savingColumn || !newColName.trim() || !newColDesc.trim()}
                onClick={() => void handleCreateColumn()}
                className="h-7 text-xs"
              >
                <Check className="h-3.5 w-3.5 mr-1" />
                {savingColumn ? "正在添加..." : "确认添加字段"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {editingColumn && (
        <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
          <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
            <span>编辑字段元数据: {editingColumn.name}</span>
            <button
              type="button"
              onClick={() => setEditingColumn(null)}
              className="text-[#71717a] hover:text-[#18181b]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-3">
            <div>
              <label
                htmlFor="edit-col-desc"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                字段描述
              </label>
              <textarea
                id="edit-col-desc"
                value={editColDesc}
                onChange={(e) => setEditColDesc(e.target.value)}
                placeholder="字段业务含义说明"
                rows={2}
                className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div>
              <label
                htmlFor="edit-col-alias"
                className="block text-xs font-medium text-[#71717a] mb-1"
              >
                同义别名（逗号分隔）
              </label>
              <input
                id="edit-col-alias"
                value={editColAlias}
                onChange={(e) => setEditColAlias(e.target.value)}
                placeholder="别名1, 别名2"
                className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label
                  htmlFor="edit-col-ref-table"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  关联引用表
                </label>
                <input
                  id="edit-col-ref-table"
                  value={editColRefTable}
                  onChange={(e) => setEditColRefTable(e.target.value)}
                  placeholder="如：dim_user"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="edit-col-ref-column"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  关联引用列
                </label>
                <input
                  id="edit-col-ref-column"
                  value={editColRefColumn}
                  onChange={(e) => setEditColRefColumn(e.target.value)}
                  placeholder="如：id"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
            </div>
            <div className="flex items-center gap-2 pt-1">
              <input
                type="checkbox"
                id="edit-col-index-values"
                checked={editColIndexValues}
                onChange={(e) => setEditColIndexValues(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-[#d4d4ce] text-[#1e2024] focus:ring-[#1e2024]"
              />
              <label
                htmlFor="edit-col-index-values"
                className="text-xs font-medium text-[#18181b] cursor-pointer"
              >
                开启取值索引
              </label>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditingColumn(null)}
                className="h-7 text-xs"
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={savingColumn || !editColDesc.trim()}
                onClick={() => void handleSaveColumn()}
                className="h-7 text-xs"
              >
                <Check className="h-3.5 w-3.5 mr-1" />
                {savingColumn ? "保存中..." : "保存字段元数据"}
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className="mt-4 rounded border border-[#d4d4ce]">
        {columns.length === 0 ? (
          <div className="py-12 text-center text-xs text-[#71717a]">
            {selectedTable ? "该表暂未配置任何字段元数据" : "请先选择数据表"}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[20%]" />
                <col className="w-[25%]" />
                <col className="w-[25%]" />
                <col className="w-[120px]" />
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
                    字段名称 / 类型
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    描述与别名
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    关联引用
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    语义索引
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
                  const isSelected = selectedColumnNames.includes(col.name);
                  return (
                    <tr
                      key={col.name}
                      className={`hover:bg-[#fafaf8] transition-colors ${
                        isSelected ? "bg-[#f4f4f0]/60" : ""
                      }`}
                    >
                      <td className="px-3.5 py-2.5 align-top text-center">
                        <input
                          type="checkbox"
                          aria-label={`选择字段 ${col.name}`}
                          checked={isSelected}
                          onChange={() => onToggleSelectColumn(col.name)}
                          className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                        />
                      </td>
                      <td className="px-3.5 py-2.5 align-top">
                        <div className="font-semibold text-[#18181b] break-all leading-tight">
                          {col.name}
                        </div>
                        <div className="text-[10px] text-[#71717a] font-mono mt-0.5 break-all">
                          {col.type || "-"}
                        </div>
                      </td>
                      <td className="px-3.5 py-2.5 align-top text-xs">
                        <div className="text-[#27272a] leading-relaxed break-words">
                          {col.description || "-"}
                        </div>
                        {col.alias && col.alias.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {col.alias.map((a) => (
                              <span
                                key={a}
                                className="rounded bg-[#deded8] px-1.5 py-0.5 text-[10px] text-[#52525b] whitespace-nowrap"
                              >
                                {a}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-3.5 py-2.5 align-top">
                        {col.reference_t_name && col.reference_c_name ? (
                          <span className="inline-block max-w-full rounded bg-[#ebebe6] px-1.5 py-0.5 text-[11px] font-mono text-[#27272a] break-all leading-tight">
                            <span className="text-[#52525b]">{col.reference_t_name}</span>
                            <span className="font-bold text-[#18181b] mx-0.5 text-xs">.</span>
                            <span className="font-semibold text-[#18181b]">
                              {col.reference_c_name}
                            </span>
                          </span>
                        ) : (
                          <span className="text-[#a1a1aa]">-</span>
                        )}
                      </td>
                      <td className="px-3.5 py-2.5 align-top">
                        <div className="flex flex-col gap-1 items-start">
                          {col.index_version === col.meta_version && col.meta_version > 0 ? (
                            <span
                              className="inline-flex items-center rounded bg-[#1e2024] text-[#ffffff] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap font-mono"
                              title={`语义索引版本与数据版本一致 (v${col.meta_version})`}
                            >
                              已同步 (v{col.meta_version})
                            </span>
                          ) : (
                            <span
                              className="inline-flex items-center rounded bg-[#e5e5df] text-[#71717a] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap font-mono"
                              title={`语义索引版本 v${col.index_version} 落后于数据版本 v${col.meta_version}`}
                            >
                              待同步 (v{col.index_version}/v{col.meta_version})
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3.5 py-2.5 align-top">
                        <div className="flex flex-col gap-1 items-start">
                          <span
                            className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap ${
                              col.index_values
                                ? "bg-[#1e2024] text-[#ffffff]"
                                : "bg-[#e5e5df] text-[#71717a]"
                            }`}
                          >
                            {col.index_values ? "已开启" : "未开启"}
                          </span>
                          {col.index_values &&
                            (col.value_index_state?.status === "syncing" ? (
                              <span className="text-[10px] text-[#71717a] font-mono whitespace-nowrap animate-pulse">
                                正在同步...
                              </span>
                            ) : col.value_index_state?.status === "failed" ? (
                              <div className="flex flex-col items-start">
                                <span
                                  className="text-[10px] text-[#71717a] font-mono whitespace-nowrap font-medium"
                                  title={col.value_index_state.last_error || "取值索引同步失败"}
                                >
                                  同步失败
                                </span>
                                {col.value_index_state.last_synced_at && (
                                  <span
                                    className="text-[9px] text-[#a1a1aa] font-mono whitespace-nowrap"
                                    title="上次成功同步时刻"
                                  >
                                    {formatDateTime(col.value_index_state.last_synced_at)}
                                  </span>
                                )}
                              </div>
                            ) : col.value_index_state?.status === "succeeded" ? (
                              <span
                                className="text-[10px] text-[#71717a] font-mono whitespace-nowrap leading-tight"
                                title={`同步代次: ${col.value_index_state.current_generation || "无"}`}
                              >
                                {formatDateTime(col.value_index_state.last_synced_at)}
                              </span>
                            ) : (
                              <span className="text-[10px] text-[#a1a1aa] font-mono whitespace-nowrap">
                                未同步
                              </span>
                            ))}
                        </div>
                      </td>
                      <td className="px-3.5 py-2.5 align-top text-right whitespace-nowrap">
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
