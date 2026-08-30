import { useCallback, useEffect, useState } from "react";
import { adminApi, type QueryExperienceDetailResponse } from "@/api/admin";
import { getApiErrorMessage } from "@/api/errors";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { AdminEditorDialog } from "../AdminEditorDialog";
import { ExperienceSourceExecutionList } from "./ExperienceSourceExecutionList";

interface Props {
  experienceId: string;
  onClose: () => void;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "-";
}

export function ExperienceDetailDialog({ experienceId, onClose }: Props) {
  const [detail, setDetail] = useState<QueryExperienceDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setDetail(await adminApi.getQueryExperience(experienceId));
    } catch (reason) {
      setError(getApiErrorMessage(reason, "加载查询经验详情失败"));
    } finally {
      setBusy(false);
    }
  }, [experienceId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AdminEditorDialog ariaLabel="查询经验详情" onClose={onClose} title="查询经验详情" wide>
      {busy && !detail ? (
        <div className="flex items-center justify-center gap-2 py-12 text-[#71717a]">
          <DotMatrixLoader />
          加载中
        </div>
      ) : error && !detail ? (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-red-700">{error}</div>
      ) : detail ? (
        <div className="space-y-4">
          {error && (
            <div className="rounded border border-red-200 bg-red-50 p-2 text-red-700">{error}</div>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
            <div>
              <dt className="text-[#71717a]">角色</dt>
              <dd className="font-semibold">{detail.role_name}</dd>
            </div>
            <div>
              <dt className="text-[#71717a]">状态</dt>
              <dd>{detail.status}</dd>
            </div>
            <div>
              <dt className="text-[#71717a]">指纹</dt>
              <dd className="truncate font-mono" title={detail.fingerprint}>
                {detail.fingerprint}
              </dd>
            </div>
            <div>
              <dt className="text-[#71717a]">最近执行</dt>
              <dd>{formatTime(detail.last_executed_at)}</dd>
            </div>
            {detail.disabled_reason && (
              <div className="col-span-2">
                <dt className="text-[#71717a]">禁用信息</dt>
                <dd>
                  {detail.disabled_reason === "admin" ? "管理员禁用" : "元数据变化"} · 用户{" "}
                  {detail.disabled_by_user_id ?? "-"} · {formatTime(detail.disabled_at)}
                </dd>
              </div>
            )}
            {detail.deletion_requested_at && (
              <div className="col-span-2">
                <dt className="text-[#71717a]">删除请求</dt>
                <dd>
                  用户 {detail.deletion_requested_by_user_id ?? "-"} ·{" "}
                  {formatTime(detail.deletion_requested_at)}
                </dd>
              </div>
            )}
          </dl>

          <section>
            <h4 className="mb-1 font-bold text-[#18181b]">SQL 模板</h4>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-[#d4d4ce] bg-[#ffffff] p-2 font-mono text-[11px] text-[#27272a]">
              {detail.sql_template}
            </pre>
          </section>

          <section>
            <h4 className="mb-1 font-bold text-[#18181b]">
              用于语义检索的查询目的 ({detail.purposes.length})
            </h4>
            <ul className="max-h-28 list-disc space-y-1 overflow-y-auto rounded border border-[#d4d4ce] bg-white p-2 pl-6">
              {detail.purposes.map((purpose) => (
                <li key={purpose}>{purpose}</li>
              ))}
            </ul>
          </section>

          <section>
            <h4 className="mb-1 font-bold text-[#18181b]">元数据资产 ({detail.assets.length})</h4>
            <div className="max-h-32 overflow-y-auto rounded border border-[#d4d4ce] bg-white p-2">
              {detail.assets.map((asset) => (
                <div key={`${asset.kind}:${asset.database}:${asset.table}:${asset.column ?? ""}`}>
                  <span className="text-[#71717a]">{asset.kind}</span> {asset.database}.
                  {asset.table}
                  {asset.column ? `.${asset.column}` : ""}
                </div>
              ))}
            </div>
          </section>

          <ExperienceSourceExecutionList experienceId={detail.id} />
        </div>
      ) : null}
    </AdminEditorDialog>
  );
}
