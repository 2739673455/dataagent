import type { UserResponse } from "@/auth";
import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type AssetGrantListResponse = ApiSchemas["AssetGrantListResponse"];
export type AssetGrantResponse = ApiSchemas["AssetGrantResponse"];
export type CreateDorisRoleRequest = ApiSchemas["CreateDorisRoleRequest"];
export type CreateUserRequest = ApiSchemas["CreateUserRequest"];
export type DorisExistingRoleResponse = ApiSchemas["DorisExistingRoleResponse"];
export type DorisRoleResponse = ApiSchemas["DorisRoleResponse"];
export type RowPolicyResponse = ApiSchemas["RowPolicyResponse"];
export type RowPolicyRequest = ApiSchemas["RowPolicyRequest"];
export type SelectGrantRequest = ApiSchemas["SelectGrantRequest"];
export type UpdateUserRequest = ApiSchemas["UpdateUserRequest"];
export type UserListResponse = ApiSchemas["UserListResponse"];
export type QueryExperienceDeletionResponse = ApiSchemas["QueryExperienceDeletionResponse"];
export type QueryExperienceDetailResponse = ApiSchemas["QueryExperienceDetailResponse"];
export type QueryExperienceSourceExecutionListResponse =
  ApiSchemas["QueryExperienceSourceExecutionListResponse"];
export type QueryExperienceListResponse = ApiSchemas["QueryExperienceListResponse"];
export type QueryExperienceStatus = ApiSchemas["QueryExperienceStatus"];

type DorisRoleListResponse = ApiSchemas["DorisRoleListResponse"];
type DropRowPolicyRequest = ApiSchemas["DropRowPolicyRequest"];
type DorisWorkloadGroupListResponse = ApiSchemas["DorisWorkloadGroupListResponse"];
type RowPolicyListResponse = ApiSchemas["RowPolicyListResponse"];
type SetUserAdministratorRequest = ApiSchemas["SetUserAdministratorRequest"];
type SetUserDorisRoleRequest = ApiSchemas["SetUserDorisRoleRequest"];

export const adminApi = {
  async listRoles(): Promise<DorisRoleResponse[]> {
    const response = await appClient.get<DorisRoleListResponse>("/api/v1/admin/doris-roles");
    return response.data.roles;
  },

  async listWorkloadGroups(): Promise<string[]> {
    const response = await appClient.get<DorisWorkloadGroupListResponse>(
      "/api/v1/admin/doris-roles/workload-groups"
    );
    return response.data.workload_groups;
  },

  async listExistingRoles(): Promise<DorisExistingRoleResponse[]> {
    const response = await appClient.get<ApiSchemas["DorisExistingRoleListResponse"]>(
      "/api/v1/admin/doris-roles/existing"
    );
    return response.data.roles;
  },

  async createRole(request: CreateDorisRoleRequest): Promise<DorisRoleResponse> {
    const response = await appClient.post<DorisRoleResponse>("/api/v1/admin/doris-roles", request);
    return response.data;
  },

  async setDefaultRole(role: string): Promise<DorisRoleResponse> {
    const response = await appClient.put<DorisRoleResponse>(
      `/api/v1/admin/doris-roles/${role}/default`
    );
    return response.data;
  },

  async clearDefaultRole(): Promise<void> {
    await appClient.delete("/api/v1/admin/doris-roles/default");
  },

  async deleteRole(role: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}`);
  },

  async listUsers(limit: number, offset: number, query?: string): Promise<UserListResponse> {
    const trimmed = query?.trim();
    const response = await appClient.get<UserListResponse>("/api/v1/admin/users", {
      params: {
        limit,
        offset,
        ...(trimmed ? { query: trimmed } : {}),
      },
    });
    return response.data;
  },

  async createUser(request: CreateUserRequest): Promise<UserResponse> {
    const response = await appClient.post<UserResponse>("/api/v1/admin/users", request);
    return response.data;
  },

  async deleteUser(userId: number): Promise<void> {
    await appClient.delete(`/api/v1/admin/users/${userId}`);
  },

  async setUserRole(userId: number, role: string): Promise<UserResponse> {
    const response = await appClient.put<UserResponse>(`/api/v1/admin/users/${userId}/doris-role`, {
      role,
    } satisfies SetUserDorisRoleRequest);
    return response.data;
  },

  async setAdministrator(userId: number, isAdmin: boolean): Promise<UserResponse> {
    const response = await appClient.put<UserResponse>(
      `/api/v1/admin/users/${userId}/administrator`,
      { is_admin: isAdmin } satisfies SetUserAdministratorRequest
    );
    return response.data;
  },

  async updateUser(userId: number, request: UpdateUserRequest): Promise<UserResponse> {
    const response = await appClient.put<UserResponse>(
      `/api/v1/admin/users/${userId}`,
      request satisfies UpdateUserRequest
    );
    return response.data;
  },

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

  async deleteQueryExperience(id: string): Promise<QueryExperienceDeletionResponse> {
    const response = await appClient.delete<QueryExperienceDeletionResponse>(
      `/api/v1/admin/query-experiences/${id}`
    );
    return response.data;
  },

  async grantSelect(role: string, request: SelectGrantRequest): Promise<void> {
    await appClient.post(`/api/v1/admin/doris-roles/${role}/select-grants`, request);
  },

  async revokeSelect(role: string, request: SelectGrantRequest): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}/select-grants`, {
      data: request,
    });
  },

  async revokeAllSelect(role: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}/select-grants/all`);
  },

  async listSelectGrants(role: string): Promise<AssetGrantResponse[]> {
    const response = await appClient.get<AssetGrantListResponse>(
      `/api/v1/admin/doris-roles/${role}/select-grants`
    );
    return response.data.grants;
  },

  async listRowPolicies(role: string): Promise<RowPolicyResponse[]> {
    const response = await appClient.get<RowPolicyListResponse>(
      `/api/v1/admin/doris-roles/${role}/row-policies`
    );
    return response.data.policies;
  },

  async createRowPolicy(role: string, request: RowPolicyRequest): Promise<void> {
    await appClient.post(`/api/v1/admin/doris-roles/${role}/row-policies`, request);
  },

  async dropRowPolicy(role: string, policyName: string, tableName: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}/row-policies`, {
      data: {
        policy_name: policyName,
        table_name: tableName,
      } satisfies DropRowPolicyRequest,
    });
  },
};
