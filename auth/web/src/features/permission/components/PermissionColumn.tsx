import { AlertCircle, ArrowDownAz, ArrowUpAz, ArrowUpDown, Plus, X } from "lucide-react";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { PanelInput } from "@/shared/components/ui/panel-input";
import type { SortState } from "@/features/permission/types";

interface SortFieldConfig<F extends string> {
  field: F;
  label: string;
}

interface PermissionColumnProps<T, F extends string> {
  title: string;
  icon: React.ReactNode;
  isFiltering: boolean;
  search: string;
  onSearchChange: (value: string) => void;
  sort: SortState<F>;
  sortFields: SortFieldConfig<F>[];
  onToggleSort: (field: F) => void;
  items: T[];
  renderItem: (item: T) => React.ReactNode;
  onCreate: () => void;
  emptyText?: string;
}

/**
 * PermissionColumn - 权限管理列组件
 *
 * 通用列布局组件，用于用户/角色/权限三栏显示
 * 包含：标题栏、搜索框、排序按钮、列表区域、创建按钮
 */
export function PermissionColumn<T, F extends string>({
  title,
  icon,
  isFiltering,
  search,
  onSearchChange,
  sort,
  sortFields,
  onToggleSort,
  items,
  renderItem,
  onCreate,
  emptyText = "空",
}: PermissionColumnProps<T, F>) {
  return (
    // 列容器：内嵌阴影的新拟态风格
    <div className="bg-[#e8e4df] rounded-3xl flex flex-col overflow-hidden shadow-[inset_6px_6px_12px_#c9c5be,inset_-6px_-6px_12px_#ffffff]">
      {/* 头部区域：标题栏、搜索栏、排序按钮 */}
      <div className="p-4">
        {/* 标题栏：图标、标题、筛选状态、创建按钮 */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            {icon}
            <span className="font-semibold text-stone-700">{title}</span>
            {isFiltering && (
              <Badge className="bg-amber-500 hover:bg-amber-500 text-white text-xs pointer-events-none">
                筛选中
              </Badge>
            )}
          </div>
          <Button
            size="sm"
            onClick={onCreate}
            className="bg-stone-600 hover:bg-stone-700 rounded-xl"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>

        {/* 搜索栏：输入框 + 清除按钮 */}
        <div className="mb-3 relative">
          <PanelInput
            placeholder={`搜索${title}`}
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            className="h-9 pr-8 text-sm bg-[#f0ece6]"
          />
          {search && (
            <button
              type="button"
              onClick={() => onSearchChange("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-600"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* 排序按钮角色：按 ID、名称等排序 */}
        <div className="flex gap-1">
          {sortFields.map(({ field, label }) => (
            <button
              key={field}
              type="button"
              onClick={() => onToggleSort(field)}
              className={`flex items-center gap-1 px-2 py-1 text-xs rounded-lg transition-colors ${
                sort.field === field
                  ? "bg-stone-600 text-white"
                  : "text-stone-500 hover:bg-stone-200/50"
              }`}
            >
              {label}
              {sort.field === field ? (
                sort.order === "asc" ? (
                  <ArrowUpAz className="h-3 w-3" />
                ) : (
                  <ArrowDownAz className="h-3 w-3" />
                )
              ) : (
                <ArrowUpDown className="h-3 w-3" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* 列表区域：显示实体列表或空状态 */}
      <div className="overflow-y-auto p-2 space-y-1" style={{ height: "calc(100% - 140px)" }}>
        {items.length === 0 ? (
          // 空状态提示
          <div className="flex flex-col items-center justify-center py-8 text-stone-400">
            <AlertCircle className="h-8 w-8 mb-2" />
            <span>{emptyText}</span>
          </div>
        ) : (
          // 渲染列表项
          items.map(renderItem)
        )}
      </div>
    </div>
  );
}
