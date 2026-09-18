import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

interface SemanticIndexSyncStats {
  created_count: number;
  updated_count: number;
  deleted_count: number;
  embedded_count: number;
  unchanged_count: number;
  target_version: number;
  version_committed: boolean;
}

export interface ColumnSemanticIndexSyncResponse extends SemanticIndexSyncStats {
  t_name: string;
  c_name: string;
}
export type ColumnInfo = ApiSchemas["ColumnInfoResponse"];
export type ColumnReference = ApiSchemas["ColumnReference"];
export type ImportMode = ApiSchemas["ImportMode"];
export type MetaImportResponse = ApiSchemas["MetaImportResponse"];
export interface ColumnValueIndexSyncResponse {
  t_name: string;
  c_name: string;
  mode: "full" | "incremental" | "clear";
  read_value_count: number;
  upserted_count: number;
  removed_count: number;
  cursor_value: unknown | null;
  sync_generation: string | null;
}

export interface MetricSemanticIndexSyncResponse extends SemanticIndexSyncStats {
  metric_name: string;
}
export type MetricInfo = ApiSchemas["MetricInfoResponse"];
type SemanticIndexUpsertResponse = ApiSchemas["SemanticIndexUpsertResponse"];
export type TableInfo = ApiSchemas["TableInfoResponse"];
export type TableRole = ApiSchemas["TableInfoRequest"]["role"];
export type ValueIndexSyncRequestMode = ApiSchemas["ColumnValueIndexSyncRequest"]["mode"];

interface BatchColumnSemanticIndexSyncResponse {
  results: ColumnSemanticIndexSyncResponse[];
}

interface BatchColumnValueIndexSyncResponse {
  results: ColumnValueIndexSyncResponse[];
}

interface BatchMetricSemanticIndexSyncResponse {
  results: MetricSemanticIndexSyncResponse[];
}
type ColumnInfoRequest = ApiSchemas["ColumnInfoRequest"];
type MetricInfoRequest = ApiSchemas["MetricInfoRequest"];
type TableInfoRequest = ApiSchemas["TableInfoRequest"];

interface TaskAcceptedResponse {
  task_id: string;
}

interface TaskStatusResponse<T> {
  state: string;
  ready: boolean;
  successful: boolean | null;
  result: T | null;
  error: string | null;
}

const METADATA_UPSERT_TASK_TIMEOUT_MS = 10 * 60 * 1000;
const BATCH_TASK_TIMEOUT_MS = 60 * 60 * 1000;

function getTaskPollInterval(elapsedMs: number): number {
  if (elapsedMs < 10 * 1000) return 1000;
  if (elapsedMs < 60 * 1000) return 2000;
  if (elapsedMs < 5 * 60 * 1000) return 5000;
  return 10 * 1000;
}

async function waitForTask<T>(
  taskId: string,
  timeoutMs: number = BATCH_TASK_TIMEOUT_MS
): Promise<T> {
  const startedAt = Date.now();
  const deadline = startedAt + timeoutMs;
  while (Date.now() < deadline) {
    const response = await appClient.get<TaskStatusResponse<T>>(`/api/v1/tasks/${taskId}`);
    const task = response.data;
    if (task.ready) {
      if (task.successful && task.result !== null) return task.result;
      throw new Error(task.error || `后台任务执行失败：${task.state}`);
    }
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) break;
    const pollIntervalMs = getTaskPollInterval(Date.now() - startedAt);
    await new Promise((resolve) =>
      window.setTimeout(resolve, Math.min(pollIntervalMs, remainingMs))
    );
  }
  throw new Error("前端等待后台任务超时，任务仍可能在后台执行，请稍后刷新查看结果");
}

async function submitTask<T>(url: string, data: unknown): Promise<T> {
  const response = await appClient.post<TaskAcceptedResponse>(url, data);
  return waitForTask<T>(response.data.task_id);
}

export const metaApi = {
  async listTables(): Promise<TableInfo[]> {
    const response = await appClient.get<TableInfo[]>("/api/v1/meta/tables");
    return response.data;
  },

  async listSourceTables(): Promise<string[]> {
    const response = await appClient.get<string[]>("/api/v1/meta/source-tables");
    return response.data;
  },

  async listColumns(tableName: string): Promise<ColumnInfo[]> {
    const response = await appClient.get<ColumnInfo[]>(`/api/v1/meta/tables/${tableName}/columns`);
    return response.data;
  },

  async listMetrics(): Promise<MetricInfo[]> {
    const response = await appClient.get<MetricInfo[]>("/api/v1/meta/metrics");
    return response.data;
  },

  async upsertTable(tableName: string, data: TableInfoRequest): Promise<void> {
    await appClient.put(`/api/v1/meta/tables/${tableName}`, data);
  },

  async upsertColumn(
    tableName: string,
    columnName: string,
    data: ColumnInfoRequest
  ): Promise<void> {
    const response = await appClient.put<SemanticIndexUpsertResponse>(
      `/api/v1/meta/tables/${tableName}/columns/${columnName}`,
      data
    );
    if (response.data.semantic_index_task_id) {
      await waitForTask<BatchColumnSemanticIndexSyncResponse>(
        response.data.semantic_index_task_id,
        METADATA_UPSERT_TASK_TIMEOUT_MS
      );
    }
  },

  async upsertMetric(metricName: string, data: MetricInfoRequest): Promise<void> {
    const response = await appClient.put<SemanticIndexUpsertResponse>(
      `/api/v1/meta/metrics/${metricName}`,
      data
    );
    if (response.data.semantic_index_task_id) {
      await waitForTask<BatchMetricSemanticIndexSyncResponse>(
        response.data.semantic_index_task_id,
        METADATA_UPSERT_TASK_TIMEOUT_MS
      );
    }
  },

  async exportMetadata(): Promise<Blob> {
    const response = await appClient.get("/api/v1/meta/export", {
      responseType: "blob",
    });
    return response.data as Blob;
  },

  async importMetadata(
    file: File,
    mode: ImportMode = "merge",
    dryRun = false
  ): Promise<MetaImportResponse> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await appClient.post<MetaImportResponse | TaskAcceptedResponse>(
      `/api/v1/meta/import?mode=${mode}&dry_run=${dryRun}`,
      formData,
      {
        headers: {
          "Content-Type": "multipart/form-data",
        },
        timeout: 180000,
      }
    );
    if ("task_id" in response.data) {
      return waitForTask<MetaImportResponse>(response.data.task_id);
    }
    return response.data;
  },

  async syncTableIndexes(tables: string[]): Promise<ColumnSemanticIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnSemanticIndexSyncResponse>(
      "/api/v1/meta/tables/sync",
      { tables }
    );
    return response.results;
  },

  async syncTableValues(
    tables: string[],
    mode: ValueIndexSyncRequestMode
  ): Promise<ColumnValueIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnValueIndexSyncResponse>(
      "/api/v1/meta/tables/sync-values",
      { tables, mode }
    );
    return response.results;
  },

  async syncColumnIndexes(columns: ColumnReference[]): Promise<ColumnSemanticIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnSemanticIndexSyncResponse>(
      "/api/v1/meta/columns/sync",
      { columns }
    );
    return response.results;
  },

  async syncColumnValues(
    columns: ColumnReference[],
    mode: ValueIndexSyncRequestMode
  ): Promise<ColumnValueIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnValueIndexSyncResponse>(
      "/api/v1/meta/columns/sync-values",
      { columns, mode }
    );
    return response.results;
  },

  async syncMetricIndexes(metrics: string[]): Promise<MetricSemanticIndexSyncResponse[]> {
    const response = await submitTask<BatchMetricSemanticIndexSyncResponse>(
      "/api/v1/meta/metrics/sync",
      { metrics }
    );
    return response.results;
  },

  async deleteTables(tables: string[]): Promise<void> {
    await appClient.post("/api/v1/meta/tables/batch-delete", { tables });
  },

  async deleteColumns(columns: ColumnReference[]): Promise<void> {
    await appClient.post("/api/v1/meta/columns/batch-delete", { columns });
  },

  async deleteMetrics(metrics: string[]): Promise<void> {
    await appClient.post("/api/v1/meta/metrics/batch-delete", { metrics });
  },
};
