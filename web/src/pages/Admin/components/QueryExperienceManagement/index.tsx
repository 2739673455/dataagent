import { Ban, History, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  adminApi,
  type DorisRoleResponse,
  type QueryExperienceListResponse,
  type QueryExperienceStatus,
} from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { PaginationControls } from "@/components/PaginationControls";
import { Button } from "@/components/ui/button";
import { ExperienceDetailDialog } from "./ExperienceDetailDialog";

const PAGE_SIZE = 20;

const STATUS_LABELS: Record<QueryExperienceStatus, string> = {
  active: "有效",
  disabled: "已禁用",
  deleting: "删除中",
};

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function QueryExperienceManagement() {
  const [page, setPage] = useState<QueryExperienceListResponse | null>(null);
  const [roles, setRoles] = useState<DorisRoleResponse[]>([]);
  const [offset, setOffset] = useState(0);
  const [roleName, setRoleName] = useState("");
  const [status, setStatus] = useState<QueryExperienceStatus | "">("");
  const [query, setQuery] = useState("");
  const [selectedExperienceId, setSelectedExperienceId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [actingIds, setActingIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPage(
        await adminApi.listQueryExperiences({
          limit: PAGE_SIZE,
          offset,
          roleName: roleName || undefined,
          status: status || undefined,
          query,
        })
      );
    } catch (reason) {
      toast.error(getApiErrorMessage(reason, "加载查询经验失败"));
    } finally {
      setLoading(false);
    }
  }, [offset, query, roleName, status]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    adminApi
      .listRoles()
      .then(setRoles)
      .catch((reason) => toast.error(getApiErrorMessage(reason, "加载角色筛选项失败")));
  }, []);

  const changeFilter = (change: () => void) => {
    change();
    setOffset(0);
    setSelectedIds([]);
  };

  const visibleIds = page?.items.map((item) => item.id) ?? [];
  const selectedVisibleCount = visibleIds.filter((id) => selectedIds.includes(id)).length;
  const disableIds =
    page?.items
      .filter((item) => selectedIds.includes(item.id) && item.status === "active")
      .map((item) => item.id) ?? [];
  const deleteIds =
    page?.items
      .filter((item) => selectedIds.includes(item.id) && item.status !== "deleting")
      .map((item) => item.id) ?? [];
  const isActing = actingIds.length > 0;

  const toggleSelection = (id: string) => {
    setSelectedIds((current) =>
      current.includes(id) ? current.filter((candidate) => candidate !== id) : [...current, id]
    );
  };

  const disableExperiences = async (ids: string[]) => {
    if (
      ids.length === 0 ||
      !window.confirm(
        ids.length === 1
          ? "确定禁用这条查询经验吗？禁用后不会再被语义召回。"
          : `确定禁用选中的 ${ids.length} 条有效查询经验吗？禁用后不会再被语义召回。`
      )
    ) {
      return;
    }
    setActingIds(ids);
    try {
      await adminApi.disableQueryExperiences(ids);
      toast.success(ids.length === 1 ? "查询经验已禁用" : `已禁用 ${ids.length} 条查询经验`);
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)));
      await load();
    } catch (reason) {
      toast.error(getApiErrorMessage(reason, "禁用查询经验失败"));
    } finally {
      setActingIds([]);
    }
  };

  const deleteExperiences = async (ids: string[]) => {
    if (
      ids.length === 0 ||
      !window.confirm(
        ids.length === 1
          ? "确定提交这条查询经验的删除请求吗？来源执行审计会继续保留。"
          : `确定提交选中的 ${ids.length} 条查询经验删除请求吗？来源执行审计会继续保留。`
      )
    ) {
      return;
    }
    setActingIds(ids);
    try {
      await adminApi.deleteQueryExperiences(ids);
      toast.success(
        ids.length === 1 ? "查询经验删除请求已提交" : `已提交 ${ids.length} 条删除请求`
      );
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)));
      await load();
    } catch (reason) {
      toast.error(getApiErrorMessage(reason, "删除查询经验失败"));
    } finally {
      setActingIds([]);
    }
  };

  return (
    <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3">
        <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
          <History className="h-4 w-4 text-[#52525b]" />
          查询经验管理 ({page?.total ?? 0})
          {loading && <DotMatrixLoader className="ml-1 text-[#71717a]" />}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={loading || isActing || disableIds.length === 0}
            onClick={() => void disableExperiences(disableIds)}
            title={
              disableIds.length
                ? `批量禁用 ${disableIds.length} 条查询经验`
                : "请先勾选有效查询经验"
            }
          >
            <Ban className="mr-1 h-3.5 w-3.5" />
            批量禁用{disableIds.length ? ` (${disableIds.length})` : ""}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="h-7 text-xs"
            disabled={loading || isActing || deleteIds.length === 0}
            onClick={() => void deleteExperiences(deleteIds)}
            title={
              deleteIds.length ? `批量删除 ${deleteIds.length} 条查询经验` : "请先勾选查询经验"
            }
          >
            <Trash2 className="mr-1 h-3.5 w-3.5" />
            批量删除{deleteIds.length ? ` (${deleteIds.length})` : ""}
          </Button>
          <select
            aria-label="Doris 角色筛选"
            value={roleName}
            onChange={(event) => changeFilter(() => setRoleName(event.target.value))}
            className="h-7 rounded border border-[#d4d4ce] bg-white px-2 text-xs"
          >
            <option value="">全部角色</option>
            {roles.map((role) => (
              <option key={role.name} value={role.name}>
                {role.name}
              </option>
            ))}
          </select>
          <select
            aria-label="查询经验状态筛选"
            value={status}
            onChange={(event) =>
              changeFilter(() => setStatus(event.target.value as QueryExperienceStatus | ""))
            }
            className="h-7 rounded border border-[#d4d4ce] bg-white px-2 text-xs"
          >
            <option value="">有效与已禁用</option>
            <option value="active">有效</option>
            <option value="disabled">已禁用</option>
            <option value="deleting">删除中</option>
          </select>
          <div className="relative w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#a1a1aa]" />
            <input
              type="text"
              value={query}
              onChange={(event) => changeFilter(() => setQuery(event.target.value))}
              placeholder="搜索目的、SQL 或指纹"
              className="h-7 w-full rounded border border-[#d4d4ce] pl-8 pr-7 text-xs focus:border-[#1e2024] focus:outline-none"
            />
            {query && (
              <button
                type="button"
                aria-label="清空搜索"
                onClick={() => changeFilter(() => setQuery(""))}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-[#a1a1aa] hover:text-[#18181b]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {page?.items.length ? (
        <div className="mt-4 overflow-x-auto rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[1180px] table-fixed text-left text-xs">
            <colgroup>
              <col className="w-11" />
              <col className="w-[26%]" />
              <col className="w-24" />
              <col className="w-24" />
              <col className="w-[31%]" />
              <col className="w-24" />
              <col className="w-24" />
              <col className="w-40" />
              <col className="w-24" />
            </colgroup>
            <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-3 py-2 text-center">
                  <input
                    type="checkbox"
                    aria-label="全选当前页查询经验"
                    checked={visibleIds.length > 0 && selectedVisibleCount === visibleIds.length}
                    ref={(element) => {
                      if (element) {
                        element.indeterminate =
                          selectedVisibleCount > 0 && selectedVisibleCount < visibleIds.length;
                      }
                    }}
                    onChange={(event) => setSelectedIds(event.target.checked ? visibleIds : [])}
                    className="h-3.5 w-3.5 cursor-pointer accent-[#1e2024]"
                  />
                </th>
                <th className="px-3 py-2">最新查询目的</th>
                <th className="px-3 py-2">角色</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">SQL 模板</th>
                <th className="px-3 py-2">资产 / 执行</th>
                <th className="px-3 py-2">索引</th>
                <th className="px-3 py-2">更新时间</th>
                <th className="px-3 py-2 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-b border-[#f0f0eb] hover:bg-[#fafaf8]"
                  onClick={() => setSelectedExperienceId(item.id)}
                >
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={`选择查询经验 ${item.latest_purpose}`}
                      checked={selectedIds.includes(item.id)}
                      onClick={(event) => event.stopPropagation()}
                      onChange={() => toggleSelection(item.id)}
                      className="h-3.5 w-3.5 cursor-pointer accent-[#1e2024]"
                    />
                  </td>
                  <td className="max-w-56 px-3 py-2 font-semibold text-[#18181b]">
                    {item.latest_purpose}
                    {item.purpose_count > 1 && (
                      <span className="ml-1 text-[10px] font-normal text-[#71717a]">
                        +{item.purpose_count - 1}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">{item.role_name}</td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-[#ebebe6] px-1.5 py-0.5">
                      {STATUS_LABELS[item.status]}
                    </span>
                  </td>
                  <td
                    className="max-w-72 truncate px-3 py-2 font-mono text-[#52525b]"
                    title={item.sql_template_preview}
                  >
                    {item.sql_template_preview}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {item.asset_count} / {item.execution_count}
                  </td>
                  <td className="px-3 py-2">
                    {item.index_status === "synced" ? "已同步" : "待同步"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{formatTime(item.updated_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <div className="inline-flex items-center justify-end gap-1.5">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2"
                        disabled={isActing || item.status !== "active"}
                        onClick={(event) => {
                          event.stopPropagation();
                          void disableExperiences([item.id]);
                        }}
                        title={item.status === "active" ? "禁用查询经验" : "当前状态不可禁用"}
                      >
                        <Ban className="h-3.5 w-3.5" />
                        <span className="sr-only">禁用查询经验</span>
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        className="h-7 px-2"
                        disabled={isActing || item.status === "deleting"}
                        onClick={(event) => {
                          event.stopPropagation();
                          void deleteExperiences([item.id]);
                        }}
                        title={item.status === "deleting" ? "删除请求已提交" : "删除查询经验"}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        <span className="sr-only">删除查询经验</span>
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-4 rounded border border-[#d4d4ce] py-12 text-center text-sm text-[#71717a]">
          {loading ? "正在加载" : "暂无符合条件的查询经验"}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between text-xs text-[#71717a]">
        <span>
          共 {page?.total ?? 0} 条，当前显示 {page?.total ? offset + 1 : 0}–
          {Math.min(offset + (page?.items.length ?? 0), page?.total ?? 0)}
        </span>
        <PaginationControls
          currentPage={Math.floor(offset / PAGE_SIZE) + 1}
          totalPages={Math.max(1, Math.ceil((page?.total ?? 0) / PAGE_SIZE))}
          disabled={loading}
          onPageChange={(nextPage) => {
            setSelectedIds([]);
            setOffset((nextPage - 1) * PAGE_SIZE);
          }}
        />
      </div>

      {selectedExperienceId && (
        <ExperienceDetailDialog
          experienceId={selectedExperienceId}
          onClose={() => setSelectedExperienceId(null)}
        />
      )}
    </section>
  );
}
