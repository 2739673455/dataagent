import type {
  BatchAddRolePermissionRequest,
  BatchAddUserRoleRequest,
  BatchRemoveRolePermissionRequest,
  BatchRemoveUserRoleRequest,
  CreatePermissionRequest,
  CreateRoleRequest,
  CreateUserRequest,
  PermissionDetailResponse,
  PermissionInfo,
  PermissionListResponse,
  RemovePermissionRequest,
  RemoveRoleRequest,
  RemoveUserRequest,
  RoleDetailResponse,
  RoleInfo,
  RoleListResponse,
  UpdatePermissionRequest,
  UpdateRoleRequest,
  UpdateUserRequest,
  UserDetailResponse,
  UserInfo,
  UserListResponse,
} from "@/features/permission/types";
import apiClient from "@/shared/libs/api-client";

// 用户管理
export const permissionUserApi = {
  // 创建用户
  createUser: (data: CreateUserRequest) =>
    apiClient.post<UserInfo>("/api/admin/create_user", data, {
      validateStatus: (status) => status === 201 || status < 400,
    }),

  // 更新用户
  updateUser: (data: UpdateUserRequest) => apiClient.post<void>("/api/admin/update_user", data),

  // 删除用户
  removeUser: (data: RemoveUserRequest) => apiClient.post<void>("/api/admin/remove_user", data),

  // 查询用户列表
  listUsers: (params: { offset?: number; limit?: number; keyword?: string; all?: boolean }) =>
    apiClient.get<UserListResponse>("/api/admin/list_users", { params }),

  // 查询用户详情
  getUser: (userId: number) => apiClient.get<UserDetailResponse>(`/api/admin/user/${userId}`),
};

// 角色管理
export const permissionRoleApi = {
  // 创建角色
  createRole: (data: CreateRoleRequest) =>
    apiClient.post<RoleInfo>("/api/admin/create_role", data, {
      validateStatus: (status) => status === 201 || status < 400,
    }),

  // 更新角色
  updateRole: (data: UpdateRoleRequest) => apiClient.post<RoleInfo>("/api/admin/update_role", data),

  // 删除角色
  removeRole: (data: RemoveRoleRequest) => apiClient.post<void>("/api/admin/remove_role", data),

  // 查询角色列表
  listRoles: (params: { offset?: number; limit?: number; keyword?: string; all?: boolean }) =>
    apiClient.get<RoleListResponse>("/api/admin/list_roles", { params }),

  // 查询角色详情
  getRole: (roleId: number) => apiClient.get<RoleDetailResponse>(`/api/admin/role/${roleId}`),
};

// 权限管理
export const permissionApi = {
  // 创建权限
  createPermission: (data: CreatePermissionRequest) =>
    apiClient.post<PermissionInfo>("/api/admin/create_permission", data, {
      validateStatus: (status) => status === 201 || status < 400,
    }),

  // 更新权限
  updatePermission: (data: UpdatePermissionRequest) =>
    apiClient.post<PermissionInfo>("/api/admin/update_permission", data),

  // 删除权限
  removePermission: (data: RemovePermissionRequest) =>
    apiClient.post<void>("/api/admin/remove_permission", data),

  // 查询权限列表
  listPermissions: (params: { offset?: number; limit?: number; keyword?: string; all?: boolean }) =>
    apiClient.get<PermissionListResponse>("/api/admin/list_permissions", {
      params,
    }),

  // 查询权限详情
  getPermission: (permissionId: number) =>
    apiClient.get<PermissionDetailResponse>(`/api/admin/permission/${permissionId}`),
};

// 关联关系管理
export const permissionRelationApi = {
  // 批量添加用户-角色关联
  addUserRole: (data: BatchAddUserRoleRequest) =>
    apiClient.post<void>("/api/admin/user-role/add", data, {
      validateStatus: (status) => status === 201 || status < 400,
    }),

  // 批量移除用户-角色关联
  removeUserRole: (data: BatchRemoveUserRoleRequest) =>
    apiClient.post<void>("/api/admin/user-role/remove", data),

  // 批量添加角色-权限关联
  addRolePermission: (data: BatchAddRolePermissionRequest) =>
    apiClient.post<void>("/api/admin/role-permission/add", data, {
      validateStatus: (status) => status === 201 || status < 400,
    }),

  // 批量移除角色-权限关联
  removeRolePermission: (data: BatchRemoveRolePermissionRequest) =>
    apiClient.post<void>("/api/admin/role-permission/remove", data),
};
