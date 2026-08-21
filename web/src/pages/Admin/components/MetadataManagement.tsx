import { useCallback, useEffect, useState } from "react";
import {
  Download,
  Upload,
  RefreshCw,
  Database,
  Layers,
  BarChart3,
  Edit2,
  Trash2,
  Check,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  metaApi,
  type TableInfo,
  type ColumnInfo,
  type MetricInfo,
  type MetaImportResponse,
} from "@/api/meta";
import { Button } from "@/components/ui/button";

function errorMessage(error: unknown): string {
  return (
    (error as { response?: { data?: { detail?: string; title?: string } } }).response?.data
      ?.detail ?? "操作失败，请检查元数据配置"
  );
}

export function MetadataManagement() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>("");
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [metrics, setMetrics] = useState<MetricInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState<string | null>(null);

  // 导入导出状态
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importMode, setImportMode] = useState<"merge" | "overwrite">("merge");
  const [dryRun, setDryRun] = useState(false);
  const [importResult, setImportResult] = useState<MetaImportResponse | null>(null);

  // 表编辑状态
  const [editingTable, setEditingTable] = useState<TableInfo | null>(null);
  const [editTableRole, setEditTableRole] = useState("fact");
  const [editTableDesc, setEditTableDesc] = useState("");

  // 字段编辑状态
  const [editingColumn, setEditingColumn] = useState<ColumnInfo | null>(null);
  const [editColDesc, setEditColDesc] = useState("");
  const [editColAlias, setEditColAlias] = useState("");
  const [editColIndexValues, setEditColIndexValues] = useState(false);
  const [editColRefTable, setEditColRefTable] = useState("");
  const [editColRefColumn, setEditColRefColumn] = useState("");

  // 指标创建状态
  const [newMetricName, setNewMetricName] = useState("");
  const [newMetricDesc, setNewMetricDesc] = useState("");
  const [newMetricColumns, setNewMetricColumns] = useState("");
  const [newMetricAlias, setNewMetricAlias] = useState("");

  const loadData = useCallback(async () => {
    setBusy(true);
    try {
      const [loadedTables, loadedMetrics] = await Promise.all([
        metaApi.listTables(),
        metaApi.listMetrics(),
      ]);
      setTables(loadedTables);
      setMetrics(loadedMetrics);
      if (loadedTables.length > 0) {
        setSelectedTable((current) =>
          loadedTables.some((t) => t.name === current) ? current : loadedTables[0].name
        );
      } else {
        setSelectedTable("");
        setColumns([]);
      }
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, []);

  const loadColumns = useCallback(async (tableName: string) => {
    if (!tableName) {
      setColumns([]);
      return;
    }
    try {
      const cols = await metaApi.listColumns(tableName);
      setColumns(cols);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (selectedTable) {
      void loadColumns(selectedTable);
    }
  }, [selectedTable, loadColumns]);

  // 导出 YAML
  const handleExport = async () => {
    setBusy(true);
    try {
      const blob = await metaApi.exportMetadata();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `metadata_${Date.now()}.yaml`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success("元数据 YAML 已导出并下载");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // 导入 YAML
  const handleImport = async () => {
    if (!importFile) {
      toast.error("请先选择 YAML 文件");
      return;
    }
    setBusy(true);
    try {
      const result = await metaApi.importMetadata(importFile, importMode, dryRun);
      setImportResult(result);
      if (dryRun) {
        toast.info("变更预览完成，未写入数据库");
      } else {
        toast.success("元数据导入并同步成功");
        setImportFile(null);
        await loadData();
      }
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // 同步所有字段向量索引
  const handleSyncColumnIndexes = async () => {
    if (columns.length === 0) return;
    setSyncing("col_vector");
    try {
      const colRefs = columns.map((col) => ({ t_name: col.t_name, c_name: col.name }));
      const res = await metaApi.syncColumnIndexes(colRefs);
      toast.success(`字段向量索引同步完成，更新 ${res.length} 个字段`);
      await loadColumns(selectedTable);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSyncing(null);
    }
  };

  // 同步所有字段取值索引
  const handleSyncColumnValues = async () => {
    if (columns.length === 0) return;
    setSyncing("col_values");
    try {
      const colRefs = columns.map((col) => ({ t_name: col.t_name, c_name: col.name }));
      const res = await metaApi.syncColumnValues(colRefs);
      toast.success(`字段枚举取值索引同步完成，更新 ${res.length} 个字段`);
      await loadColumns(selectedTable);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSyncing(null);
    }
  };

  // 同步指标向量索引
  const handleSyncMetricIndexes = async () => {
    if (metrics.length === 0) return;
    setSyncing("metric_vector");
    try {
      const metricNames = metrics.map((m) => m.name);
      const res = await metaApi.syncMetricIndexes(metricNames);
      toast.success(`指标向量索引同步完成，更新 ${res.length} 个指标`);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSyncing(null);
    }
  };

  // 保存表编辑
  const handleSaveTable = async () => {
    if (!editingTable) return;
    setBusy(true);
    try {
      await metaApi.upsertTable(editingTable.name, {
        role: editTableRole,
        description: editTableDesc.trim(),
      });
      toast.success(`表 ${editingTable.name} 元数据已更新`);
      setEditingTable(null);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // 保存字段编辑
  const handleSaveColumn = async () => {
    if (!editingColumn) return;
    setBusy(true);
    try {
      const aliases = editColAlias
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
      await metaApi.upsertColumn(editingColumn.t_name, editingColumn.name, {
        description: editColDesc.trim(),
        alias: aliases,
        index_values: editColIndexValues,
        reference_t_name: editColRefTable.trim() || null,
        reference_c_name: editColRefColumn.trim() || null,
      });
      toast.success(`字段 ${editingColumn.name} 元数据已更新`);
      setEditingColumn(null);
      await loadColumns(editingColumn.t_name);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // 保存新指标
  const handleCreateMetric = async () => {
    if (!newMetricName.trim() || !newMetricDesc.trim()) return;
    setBusy(true);
    try {
      const aliases = newMetricAlias
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
      const colPairs = newMetricColumns
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean)
        .map((c) => {
          const [t_name, c_name] = c.split(".");
          return { t_name: t_name || "", c_name: c_name || "" };
        })
        .filter((c) => c.t_name && c.c_name);

      await metaApi.upsertMetric(newMetricName.trim(), {
        description: newMetricDesc.trim(),
        relevant_columns: colPairs,
        alias: aliases,
      });
      toast.success(`指标 ${newMetricName.trim()} 已保存并同步`);
      setNewMetricName("");
      setNewMetricDesc("");
      setNewMetricColumns("");
      setNewMetricAlias("");
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // 删除指标
  const handleDeleteMetric = async (metricName: string) => {
    if (!window.confirm(`确定要删除指标 "${metricName}" 吗？`)) return;
    setBusy(true);
    try {
      await metaApi.deleteMetric(metricName);
      toast.success(`指标 ${metricName} 已删除`);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 元数据导入导出与索引维护控制台 */}
      <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#e5e5df] pb-3">
          <h2 className="text-base font-bold text-[#18181b]">元数据 YAML 导入导出与索引同步</h2>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void handleExport()}
              className="text-xs"
            >
              <Download className="h-3.5 w-3.5 mr-1" />
              导出 YAML 元数据
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || syncing !== null || !selectedTable}
              onClick={() => void handleSyncColumnIndexes()}
              className="text-xs"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 mr-1 ${syncing === "col_vector" ? "animate-spin" : ""}`}
              />
              同步当前表字段向量
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || syncing !== null || !selectedTable}
              onClick={() => void handleSyncColumnValues()}
              className="text-xs"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 mr-1 ${syncing === "col_values" ? "animate-spin" : ""}`}
              />
              同步当前表取值索引
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || syncing !== null}
              onClick={() => void handleSyncMetricIndexes()}
              className="text-xs"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 mr-1 ${syncing === "metric_vector" ? "animate-spin" : ""}`}
              />
              同步全部指标向量
            </Button>
          </div>
        </div>

        {/* 导入操作区 */}
        <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-4 text-sm">
          <p className="font-semibold text-[#18181b] mb-2">批量导入元数据 (YAML 配置文件)</p>
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="file"
              accept=".yaml,.yml"
              onChange={(e) => setImportFile(e.target.files?.[0] || null)}
              className="text-xs text-[#52525b] file:mr-2 file:rounded file:border file:border-[#d4d4ce] file:bg-[#ffffff] file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-[#18181b] hover:file:bg-[#ebebe6]"
            />
            <div className="flex items-center gap-2 text-xs">
              <label htmlFor="metadata-import-mode" className="text-[#71717a]">
                模式：
              </label>
              <select
                id="metadata-import-mode"
                value={importMode}
                onChange={(e) => setImportMode(e.target.value as "merge" | "overwrite")}
                className="h-8 rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024]"
              >
                <option value="merge">增量合并 (Merge)</option>
                <option value="overwrite">全量覆写 (Overwrite)</option>
              </select>
            </div>
            <label className="flex items-center gap-1.5 cursor-pointer text-xs text-[#52525b]">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)}
                className="h-4 w-4 rounded accent-[#1e2024]"
              />
              <span>仅预览变更 (dry-run)</span>
            </label>
            <Button
              size="sm"
              disabled={busy || !importFile}
              onClick={() => void handleImport()}
              className="text-xs"
            >
              <Upload className="h-3.5 w-3.5 mr-1" />
              {dryRun ? "预览导入变更" : "执行导入"}
            </Button>
          </div>

          {importResult && (
            <div className="mt-3 rounded border border-[#d4d4ce] bg-[#ffffff] p-3 text-xs">
              <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-2">
                <span>导入执行结果 ({importResult.dry_run ? "预览模式" : "已写入"})</span>
                <button
                  type="button"
                  onClick={() => setImportResult(null)}
                  className="text-[#71717a] hover:text-[#18181b]"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded bg-[#fafaf8] p-2 border border-[#ebebe6]">
                  <span className="font-semibold text-[#18181b]">数据表：</span>
                  <span className="text-[#166534] ml-1">+{importResult.tables.created_count}</span>{" "}
                  /<span className="text-[#854d0e] ml-1">~{importResult.tables.updated_count}</span>{" "}
                  /<span className="text-[#991b1b] ml-1">-{importResult.tables.deleted_count}</span>
                </div>
                <div className="rounded bg-[#fafaf8] p-2 border border-[#ebebe6]">
                  <span className="font-semibold text-[#18181b]">数据字段：</span>
                  <span className="text-[#166534] ml-1">+{importResult.columns.created_count}</span>{" "}
                  /
                  <span className="text-[#854d0e] ml-1">~{importResult.columns.updated_count}</span>{" "}
                  /
                  <span className="text-[#991b1b] ml-1">-{importResult.columns.deleted_count}</span>
                </div>
                <div className="rounded bg-[#fafaf8] p-2 border border-[#ebebe6]">
                  <span className="font-semibold text-[#18181b]">业务指标：</span>
                  <span className="text-[#166534] ml-1">+{importResult.metrics.created_count}</span>{" "}
                  /
                  <span className="text-[#854d0e] ml-1">~{importResult.metrics.updated_count}</span>{" "}
                  /
                  <span className="text-[#991b1b] ml-1">-{importResult.metrics.deleted_count}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* 数据表与字段元数据列表 */}
      <section className="grid gap-6 lg:grid-cols-12">
        {/* 左侧：表列表 */}
        <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs lg:col-span-4">
          <div className="flex items-center justify-between border-b border-[#e5e5df] pb-3">
            <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
              <Database className="h-4 w-4 text-[#52525b]" />
              <span>数据表目录 ({tables.length})</span>
            </h2>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void loadData()}
              className="h-7 text-xs"
            >
              <RefreshCw className={`h-3 w-3 ${busy ? "animate-spin" : ""}`} />
            </Button>
          </div>

          <div className="mt-3 space-y-2 max-h-[600px] overflow-y-auto">
            {tables.map((table) => {
              const isSelected = table.name === selectedTable;
              return (
                <div
                  key={table.name}
                  className={`rounded border p-3 text-xs transition-colors ${
                    isSelected
                      ? "border-[#1e3a8a] bg-[#1e3a8a] text-[#ffffff]"
                      : "border-[#e5e5df] bg-[#fafaf8] text-[#1e2024] hover:bg-[#ebebe6]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setSelectedTable(table.name)}
                    className="block w-full cursor-pointer text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-sm">{table.name}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                          isSelected ? "bg-[#3b82f6] text-[#ffffff]" : "bg-[#e5e5df] text-[#52525b]"
                        }`}
                      >
                        {table.role}
                      </span>
                    </div>
                    <p
                      className={`mt-1 line-clamp-2 text-xs ${isSelected ? "text-[#bfdbfe]" : "text-[#71717a]"}`}
                    >
                      {table.description || "暂无表描述"}
                    </p>
                  </button>
                  <div className="mt-2 flex items-center justify-between border-t border-white/20 pt-1.5 text-[11px]">
                    <span className={isSelected ? "text-[#bfdbfe]" : "text-[#a1a1aa]"}>
                      主键: {table.primary_key_columns?.join(", ") || "-"}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingTable(table);
                        setEditTableRole(table.role);
                        setEditTableDesc(table.description);
                      }}
                      className={`hover:underline flex items-center gap-1 ${
                        isSelected ? "text-white" : "text-[#18181b]"
                      }`}
                    >
                      <Edit2 className="h-3 w-3" />
                      编辑
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* 表编辑弹窗/表单 */}
          {editingTable && (
            <div className="mt-4 rounded border border-[#1e3a8a] bg-[#ffffff] p-3 text-xs shadow-sm">
              <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1 mb-2">
                <span>编辑表元数据: {editingTable.name}</span>
                <button type="button" onClick={() => setEditingTable(null)}>
                  <X className="h-3.5 w-3.5 text-[#71717a]" />
                </button>
              </div>
              <div className="space-y-2">
                <div>
                  <label
                    htmlFor="metadata-table-role"
                    className="block text-[11px] text-[#71717a] mb-1"
                  >
                    表角色：
                  </label>
                  <select
                    id="metadata-table-role"
                    value={editTableRole}
                    onChange={(e) => setEditTableRole(e.target.value)}
                    className="h-8 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-2 text-xs"
                  >
                    <option value="fact">事实表 (fact)</option>
                    <option value="dimension">维度表 (dimension)</option>
                    <option value="aggregate">聚合表 (aggregate)</option>
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="metadata-table-description"
                    className="block text-[11px] text-[#71717a] mb-1"
                  >
                    表描述：
                  </label>
                  <textarea
                    id="metadata-table-description"
                    value={editTableDesc}
                    onChange={(e) => setEditTableDesc(e.target.value)}
                    rows={3}
                    className="w-full rounded border border-[#d4d4ce] bg-[#fafaf8] p-2 text-xs"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-1">
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
                    disabled={busy || !editTableDesc.trim()}
                    onClick={() => void handleSaveTable()}
                    className="h-7 text-xs"
                  >
                    保存
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 右侧：字段列表 */}
        <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs lg:col-span-8">
          <div className="flex items-center justify-between border-b border-[#e5e5df] pb-3">
            <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
              <Layers className="h-4 w-4 text-[#52525b]" />
              <span>
                表 [{selectedTable || "未选择"}] 字段明细 ({columns.length})
              </span>
            </h2>
          </div>

          <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
            <table className="w-full min-w-[700px] text-left text-sm font-mono">
              <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                <tr>
                  <th className="px-3 py-2 text-xs">字段名称 / 类型</th>
                  <th className="px-3 py-2 text-xs">描述与别名</th>
                  <th className="px-3 py-2 text-xs">取值索引</th>
                  <th className="px-3 py-2 text-xs">关联引用</th>
                  <th className="px-3 py-2 text-xs text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((col) => (
                  <tr key={col.name} className="border-b border-[#f0f0eb] hover:bg-[#fafaf8]">
                    <td className="px-3 py-2">
                      <div className="font-semibold text-[#18181b] text-xs">{col.name}</div>
                      <div className="text-[11px] text-[#71717a] font-mono">{col.type}</div>
                    </td>
                    <td className="px-3 py-2 text-xs max-w-xs">
                      <div className="text-[#27272a]">{col.description || "-"}</div>
                      {col.alias && col.alias.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {col.alias.map((a) => (
                            <span
                              key={a}
                              className="rounded bg-[#deded8] px-1 py-0.5 text-[10px] text-[#52525b]"
                            >
                              {a}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            col.index_values
                              ? "bg-[#1e3a8a] text-[#ffffff]"
                              : "bg-[#e5e5df] text-[#71717a]"
                          }`}
                        >
                          {col.index_values ? "已开启" : "未开启"}
                        </span>
                        {col.value_index_sync_status && (
                          <span className="text-[10px] text-[#71717a]">
                            ({col.value_index_sync_status})
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs text-[#71717a]">
                      {col.reference_t_name && col.reference_c_name ? (
                        <span className="rounded bg-[#ebebe6] px-1.5 py-0.5 text-[11px] font-mono text-[#27272a]">
                          {col.reference_t_name}.{col.reference_c_name}
                        </span>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setEditingColumn(col);
                          setEditColDesc(col.description);
                          setEditColAlias(col.alias?.join(", ") || "");
                          setEditColIndexValues(col.index_values);
                          setEditColRefTable(col.reference_t_name || "");
                          setEditColRefColumn(col.reference_c_name || "");
                        }}
                        className="h-7 px-2 text-xs"
                      >
                        <Edit2 className="h-3 w-3 mr-1" />
                        编辑
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 字段编辑抽屉/表单 */}
          {editingColumn && (
            <div className="mt-4 rounded border border-[#1e3a8a] bg-[#fafaf8] p-4 text-xs shadow-sm">
              <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
                <span>
                  编辑字段元数据: {editingColumn.t_name}.{editingColumn.name}
                </span>
                <button type="button" onClick={() => setEditingColumn(null)}>
                  <X className="h-4 w-4 text-[#71717a]" />
                </button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <label
                    htmlFor="metadata-column-description"
                    className="block text-[11px] text-[#71717a] mb-1"
                  >
                    字段语义描述：
                  </label>
                  <input
                    id="metadata-column-description"
                    value={editColDesc}
                    onChange={(e) => setEditColDesc(e.target.value)}
                    placeholder="字段语义说明"
                    className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024]"
                  />
                </div>
                <div>
                  <label
                    htmlFor="metadata-column-aliases"
                    className="block text-[11px] text-[#71717a] mb-1"
                  >
                    同义别名（逗号分隔）：
                  </label>
                  <input
                    id="metadata-column-aliases"
                    value={editColAlias}
                    onChange={(e) => setEditColAlias(e.target.value)}
                    placeholder="别名1, 别名2"
                    className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024]"
                  />
                </div>
                <div className="flex items-center pt-4">
                  <label className="flex items-center gap-1.5 cursor-pointer text-xs text-[#18181b]">
                    <input
                      type="checkbox"
                      checked={editColIndexValues}
                      onChange={(e) => setEditColIndexValues(e.target.checked)}
                      className="h-4 w-4 rounded accent-[#1e2024]"
                    />
                    <span>开启枚举值语义索引 (index_values)</span>
                  </label>
                </div>
                <div>
                  <label
                    htmlFor="metadata-reference-table"
                    className="block text-[11px] text-[#71717a] mb-1"
                  >
                    外键关联表名（可选）：
                  </label>
                  <input
                    id="metadata-reference-table"
                    value={editColRefTable}
                    onChange={(e) => setEditColRefTable(e.target.value)}
                    placeholder="如：dim_user"
                    className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024]"
                  />
                </div>
                <div>
                  <label
                    htmlFor="metadata-reference-column"
                    className="block text-[11px] text-[#71717a] mb-1"
                  >
                    外键关联字段名（可选）：
                  </label>
                  <input
                    id="metadata-reference-column"
                    value={editColRefColumn}
                    onChange={(e) => setEditColRefColumn(e.target.value)}
                    placeholder="如：user_id"
                    className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024]"
                  />
                </div>
              </div>
              <div className="mt-3 flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditingColumn(null)}
                  className="h-8 text-xs"
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  disabled={busy || !editColDesc.trim()}
                  onClick={() => void handleSaveColumn()}
                  className="h-8 text-xs"
                >
                  <Check className="h-3.5 w-3.5 mr-1" />
                  保存字段元数据
                </Button>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* 业务指标元数据管理 */}
      <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
        <h2 className="flex items-center gap-1.5 border-b border-[#e5e5df] pb-3 text-base font-bold text-[#18181b]">
          <BarChart3 className="h-4 w-4 text-[#52525b]" />
          <span>业务指标元数据管理 ({metrics.length})</span>
        </h2>

        {/* 新增指标输入行 */}
        <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-3 text-sm">
          <p className="mb-2 font-medium text-[#71717a]">创建新业务指标</p>
          <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4">
            <input
              value={newMetricName}
              onChange={(e) => setNewMetricName(e.target.value)}
              placeholder="指标名称 (如: gmv_total)"
              className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
            <input
              value={newMetricColumns}
              onChange={(e) => setNewMetricColumns(e.target.value)}
              placeholder="关联字段 (如: ods_orders.pay_amount)"
              className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
            <input
              value={newMetricAlias}
              onChange={(e) => setNewMetricAlias(e.target.value)}
              placeholder="同义词别名 (逗号分隔)"
              className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
            <input
              value={newMetricDesc}
              onChange={(e) => setNewMetricDesc(e.target.value)}
              placeholder="指标业务口径说明"
              className="h-9 rounded border border-[#d4d4ce] bg-[#ffffff] px-3 text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
          </div>
          <div className="mt-2.5 flex justify-end">
            <Button
              size="sm"
              disabled={busy || !newMetricName.trim() || !newMetricDesc.trim()}
              onClick={() => void handleCreateMetric()}
            >
              创建并同步指标
            </Button>
          </div>
        </div>

        {/* 指标列表表格 */}
        <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[700px] text-left text-sm font-mono">
            <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-3.5 py-2.5">指标名称</th>
                <th className="px-3.5 py-2.5">业务口径说明</th>
                <th className="px-3.5 py-2.5">关联数据列</th>
                <th className="px-3.5 py-2.5">同义别名</th>
                <th className="px-3.5 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric) => (
                <tr key={metric.name} className="border-b border-[#f0f0eb] hover:bg-[#fafaf8]">
                  <td className="px-3.5 py-2.5 font-semibold text-[#18181b]">{metric.name}</td>
                  <td className="px-3.5 py-2.5 text-xs text-[#27272a] max-w-sm">
                    {metric.description}
                  </td>
                  <td className="px-3.5 py-2.5 text-xs">
                    <div className="flex flex-wrap gap-1">
                      {metric.relevant_columns?.map((c) => (
                        <span
                          key={`${c.t_name}.${c.c_name}`}
                          className="rounded bg-[#ebebe6] px-1.5 py-0.5 text-[10px] font-mono text-[#27272a]"
                        >
                          {c.t_name}.{c.c_name}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 text-xs">
                    <div className="flex flex-wrap gap-1">
                      {metric.alias?.map((a) => (
                        <span
                          key={a}
                          className="rounded bg-[#deded8] px-1.5 py-0.5 text-[10px] text-[#52525b]"
                        >
                          {a}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 text-right">
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={busy}
                      onClick={() => void handleDeleteMetric(metric.name)}
                      className="h-8 px-2.5 text-xs"
                    >
                      <Trash2 className="h-3 w-3 mr-1" />
                      删除
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
