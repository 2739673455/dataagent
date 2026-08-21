import appClient from "@/api/appClient";

export interface ColumnReference {
  t_name: string;
  c_name: string;
}

export interface TableInfo {
  name: string;
  role: string;
  primary_key_columns: string[];
  description: string;
  meta_version: number;
}

export interface ColumnInfo {
  t_name: string;
  name: string;
  type: string;
  examples: unknown[];
  description: string;
  alias: string[];
  index_values: boolean;
  reference_t_name: string | null;
  reference_c_name: string | null;
  meta_version: number;
  index_version: number;
  value_index_synced_at: string | null;
  value_index_sync_status: "syncing" | "succeeded" | "failed" | null;
}

export interface MetricInfo {
  name: string;
  description: string;
  relevant_columns: ColumnReference[];
  alias: string[];
  meta_version: number;
  index_version: number;
}

interface ResourceImportChanges {
  created_count: number;
  updated_count: number;
  deleted_count: number;
  created_keys: string[];
  updated_keys: string[];
  deleted_keys: string[];
}

export interface MetaImportResponse {
  mode: string;
  dry_run: boolean;
  tables: ResourceImportChanges;
  columns: ResourceImportChanges;
  metrics: ResourceImportChanges;
}

export interface ColumnIndexSyncResponse {
  t_name: string;
  c_name: string;
  indexed_count: number;
}

export interface MetricIndexSyncResponse {
  metric_name: string;
  indexed_count: number;
}

export const metaApi = {
  async listTables(): Promise<TableInfo[]> {
    const response = await appClient.get<TableInfo[]>("/api/v1/meta/tables");
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

  async upsertTable(tableName: string, data: { role: string; description: string }): Promise<void> {
    await appClient.put(`/api/v1/meta/tables/${tableName}`, data);
  },

  async upsertColumn(
    tableName: string,
    columnName: string,
    data: {
      description: string;
      alias: string[];
      index_values: boolean;
      reference_t_name?: string | null;
      reference_c_name?: string | null;
    }
  ): Promise<void> {
    await appClient.put(`/api/v1/meta/tables/${tableName}/columns/${columnName}`, data);
  },

  async upsertMetric(
    metricName: string,
    data: {
      description: string;
      relevant_columns: ColumnReference[];
      alias: string[];
    }
  ): Promise<void> {
    await appClient.put(`/api/v1/meta/metrics/${metricName}`, data);
  },

  async deleteTable(tableName: string): Promise<void> {
    await appClient.delete(`/api/v1/meta/tables/${tableName}`);
  },

  async deleteColumn(tableName: string, columnName: string): Promise<void> {
    await appClient.delete(`/api/v1/meta/tables/${tableName}/columns/${columnName}`);
  },

  async deleteMetric(metricName: string): Promise<void> {
    await appClient.delete(`/api/v1/meta/metrics/${metricName}`);
  },

  async exportMetadata(): Promise<Blob> {
    const response = await appClient.get("/api/v1/meta/export", {
      responseType: "blob",
    });
    return response.data as Blob;
  },

  async importMetadata(
    file: File,
    mode: "merge" | "overwrite" = "merge",
    dryRun = false
  ): Promise<MetaImportResponse> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await appClient.post<MetaImportResponse>(
      `/api/v1/meta/import?mode=${mode}&dry_run=${dryRun}`,
      formData,
      {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      }
    );
    return response.data;
  },

  async syncColumnIndexes(columns: ColumnReference[]): Promise<ColumnIndexSyncResponse[]> {
    const response = await appClient.post<{ results: ColumnIndexSyncResponse[] }>(
      "/api/v1/meta/columns/sync",
      { columns }
    );
    return response.data.results;
  },

  async syncColumnValues(columns: ColumnReference[]): Promise<ColumnIndexSyncResponse[]> {
    const response = await appClient.post<{ results: ColumnIndexSyncResponse[] }>(
      "/api/v1/meta/columns/sync-values",
      { columns }
    );
    return response.data.results;
  },

  async syncMetricIndexes(metrics: string[]): Promise<MetricIndexSyncResponse[]> {
    const response = await appClient.post<{ results: MetricIndexSyncResponse[] }>(
      "/api/v1/meta/metrics/sync",
      { metrics }
    );
    return response.data.results;
  },
};
