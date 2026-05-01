export interface UserInfo {
  id: number;
  email: string;
  username: string;
  yn: number;
  effective?: number;
  create_at: string | null;
}

export interface RoleInfo {
  id: number;
  name: string;
  yn: number;
  create_at: string | null;
}

export interface PermissionInfo {
  id: number;
  name: string;
  description: string | null;
  yn: number;
  effective?: number;
  create_at: string | null;
}

export interface CreateUserRequest {
  email: string;
  username: string;
  password: string;
}

export interface UpdateUserRequest {
  user_id: number;
  email?: string;
  username?: string;
  password?: string;
  yn?: number;
}

export interface RemoveUserRequest {
  user_id: number;
}

export interface UserListResponse {
  total: number;
  items: UserInfo[];
}

export interface UserDetailResponse extends UserInfo {
  roles: RoleInfo[];
  permissions: PermissionInfo[];
}

export interface CreateRoleRequest {
  name: string;
}

export interface UpdateRoleRequest {
  role_id: number;
  name?: string;
  yn?: number;
}

export interface RemoveRoleRequest {
  role_id: number;
}

export interface RoleListResponse {
  total: number;
  items: RoleInfo[];
}

export interface RoleDetailResponse extends RoleInfo {
  users: UserInfo[];
  permissions: PermissionInfo[];
}

export interface CreatePermissionRequest {
  name: string;
  description?: string;
}

export interface UpdatePermissionRequest {
  permission_id: number;
  name?: string;
  description?: string;
  yn?: number;
}

export interface RemovePermissionRequest {
  permission_id: number;
}

export interface PermissionListResponse {
  total: number;
  items: PermissionInfo[];
}

export interface PermissionDetailResponse extends PermissionInfo {
  roles: RoleInfo[];
  users: UserInfo[];
}

export interface UserRoleRelation {
  user_id: number;
  role_id: number;
}

export interface RolePermissionRelation {
  role_id: number;
  permission_id: number;
}

export interface BatchAddUserRoleRequest {
  relations: UserRoleRelation[];
}

export interface BatchRemoveUserRoleRequest {
  relations: UserRoleRelation[];
}

export interface BatchAddRolePermissionRequest {
  relations: RolePermissionRelation[];
}

export interface BatchRemoveRolePermissionRequest {
  relations: RolePermissionRelation[];
}

// Sort / Filter / View types for the Permission management UI

export type UserSortField = "id" | "username" | "email";
export type RoleSortField = "id" | "name";
export type PermissionSortField = "id" | "name";

export type SortOrder = "asc" | "desc";

export interface SortState<T> {
  field: T;
  order: SortOrder;
}

export function toggleSort<T>(current: SortState<T>, field: T): SortState<T> {
  if (current.field === field) {
    return { field, order: current.order === "asc" ? "desc" : "asc" };
  }
  return { field, order: "asc" };
}

export interface FilterState {
  userId: number | null;
  roleId: number | null;
  permissionId: number | null;
}

export interface PermissionData {
  users: UserInfo[];
  roles: RoleInfo[];
  permissions: PermissionInfo[];
}

export interface PermissionDetail {
  userDetail: UserDetailResponse | null;
  roleDetail: RoleDetailResponse | null;
  permissionDetail: PermissionDetailResponse | null;
}
