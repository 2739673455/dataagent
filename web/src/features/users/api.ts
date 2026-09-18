import appClient from "@/api/appClient";
import type { components } from "@/api/generated";
import type { UserResponse } from "@/auth";

type ApiSchemas = components["schemas"];

export type CreateUserRequest = ApiSchemas["CreateUserRequest"];
export type UpdateUserRequest = ApiSchemas["UpdateUserRequest"];
export type UserListResponse = ApiSchemas["UserListResponse"];

export const usersApi = {
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

  async updateUser(userId: number, request: UpdateUserRequest): Promise<UserResponse> {
    const response = await appClient.put<UserResponse>(
      `/api/v1/admin/users/${userId}`,
      request satisfies UpdateUserRequest
    );
    return response.data;
  },
};
