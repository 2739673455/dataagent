import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, type QueryExperienceSourceExecutionListResponse } from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { Button } from "@/components/ui/button";

const PAGE_SIZE = 10;

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function ExperienceSourceExecutionList({ experienceId }: { experienceId: string }) {
  const [page, setPage] = useState<QueryExperienceSourceExecutionListResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPage(await adminApi.listQueryExperienceSourceExecutions(experienceId, PAGE_SIZE, offset));
    } catch (reason) {
      setError(getApiErrorMessage(reason, "加载来源执行记录失败"));
    } finally {
      setLoading(false);
    }
  }, [experienceId, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="font-bold text-[#18181b]">来源执行记录 ({page?.total ?? 0})</h4>
        {loading && <DotMatrixLoader className="text-[#71717a]" />}
      </div>
      {error ? (
        <div className="rounded border border-red-200 bg-red-50 p-2 text-red-700">{error}</div>
      ) : page?.items.length ? (
        <div className="overflow-x-auto rounded border border-[#d4d4ce]">
          <table className="w-full min-w-[620px] text-left text-[11px]">
            <thead className="bg-[#f4f4f0] text-[#52525b]">
              <tr>
                <th className="px-2 py-1.5">查询目的</th>
                <th className="px-2 py-1.5">用户</th>
                <th className="px-2 py-1.5">分析 / 会话</th>
                <th className="px-2 py-1.5">行数</th>
                <th className="px-2 py-1.5">执行时间</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.id} className="border-t border-[#ecece7]">
                  <td className="max-w-60 px-2 py-1.5 text-[#18181b]">{item.purpose}</td>
                  <td className="px-2 py-1.5">{item.user_id}</td>
                  <td className="px-2 py-1.5 text-[#71717a]">
                    {item.analysis_id} / {item.session_id}
                  </td>
                  <td className="px-2 py-1.5">{item.row_count ?? "-"}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">{formatTime(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded border border-[#d4d4ce] py-5 text-center text-[#71717a]">
          暂无来源执行记录
        </div>
      )}
      <div className="flex justify-end gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-1.5"
          disabled={loading || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          <ChevronLeft className="h-3 w-3" />
          上一页
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-1.5"
          disabled={loading || !page?.has_more}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          下一页
          <ChevronRight className="h-3 w-3" />
        </Button>
      </div>
    </section>
  );
}
