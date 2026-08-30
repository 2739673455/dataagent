import type { ColumnInfo } from "@/api/meta";

// 格式化 ISO 时间字符串为 YYYY-MM-DD HH:mm:ss
export function formatDateTime(isoString?: string | null): string {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return isoString;
    const pad = (n: number) => String(n).padStart(2, "0");
    const year = d.getFullYear();
    const month = pad(d.getMonth() + 1);
    const day = pad(d.getDate());
    const hours = pad(d.getHours());
    const minutes = pad(d.getMinutes());
    const seconds = pad(d.getSeconds());
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
  } catch {
    return isoString;
  }
}

// 格式化取值索引同步模式
export function formatValueIndexSyncMode(mode?: "full" | "incremental" | null): string {
  if (mode === "full") return "全量";
  if (mode === "incremental") return "增量";
  return "未知";
}

// 构造取值索引同步状态悬停详情
export function formatValueIndexSyncDetails(
  state: NonNullable<ColumnInfo["value_index_state"]>
): string {
  const statusLabel =
    state.status === "syncing" ? "同步中" : state.status === "failed" ? "同步失败" : "同步成功";
  const lines = [`当前状态：${statusLabel}`];
  if (state.last_synced_at) {
    lines.push(
      `最近成功：${formatValueIndexSyncMode(state.last_sync_mode)} ${formatDateTime(state.last_synced_at)}`
    );
  }
  lines.push(`最近全量：${formatDateTime(state.last_full_synced_at) || "无"}`);
  lines.push(`最近增量：${formatDateTime(state.last_incremental_synced_at) || "无"}`);
  lines.push(`同步代次：${state.current_generation || "无"}`);
  if (state.last_error) lines.push(`失败原因：${state.last_error}`);
  return lines.join("\n");
}

// 拆分逗号分隔字符串为干净数组
export function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

// 解析指标关联的数据列
export function parseMetricColumns(value: string): Array<{ t_name: string; c_name: string }> {
  return splitCsv(value)
    .map((item) => {
      const parts = item.split(".");
      if (parts.length >= 2) {
        return {
          t_name: parts[0].trim(),
          c_name: parts.slice(1).join(".").trim(),
        };
      }
      return null;
    })
    .filter(
      (col): col is { t_name: string; c_name: string } =>
        col !== null && Boolean(col.t_name && col.c_name)
    );
}
