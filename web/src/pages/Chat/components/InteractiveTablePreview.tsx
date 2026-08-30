import { Download, Filter, Table } from "lucide-react";
import { useMemo, useState } from "react";
import { PaginationControls } from "@/components/PaginationControls";
import { Button } from "@/components/ui/button";
import type { InteractiveTableArtifact } from "@/types";

const PAGE_SIZE = 50;

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function compareValues(left: unknown, right: unknown) {
  if (typeof left === "number" && typeof right === "number") {
    return left - right;
  }
  return displayValue(left).localeCompare(displayValue(right), "zh-CN", {
    numeric: true,
  });
}

export function parseInteractiveTableArtifact(value: unknown): InteractiveTableArtifact {
  if (typeof value !== "object" || value === null) {
    throw new TypeError("表格产物必须是对象");
  }
  const candidate = value as Partial<InteractiveTableArtifact>;
  if (
    candidate.format !== "dataagent-interactive-table-v1" ||
    typeof candidate.source_path !== "string" ||
    !Array.isArray(candidate.columns) ||
    candidate.columns.length < 1 ||
    candidate.columns.length > 100 ||
    !candidate.columns.every((column) => typeof column === "string") ||
    !Array.isArray(candidate.rows) ||
    candidate.rows.length > 1000 ||
    !candidate.rows.every((row) => typeof row === "object" && row !== null) ||
    typeof candidate.total_rows !== "number" ||
    !Number.isInteger(candidate.total_rows) ||
    candidate.total_rows < 0 ||
    typeof candidate.truncated !== "boolean"
  ) {
    throw new TypeError("表格产物结构无效");
  }
  return candidate as InteractiveTableArtifact;
}

export function InteractiveTablePreview({ artifact }: { artifact: InteractiveTableArtifact }) {
  const [query, setQuery] = useState("");
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [ascending, setAscending] = useState(true);
  const [page, setPage] = useState(0);

  const filteredRows = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    const indexedRows = artifact.rows.map((row, ordinal) => ({
      key: `${artifact.source_path}:${ordinal}`,
      row,
      ordinal: ordinal + 1,
    }));
    const rows = normalizedQuery
      ? indexedRows.filter(({ row }) =>
          artifact.columns.some((column) =>
            displayValue(row[column]).toLocaleLowerCase().includes(normalizedQuery)
          )
        )
      : indexedRows;
    if (sortColumn) {
      rows.sort((left, right) => {
        const order = compareValues(left.row[sortColumn], right.row[sortColumn]);
        return ascending ? order : -order;
      });
    }
    return rows;
  }, [artifact, ascending, query, sortColumn]);

  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visibleRows = filteredRows.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  const handleSort = (column: string) => {
    setPage(0);
    if (sortColumn === column) {
      setAscending((value) => !value);
      return;
    }
    setSortColumn(column);
    setAscending(true);
  };

  const handleExportCsv = () => {
    const headers = artifact.columns.map((c) => `"${c.replace(/"/g, '""')}"`).join(",");
    const rows = filteredRows.map(({ row }) =>
      artifact.columns.map((c) => `"${displayValue(row[c]).replace(/"/g, '""')}"`).join(",")
    );
    const csvContent = [headers, ...rows].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `export_${Date.now()}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f4f4f0] font-mono text-[#1e2024]">
      {/* 表格顶部控制栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#d4d4ce] bg-[#fafaf8] px-3.5 py-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 font-semibold text-[#18181b]">
            <Table className="h-4 w-4 text-[#52525b]" />
            <span>数据表格</span>
          </span>
          <span className="rounded bg-[#deded8] px-2 py-0.5 text-xs text-[#52525b]">
            共 {artifact.total_rows} 行{artifact.truncated ? " (已截断)" : ""}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 py-1">
            <Filter className="h-3.5 w-3.5 text-[#71717a]" />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
              placeholder="过滤搜索..."
              className="w-28 bg-transparent text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:outline-none sm:w-40"
            />
          </div>

          <Button
            size="sm"
            variant="outline"
            onClick={handleExportCsv}
            className="h-8 text-xs"
            title="导出为 CSV"
          >
            <Download className="h-3.5 w-3.5 mr-1" />
            导出 CSV
          </Button>
        </div>
      </div>

      {/* 数据网格区域 */}
      <div className="min-h-0 flex-1 overflow-auto bg-[#ffffff]">
        <table className="min-w-full border-collapse text-left font-mono text-sm">
          <thead className="sticky top-0 z-10 border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
            <tr>
              <th className="w-12 border-r border-[#e5e5df] px-2.5 py-2 text-center text-xs text-[#71717a]">
                序号
              </th>
              {artifact.columns.map((column) => (
                <th
                  key={column}
                  className="whitespace-nowrap border-r border-[#e5e5df] last:border-r-0"
                >
                  <button
                    type="button"
                    onClick={() => handleSort(column)}
                    className="flex w-full items-center justify-between gap-1 px-3.5 py-2 font-medium hover:bg-[#deded8] hover:text-[#18181b]"
                  >
                    <span>{column}</span>
                    <span className="text-xs text-[#18181b]">
                      {sortColumn === column ? (ascending ? "▲" : "▼") : ""}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map(({ key, row, ordinal }) => (
              <tr key={key} className="border-b border-[#f0f0eb] hover:bg-[#fafaf8]">
                <td className="border-r border-[#f0f0eb] px-2.5 py-1.5 text-center text-xs text-[#71717a]">
                  {ordinal}
                </td>
                {artifact.columns.map((column) => (
                  <td
                    key={column}
                    className="max-w-xs border-r border-[#f0f0eb] px-3.5 py-1.5 align-top text-[#27272a] last:border-r-0"
                    title={displayValue(row[column])}
                  >
                    <span className="block truncate text-sm">{displayValue(row[column])}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 底部翻页栏 */}
      <div className="flex items-center justify-between border-t border-[#d4d4ce] bg-[#fafaf8] px-3 py-1.5 text-xs text-[#71717a]">
        <span>匹配 {filteredRows.length} 条记录</span>
        <PaginationControls
          currentPage={safePage + 1}
          totalPages={pageCount}
          onPageChange={(nextPage) => setPage(nextPage - 1)}
        />
      </div>
    </div>
  );
}
