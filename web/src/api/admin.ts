import type { UserResponse } from "@/auth";
import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type AssetGrantListResponse = ApiSchemas["AssetGrantListResponse"];
export type AssetGrantResponse = ApiSchemas["AssetGrantResponse"];
export type AttachDorisRoleRequest = ApiSchemas["AttachDorisRoleRequest"];
export type CreateDorisRoleRequest = ApiSchemas["CreateDorisRoleRequest"];
export type CreateUserRequest = ApiSchemas["CreateUserRequest"];
export type DiscoveredDorisRoleResponse = ApiSchemas["DiscoveredDorisRoleResponse"];
export type DorisRoleResponse = ApiSchemas["DorisRoleResponse"];
export type RowPolicyRequest = ApiSchemas["RowPolicyRequest"];
export type SelectGrantRequest = ApiSchemas["SelectGrantRequest"];
export type UpdateUserRequest = ApiSchemas["UpdateUserRequest"];
export type UserListResponse = ApiSchemas["UserListResponse"];

type DorisRoleListResponse = ApiSchemas["DorisRoleListResponse"];
type DiscoveredDorisRoleListResponse = ApiSchemas["DiscoveredDorisRoleListResponse"];
type DropRowPolicyRequest = ApiSchemas["DropRowPolicyRequest"];
type RowPolicy = ApiSchemas["RowPolicyListResponse"]["policies"][number];
type RowPolicyListResponse = ApiSchemas["RowPolicyListResponse"];
type SetUserAdministratorRequest = ApiSchemas["SetUserAdministratorRequest"];
type SetUserDorisRoleRequest = ApiSchemas["SetUserDorisRoleRequest"];

export const adminApi = {
  async listRoles(): Promise<DorisRoleResponse[]> {
    const response = await appClient.get<DorisRoleListResponse>("/api/v1/admin/doris-roles");
    return response.data.roles;
  },

  async discoverRoles(): Promise<DiscoveredDorisRoleResponse[]> {
    const response = await appClient.get<DiscoveredDorisRoleListResponse>(
      "/api/v1/admin/doris-roles/discover"
    );
    return response.data.roles;
  },

  async attachRole(request: AttachDorisRoleRequest): Promise<DorisRoleResponse> {
    const response = await appClient.post<DorisRoleResponse>(
      "/api/v1/admin/doris-roles/attach",
      request
    );
    return response.data;
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

  async grantSelect(role: string, request: SelectGrantRequest): Promise<void> {
    await appClient.post(`/api/v1/admin/doris-roles/${role}/select-grants`, request);
  },

  async revokeSelect(role: string, request: SelectGrantRequest): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}/select-grants`, {
      data: request,
    });
  },

  async listSelectGrants(role: string): Promise<AssetGrantResponse[]> {
    const response = await appClient.get<AssetGrantListResponse>(
      `/api/v1/admin/doris-roles/${role}/select-grants`
    );
    return response.data.grants;
  },

  async listRowPolicies(role: string): Promise<RowPolicy[]> {
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
