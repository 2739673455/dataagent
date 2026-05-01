import { Edit, Trash2 } from "lucide-react";
import { Button } from "@/shared/components/ui/button";

interface ListItemProps {
  children: React.ReactNode;
  isSelected: boolean;
  isDisabled: boolean;
  onClick: () => void;
  onEdit: () => void;
  onDelete: () => void;
  extraButtons?: React.ReactNode;
}

/**
 * ListItem - 列表项组件
 *
 * 用于显示用户/组/权限列表中的单个项目
 * 包含：内容区域、关联编辑按钮、编辑按钮、删除按钮
 */
export function ListItem({
  children,
  isSelected,
  isDisabled,
  onClick,
  onEdit,
  onDelete,
  extraButtons,
}: ListItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full cursor-pointer items-center justify-between rounded-xl border px-3 py-2 text-left transition-colors ${
        isSelected
          ? "border-stone-600 bg-stone-600 text-white"
          : isDisabled
            ? "border-transparent opacity-50 hover:border-stone-600 hover:bg-stone-200/50"
            : "border-transparent hover:border-stone-600 hover:bg-stone-200/50"
      }`}
    >
      {children}
      <div className="flex items-center gap-1">
        {extraButtons}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`h-7 w-7 ${
            isSelected
              ? "text-white hover:bg-white/20"
              : "text-stone-500 hover:bg-stone-300/50 hover:text-stone-700"
          }`}
          onClick={(e) => {
            e.stopPropagation();
            onEdit();
          }}
          title="编辑信息"
        >
          <Edit className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`h-7 w-7 ${
            isSelected
              ? "text-white hover:bg-white/20"
              : "text-red-400 hover:bg-stone-300/50 hover:text-red-600"
          }`}
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title="删除"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
    </button>
  );
}
