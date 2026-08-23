import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type ColumnIndexSyncResponse = ApiSchemas["ColumnIndexSyncResponse"];
export type ColumnInfo = ApiSchemas["ColumnInfoResponse"];
export type ColumnReference = ApiSchemas["ColumnReference"];
export type ImportMode = ApiSchemas["ImportMode"];
export type MetaImportResponse = ApiSchemas["MetaImportResponse"];
export type MetricIndexSyncResponse = ApiSchemas["MetricIndexSyncResponse"];
export type MetricInfo = ApiSchemas["MetricInfoResponse"];
export type TableInfo = ApiSchemas["TableInfoResponse"];
export type TableRole = ApiSchemas["TableInfoRequest"]["role"];

type BatchIndexSyncResponse = ApiSchemas["BatchIndexSyncResponse"];
type BatchMetricIndexSyncResponse = ApiSchemas["BatchMetricIndexSyncResponse"];
type ColumnInfoRequest = ApiSchemas["ColumnInfoRequest"];
type MetricInfoRequest = ApiSchemas["MetricInfoRequest"];
type TableInfoRequest = ApiSchemas["TableInfoRequest"];

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
    const response = await appClient.post<MetaImportResponse>(
      `/api/v1/meta/import?mode=${mode}&dry_run=${dryRun}`,
      formData,
      {
        headers: {
          "Content-Type": "multipart/form-data",
        },
        timeout: 180000,
      }
    );
    return response.data;
  },

  async syncTableIndexes(tables: string[]): Promise<ColumnIndexSyncResponse[]> {
    const response = await appClient.post<BatchIndexSyncResponse>(
      "/api/v1/meta/tables/sync",
      { tables },
      { timeout: 180000 }
    );
    return response.data.results;
  },

  async syncTableValues(tables: string[]): Promise<ColumnIndexSyncResponse[]> {
    const response = await appClient.post<BatchIndexSyncResponse>(
      "/api/v1/meta/tables/sync-values",
      { tables },
      { timeout: 180000 }
    );
    return response.data.results;
  },

  async syncColumnIndexes(columns: ColumnReference[]): Promise<ColumnIndexSyncResponse[]> {
    const response = await appClient.post<BatchIndexSyncResponse>(
      "/api/v1/meta/columns/sync",
      { columns },
      { timeout: 180000 }
    );
    return response.data.results;
  },

  async syncColumnValues(columns: ColumnReference[]): Promise<ColumnIndexSyncResponse[]> {
    const response = await appClient.post<BatchIndexSyncResponse>(
      "/api/v1/meta/columns/sync-values",
      { columns },
      { timeout: 180000 }
    );
    return response.data.results;
  },

  async syncMetricIndexes(metrics: string[]): Promise<MetricIndexSyncResponse[]> {
    const response = await appClient.post<BatchMetricIndexSyncResponse>(
      "/api/v1/meta/metrics/sync",
      { metrics },
      { timeout: 180000 }
    );
    return response.data.results;
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
