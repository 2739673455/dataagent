import type { ColumnInfo } from "@/api/meta";
import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "../AdminEditorDialog";
import { formatDateTime, formatValueIndexSyncDetails, formatValueIndexSyncMode } from "./utils";

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

export function ColumnCreateDialog({
  isOpen,
  newColAlias,
  newColDesc,
  newColIndexValues,
  newColName,
  newColRefColumn,
  newColRefTable,
  onClose,
  onSubmit,
  savingColumn,
  selectedTable,
  setNewColAlias,
  setNewColDesc,
  setNewColIndexValues,
  setNewColName,
  setNewColRefColumn,
  setNewColRefTable,
}: {
  isOpen: boolean;
  newColAlias: string;
  newColDesc: string;
  newColIndexValues: boolean;
  newColName: string;
  newColRefColumn: string;
  newColRefTable: string;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  savingColumn: boolean;
  selectedTable: string | null;
  setNewColAlias: (val: string) => void;
  setNewColDesc: (val: string) => void;
  setNewColIndexValues: (val: boolean) => void;
  setNewColName: (val: string) => void;
  setNewColRefColumn: (val: string) => void;
  setNewColRefTable: (val: string) => void;
}) {
  if (!isOpen) return null;

  return (
    <AdminEditorDialog
      ariaLabel={`添加字段元数据 ${selectedTable || "未选择"}`}
      onClose={onClose}
      title={`添加字段元数据: ${selectedTable}`}
    >
      <div className="space-y-3">
        <div>
          <label htmlFor="new-col-name" className="block text-xs font-medium text-[#71717a] mb-1">
            字段名称
          </label>
          <input
            id="new-col-name"
            value={newColName}
            onChange={(e) => setNewColName(e.target.value)}
            placeholder="如：order_id"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="new-col-desc" className="block text-xs font-medium text-[#71717a] mb-1">
            字段描述
          </label>
          <textarea
            id="new-col-desc"
            value={newColDesc}
            onChange={(e) => setNewColDesc(e.target.value)}
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
            value={newColAlias}
            onChange={(e) => setNewColAlias(e.target.value)}
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
              value={newColRefTable}
              onChange={(e) => setNewColRefTable(e.target.value)}
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
              value={newColRefColumn}
              onChange={(e) => setNewColRefColumn(e.target.value)}
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
              checked={newColIndexValues}
              onChange={(e) => setNewColIndexValues(e.target.checked)}
              className="h-4 w-4 rounded accent-[#1e2024]"
            />
            <span>开启取值索引</span>
          </label>
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={savingColumn || !newColName.trim() || !newColDesc.trim()}
            onClick={() => void onSubmit()}
          >
            {savingColumn ? "正在添加..." : "确认添加字段"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}

export function ColumnEditDialog({
  editColAlias,
  editColDesc,
  editColIndexValues,
  editColRefColumn,
  editColRefTable,
  editingColumn,
  onClose,
  onSubmit,
  savingColumn,
  setEditColAlias,
  setEditColDesc,
  setEditColIndexValues,
  setEditColRefColumn,
  setEditColRefTable,
}: {
  editColAlias: string;
  editColDesc: string;
  editColIndexValues: boolean;
  editColRefColumn: string;
  editColRefTable: string;
  editingColumn: ColumnInfo | null;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  savingColumn: boolean;
  setEditColAlias: (val: string) => void;
  setEditColDesc: (val: string) => void;
  setEditColIndexValues: (val: boolean) => void;
  setEditColRefColumn: (val: string) => void;
  setEditColRefTable: (val: string) => void;
}) {
  if (!editingColumn) return null;

  return (
    <AdminEditorDialog
      ariaLabel={`编辑字段元数据 ${editingColumn.name}`}
      onClose={onClose}
      title={`编辑字段元数据: ${editingColumn.name}`}
    >
      <div className="space-y-3">
        <div>
          <label htmlFor="edit-col-desc" className="block text-xs font-medium text-[#71717a] mb-1">
            字段描述
          </label>
          <textarea
            id="edit-col-desc"
            value={editColDesc}
            onChange={(e) => setEditColDesc(e.target.value)}
            placeholder="字段业务含义说明"
            rows={2}
            className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="edit-col-alias" className="block text-xs font-medium text-[#71717a] mb-1">
            同义别名（逗号分隔）
          </label>
          <input
            id="edit-col-alias"
            value={editColAlias}
            onChange={(e) => setEditColAlias(e.target.value)}
            placeholder="别名1, 别名2"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div className="grid gap-3">
          <div>
            <label
              htmlFor="edit-col-ref-table"
              className="block text-xs font-medium text-[#71717a] mb-1"
            >
              关联引用表
            </label>
            <input
              id="edit-col-ref-table"
              value={editColRefTable}
              onChange={(e) => setEditColRefTable(e.target.value)}
              placeholder="如：dim_user"
              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
          </div>
          <div>
            <label
              htmlFor="edit-col-ref-column"
              className="block text-xs font-medium text-[#71717a] mb-1"
            >
              关联引用列
            </label>
            <input
              id="edit-col-ref-column"
              value={editColRefColumn}
              onChange={(e) => setEditColRefColumn(e.target.value)}
              placeholder="如：id"
              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
          </div>
        </div>
        <div className="flex items-center">
          <label
            htmlFor="edit-col-index-values"
            className="flex cursor-pointer items-center gap-1.5 text-xs text-[#52525b]"
          >
            <input
              type="checkbox"
              id="edit-col-index-values"
              checked={editColIndexValues}
              onChange={(e) => setEditColIndexValues(e.target.checked)}
              className="h-4 w-4 rounded accent-[#1e2024]"
            />
            <span>开启取值索引</span>
          </label>
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={savingColumn || !editColDesc.trim()}
            onClick={() => void onSubmit()}
          >
            {savingColumn ? "保存中..." : "保存字段元数据"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}
