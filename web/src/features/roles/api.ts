import appClient from "@/api/appClient";
import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type AssetGrantResponse = ApiSchemas["AssetGrantResponse"];
export type CreateBusinessPgRoleRequest = ApiSchemas["CreateBusinessPgRoleRequest"];
export type BusinessPgExistingRoleResponse = ApiSchemas["BusinessPgExistingRoleResponse"];
export type BusinessPgRoleResponse = ApiSchemas["BusinessPgRoleResponse"];
export type RowPolicyResponse = ApiSchemas["RowPolicyResponse"];
export type RowPolicyRequest = ApiSchemas["RowPolicyRequest"];
export type SelectGrantRequest = ApiSchemas["SelectGrantRequest"];
type DropRowPolicyRequest = ApiSchemas["DropRowPolicyRequest"];

export const rolesApi = {
  async listRoles(): Promise<BusinessPgRoleResponse[]> {
    const response = await appClient.get<BusinessPgRoleResponse[]>(
      "/api/v1/admin/business_pg-roles"
    );
    return response.data;
  },

  async listWorkloadGroups(): Promise<string[]> {
    const response = await appClient.get<string[]>(
      "/api/v1/admin/business_pg-roles/workload-groups"
    );
    return response.data;
  },

  async listExistingRoles(): Promise<BusinessPgExistingRoleResponse[]> {
    const response = await appClient.get<BusinessPgExistingRoleResponse[]>(
      "/api/v1/admin/business_pg-roles/existing"
    );
    return response.data;
  },

  async createRole(request: CreateBusinessPgRoleRequest): Promise<BusinessPgRoleResponse> {
    const response = await appClient.post<BusinessPgRoleResponse>(
      "/api/v1/admin/business_pg-roles",
      request
    );
    return response.data;
  },

  async setDefaultRole(role: string): Promise<BusinessPgRoleResponse> {
    const response = await appClient.put<BusinessPgRoleResponse>(
      `/api/v1/admin/business_pg-roles/${role}/default`
    );
    return response.data;
  },

  async clearDefaultRole(): Promise<void> {
    await appClient.delete("/api/v1/admin/business_pg-roles/default");
  },

  async deleteRole(role: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/business_pg-roles/${role}`);
  },

  async grantSelect(role: string, request: SelectGrantRequest): Promise<void> {
    await appClient.post(`/api/v1/admin/business_pg-roles/${role}/select-grants`, request);
  },

  async revokeSelect(role: string, request: SelectGrantRequest): Promise<void> {
    await appClient.delete(`/api/v1/admin/business_pg-roles/${role}/select-grants`, {
      data: request,
    });
  },

  async revokeAllSelect(role: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/business_pg-roles/${role}/select-grants/all`);
  },

  async listSelectGrants(role: string): Promise<AssetGrantResponse[]> {
    const response = await appClient.get<AssetGrantResponse[]>(
      `/api/v1/admin/business_pg-roles/${role}/select-grants`
    );
    return response.data;
  },

  async listRowPolicies(role: string): Promise<RowPolicyResponse[]> {
    const response = await appClient.get<RowPolicyResponse[]>(
      `/api/v1/admin/business_pg-roles/${role}/row-policies`
    );
    return response.data;
  },

  async createRowPolicy(role: string, request: RowPolicyRequest): Promise<void> {
    await appClient.post(`/api/v1/admin/business_pg-roles/${role}/row-policies`, request);
  },

  async dropRowPolicy(role: string, policyName: string, tableName: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/business_pg-roles/${role}/row-policies`, {
      data: {
        policy_name: policyName,
        table_name: tableName,
      } satisfies DropRowPolicyRequest,
    });
  },
};
