import type { UserResponse } from "@/auth";
import appClient from "@/api/appClient";

export interface DorisRoleResponse {
  name: string;
  description: string;
  is_default: boolean;
  query_user: string;
  exists_in_doris: boolean;
  doris_grants: Record<string, unknown> | null;
}

export interface SelectGrantRequest {
  table_name: string | null;
  columns: string[];
}

export interface RowPolicyRequest {
  policy_name: string;
  table_name: string;
  policy_type: "RESTRICTIVE" | "PERMISSIVE";
  predicate: string;
}

export const adminApi = {
  async listRoles(): Promise<DorisRoleResponse[]> {
    const response = await appClient.get<{ roles: DorisRoleResponse[] }>(
      "/api/v1/admin/doris-roles"
    );
    return response.data.roles;
  },

  async listUsers(): Promise<UserResponse[]> {
    const response = await appClient.get<{ users: UserResponse[] }>("/api/v1/admin/users");
    return response.data.users;
  },

  async setUserRole(userId: number, role: string): Promise<UserResponse> {
    const response = await appClient.put<UserResponse>(`/api/v1/admin/users/${userId}/doris-role`, {
      role,
    });
    return response.data;
  },

  async setAdministrator(userId: number, isAdmin: boolean): Promise<UserResponse> {
    const response = await appClient.put<UserResponse>(
      `/api/v1/admin/users/${userId}/administrator`,
      { is_admin: isAdmin }
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

  async listRowPolicies(role: string): Promise<Record<string, unknown>[]> {
    const response = await appClient.get<{ policies: Record<string, unknown>[] }>(
      `/api/v1/admin/doris-roles/${role}/row-policies`
    );
    return response.data.policies;
  },

  async createRowPolicy(role: string, request: RowPolicyRequest): Promise<void> {
    await appClient.post(`/api/v1/admin/doris-roles/${role}/row-policies`, request);
  },

  async dropRowPolicy(role: string, policyName: string, tableName: string): Promise<void> {
    await appClient.delete(`/api/v1/admin/doris-roles/${role}/row-policies`, {
      data: { policy_name: policyName, table_name: tableName },
    });
  },
};
