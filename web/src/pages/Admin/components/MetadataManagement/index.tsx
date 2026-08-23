import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { type ColumnInfo, type MetricInfo, type TableInfo, metaApi } from "@/api/meta";
import { ColumnSection } from "./ColumnSection";
import { MetricSection } from "./MetricSection";
import { TableSection } from "./TableSection";
import { YamlSection } from "./YamlSection";
import { extractErrorMessage } from "./utils";

export function MetadataManagement() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [metrics, setMetrics] = useState<MetricInfo[]>([]);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [selectedTableNames, setSelectedTableNames] = useState<string[]>([]);
  const [selectedColumnNames, setSelectedColumnNames] = useState<string[]>([]);
  const [selectedMetricNames, setSelectedMetricNames] = useState<string[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [loadingColumns, setLoadingColumns] = useState(false);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState("section-yaml");

  // 加载全量目录 (表列表与指标列表)
  const loadData = useCallback(async () => {
    setLoadingCatalog(true);
    try {
      const [tableList, metricList] = await Promise.all([
        metaApi.listTables(),
        metaApi.listMetrics(),
      ]);
      setTables(tableList);
      setMetrics(metricList);
      setSelectedTable((prev) => {
        if (tableList.length === 0) return null;
        if (prev && tableList.some((t) => t.name === prev)) return prev;
        return tableList[0].name;
      });
      setSelectedTableNames((prev) =>
        prev.filter((name) => tableList.some((t) => t.name === name))
      );
    } catch (error) {
      toast.error(extractErrorMessage(error, "获取元数据目录失败"));
    } finally {
      setLoadingCatalog(false);
    }
  }, []);

  // 加载指定表的字段列表
  const loadColumns = useCallback(async (tableName: string | null) => {
    if (!tableName) {
      setColumns([]);
      setSelectedColumnNames([]);
      return;
    }
    setLoadingColumns(true);
    try {
      const colList = await metaApi.listColumns(tableName);
      setColumns(colList);
      setSelectedColumnNames((prev) =>
        prev.filter((colName) => colList.some((c) => c.name === colName))
      );
    } catch (error) {
      setColumns([]);
      setSelectedColumnNames([]);
      toast.error(extractErrorMessage(error, "获取字段元数据失败"));
    } finally {
      setLoadingColumns(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    void loadColumns(selectedTable);
  }, [selectedTable, loadColumns]);

  // 滚动监听更新当前活跃区块
  useEffect(() => {
    const handleScroll = () => {
      const sections = ["section-yaml", "section-tables", "section-columns", "section-metrics"];
      const scrollPosition = window.scrollY + 120;
      for (const sectionId of sections) {
        const element = document.getElementById(sectionId);
        if (element) {
          const top = element.offsetTop;
          const height = element.offsetHeight;
          if (scrollPosition >= top && scrollPosition < top + height) {
            setActiveSection(sectionId);
            break;
          }
        }
      }
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const scrollToSection = (id: string) => {
    const element = document.getElementById(id);
    if (element) {
      const yOffset = -20;
      const y = element.getBoundingClientRect().top + window.pageYOffset + yOffset;
      window.scrollTo({ top: y, behavior: "smooth" });
      setActiveSection(id);
    }
  };

  // 表多选切换
  const handleToggleSelectTable = (tableName: string) => {
    setSelectedTableNames((prev) =>
      prev.includes(tableName) ? prev.filter((t) => t !== tableName) : [...prev, tableName]
    );
  };

  // 字段多选切换
  const handleToggleSelectColumn = (colName: string) => {
    setSelectedColumnNames((prev) =>
      prev.includes(colName) ? prev.filter((c) => c !== colName) : [...prev, colName]
    );
  };

  // 指标多选切换
  const handleToggleSelectMetric = (metricName: string) => {
    setSelectedMetricNames((prev) =>
      prev.includes(metricName) ? prev.filter((m) => m !== metricName) : [...prev, metricName]
    );
  };

  // 同步已选表字段语义索引
  const handleSyncTableIndexes = async () => {
    if (selectedTableNames.length === 0) {
      toast.error("请先选择需要同步语义索引的数据表");
      return;
    }
    setSyncing("table_semantic");
    try {
      const res = await metaApi.syncTableIndexes(selectedTableNames);
      toast.success(`所选数据表字段语义索引同步完成，更新 ${res.length} 个字段`);
      if (selectedTable) await loadColumns(selectedTable);
    } catch (error) {
      toast.error(extractErrorMessage(error, "同步表字段语义索引失败"));
    } finally {
      setSyncing(null);
    }
  };

  // 同步已选表字段取值索引
  const handleSyncTableValues = async () => {
    if (selectedTableNames.length === 0) {
      toast.error("请先选择需要同步取值索引的数据表");
      return;
    }
    setSyncing("table_values");
    try {
      const res = await metaApi.syncTableValues(selectedTableNames);
      toast.success(`所选数据表枚举取值同步完成，更新 ${res.length} 个字段`);
      if (selectedTable) await loadColumns(selectedTable);
    } catch (error) {
      toast.error(extractErrorMessage(error, "同步表取值索引失败"));
    } finally {
      setSyncing(null);
    }
  };

  // 同步已选字段语义索引
  const handleSyncColumnIndexes = async () => {
    if (!selectedTable || selectedColumnNames.length === 0) {
      toast.error("请先选择需要同步语义索引的字段");
      return;
    }
    setSyncing("col_semantic");
    try {
      const refs = selectedColumnNames.map((c) => ({ t_name: selectedTable, c_name: c }));
      const res = await metaApi.syncColumnIndexes(refs);
      toast.success(`字段语义索引同步完成，更新 ${res.length} 个字段`);
      await loadColumns(selectedTable);
    } catch (error) {
      toast.error(extractErrorMessage(error, "同步字段语义索引失败"));
    } finally {
      setSyncing(null);
    }
  };

  // 同步已选字段枚举取值索引
  const handleSyncColumnValues = async () => {
    if (!selectedTable || selectedColumnNames.length === 0) {
      toast.error("请先选择需要同步取值索引的字段");
      return;
    }
    setSyncing("col_values");
    try {
      const refs = selectedColumnNames.map((c) => ({ t_name: selectedTable, c_name: c }));
      const res = await metaApi.syncColumnValues(refs);
      toast.success(`字段枚举取值同步完成，更新 ${res.length} 个字段`);
      await loadColumns(selectedTable);
    } catch (error) {
      toast.error(extractErrorMessage(error, "同步字段取值失败"));
    } finally {
      setSyncing(null);
    }
  };

  // 同步已选指标语义索引
  const handleSyncMetricIndexes = async () => {
    if (selectedMetricNames.length === 0) {
      toast.error("请先选择需要同步语义索引的业务指标");
      return;
    }
    setSyncing("metric_semantic");
    try {
      const res = await metaApi.syncMetricIndexes(selectedMetricNames);
      toast.success(`指标语义索引同步完成，更新 ${res.length} 个指标`);
      await loadData();
    } catch (error) {
      toast.error(extractErrorMessage(error, "同步指标语义索引失败"));
    } finally {
      setSyncing(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* 右侧悬浮模块快捷导航 */}
      <nav
        aria-label="模块快捷导航"
        className="fixed right-1.5 top-1/2 -translate-y-1/2 z-40 hidden xl:flex flex-col gap-1.5 rounded-lg border border-[#d4d4ce] bg-[#ffffff]/95 p-2 shadow-lg backdrop-blur-xs font-mono"
      >
        {[
          { id: "section-yaml", label: "YAML", count: null },
          { id: "section-tables", label: "表", count: tables.length },
          { id: "section-columns", label: "字段", count: columns.length },
          { id: "section-metrics", label: "指标", count: metrics.length },
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
                      ? "bg-[#ffffff] text-[#1e2024]"
                      : "bg-[#e5e5df] text-[#52525b] group-hover:bg-[#deded8]"
                  }`}
                >
                  {item.count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* 1. YAML 批量导入导出卡片 */}
      <YamlSection onDataReload={loadData} />

      {/* 2. 数据表元数据 */}
      <TableSection
        tables={tables}
        selectedTable={selectedTable}
        onSelectTable={(tableName) => {
          setSelectedTable(tableName);
          setSelectedColumnNames([]);
        }}
        selectedTableNames={selectedTableNames}
        onToggleSelectTable={handleToggleSelectTable}
        onSelectAllTables={setSelectedTableNames}
        loadingCatalog={loadingCatalog}
        syncing={syncing}
        onSyncTableIndexes={handleSyncTableIndexes}
        onSyncTableValues={handleSyncTableValues}
        onReloadCatalog={loadData}
      />

      {/* 3. 表字段元数据 */}
      <ColumnSection
        selectedTable={selectedTable}
        columns={columns}
        selectedColumnNames={selectedColumnNames}
        onToggleSelectColumn={handleToggleSelectColumn}
        onSelectAllColumns={setSelectedColumnNames}
        loadingColumns={loadingColumns}
        syncing={syncing}
        onSyncColumnIndexes={handleSyncColumnIndexes}
        onSyncColumnValues={handleSyncColumnValues}
        onReloadColumns={loadColumns}
      />

      {/* 4. 业务指标元数据 */}
      <MetricSection
        metrics={metrics}
        selectedMetricNames={selectedMetricNames}
        onToggleSelectMetric={handleToggleSelectMetric}
        onSelectAllMetrics={setSelectedMetricNames}
        syncing={syncing}
        onSyncMetricIndexes={handleSyncMetricIndexes}
        onReloadCatalog={loadData}
      />
    </div>
  );
}
