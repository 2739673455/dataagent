import { useCallback, useEffect, useMemo, useState } from "react";
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
  Plus,
  Search,
  Eye,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import {
  metaApi,
  type TableInfo,
  type TableRole,
  type ColumnInfo,
  type MetricInfo,
  type ImportMode,
  type MetaImportResponse,
} from "@/api/meta";
import axios from "axios";
import { Button } from "@/components/ui/button";

function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.code === "ECONNABORTED" || error.message.includes("timeout")) {
      return "请求超时，后台可能仍在处理中，请稍后刷新页面查看";
    }
    return (
      (error.response?.data as { detail?: string; title?: string } | undefined)?.detail ??
      error.message ??
      "操作失败，请检查元数据配置"
    );
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "操作失败，请检查元数据配置";
}

interface DiffItem {
  type: "table" | "column" | "metric";
  action: "create" | "update" | "delete";
  key: string;
}

export function MetadataManagement() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>("");
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [metrics, setMetrics] = useState<MetricInfo[]>([]);

  // 细粒度加载与操作状态，避免单一 busy 状态导致无关按钮误转
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [loadingColumns, setLoadingColumns] = useState(false);
  const [importingStage, setImportingStage] = useState<"preview" | "import" | null>(null);
  const importing = importingStage !== null;
  const [exporting, setExporting] = useState(false);
  const [savingTable, setSavingTable] = useState(false);
  const [deletingTable, setDeletingTable] = useState<string | null>(null);
  const [savingColumn, setSavingColumn] = useState(false);
  const [deletingColumn, setDeletingColumn] = useState<string | null>(null);
  const [savingMetric, setSavingMetric] = useState(false);
  const [deletingMetric, setDeletingMetric] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);

  // 导入导出状态
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importMode, setImportMode] = useState<ImportMode>("merge");
  const [dryRun, setDryRun] = useState(false);
  const [importResult, setImportResult] = useState<MetaImportResponse | null>(null);
  const [diffActiveTab, setDiffActiveTab] = useState<"all" | "tables" | "columns" | "metrics">(
    "all"
  );
  const [diffSearchQuery, setDiffSearchQuery] = useState("");

  // 手动添加表状态
  const [isCreatingTable, setIsCreatingTable] = useState(false);
  const [newTableName, setNewTableName] = useState("");
  const [newTableRole, setNewTableRole] = useState<TableRole>("fact");
  const [newTableDesc, setNewTableDesc] = useState("");
  const [sourceTables, setSourceTables] = useState<string[]>([]);
  const [loadingSourceTables, setLoadingSourceTables] = useState(false);
  const [isTableDropdownOpen, setIsTableDropdownOpen] = useState(false);

  // 表编辑状态
  const [editingTable, setEditingTable] = useState<TableInfo | null>(null);
  const [editTableRole, setEditTableRole] = useState<TableRole>("fact");
  const [editTableDesc, setEditTableDesc] = useState("");

  // 手动添加字段状态
  const [isCreatingColumn, setIsCreatingColumn] = useState(false);
  const [newColName, setNewColName] = useState("");
  const [newColDesc, setNewColDesc] = useState("");
  const [newColAlias, setNewColAlias] = useState("");
  const [newColIndexValues, setNewColIndexValues] = useState(false);
  const [newColRefTable, setNewColRefTable] = useState("");
  const [newColRefColumn, setNewColRefColumn] = useState("");

  // 字段编辑状态
  const [editingColumn, setEditingColumn] = useState<ColumnInfo | null>(null);
  const [editColDesc, setEditColDesc] = useState("");
  const [editColAlias, setEditColAlias] = useState("");
  const [editColIndexValues, setEditColIndexValues] = useState(false);
  const [editColRefTable, setEditColRefTable] = useState("");
  const [editColRefColumn, setEditColRefColumn] = useState("");

  // 指标创建状态
  const [isCreatingMetric, setIsCreatingMetric] = useState(false);
  const [newMetricName, setNewMetricName] = useState("");
  const [newMetricDesc, setNewMetricDesc] = useState("");
  const [newMetricColumns, setNewMetricColumns] = useState("");
  const [newMetricAlias, setNewMetricAlias] = useState("");

  // 指标编辑状态
  const [editingMetric, setEditingMetric] = useState<MetricInfo | null>(null);
  const [editMetricDesc, setEditMetricDesc] = useState("");
  const [editMetricColumns, setEditMetricColumns] = useState("");
  const [editMetricAlias, setEditMetricAlias] = useState("");

  const loadData = useCallback(async () => {
    setLoadingCatalog(true);
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
      setLoadingCatalog(false);
    }
  }, []);

  const loadColumns = useCallback(async (tableName: string) => {
    if (!tableName) {
      setColumns([]);
      return;
    }
    setLoadingColumns(true);
    try {
      const cols = await metaApi.listColumns(tableName);
      setColumns(cols);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setLoadingColumns(false);
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
    setExporting(true);
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
      setExporting(false);
    }
  };

  // 导入 YAML
  const handleImport = async (overrideDryRun?: boolean) => {
    if (!importFile) {
      toast.error("请先选择 YAML 文件");
      return;
    }
    const isDryRun = overrideDryRun !== undefined ? overrideDryRun : dryRun;
    setImportingStage(isDryRun ? "preview" : "import");
    try {
      const result = await metaApi.importMetadata(importFile, importMode, isDryRun);
      setImportResult(result);
      setDiffActiveTab("all");
      setDiffSearchQuery("");
      if (isDryRun) {
        toast.info("变更预览完成，未写入数据库");
      } else {
        toast.success("元数据导入并同步成功");
        setImportFile(null);
        setDryRun(false);
        await loadData();
      }
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setImportingStage(null);
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

  // 打开手动创建数据表抽屉
  const handleOpenCreateTable = async () => {
    setEditingTable(null);
    setIsCreatingTable(true);
    setNewTableName("");
    setNewTableRole("fact");
    setNewTableDesc("");
    setLoadingSourceTables(true);
    try {
      const srcTables = await metaApi.listSourceTables();
      setSourceTables(srcTables);
    } catch {
      setSourceTables([]);
    } finally {
      setLoadingSourceTables(false);
    }
  };

  // 手动保存新建表
  const handleCreateTable = async () => {
    const name = newTableName.trim();
    const desc = newTableDesc.trim();
    if (!name) {
      toast.error("请输入或选择数据表名称");
      return;
    }
    if (!desc) {
      toast.error("请输入数据表业务描述");
      return;
    }
    setSavingTable(true);
    try {
      await metaApi.upsertTable(name, {
        role: newTableRole,
        description: desc,
      });
      toast.success(`数据表 ${name} 元数据已添加`);
      setIsCreatingTable(false);
      await loadData();
      setSelectedTable(name);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSavingTable(false);
    }
  };

  // 保存表编辑
  const handleSaveTable = async () => {
    if (!editingTable) return;
    const desc = editTableDesc.trim();
    if (!desc) {
      toast.error("表描述不能为空");
      return;
    }
    setSavingTable(true);
    try {
      await metaApi.upsertTable(editingTable.name, {
        role: editTableRole,
        description: desc,
      });
      toast.success(`表 ${editingTable.name} 元数据已更新`);
      setEditingTable(null);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSavingTable(false);
    }
  };

  const handleDeleteTable = async (tableName: string) => {
    if (!window.confirm(`确定要删除表 "${tableName}" 的元数据吗？关联索引会同步删除。`)) return;
    setDeletingTable(tableName);
    try {
      await metaApi.deleteTable(tableName);
      if (editingTable?.name === tableName) setEditingTable(null);
      if (selectedTable === tableName) {
        setSelectedTable("");
        setColumns([]);
      }
      toast.success(`表 ${tableName} 元数据与索引已删除`);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setDeletingTable(null);
    }
  };

  // 保存新建字段
  const handleCreateColumn = async () => {
    if (!selectedTable) return;
    const name = newColName.trim();
    const desc = newColDesc.trim();
    if (!name) {
      toast.error("请输入字段名称");
      return;
    }
    if (!desc) {
      toast.error("请输入字段语义描述");
      return;
    }
    setSavingColumn(true);
    try {
      const aliases = newColAlias
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
      await metaApi.upsertColumn(selectedTable, name, {
        description: desc,
        alias: aliases,
        index_values: newColIndexValues,
        reference_t_name: newColRefTable.trim() || null,
        reference_c_name: newColRefColumn.trim() || null,
      });
      toast.success(`字段 ${name} 元数据已添加`);
      setIsCreatingColumn(false);
      setNewColName("");
      setNewColDesc("");
      setNewColAlias("");
      setNewColIndexValues(false);
      setNewColRefTable("");
      setNewColRefColumn("");
      await loadColumns(selectedTable);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSavingColumn(false);
    }
  };

  // 保存字段编辑
  const handleSaveColumn = async () => {
    if (!editingColumn) return;
    const desc = editColDesc.trim();
    if (!desc) {
      toast.error("字段描述不能为空");
      return;
    }
    setSavingColumn(true);
    try {
      const aliases = editColAlias
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
      await metaApi.upsertColumn(editingColumn.t_name, editingColumn.name, {
        description: desc,
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
      setSavingColumn(false);
    }
  };

  const handleDeleteColumn = async (column: ColumnInfo) => {
    if (!window.confirm(`确定要删除字段 "${column.t_name}.${column.name}" 的元数据吗？`)) return;
    setDeletingColumn(column.name);
    try {
      await metaApi.deleteColumn(column.t_name, column.name);
      if (editingColumn?.name === column.name) setEditingColumn(null);
      toast.success(`字段 ${column.t_name}.${column.name} 元数据与索引已删除`);
      await Promise.all([loadData(), loadColumns(column.t_name)]);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setDeletingColumn(null);
    }
  };

  // 保存新指标
  const handleCreateMetric = async () => {
    if (!newMetricName.trim() || !newMetricDesc.trim()) return;
    setSavingMetric(true);
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
      setIsCreatingMetric(false);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSavingMetric(false);
    }
  };

  // 保存编辑指标
  const handleSaveMetric = async () => {
    if (!editingMetric || !editMetricDesc.trim()) return;
    setSavingMetric(true);
    try {
      const aliases = editMetricAlias
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
      const colPairs = editMetricColumns
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean)
        .map((c) => {
          const [t_name, c_name] = c.split(".");
          return { t_name: t_name || "", c_name: c_name || "" };
        })
        .filter((c) => c.t_name && c.c_name);

      await metaApi.upsertMetric(editingMetric.name, {
        description: editMetricDesc.trim(),
        relevant_columns: colPairs,
        alias: aliases,
      });
      toast.success(`指标 ${editingMetric.name} 元数据已更新`);
      setEditingMetric(null);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSavingMetric(false);
    }
  };

  // 删除指标
  const handleDeleteMetric = async (metricName: string) => {
    if (!window.confirm(`确定要删除指标 "${metricName}" 吗？`)) return;
    setDeletingMetric(metricName);
    try {
      await metaApi.deleteMetric(metricName);
      toast.success(`指标 ${metricName} 已删除`);
      await loadData();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setDeletingMetric(null);
    }
  };

  // 解析变更明细列表
  const allDiffItems = useMemo<DiffItem[]>(() => {
    if (!importResult) return [];
    const items: DiffItem[] = [];

    // 数据表
    for (const key of importResult.tables.created_keys || []) {
      items.push({ type: "table", action: "create", key });
    }
    for (const key of importResult.tables.updated_keys || []) {
      items.push({ type: "table", action: "update", key });
    }
    for (const key of importResult.tables.deleted_keys || []) {
      items.push({ type: "table", action: "delete", key });
    }

    // 数据字段
    for (const key of importResult.columns.created_keys || []) {
      items.push({ type: "column", action: "create", key });
    }
    for (const key of importResult.columns.updated_keys || []) {
      items.push({ type: "column", action: "update", key });
    }
    for (const key of importResult.columns.deleted_keys || []) {
      items.push({ type: "column", action: "delete", key });
    }

    // 业务指标
    for (const key of importResult.metrics.created_keys || []) {
      items.push({ type: "metric", action: "create", key });
    }
    for (const key of importResult.metrics.updated_keys || []) {
      items.push({ type: "metric", action: "update", key });
    }
    for (const key of importResult.metrics.deleted_keys || []) {
      items.push({ type: "metric", action: "delete", key });
    }

    return items;
  }, [importResult]);

  const filteredDiffItems = useMemo(() => {
    return allDiffItems.filter((item) => {
      if (diffActiveTab !== "all") {
        if (diffActiveTab === "tables" && item.type !== "table") return false;
        if (diffActiveTab === "columns" && item.type !== "column") return false;
        if (diffActiveTab === "metrics" && item.type !== "metric") return false;
      }
      if (diffSearchQuery.trim()) {
        const q = diffSearchQuery.trim().toLowerCase();
        return item.key.toLowerCase().includes(q);
      }
      return true;
    });
  }, [allDiffItems, diffActiveTab, diffSearchQuery]);

  const existingTableNames = useMemo(() => new Set(tables.map((t) => t.name)), [tables]);

  const filteredSourceTables = useMemo(() => {
    if (!newTableName.trim()) return sourceTables;
    const q = newTableName.trim().toLowerCase();
    return sourceTables.filter((t) => t.toLowerCase().includes(q));
  }, [sourceTables, newTableName]);

  const [activeSection, setActiveSection] = useState<string>("section-yaml");

  useEffect(() => {
    const sections = ["section-yaml", "section-tables", "section-columns", "section-metrics"];
    const handleScroll = () => {
      const scrollPosition = window.scrollY + 200;
      for (let i = sections.length - 1; i >= 0; i--) {
        const el = document.getElementById(sections[i]);
        if (el && el.offsetTop <= scrollPosition) {
          setActiveSection(sections[i]);
          break;
        }
      }
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const scrollToSection = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <div className="space-y-6">
      {/* 右侧悬浮模块快捷导航 */}
      <nav
        aria-label="模块快捷导航"
        className="fixed right-4 top-1/2 -translate-y-1/2 z-40 hidden lg:flex flex-col gap-1.5 rounded-lg border border-[#d4d4ce] bg-[#ffffff]/95 p-2 shadow-lg backdrop-blur-xs font-mono"
      >
        {[
          { id: "section-yaml", label: "YAML维护", count: null },
          { id: "section-tables", label: "数据表", count: tables.length },
          { id: "section-columns", label: "表字段", count: columns.length },
          { id: "section-metrics", label: "业务指标", count: metrics.length },
        ].map((item) => {
          const isActive = activeSection === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => scrollToSection(item.id)}
              className={`group flex items-center justify-between gap-3 rounded px-2.5 py-1.5 text-left text-xs transition-all cursor-pointer ${
                isActive
                  ? "bg-[#1e2024] font-medium text-[#ffffff] shadow-xs"
                  : "text-[#52525b] hover:bg-[#f4f4f0] hover:text-[#18181b]"
              }`}
            >
              <span>{item.label}</span>
              {item.count !== null && (
                <span
                  className={`rounded px-1.5 py-0.2 text-[10px] font-semibold ${
                    isActive
                      ? "bg-[#ffffff]/20 text-[#ffffff]"
                      : "bg-[#ebebe6] text-[#71717a] group-hover:bg-[#d4d4ce] group-hover:text-[#18181b]"
                  }`}
                >
                  {item.count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* 元数据导入导出与索引维护控制台 */}
      <section
        id="section-yaml"
        className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
      >
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#e5e5df] pb-3">
          <h2 className="text-base font-bold text-[#18181b]">元数据 YAML 导入导出与索引同步</h2>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={syncing !== null || !selectedTable}
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
              disabled={syncing !== null || !selectedTable}
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
              disabled={syncing !== null}
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

        {/* 导入与导出操作区 */}
        <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-4 text-sm">
          <p className="font-semibold text-[#18181b] mb-3">批量导入与导出元数据 (YAML)</p>
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="file"
              accept=".yaml,.yml"
              disabled={importing}
              onChange={(e) => {
                setImportFile(e.target.files?.[0] || null);
                setImportResult(null);
              }}
              className="text-xs text-[#52525b] file:mr-2 file:rounded file:border file:border-[#d4d4ce] file:bg-[#ffffff] file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-[#18181b] hover:file:bg-[#ebebe6] disabled:opacity-50"
            />
            <div className="flex items-center gap-2 text-xs">
              <label htmlFor="metadata-import-mode" className="text-[#71717a]">
                模式
              </label>
              <select
                id="metadata-import-mode"
                value={importMode}
                disabled={importing}
                onChange={(e) => {
                  setImportMode(e.target.value as ImportMode);
                  setImportResult(null);
                }}
                className="h-8 rounded border border-[#d4d4ce] bg-[#ffffff] px-2 text-xs text-[#1e2024] disabled:opacity-50"
              >
                <option value="merge">增量合并</option>
                <option value="replace">全量替换</option>
              </select>
            </div>
            <label
              className={`flex items-center gap-1.5 text-xs text-[#52525b] ${
                importing ? "cursor-not-allowed opacity-50" : "cursor-pointer"
              }`}
            >
              <input
                type="checkbox"
                checked={dryRun}
                disabled={importing}
                onChange={(e) => {
                  setDryRun(e.target.checked);
                  setImportResult(null);
                }}
                className="h-4 w-4 rounded accent-[#1e2024]"
              />
              <span>仅预览变更</span>
            </label>
            <Button
              size="sm"
              variant="outline"
              disabled={importing || !importFile}
              onClick={() => void handleImport()}
              className="h-8 text-xs"
            >
              {importing ? (
                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              ) : dryRun ? (
                <Eye className="h-3.5 w-3.5 mr-1" />
              ) : (
                <Upload className="h-3.5 w-3.5 mr-1" />
              )}
              {importingStage === "preview"
                ? "正在预览变更..."
                : importingStage === "import"
                  ? "正在执行导入..."
                  : dryRun
                    ? "预览导入变更"
                    : "执行导入"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={exporting}
              onClick={() => void handleExport()}
              className="h-8 text-xs"
            >
              {exporting ? (
                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5 mr-1" />
              )}
              {exporting ? "正在导出..." : "导出 YAML 元数据"}
            </Button>
          </div>

          {/* 导入结果与变更明细全景展开面板 */}
          {importResult && (
            <div className="mt-4 rounded border border-[#d4d4ce] bg-[#ffffff] p-4 text-xs shadow-xs space-y-3">
              <div className="flex items-center justify-between font-bold text-sm text-[#18181b] border-b border-[#e5e5df] pb-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span>
                    导入执行结果 ({importResult.dry_run ? "预览模式 · 未写入" : "已写入生效"})
                  </span>
                  <span className="text-xs font-normal text-[#71717a]">
                    共 {allDiffItems.length} 项元数据发生变更
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {importResult.dry_run && (
                    <Button
                      size="sm"
                      disabled={importing || !importFile}
                      onClick={() => void handleImport(false)}
                      className="h-7 text-xs"
                    >
                      {importingStage === "import" ? (
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      ) : (
                        <Upload className="h-3 w-3 mr-1" />
                      )}
                      {importingStage === "import" ? "正在执行导入..." : "确认正式导入"}
                    </Button>
                  )}
                  <button
                    type="button"
                    onClick={() => setImportResult(null)}
                    className="text-[#71717a] hover:text-[#18181b] p-1 rounded hover:bg-[#ebebe6]"
                    title="关闭面板"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {/* 汇总统计卡片 */}
              <div className="grid gap-2.5 sm:grid-cols-3">
                <div className="rounded bg-[#fafaf8] p-3 border border-[#ebebe6]">
                  <div className="font-semibold text-xs text-[#18181b] mb-1">数据表</div>
                  <div className="flex items-center gap-2 font-mono text-xs">
                    <span className="text-[#166534] font-medium">
                      +{importResult.tables.created_count} 新增
                    </span>
                    <span className="text-[#854d0e] font-medium">
                      ~{importResult.tables.updated_count} 更新
                    </span>
                    <span className="text-[#991b1b] font-medium">
                      -{importResult.tables.deleted_count} 删除
                    </span>
                  </div>
                </div>
                <div className="rounded bg-[#fafaf8] p-3 border border-[#ebebe6]">
                  <div className="font-semibold text-xs text-[#18181b] mb-1">数据字段</div>
                  <div className="flex items-center gap-2 font-mono text-xs">
                    <span className="text-[#166534] font-medium">
                      +{importResult.columns.created_count} 新增
                    </span>
                    <span className="text-[#854d0e] font-medium">
                      ~{importResult.columns.updated_count} 更新
                    </span>
                    <span className="text-[#991b1b] font-medium">
                      -{importResult.columns.deleted_count} 删除
                    </span>
                  </div>
                </div>
                <div className="rounded bg-[#fafaf8] p-3 border border-[#ebebe6]">
                  <div className="font-semibold text-xs text-[#18181b] mb-1">业务指标</div>
                  <div className="flex items-center gap-2 font-mono text-xs">
                    <span className="text-[#166534] font-medium">
                      +{importResult.metrics.created_count} 新增
                    </span>
                    <span className="text-[#854d0e] font-medium">
                      ~{importResult.metrics.updated_count} 更新
                    </span>
                    <span className="text-[#991b1b] font-medium">
                      -{importResult.metrics.deleted_count} 删除
                    </span>
                  </div>
                </div>
              </div>

              {/* 变更明细过滤与搜索栏 */}
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-[#f0f0eb]">
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setDiffActiveTab("all")}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      diffActiveTab === "all"
                        ? "bg-[#1e2024] text-[#ffffff]"
                        : "bg-[#fafaf8] text-[#52525b] hover:bg-[#ebebe6]"
                    }`}
                  >
                    全部变更 ({allDiffItems.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setDiffActiveTab("tables")}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      diffActiveTab === "tables"
                        ? "bg-[#1e2024] text-[#ffffff]"
                        : "bg-[#fafaf8] text-[#52525b] hover:bg-[#ebebe6]"
                    }`}
                  >
                    数据表 (
                    {importResult.tables.created_count +
                      importResult.tables.updated_count +
                      importResult.tables.deleted_count}
                    )
                  </button>
                  <button
                    type="button"
                    onClick={() => setDiffActiveTab("columns")}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      diffActiveTab === "columns"
                        ? "bg-[#1e2024] text-[#ffffff]"
                        : "bg-[#fafaf8] text-[#52525b] hover:bg-[#ebebe6]"
                    }`}
                  >
                    数据字段 (
                    {importResult.columns.created_count +
                      importResult.columns.updated_count +
                      importResult.columns.deleted_count}
                    )
                  </button>
                  <button
                    type="button"
                    onClick={() => setDiffActiveTab("metrics")}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      diffActiveTab === "metrics"
                        ? "bg-[#1e2024] text-[#ffffff]"
                        : "bg-[#fafaf8] text-[#52525b] hover:bg-[#ebebe6]"
                    }`}
                  >
                    业务指标 (
                    {importResult.metrics.created_count +
                      importResult.metrics.updated_count +
                      importResult.metrics.deleted_count}
                    )
                  </button>
                </div>

                <div className="relative flex items-center">
                  <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-[#a1a1aa]" />
                  <input
                    type="text"
                    value={diffSearchQuery}
                    onChange={(e) => setDiffSearchQuery(e.target.value)}
                    placeholder="搜索变更对象名称..."
                    className="h-8 w-56 rounded border border-[#d4d4ce] bg-[#ffffff] pl-8 pr-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                  />
                </div>
              </div>

              {/* 变更清单展示区 */}
              <div className="max-h-64 overflow-y-auto rounded border border-[#e5e5df] bg-[#fafaf8] p-2.5">
                {filteredDiffItems.length > 0 ? (
                  <div className="grid gap-1.5 sm:grid-cols-2 md:grid-cols-3">
                    {filteredDiffItems.map((item) => (
                      <div
                        key={`${item.type}-${item.action}-${item.key}`}
                        className={`flex items-center justify-between rounded border px-2.5 py-1.5 text-xs font-mono transition-colors ${
                          item.action === "create"
                            ? "border-[#bbf7d0] bg-[#f0fdf4] text-[#166534]"
                            : item.action === "update"
                              ? "border-[#fef08a] bg-[#fefce8] text-[#854d0e]"
                              : "border-[#fecaca] bg-[#fef2f2] text-[#991b1b]"
                        }`}
                      >
                        <span className="truncate mr-2 font-medium" title={item.key}>
                          {item.key}
                        </span>
                        <span className="shrink-0 text-[10px] font-bold uppercase rounded px-1 py-0.2 bg-white/70">
                          {item.action === "create"
                            ? "新增"
                            : item.action === "update"
                              ? "更新"
                              : "删除"}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-4 text-center text-xs text-[#71717a]">
                    {allDiffItems.length === 0
                      ? "本次导入未产生任何元数据变更"
                      : "未找到匹配的变更项"}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* 数据表元数据管理 */}
      <section
        id="section-tables"
        className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
      >
        <div className="flex items-center justify-between border-b border-[#e5e5df] pb-3 shrink-0">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
            <Database className="h-4 w-4 text-[#52525b]" />
            <span>数据表元数据 ({tables.length})</span>
            {loadingCatalog && <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />}
          </h2>
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

        {/* 手动添加表抽屉/表单 */}
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
                      // 延时关闭以允许点击选项触发 onClick
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

                {/* 物理源表自动联想下拉框 */}
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
                            {isManaged && (
                              <span className="text-[10px] text-[#71717a]">已纳管</span>
                            )}
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

        {/* 表编辑弹窗/表单 */}
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

        {/* 表列表表格 */}
        <div className="mt-4 max-h-[410px] overflow-auto rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
            <colgroup>
              <col className="w-[28%]" />
              <col className="w-[140px]" />
              <col className="w-[55%]" />
              <col className="w-[84px]" />
            </colgroup>
            <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">表名称</th>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">表角色</th>
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
                return (
                  <tr
                    key={table.name}
                    onClick={() => {
                      setSelectedTable(table.name);
                      setIsCreatingColumn(false);
                      setEditingColumn(null);
                    }}
                    className={`transition-colors cursor-pointer ${
                      isSelected
                        ? "bg-[#1e2024] text-[#ffffff]"
                        : "hover:bg-[#fafaf8] text-[#1e2024]"
                    }`}
                  >
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
                        className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${
                          isSelected ? "bg-[#2d3139] text-[#ffffff]" : "bg-[#e5e5df] text-[#52525b]"
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
                          title={`编辑表 ${table.name}`}
                        >
                          <Edit2 className="h-3 w-3" />
                          <span className="sr-only">编辑表 {table.name}</span>
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={deletingTable === table.name}
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleDeleteTable(table.name);
                          }}
                          className="h-7 px-2 text-xs"
                          title={`删除表 ${table.name}`}
                        >
                          <Trash2 className="h-3 w-3" />
                          <span className="sr-only">删除表 {table.name}</span>
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* 字段元数据管理 */}
      <section
        id="section-columns"
        className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
      >
        <div className="flex items-center justify-between border-b border-[#e5e5df] pb-3 shrink-0">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
            <Layers className="h-4 w-4 text-[#52525b]" />
            <span>
              表字段元数据 [{selectedTable || "未选择"}] ({columns.length})
            </span>
            {loadingColumns && <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />}
          </h2>
          {selectedTable && (
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
          )}
        </div>

        {/* 手动添加字段表单 */}
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

        {/* 字段编辑弹窗/表单 */}
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

        {/* 字段列表表格 */}
        <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
            <colgroup>
              <col className="w-[24%]" />
              <col className="w-[32%]" />
              <col className="w-[32%]" />
              <col className="w-[84px]" />
              <col className="w-[84px]" />
            </colgroup>
            <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
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
                  取值索引
                </th>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap text-right bg-[#f4f4f0]">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#f0f0eb]">
              {columns.map((col) => (
                <tr key={col.name} className="hover:bg-[#fafaf8] transition-colors">
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
                      <span className="inline-flex items-center rounded bg-[#ebebe6] px-1.5 py-0.5 text-[11px] font-mono text-[#27272a] break-all leading-tight">
                        <span className="text-[#52525b]">{col.reference_t_name}</span>
                        <span className="font-bold text-[#18181b] mx-0.5 text-xs">.</span>
                        <span className="font-semibold text-[#18181b]">{col.reference_c_name}</span>
                      </span>
                    ) : (
                      <span className="text-[#a1a1aa]">-</span>
                    )}
                  </td>
                  <td className="px-3.5 py-2.5 align-top">
                    <div className="flex flex-col gap-1 items-start">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap ${
                          col.index_values
                            ? "bg-[#1e2024] text-[#ffffff]"
                            : "bg-[#e5e5df] text-[#71717a]"
                        }`}
                      >
                        {col.index_values ? "已开启" : "未开启"}
                      </span>
                      {col.value_index_sync_status && (
                        <span className="text-[9px] text-[#71717a] whitespace-nowrap">
                          {col.value_index_sync_status}
                        </span>
                      )}
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
                        onClick={() => void handleDeleteColumn(col)}
                        className="h-7 px-2 text-xs"
                        title={`删除字段 ${col.name}`}
                      >
                        <Trash2 className="h-3 w-3" />
                        <span className="sr-only">删除字段 {col.name}</span>
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {columns.length === 0 && (
            <div className="p-8 text-center text-xs text-[#71717a]">
              {selectedTable ? "该表暂无字段元数据" : "请在上方数据表列表中选择一张数据表查看字段"}
            </div>
          )}
        </div>
      </section>

      {/* 业务指标元数据管理 */}
      <section
        id="section-metrics"
        className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
      >
        <div className="flex items-center justify-between border-b border-[#e5e5df] pb-3 shrink-0">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
            <BarChart3 className="h-4 w-4 text-[#52525b]" />
            <span>业务指标元数据 ({metrics.length})</span>
          </h2>
          <Button
            size="sm"
            onClick={() => {
              setEditingMetric(null);
              setIsCreatingMetric(true);
              setNewMetricName("");
              setNewMetricDesc("");
              setNewMetricColumns("");
              setNewMetricAlias("");
            }}
            className="h-7 px-2 text-xs"
            title="添加业务指标"
          >
            <Plus className="h-3 w-3 mr-1" />
            添加指标
          </Button>
        </div>

        {/* 新增指标输入抽屉/表单 */}
        {isCreatingMetric && (
          <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
            <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
              <span>添加指标元数据</span>
              <button
                type="button"
                onClick={() => setIsCreatingMetric(false)}
                className="text-[#71717a] hover:text-[#18181b]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label
                  htmlFor="new-metric-name"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  指标名称
                </label>
                <input
                  id="new-metric-name"
                  value={newMetricName}
                  onChange={(e) => setNewMetricName(e.target.value)}
                  placeholder="如：gmv_total"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="new-metric-desc"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  业务口径说明
                </label>
                <textarea
                  id="new-metric-desc"
                  value={newMetricDesc}
                  onChange={(e) => setNewMetricDesc(e.target.value)}
                  placeholder="指标的业务统计口径、计算公式与业务含义"
                  rows={2}
                  className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="new-metric-columns"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  关联数据列（逗号分隔）
                </label>
                <input
                  id="new-metric-columns"
                  value={newMetricColumns}
                  onChange={(e) => setNewMetricColumns(e.target.value)}
                  placeholder="ods_orders.pay_amount"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="new-metric-alias"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  同义别名（逗号分隔）
                </label>
                <input
                  id="new-metric-alias"
                  value={newMetricAlias}
                  onChange={(e) => setNewMetricAlias(e.target.value)}
                  placeholder="别名1, 别名2"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div className="mt-3 flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setIsCreatingMetric(false)}
                  className="h-7 text-xs"
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  disabled={savingMetric || !newMetricName.trim() || !newMetricDesc.trim()}
                  onClick={() => void handleCreateMetric()}
                  className="h-7 text-xs"
                >
                  <Check className="h-3.5 w-3.5 mr-1" />
                  {savingMetric ? "正在添加..." : "确认添加指标"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* 编辑指标输入抽屉/表单 */}
        {editingMetric && (
          <div className="mt-3 rounded border border-[#1e2024] bg-[#fafaf8] p-4 text-xs shadow-sm shrink-0 mb-1">
            <div className="flex items-center justify-between font-semibold text-[#18181b] border-b border-[#e5e5df] pb-1.5 mb-3">
              <span>编辑指标元数据: {editingMetric.name}</span>
              <button
                type="button"
                onClick={() => setEditingMetric(null)}
                className="text-[#71717a] hover:text-[#18181b]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label
                  htmlFor="edit-metric-desc"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  业务口径说明
                </label>
                <textarea
                  id="edit-metric-desc"
                  value={editMetricDesc}
                  onChange={(e) => setEditMetricDesc(e.target.value)}
                  placeholder="指标的业务统计口径、计算公式与业务含义"
                  rows={2}
                  className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="edit-metric-columns"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  关联数据列（逗号分隔）
                </label>
                <input
                  id="edit-metric-columns"
                  value={editMetricColumns}
                  onChange={(e) => setEditMetricColumns(e.target.value)}
                  placeholder="ods_orders.pay_amount"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="edit-metric-alias"
                  className="block text-xs font-medium text-[#71717a] mb-1"
                >
                  同义别名（逗号分隔）
                </label>
                <input
                  id="edit-metric-alias"
                  value={editMetricAlias}
                  onChange={(e) => setEditMetricAlias(e.target.value)}
                  placeholder="别名1, 别名2"
                  className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
                />
              </div>
              <div className="mt-3 flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditingMetric(null)}
                  className="h-7 text-xs"
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  disabled={savingMetric || !editMetricDesc.trim()}
                  onClick={() => void handleSaveMetric()}
                  className="h-7 text-xs"
                >
                  <Check className="h-3.5 w-3.5 mr-1" />
                  {savingMetric ? "保存中..." : "保存指标元数据"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* 指标列表表格 */}
        <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
            <colgroup>
              <col className="w-[18%]" />
              <col className="w-[31%]" />
              <col className="w-[31%]" />
              <col className="w-[14%]" />
              <col className="w-[84px]" />
            </colgroup>
            <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                  指标名称
                </th>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                  业务口径说明
                </th>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                  关联数据列
                </th>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                  同义别名
                </th>
                <th className="px-3.5 py-2.5 font-medium whitespace-nowrap text-right bg-[#f4f4f0]">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#f0f0eb]">
              {metrics.map((metric) => (
                <tr key={metric.name} className="hover:bg-[#fafaf8] transition-colors">
                  <td className="px-3.5 py-2.5 align-top font-semibold text-[#18181b] break-all leading-tight">
                    {metric.name}
                  </td>
                  <td className="px-3.5 py-2.5 align-top text-xs text-[#27272a] break-words leading-relaxed">
                    {metric.description || "-"}
                  </td>
                  <td className="px-3.5 py-2.5 align-top text-xs">
                    <div className="flex flex-wrap gap-1">
                      {metric.relevant_columns?.map((c) => (
                        <span
                          key={`${c.t_name}.${c.c_name}`}
                          className="inline-flex items-center rounded bg-[#ebebe6] px-1.5 py-0.5 text-[10px] font-mono text-[#27272a] break-all leading-tight"
                        >
                          <span className="text-[#52525b]">{c.t_name}</span>
                          <span className="font-bold text-[#18181b] mx-0.5 text-xs">.</span>
                          <span className="font-semibold text-[#18181b]">{c.c_name}</span>
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 align-top text-xs">
                    <div className="flex flex-wrap gap-1">
                      {metric.alias?.map((a) => (
                        <span
                          key={a}
                          className="rounded bg-[#deded8] px-1.5 py-0.5 text-[10px] text-[#52525b] whitespace-nowrap"
                        >
                          {a}
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
                          setIsCreatingMetric(false);
                          setEditingMetric(metric);
                          setEditMetricDesc(metric.description);
                          setEditMetricColumns(
                            metric.relevant_columns
                              ?.map((c) => `${c.t_name}.${c.c_name}`)
                              .join(", ") || ""
                          );
                          setEditMetricAlias(metric.alias?.join(", ") || "");
                        }}
                        className="h-7 px-2 text-xs"
                        title={`编辑指标 ${metric.name}`}
                      >
                        <Edit2 className="h-3 w-3" />
                        <span className="sr-only">编辑指标 {metric.name}</span>
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deletingMetric === metric.name}
                        onClick={() => void handleDeleteMetric(metric.name)}
                        className="h-7 px-2 text-xs"
                        title={`删除指标 ${metric.name}`}
                      >
                        <Trash2 className="h-3 w-3" />
                        <span className="sr-only">删除指标 {metric.name}</span>
                      </Button>
                    </div>
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
