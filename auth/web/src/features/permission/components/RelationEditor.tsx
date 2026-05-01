import { Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { PanelInput } from "@/shared/components/ui/panel-input";

interface RelationItem {
  id: number;
  name: string;
  description?: string | null;
}

interface RelationEditorProps {
  title: string;
  allItems: RelationItem[];
  selectedIds: number[];
  onChange: (selected: number[]) => void;
  currentItem?: {
    type: string;
    id?: number | null;
    name: string;
    description?: string | null;
  };
}

export function RelationEditor({
  title,
  allItems,
  selectedIds,
  onChange,
  currentItem,
}: RelationEditorProps) {
  const [leftSearch, setLeftSearch] = useState("");
  const [rightSearch, setRightSearch] = useState("");
  const [leftSelected, setLeftSelected] = useState<number[]>([]);
  const [rightSelected, setRightSelected] = useState<number[]>([]);

  // 保存初始状态，用于对比显示待提交变更
  const [initialSelectedIds] = useState(() => [...selectedIds]);

  // 计算待提交的变更
  const pendingChanges = useMemo(() => {
    const toAdd = selectedIds.filter((id) => !initialSelectedIds.includes(id));
    const toRemove = initialSelectedIds.filter((id) => !selectedIds.includes(id));
    return { toAdd, toRemove };
  }, [selectedIds, initialSelectedIds]);

  // 已关联和未关联的项目
  const associated = allItems.filter((item) => selectedIds.includes(item.id));
  const unassociated = allItems.filter((item) => !selectedIds.includes(item.id));

  // 搜索过滤
  const filteredAssociated = associated.filter(
    (item) =>
      item.name.toLowerCase().includes(leftSearch.toLowerCase()) ||
      item.description?.toLowerCase().includes(leftSearch.toLowerCase())
  );
  const filteredUnassociated = unassociated.filter(
    (item) =>
      item.name.toLowerCase().includes(rightSearch.toLowerCase()) ||
      item.description?.toLowerCase().includes(rightSearch.toLowerCase())
  );

  // 全选
  const selectAllLeft = () => {
    setLeftSelected(filteredAssociated.map((i) => i.id));
  };
  const selectAllRight = () => {
    setRightSelected(filteredUnassociated.map((i) => i.id));
  };

  // 清除选择
  const clearLeft = () => {
    setLeftSelected([]);
  };
  const clearRight = () => {
    setRightSelected([]);
  };

  // 移动到右侧（取消关联）
  const moveToRight = () => {
    onChange(selectedIds.filter((id) => !leftSelected.includes(id)));
    setLeftSelected([]);
  };

  // 移动到左侧（添加关联）
  const moveToLeft = () => {
    onChange([...selectedIds, ...rightSelected]);
    setRightSelected([]);
  };

  // 切换选择
  const toggleLeft = (id: number) => {
    setLeftSelected((prev) => (prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]));
  };
  const toggleRight = (id: number) => {
    setRightSelected((prev) => (prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]));
  };

  const renderList = (
    items: RelationItem[],
    selected: number[],
    toggle: (id: number) => void,
    search: string,
    setSearch: (s: string) => void,
    selectAll: () => void,
    clearSelection: () => void,
    emptyText: string,
    pendingCount?: number,
    pendingLabel?: string,
    pendingColor?: "green" | "red"
  ) => (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 mb-2">
        <div className="relative flex-1">
          <PanelInput
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 w-full pr-8 text-sm"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-600"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={selectAll}
          className="h-8 rounded-xl border-stone-600 bg-transparent px-2 text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
        >
          全选
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={clearSelection}
          className="h-8 rounded-xl border-stone-600 bg-transparent px-2 text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
        >
          取消
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto rounded-xl bg-[#faf7f2] p-2">
        {items.length === 0 ? (
          <p className="text-sm text-stone-400 text-center py-4">{emptyText}</p>
        ) : (
          <div className="space-y-1">
            {items.map((item) => {
              // 判断是否是待提交的变更
              const isPendingAdd = pendingChanges.toAdd.includes(item.id);
              const isPendingRemove = pendingChanges.toRemove.includes(item.id);

              return (
                <button
                  type="button"
                  key={item.id}
                  onClick={() => toggle(item.id)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                    selected.includes(item.id)
                      ? "border border-stone-600 bg-[#f0ece6] text-stone-700 ring-1 ring-stone-600"
                      : "border border-transparent hover:border-stone-400 hover:bg-[#f0ece6]"
                  } ${
                    // 待提交变更改为 ring 高亮，避免出现边框
                    isPendingAdd
                      ? "ring-2 ring-green-400"
                      : isPendingRemove
                        ? "ring-2 ring-red-400"
                        : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <div className="font-medium flex-1">{item.name}</div>
                    {/* 待提交变更标识 - 去掉 hover 效果 */}
                    {isPendingAdd && (
                      <Badge className="bg-green-500 hover:bg-green-500 text-white text-[10px] px-1.5 py-0 h-5 flex items-center gap-0.5 shrink-0 pointer-events-none">
                        <Plus className="h-3 w-3" />
                        待添加
                      </Badge>
                    )}
                    {isPendingRemove && (
                      <Badge className="bg-red-500 hover:bg-red-500 text-white text-[10px] px-1.5 py-0 h-5 flex items-center gap-0.5 shrink-0 pointer-events-none">
                        <Trash2 className="h-3 w-3" />
                        待移除
                      </Badge>
                    )}
                  </div>
                  {item.description && (
                    <div className="text-xs opacity-70 truncate">{item.description}</div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
      {/* 变更统计 - 左下显示待添加，右下显示待移除 */}
      {pendingCount !== undefined && pendingCount > 0 && (
        <div className="mt-2 text-xs">
          <span className={pendingColor === "green" ? "text-green-600" : "text-red-600"}>
            {pendingLabel} {pendingCount} 项
          </span>
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-3">
      {currentItem && (
        <div className="flex min-h-11 items-center gap-3 rounded-xl bg-[#faf7f2] px-4 py-2 text-sm text-stone-700">
          <span className="shrink-0 text-stone-500">当前编辑</span>
          <Badge className="shrink-0 bg-stone-600 text-[#e8e4df] hover:bg-stone-600">
            {currentItem.type}
          </Badge>
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              {currentItem.id !== undefined && currentItem.id !== null && (
                <span className="shrink-0 text-xs text-stone-400">#{currentItem.id}</span>
              )}
              <span className="truncate font-medium">{currentItem.name}</span>
            </div>
            {currentItem.description && (
              <div className="truncate text-xs text-stone-500">{currentItem.description}</div>
            )}
          </div>
        </div>
      )}
      <div className="flex h-[560px] gap-4">
        <div className="flex-1">
          <div className="mb-2 text-sm font-medium text-stone-600">已关联{title}</div>
          {renderList(
            filteredAssociated,
            leftSelected,
            toggleLeft,
            leftSearch,
            setLeftSearch,
            selectAllLeft,
            clearLeft,
            "暂无已关联",
            pendingChanges.toAdd.length,
            "待添加",
            "green"
          )}
        </div>
        <div className="flex flex-col justify-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={moveToLeft}
            disabled={rightSelected.length === 0}
            className="rounded-lg border-stone-600 bg-transparent text-stone-600 shadow-none transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
          >
            &lt;
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={moveToRight}
            disabled={leftSelected.length === 0}
            className="rounded-lg border-stone-600 bg-transparent text-stone-600 shadow-none transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
          >
            &gt;
          </Button>
        </div>
        <div className="flex-1">
          <div className="mb-2 text-sm font-medium text-stone-600">未关联{title}</div>
          {renderList(
            filteredUnassociated,
            rightSelected,
            toggleRight,
            rightSearch,
            setRightSearch,
            selectAllRight,
            clearRight,
            "暂无未关联",
            pendingChanges.toRemove.length,
            "待移除",
            "red"
          )}
        </div>
      </div>
    </div>
  );
}
