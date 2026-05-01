/**
 * 计算关联关系的差异
 * @param current 当前选中的ID列表
 * @param original 原始的ID列表
 * @returns 需要添加和移除的ID列表
 */
export function computeRelationDiff(current: number[], original: number[]) {
  return {
    toAdd: current.filter((id) => !original.includes(id)),
    toRemove: original.filter((id) => !current.includes(id)),
  };
}

/**
 * 检查关联关系是否有变化
 */
export function hasRelationChanges(current: number[], original: number[]): boolean {
  const diff = computeRelationDiff(current, original);
  return diff.toAdd.length > 0 || diff.toRemove.length > 0;
}
