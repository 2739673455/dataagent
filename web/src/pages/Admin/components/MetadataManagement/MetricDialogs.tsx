import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "../AdminEditorDialog";

export interface MetricDraft {
  mode: "create" | "edit";
  name: string;
  description: string;
  columns: string;
  alias: string;
}

export function MetricEditorDialog({
  draft,
  onChange,
  onClose,
  onSubmit,
  saving,
}: {
  draft: MetricDraft;
  onChange: (draft: MetricDraft) => void;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  saving: boolean;
}) {
  const creating = draft.mode === "create";
  return (
    <AdminEditorDialog
      ariaLabel={creating ? "添加指标元数据" : `编辑指标元数据 ${draft.name}`}
      onClose={onClose}
      title={creating ? "添加指标元数据" : `编辑指标元数据: ${draft.name}`}
    >
      <div className="space-y-3">
        {creating && (
          <div>
            <label
              htmlFor="new-metric-name"
              className="block text-xs font-medium text-[#71717a] mb-1"
            >
              指标名称
            </label>
            <input
              id="new-metric-name"
              value={draft.name}
              onChange={(e) => onChange({ ...draft, name: e.target.value })}
              placeholder="如：gmv_total"
              className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
            />
          </div>
        )}
        <div>
          <label
            htmlFor="new-metric-desc"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            业务口径说明
          </label>
          <textarea
            id="new-metric-desc"
            value={draft.description}
            onChange={(e) => onChange({ ...draft, description: e.target.value })}
            placeholder="指标的业务统计口径、计算公式与业务含义"
            rows={2}
            className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="new-metric-columns"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            关联数据列（逗号分隔）
          </label>
          <input
            id="new-metric-columns"
            value={draft.columns}
            onChange={(e) => onChange({ ...draft, columns: e.target.value })}
            placeholder="ods_orders.pay_amount"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="new-metric-alias"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            同义别名（逗号分隔）
          </label>
          <input
            id="new-metric-alias"
            value={draft.alias}
            onChange={(e) => onChange({ ...draft, alias: e.target.value })}
            placeholder="别名1, 别名2"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={saving || !draft.name.trim() || !draft.description.trim()}
            onClick={() => void onSubmit()}
          >
            {saving ? "保存中..." : creating ? "确认添加指标" : "保存指标元数据"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}
