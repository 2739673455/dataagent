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
  mode: "bootstrap" | "incremental" | "reconcile" | "clear";
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
export type TableInfo = ApiSchemas["TableInfoResponse"];
export type TableRole = ApiSchemas["TableInfoRequest"]["role"];

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

const TASK_POLL_INTERVAL_MS = 1000;
const TASK_TIMEOUT_MS = 60 * 60 * 1000;

async function waitForTask<T>(taskId: string): Promise<T> {
  const deadline = Date.now() + TASK_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const response = await appClient.get<TaskStatusResponse<T>>(`/api/v1/tasks/${taskId}`);
    const task = response.data;
    if (task.ready) {
      if (task.successful && task.result !== null) return task.result;
      throw new Error(task.error || `后台任务执行失败：${task.state}`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, TASK_POLL_INTERVAL_MS));
  }
  throw new Error("后台任务执行超时，请稍后在任务状态中查看结果");
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
    await appClient.put(`/api/v1/meta/tables/${tableName}/columns/${columnName}`, data);
  },

  async upsertMetric(metricName: string, data: MetricInfoRequest): Promise<void> {
    await appClient.put(`/api/v1/meta/metrics/${metricName}`, data);
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

  async syncTableValues(tables: string[]): Promise<ColumnValueIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnValueIndexSyncResponse>(
      "/api/v1/meta/tables/sync-values",
      { tables }
    );
    return response.results;
  },

  async syncColumnIndexes(
    columns: ColumnReference[]
  ): Promise<ColumnSemanticIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnSemanticIndexSyncResponse>(
      "/api/v1/meta/columns/sync",
      { columns }
    );
    return response.results;
  },

  async syncColumnValues(
    columns: ColumnReference[]
  ): Promise<ColumnValueIndexSyncResponse[]> {
    const response = await submitTask<BatchColumnValueIndexSyncResponse>(
      "/api/v1/meta/columns/sync-values",
      { columns }
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
