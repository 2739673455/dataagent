import { Edit2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { type MetricInfo, metaApi } from "@/api/meta";
import { Button } from "@/components/ui/button";
import { MetricCreateDialog, MetricEditDialog } from "./MetricDialogs";
import { parseMetricColumns, splitCsv } from "./utils";

interface MetricSectionProps {
  metrics: MetricInfo[];
  selectedMetricNames: string[];
  onToggleSelectMetric: (metricName: string) => void;
  onSelectAllMetrics: (metricNames: string[]) => void;
  syncing: string | null;
  onSyncMetricIndexes: () => Promise<void>;
  onReloadCatalog: () => Promise<void>;
}

export function MetricSection({
  metrics,
  selectedMetricNames,
  onToggleSelectMetric,
  onSelectAllMetrics,
  syncing,
  onSyncMetricIndexes,
  onReloadCatalog,
}: MetricSectionProps) {
  const [isCreatingMetric, setIsCreatingMetric] = useState(false);
  const [newMetricName, setNewMetricName] = useState("");
  const [newMetricDesc, setNewMetricDesc] = useState("");
  const [newMetricColumns, setNewMetricColumns] = useState("");
  const [newMetricAlias, setNewMetricAlias] = useState("");
  const [editingMetric, setEditingMetric] = useState<MetricInfo | null>(null);
  const [editMetricDesc, setEditMetricDesc] = useState("");
  const [editMetricColumns, setEditMetricColumns] = useState("");
  const [editMetricAlias, setEditMetricAlias] = useState("");
  const [savingMetric, setSavingMetric] = useState(false);
  const [deletingMetric, setDeletingMetric] = useState<string | null>(null);
  const [isBatchDeleting, setIsBatchDeleting] = useState(false);

  const handleBatchDeleteMetrics = async () => {
    if (selectedMetricNames.length === 0) return;
    const confirmed = window.confirm(
      `确认批量删除选中的 ${selectedMetricNames.length} 个业务指标吗？\n此操作将同时删除对应的指标语义索引。`
    );
    if (!confirmed) return;
    setIsBatchDeleting(true);
    try {
      await metaApi.deleteMetrics(selectedMetricNames);
      toast.success(`已成功删除 ${selectedMetricNames.length} 个业务指标`);
      onSelectAllMetrics([]);
      await onReloadCatalog();
    } catch (error) {
      toast.error(getApiErrorMessage(error, "批量删除指标失败"));
    } finally {
      setIsBatchDeleting(false);
    }
  };

  const handleCreateMetric = async () => {
    if (!newMetricName.trim() || !newMetricDesc.trim()) {
      toast.error("指标名称和业务口径说明不能为空");
      return;
    }
    setSavingMetric(true);
    try {
      await metaApi.upsertMetric(newMetricName.trim(), {
        description: newMetricDesc.trim(),
        relevant_columns: parseMetricColumns(newMetricColumns),
        alias: splitCsv(newMetricAlias),
      });
      toast.success(`指标 ${newMetricName.trim()} 添加成功`);
      setIsCreatingMetric(false);
      await onReloadCatalog();
    } catch (error) {
      toast.error(getApiErrorMessage(error, "添加指标失败"));
    } finally {
      setSavingMetric(false);
    }
  };

  const handleSaveMetric = async () => {
    if (!editingMetric) return;
    setSavingMetric(true);
    try {
      await metaApi.upsertMetric(editingMetric.name, {
        description: editMetricDesc.trim(),
        relevant_columns: parseMetricColumns(editMetricColumns),
        alias: splitCsv(editMetricAlias),
      });
      toast.success(`指标 ${editingMetric.name} 更新成功`);
      setEditingMetric(null);
      await onReloadCatalog();
    } catch (error) {
      toast.error(getApiErrorMessage(error, "更新指标失败"));
    } finally {
      setSavingMetric(false);
    }
  };

  const handleDeleteMetric = async (metricName: string) => {
    if (!window.confirm(`确定删除指标 ${metricName} 吗？此操作不可逆。`)) return;
    setDeletingMetric(metricName);
    try {
      await metaApi.deleteMetrics([metricName]);
      toast.success(`指标 ${metricName} 已删除`);
      await onReloadCatalog();
    } catch (error) {
      toast.error(getApiErrorMessage(error, "删除指标失败"));
    } finally {
      setDeletingMetric(null);
    }
  };

  return (
    <section
      id="section-metrics"
      className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3 shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
            <span>业务指标元数据({metrics.length})</span>
            {syncing === "metric_semantic" && (
              <RefreshCw className="h-3 w-3 animate-spin text-[#71717a] ml-1" />
            )}
          </h2>
          {selectedMetricNames.length > 0 && (
            <span className="rounded bg-[#ebebe6] px-2 py-0.5 text-xs text-[#52525b] font-mono">
              已选 {selectedMetricNames.length} 指标
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={syncing !== null || selectedMetricNames.length === 0}
            onClick={() => void onSyncMetricIndexes()}
            className="h-7 text-xs"
            title={
              selectedMetricNames.length === 0
                ? "请先勾选需要同步语义索引的指标"
                : `同步已选 ${selectedMetricNames.length} 个指标语义索引`
            }
          >
            <RefreshCw
              className={`h-3.5 w-3.5 mr-1 ${syncing === "metric_semantic" ? "animate-spin" : ""}`}
            />
            {selectedMetricNames.length > 0
              ? `同步指标语义索引 (${selectedMetricNames.length})`
              : "同步指标语义索引"}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={syncing !== null || isBatchDeleting || selectedMetricNames.length === 0}
            onClick={() => void handleBatchDeleteMetrics()}
            className="h-7 text-xs"
            title={
              selectedMetricNames.length === 0
                ? "请先勾选需要删除的业务指标"
                : `批量删除已选 ${selectedMetricNames.length} 个业务指标`
            }
          >
            <Trash2 className="h-3 w-3 mr-1" />
            {isBatchDeleting
              ? "删除中..."
              : selectedMetricNames.length > 0
                ? `批量删除 (${selectedMetricNames.length})`
                : "批量删除"}
          </Button>
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
      </div>

      <MetricCreateDialog
        isOpen={isCreatingMetric}
        newMetricAlias={newMetricAlias}
        newMetricColumns={newMetricColumns}
        newMetricDesc={newMetricDesc}
        newMetricName={newMetricName}
        onClose={() => setIsCreatingMetric(false)}
        onSubmit={handleCreateMetric}
        savingMetric={savingMetric}
        setNewMetricAlias={setNewMetricAlias}
        setNewMetricColumns={setNewMetricColumns}
        setNewMetricDesc={setNewMetricDesc}
        setNewMetricName={setNewMetricName}
      />

      <MetricEditDialog
        editMetricAlias={editMetricAlias}
        editMetricColumns={editMetricColumns}
        editMetricDesc={editMetricDesc}
        editingMetric={editingMetric}
        onClose={() => setEditingMetric(null)}
        onSubmit={handleSaveMetric}
        savingMetric={savingMetric}
        setEditMetricAlias={setEditMetricAlias}
        setEditMetricColumns={setEditMetricColumns}
        setEditMetricDesc={setEditMetricDesc}
      />

      <div className="mt-4 rounded border border-[#d4d4ce]">
        {metrics.length === 0 ? (
          <div className="py-12 text-center text-xs text-[#71717a]">暂无业务指标元数据</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] table-fixed text-left text-xs font-mono">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[18%]" />
                <col className="w-[30%]" />
                <col className="w-[26%]" />
                <col className="w-[14%]" />
                <col className="w-[125px]" />
                <col className="w-[84px]" />
              </colgroup>
              <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
                <tr>
                  <th className="w-[44px] px-3.5 py-2.5 bg-[#f4f4f0] text-center">
                    <input
                      type="checkbox"
                      aria-label="全选指标"
                      checked={metrics.length > 0 && selectedMetricNames.length === metrics.length}
                      ref={(el) => {
                        if (el) {
                          el.indeterminate =
                            selectedMetricNames.length > 0 &&
                            selectedMetricNames.length < metrics.length;
                        }
                      }}
                      onChange={(e) => {
                        if (e.target.checked) {
                          onSelectAllMetrics(metrics.map((m) => m.name));
                        } else {
                          onSelectAllMetrics([]);
                        }
                      }}
                      className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                    />
                  </th>
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
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap bg-[#f4f4f0]">
                    语义索引
                  </th>
                  <th className="px-3.5 py-2.5 font-medium whitespace-nowrap text-right bg-[#f4f4f0]">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#f0f0eb]">
                {metrics.map((metric) => {
                  const isSelected = selectedMetricNames.includes(metric.name);
                  return (
                    <tr
                      key={metric.name}
                      className={`hover:bg-[#fafaf8] transition-colors ${
                        isSelected ? "bg-[#f4f4f0]/60" : ""
                      }`}
                    >
                      <td className="px-3.5 py-2.5 align-top text-center">
                        <input
                          type="checkbox"
                          aria-label={`选择指标 ${metric.name}`}
                          checked={isSelected}
                          onChange={() => onToggleSelectMetric(metric.name)}
                          className="h-3.5 w-3.5 rounded border-[#d4d4ce] accent-[#1e2024] cursor-pointer align-middle"
                        />
                      </td>
                      <td className="px-3.5 py-2.5 align-top font-semibold text-[#18181b] break-all leading-tight">
                        {metric.name}
                      </td>
                      <td className="px-3.5 py-2.5 align-top text-xs text-[#27272a] break-words leading-relaxed">
                        {metric.description || "-"}
                      </td>
                      <td className="px-3.5 py-2.5 align-top text-xs">
                        <div className="flex flex-wrap gap-1 max-w-full">
                          {metric.relevant_columns?.map((c) => (
                            <span
                              key={`${c.t_name}.${c.c_name}`}
                              className="inline-block max-w-full rounded bg-[#ebebe6] px-1.5 py-0.5 text-[10px] font-mono text-[#27272a] break-all leading-tight"
                            >
                              <span className="text-[#52525b]">{c.t_name}</span>
                              <span className="font-bold text-[#18181b] mx-0.5 text-xs">.</span>
                              <span className="font-semibold text-[#18181b]">{c.c_name}</span>
                            </span>
                          ))}
                          {!metric.relevant_columns?.length && (
                            <span className="text-[#a1a1aa]">-</span>
                          )}
                        </div>
                      </td>
                      <td className="px-3.5 py-2.5 align-top text-xs">
                        {metric.alias?.length ? (
                          <div className="flex flex-wrap gap-1">
                            {metric.alias.map((a) => (
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
                      <td className="px-3.5 py-2.5 align-top">
                        <span className="inline-flex items-center rounded bg-[#1e2024] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-[#ffffff]">
                          已同步
                        </span>
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
