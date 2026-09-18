import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type AssetGrantResponse = ApiSchemas["AssetGrantResponse"];
export type CreateDorisRoleRequest = ApiSchemas["CreateDorisRoleRequest"];
export type DorisExistingRoleResponse = ApiSchemas["DorisExistingRoleResponse"];
export type DorisRoleResponse = ApiSchemas["DorisRoleResponse"];
export type RowPolicyResponse = ApiSchemas["RowPolicyResponse"];
export type RowPolicyRequest = ApiSchemas["RowPolicyRequest"];
export type SelectGrantRequest = ApiSchemas["SelectGrantRequest"];
type DropRowPolicyRequest = ApiSchemas["DropRowPolicyRequest"];

export const rolesApi = {
  async listRoles(): Promise<DorisRoleResponse[]> {
    const response = await appClient.get<DorisRoleResponse[]>("/api/v1/admin/doris-roles");
    return response.data;
  },

  async listWorkloadGroups(): Promise<string[]> {
    const response = await appClient.get<string[]>("/api/v1/admin/doris-roles/workload-groups");
    return response.data;
  },

  async listExistingRoles(): Promise<DorisExistingRoleResponse[]> {
    const response = await appClient.get<DorisExistingRoleResponse[]>(
      "/api/v1/admin/doris-roles/existing"
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

  async clearDefaultRole(): Promise<void> {
    await appClient.delete("/api/v1/admin/doris-roles/default");
  },

  async deleteRole(role: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}`);
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
    const response = await appClient.get<AssetGrantResponse[]>(
      `/api/v1/admin/doris-roles/${role}/select-grants`
    );
    return response.data;
  },

  async listRowPolicies(role: string): Promise<RowPolicyResponse[]> {
    const response = await appClient.get<RowPolicyResponse[]>(
      `/api/v1/admin/doris-roles/${role}/row-policies`
    );
    return response.data;
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
