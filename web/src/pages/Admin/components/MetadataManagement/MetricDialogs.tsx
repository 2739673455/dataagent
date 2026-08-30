import type { MetricInfo } from "@/api/meta";
import {
  AdminDialogActions,
  AdminDialogCancelButton,
  AdminDialogPrimaryButton,
  AdminEditorDialog,
} from "../AdminEditorDialog";

export function MetricCreateDialog({
  isOpen,
  newMetricAlias,
  newMetricColumns,
  newMetricDesc,
  newMetricName,
  onClose,
  onSubmit,
  savingMetric,
  setNewMetricAlias,
  setNewMetricColumns,
  setNewMetricDesc,
  setNewMetricName,
}: {
  isOpen: boolean;
  newMetricAlias: string;
  newMetricColumns: string;
  newMetricDesc: string;
  newMetricName: string;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  savingMetric: boolean;
  setNewMetricAlias: (val: string) => void;
  setNewMetricColumns: (val: string) => void;
  setNewMetricDesc: (val: string) => void;
  setNewMetricName: (val: string) => void;
}) {
  if (!isOpen) return null;

  return (
    <AdminEditorDialog ariaLabel="添加指标元数据" onClose={onClose} title="添加指标元数据">
      <div className="space-y-3">
        <div>
          <label
            htmlFor="new-metric-name"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            指标名称
          </label>
          <input
            id="new-metric-name"
            value={newMetricName}
            onChange={(e) => setNewMetricName(e.target.value)}
            placeholder="如：gmv_total"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="new-metric-desc"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            业务口径说明
          </label>
          <textarea
            id="new-metric-desc"
            value={newMetricDesc}
            onChange={(e) => setNewMetricDesc(e.target.value)}
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
            value={newMetricColumns}
            onChange={(e) => setNewMetricColumns(e.target.value)}
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
            value={newMetricAlias}
            onChange={(e) => setNewMetricAlias(e.target.value)}
            placeholder="别名1, 别名2"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={savingMetric || !newMetricName.trim() || !newMetricDesc.trim()}
            onClick={() => void onSubmit()}
          >
            {savingMetric ? "正在添加..." : "确认添加指标"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}

export function MetricEditDialog({
  editMetricAlias,
  editMetricColumns,
  editMetricDesc,
  editingMetric,
  onClose,
  onSubmit,
  savingMetric,
  setEditMetricAlias,
  setEditMetricColumns,
  setEditMetricDesc,
}: {
  editMetricAlias: string;
  editMetricColumns: string;
  editMetricDesc: string;
  editingMetric: MetricInfo | null;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  savingMetric: boolean;
  setEditMetricAlias: (val: string) => void;
  setEditMetricColumns: (val: string) => void;
  setEditMetricDesc: (val: string) => void;
}) {
  if (!editingMetric) return null;

  return (
    <AdminEditorDialog
      ariaLabel={`编辑指标元数据 ${editingMetric.name}`}
      onClose={onClose}
      title={`编辑指标元数据: ${editingMetric.name}`}
    >
      <div className="space-y-3">
        <div>
          <label
            htmlFor="edit-metric-desc"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            业务口径说明
          </label>
          <textarea
            id="edit-metric-desc"
            value={editMetricDesc}
            onChange={(e) => setEditMetricDesc(e.target.value)}
            placeholder="指标的业务统计口径、计算公式与业务含义"
            rows={2}
            className="w-full rounded border border-[#d4d4ce] bg-[#ffffff] p-2 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="edit-metric-columns"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            关联数据列（逗号分隔）
          </label>
          <input
            id="edit-metric-columns"
            value={editMetricColumns}
            onChange={(e) => setEditMetricColumns(e.target.value)}
            placeholder="ods_orders.pay_amount"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="edit-metric-alias"
            className="block text-xs font-medium text-[#71717a] mb-1"
          >
            同义别名（逗号分隔）
          </label>
          <input
            id="edit-metric-alias"
            value={editMetricAlias}
            onChange={(e) => setEditMetricAlias(e.target.value)}
            placeholder="别名1, 别名2"
            className="h-8 w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-2.5 text-xs text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none"
          />
        </div>
        <AdminDialogActions>
          <AdminDialogCancelButton onClick={onClose}>取消</AdminDialogCancelButton>
          <AdminDialogPrimaryButton
            disabled={savingMetric || !editMetricDesc.trim()}
            onClick={() => void onSubmit()}
          >
            {savingMetric ? "保存中..." : "保存指标元数据"}
          </AdminDialogPrimaryButton>
        </AdminDialogActions>
      </div>
    </AdminEditorDialog>
  );
}
