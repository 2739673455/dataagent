import type { ColumnInfo } from "@/features/metadata/api";
import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "@/components/AdminEditorDialog";
import {
  formatDateTime,
  formatValueIndexSyncDetails,
  formatValueIndexSyncMode,
} from "@/features/metadata/utils";

export function ValueIndexStatus({ column }: { column: ColumnInfo }) {
  if (!column.index_values) {
    return (
      <span className="inline-flex items-center rounded bg-[#e5e5df] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-[#71717a]">
        未开启
      </span>
    );
  }

  const state = column.value_index_state;
  const modeLabel = formatValueIndexSyncMode(state?.last_sync_mode);
  const lastSuccess = state?.last_synced_at
    ? state.status === "succeeded"
      ? formatDateTime(state.last_synced_at)
      : `上次${modeLabel} · ${formatDateTime(state.last_synced_at)}`
    : null;

  return (
    <div
      className="flex flex-col items-start gap-1"
      title={state ? formatValueIndexSyncDetails(state) : "尚未执行取值索引同步"}
    >
      <div className="flex items-center gap-1">
        <span className="inline-flex items-center rounded bg-[#1e2024] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-[#ffffff]">
          已开启
        </span>
        {state?.status === "syncing" ? (
          <span className="inline-flex animate-pulse items-center rounded bg-[#e5e5df] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-[#52525b]">
            同步中
          </span>
        ) : state?.status === "failed" ? (
          <span className="inline-flex items-center rounded bg-[#fee2e2] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-[#b91c1c]">
            同步失败
          </span>
        ) : state?.last_sync_mode ? (
          <span className="inline-flex items-center rounded bg-[#deded8] px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-[#52525b]">
            上次{modeLabel}
          </span>
        ) : null}
      </div>
      {lastSuccess ? (
        <span className="text-[9px] text-[#71717a] font-mono whitespace-nowrap leading-tight">
          {lastSuccess}
        </span>
      ) : (
        <span className="text-[10px] text-[#a1a1aa] font-mono whitespace-nowrap">未同步</span>
      )}
    </div>
  );
}

export interface ColumnDraft {
  mode: "create" | "edit";
  tableName: string;
  name: string;
  description: string;
  alias: string;
  indexValues: boolean;
  refTable: string;
  refColumn: string;
}

export function ColumnEditorDialog({
  draft,
  onChange,
  onClose,
  onSubmit,
  saving,
}: {
  draft: ColumnDraft;
  onChange: (draft: ColumnDraft) => void;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  saving: boolean;
}) {
  const creating = draft.mode === "create";
  return (
    <AdminEditorDialog
      ariaLabel={creating ? `添加字段元数据 ${draft.tableName}` : `编辑字段元数据 ${draft.name}`}
      onClose={onClose}
      title={creating ? `添加字段元数据: ${draft.tableName}` : `编辑字段元数据: ${draft.name}`}
    >
      <div className="space-y-3">
        {creating && (
          <div>
            <label htmlFor="new-col-name" className="block text-xs font-medium text-[#71717a] mb-1">
              字段名称
            </label>
            <input
              id="new-col-name"
              value={draft.name}
              onChange={(e) => onChange({ ...draft, name: e.target.value })}
              placeholder="如：order_id"
              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
          </div>
        )}
        <div>
          <label htmlFor="new-col-desc" className="block text-xs font-medium text-[#71717a] mb-1">
            字段描述
          </label>
          <textarea
            id="new-col-desc"
            value={draft.description}
            onChange={(e) => onChange({ ...draft, description: e.target.value })}
            placeholder="字段业务含义说明"
            rows={2}
            className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="new-col-alias" className="block text-xs font-medium text-[#71717a] mb-1">
            同义别名（逗号分隔）
          </label>
          <input
            id="new-col-alias"
            value={draft.alias}
            onChange={(e) => onChange({ ...draft, alias: e.target.value })}
            placeholder="别名1, 别名2"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div className="grid gap-3">
          <div>
            <label
              htmlFor="new-col-ref-table"
              className="block text-xs font-medium text-[#71717a] mb-1"
            >
              关联引用表
            </label>
            <input
              id="new-col-ref-table"
              value={draft.refTable}
              onChange={(e) => onChange({ ...draft, refTable: e.target.value })}
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
              value={draft.refColumn}
              onChange={(e) => onChange({ ...draft, refColumn: e.target.value })}
              placeholder="如：id"
              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
          </div>
        </div>
        <div className="flex items-center">
          <label
            htmlFor="new-col-index-values"
            className="flex cursor-pointer items-center gap-1.5 text-xs text-[#52525b]"
          >
            <input
              type="checkbox"
              id="new-col-index-values"
              checked={draft.indexValues}
              onChange={(e) => onChange({ ...draft, indexValues: e.target.checked })}
              className="h-4 w-4 rounded accent-[#1e2024]"
            />
            <span>开启取值索引</span>
          </label>
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={saving || !draft.name.trim() || !draft.description.trim()}
            onClick={() => void onSubmit()}
          >
            {saving ? "保存中..." : creating ? "确认添加字段" : "保存字段元数据"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}
