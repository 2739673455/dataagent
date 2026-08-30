import { Download, Eye, FileText, Search, Upload, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { getApiErrorMessage } from "@/api/errors";
import { type ImportMode, type MetaImportResponse, metaApi } from "@/api/meta";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { Button } from "@/components/ui/button";

interface YamlSectionProps {
  onDataReload: () => Promise<void>;
}

export function YamlSection({ onDataReload }: YamlSectionProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importMode, setImportMode] = useState<ImportMode>("merge");
  const [dryRun, setDryRun] = useState(false);
  const [importingStage, setImportingStage] = useState<"preview" | "import" | null>(null);
  const [exporting, setExporting] = useState(false);
  const [importResult, setImportResult] = useState<MetaImportResponse | null>(null);
  const [diffActiveTab, setDiffActiveTab] = useState<"all" | "tables" | "columns" | "metrics">(
    "all"
  );
  const [diffSearchQuery, setDiffSearchQuery] = useState("");

  const importing = importingStage !== null;

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
      toast.error(getApiErrorMessage(error, "导出 YAML 失败"));
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
        await onDataReload();
      }
    } catch (error) {
      toast.error(getApiErrorMessage(error, "导入 YAML 失败"));
    } finally {
      setImportingStage(null);
    }
  };

  const allDiffItems = useMemo(() => {
    if (!importResult) return [];
    const items: Array<{
      type: "table" | "column" | "metric";
      action: "create" | "update" | "delete";
      key: string;
    }> = [];

    for (const key of importResult.tables.created_keys)
      items.push({ type: "table", action: "create", key });
    for (const key of importResult.tables.updated_keys)
      items.push({ type: "table", action: "update", key });
    for (const key of importResult.tables.deleted_keys)
      items.push({ type: "table", action: "delete", key });

    for (const key of importResult.columns.created_keys)
      items.push({ type: "column", action: "create", key });
    for (const key of importResult.columns.updated_keys)
      items.push({ type: "column", action: "update", key });
    for (const key of importResult.columns.deleted_keys)
      items.push({ type: "column", action: "delete", key });

    for (const key of importResult.metrics.created_keys)
      items.push({ type: "metric", action: "create", key });
    for (const key of importResult.metrics.updated_keys)
      items.push({ type: "metric", action: "update", key });
    for (const key of importResult.metrics.deleted_keys)
      items.push({ type: "metric", action: "delete", key });

    return items;
  }, [importResult]);

  const filteredDiffItems = useMemo(() => {
    return allDiffItems.filter((item) => {
      if (diffActiveTab === "tables" && item.type !== "table") return false;
      if (diffActiveTab === "columns" && item.type !== "column") return false;
      if (diffActiveTab === "metrics" && item.type !== "metric") return false;
      if (diffSearchQuery.trim()) {
        const query = diffSearchQuery.toLowerCase().trim();
        return item.key.toLowerCase().includes(query);
      }
      return true;
    });
  }, [allDiffItems, diffActiveTab, diffSearchQuery]);

  return (
    <section
      id="section-yaml"
      className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#e5e5df] pb-3">
        <h2 className="text-base font-bold text-[#18181b]">元数据 YAML 导入导出</h2>
      </div>

      <div className="mt-4 rounded border border-[#d4d4ce] bg-[#fafaf8] p-4 text-sm">
        <p className="font-semibold text-[#18181b] mb-3">批量导入与导出元数据 (YAML)</p>
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".yaml,.yml"
            disabled={importing}
            onChange={(e) => {
              setImportFile(e.target.files?.[0] || null);
              setImportResult(null);
            }}
            className="hidden"
          />
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={importing}
              onClick={() => fileInputRef.current?.click()}
              className="h-8 text-xs"
            >
              <FileText className="h-3.5 w-3.5 mr-1 text-[#52525b]" />
              选择文件
            </Button>
            {importFile ? (
              <div className="flex items-center gap-1.5 rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 py-1 text-xs text-[#27272a]">
                <span
                  className="max-w-[180px] truncate font-mono text-[11px]"
                  title={importFile.name}
                >
                  {importFile.name}
                </span>
                <button
                  type="button"
                  disabled={importing}
                  onClick={() => {
                    setImportFile(null);
                    setImportResult(null);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  className="rounded p-0.5 text-[#71717a] hover:bg-[#f4f4f0] hover:text-[#18181b] transition"
                  title="清除文件"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ) : (
              <span className="text-xs text-[#a1a1aa]">未选择文件</span>
            )}
          </div>
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
              <DotMatrixLoader className="mr-1" />
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
              <DotMatrixLoader className="mr-1" />
            ) : (
              <Download className="h-3.5 w-3.5 mr-1" />
            )}
            {exporting ? "正在导出..." : "导出 YAML 元数据"}
          </Button>
        </div>

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
                      <DotMatrixLoader className="mr-1" />
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
  );
}
