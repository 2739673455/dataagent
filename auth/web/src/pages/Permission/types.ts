import type {
	RoleDetailResponse,
	RoleInfo,
	PermissionDetailResponse,
	PermissionInfo,
	UserDetailResponse,
	UserInfo,
} from "../../types";

export type UserSortField = "id" | "username" | "email";
export type RoleSortField = "id" | "name";
export type PermissionSortField = "id" | "name";

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
