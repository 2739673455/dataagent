import { History, RefreshCw, Search, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  adminApi,
  type DorisRoleResponse,
  type QueryExperienceListResponse,
  type QueryExperienceStatus,
} from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
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
  };

  return (
    <section className="rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e5df] pb-3">
        <h2 className="flex items-center gap-1.5 text-base font-bold text-[#18181b]">
          <History className="h-4 w-4 text-[#52525b]" />
          查询经验 ({page?.total ?? 0})
          {loading && <RefreshCw className="ml-1 h-3 w-3 animate-spin text-[#71717a]" />}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
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
            <option value="">全部状态</option>
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
          <table className="w-full min-w-[980px] text-left text-xs">
            <thead className="border-b border-[#d4d4ce] bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-3 py-2">最新查询目的</th>
                <th className="px-3 py-2">角色</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">SQL 模板</th>
                <th className="px-3 py-2">资产 / 执行</th>
                <th className="px-3 py-2">索引</th>
                <th className="px-3 py-2">更新时间</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-b border-[#f0f0eb] hover:bg-[#fafaf8]"
                  onClick={() => setSelectedExperienceId(item.id)}
                >
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
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={loading || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            上一页
          </Button>
          <span>
            第 {Math.floor(offset / PAGE_SIZE) + 1} /{" "}
            {Math.max(1, Math.ceil((page?.total ?? 0) / PAGE_SIZE))} 页
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={loading || !page?.has_more}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            下一页
          </Button>
        </div>
      </div>

      {selectedExperienceId && (
        <ExperienceDetailDialog
          experienceId={selectedExperienceId}
          onClose={() => setSelectedExperienceId(null)}
          onChanged={() => void load()}
        />
      )}
    </section>
  );
}
