import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type QueryExperienceDeletionResponse = ApiSchemas["QueryExperienceDeletionResponse"];
export type QueryExperienceDetailResponse = ApiSchemas["QueryExperienceDetailResponse"];
export type QueryExperienceSourceExecutionListResponse =
  ApiSchemas["QueryExperienceSourceExecutionListResponse"];
export type QueryExperienceListResponse = ApiSchemas["QueryExperienceListResponse"];
export type QueryExperienceStatus = ApiSchemas["QueryExperienceStatus"];
type QueryExperienceBatchRequest = ApiSchemas["QueryExperienceBatchRequest"];

export const queryExperiencesApi = {
  async listQueryExperiences(params: {
    limit: number;
    offset: number;
    roleName?: string;
    status?: QueryExperienceStatus;
    query?: string;
  }): Promise<QueryExperienceListResponse> {
    const response = await appClient.get<QueryExperienceListResponse>(
      "/api/v1/admin/query-experiences",
      {
        params: {
          limit: params.limit,
          offset: params.offset,
          ...(params.roleName ? { role_name: params.roleName } : {}),
          ...(params.status ? { status: params.status } : {}),
          ...(params.query?.trim() ? { query: params.query.trim() } : {}),
        },
      }
    );
    return response.data;
  },

  async getQueryExperience(id: string): Promise<QueryExperienceDetailResponse> {
    const response = await appClient.get<QueryExperienceDetailResponse>(
      `/api/v1/admin/query-experiences/${id}`
    );
    return response.data;
  },

  async listQueryExperienceSourceExecutions(
    id: string,
    limit: number,
    offset: number
  ): Promise<QueryExperienceSourceExecutionListResponse> {
    const response = await appClient.get<QueryExperienceSourceExecutionListResponse>(
      `/api/v1/admin/query-experiences/${id}/executions`,
      { params: { limit, offset } }
    );
    return response.data;
  },

  async disableQueryExperience(id: string): Promise<QueryExperienceDetailResponse> {
    const response = await appClient.post<QueryExperienceDetailResponse>(
      `/api/v1/admin/query-experiences/${id}/disable`
    );
    return response.data;
  },

  async disableQueryExperiences(experienceIds: string[]): Promise<void> {
    await appClient.post("/api/v1/admin/query-experiences/batch-disable", {
      experience_ids: experienceIds,
    } satisfies QueryExperienceBatchRequest);
  },

  async deleteQueryExperience(id: string): Promise<QueryExperienceDeletionResponse> {
    const response = await appClient.delete<QueryExperienceDeletionResponse>(
      `/api/v1/admin/query-experiences/${id}`
    );
    return response.data;
  },

  async deleteQueryExperiences(experienceIds: string[]): Promise<void> {
    await appClient.post("/api/v1/admin/query-experiences/batch-delete", {
      experience_ids: experienceIds,
    } satisfies QueryExperienceBatchRequest);
  },
};
