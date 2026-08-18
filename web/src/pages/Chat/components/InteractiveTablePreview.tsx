import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useMemo, useState } from "react";
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

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3">
        <label className="flex min-w-52 flex-1 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            placeholder="筛选当前表格"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
        </label>
        <span className="text-xs text-slate-500">
          展示 {artifact.rows.length} / {artifact.total_rows} 行
          {artifact.truncated ? "，源数据已截断" : ""}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="min-w-full border-collapse text-left text-xs text-slate-700">
          <thead className="sticky top-0 z-10 bg-slate-100">
            <tr>
              {artifact.columns.map((column) => (
                <th key={column} className="whitespace-nowrap border-b border-r border-slate-200">
                  <button
                    type="button"
                    onClick={() => handleSort(column)}
                    className="flex w-full items-center gap-1 px-3 py-2.5 font-semibold text-slate-900 hover:bg-slate-200"
                  >
                    {column}
                    {sortColumn === column ? (ascending ? " ↑" : " ↓") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map(({ key, row }) => (
              <tr key={key} className="odd:bg-white even:bg-slate-50">
                {artifact.columns.map((column) => (
                  <td
                    key={column}
                    className="max-w-80 border-b border-r border-slate-100 px-3 py-2 align-top"
                    title={displayValue(row[column])}
                  >
                    <span className="block max-h-20 overflow-hidden whitespace-pre-wrap break-words">
                      {displayValue(row[column])}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between border-t border-slate-200 px-4 py-2 text-xs text-slate-600">
        <span>{filteredRows.length} 行匹配</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setPage(Math.max(0, safePage - 1))}
            disabled={safePage === 0}
            className="rounded p-1 hover:bg-slate-100 disabled:opacity-30"
            title="上一页"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span>
            {safePage + 1} / {pageCount}
          </span>
          <button
            type="button"
            onClick={() => setPage(Math.min(pageCount - 1, safePage + 1))}
            disabled={safePage >= pageCount - 1}
            className="rounded p-1 hover:bg-slate-100 disabled:opacity-30"
            title="下一页"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
