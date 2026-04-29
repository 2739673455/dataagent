import { useState } from "react";
import { toast } from "sonner";
import {
	permissionRoleApi,
	permissionRelationApi,
	permissionApi,
	permissionUserApi,
} from "../../../apis/permission";
import { handleApiError } from "../../../libs/error";
import type { RoleInfo, PermissionInfo, UserInfo } from "../../../types";
import { computeRelationDiff } from "../utils/relation";

// 通用编辑状态接口
interface EditState<T extends string> {
	id: number | null;
	open: boolean;
	loading: boolean;
	yn: number;
	// 动态字段
	fields: Record<T, string>;
}

// 用户编辑状态
interface UserEditState extends EditState<"username" | "email" | "password"> {
	roles: number[];
	originalRoles: number[];
	relationOpen: boolean;
}

// 角色编辑状态
interface RoleEditState extends EditState<"name"> {
	users: number[];
	originalUsers: number[];
	permissions: number[];
	originalPermissions: number[];
	relationOpen: boolean;
	relationTab: "users" | "permissions";
}

// 权限编辑状态
interface PermissionEditState extends EditState<"name" | "description"> {
	roles: number[];
	originalRoles: number[];
	relationOpen: boolean;
}

// 创建用户编辑hook
export function useUserEditor(
	refreshData: () => Promise<void>,
	fetchDetail: (filter: { userId: number | null }) => void,
) {
	const [createOpen, setCreateOpen] = useState(false);
	const [createLoading, setCreateLoading] = useState(false);
	const [edit, setEdit] = useState<UserEditState>({
		id: null,
		open: false,
		loading: false,
		yn: 1,
		fields: { username: "", email: "", password: "" },
		roles: [],
		originalRoles: [],
		relationOpen: false,
	});

	// 打开创建弹窗
	const openCreate = () => {
		setCreateOpen(true);
	};

	// 打开编辑弹窗
	const openEdit = async (user: UserInfo) => {
		setEdit({
			id: user.id,
			open: true,
			loading: false,
			yn: user.yn,
			fields: {
				username: user.username,
				email: user.email,
				password: "",
			},
			roles: [],
			originalRoles: [],
			relationOpen: false,
		});
		try {
			const res = await permissionUserApi.getUser(user.id);
			const roleIds = res.roles.map((g) => g.id);
			setEdit((s) => ({ ...s, roles: roleIds, originalRoles: roleIds }));
		} catch {
			// 错误已处理
		}
	};

	// 打开关联编辑弹窗
	const openRelationEdit = async (user: UserInfo) => {
		setEdit({
			id: user.id,
			open: false,
			loading: false,
			yn: user.yn,
			fields: { username: user.username, email: user.email, password: "" },
			roles: [],
			originalRoles: [],
			relationOpen: true,
		});
		try {
			const res = await permissionUserApi.getUser(user.id);
			const roleIds = res.roles.map((g) => g.id);
			setEdit((s) => ({ ...s, roles: roleIds, originalRoles: roleIds }));
		} catch {
			// 错误已处理
		}
	};

	// 提交关联修改
	const submitRelation = async () => {
		if (!edit.id) return;
		const userId = edit.id;
		try {
			const { toAdd, toRemove } = computeRelationDiff(
				edit.roles,
				edit.originalRoles,
			);
			if (toAdd.length > 0) {
				await permissionRelationApi.addUserRole({
					relations: toAdd.map((roleId) => ({
						user_id: userId,
						role_id: roleId,
					})),
				});
			}
			if (toRemove.length > 0) {
				await permissionRelationApi.removeUserRole({
					relations: toRemove.map((roleId) => ({
						user_id: userId,
						role_id: roleId,
					})),
				});
			}
			toast.success("关联关系更新成功");
			setEdit((s) => ({ ...s, relationOpen: false }));
			refreshData();
			fetchDetail({ userId });
		} catch (error: unknown) {
			handleApiError(error, "更新关联关系失败");
		}
	};

	// 更新字段
	const updateField = (field: keyof typeof edit.fields, value: string) => {
		setEdit((s) => ({ ...s, fields: { ...s.fields, [field]: value } }));
	};

	// 更新关联
	const updateRoles = (roles: number[]) => {
		setEdit((s) => ({ ...s, roles }));
	};

	return {
		createOpen,
		setCreateOpen,
		createLoading,
		setCreateLoading,
		edit,
		setEdit,
		openCreate,
		openEdit,
		openRelationEdit,
		submitRelation,
		updateField,
		updateRoles,
	};
}

// 创建角色编辑hook
export function useRoleEditor(
	refreshData: () => Promise<void>,
	fetchDetail: (filter: { roleId: number | null }) => void,
) {
	const [createOpen, setCreateOpen] = useState(false);
	const [createLoading, setCreateLoading] = useState(false);
	const [edit, setEdit] = useState<RoleEditState>({
		id: null,
		open: false,
		loading: false,
		yn: 1,
		fields: { name: "" },
		users: [],
		originalUsers: [],
		permissions: [],
		originalPermissions: [],
		relationOpen: false,
		relationTab: "users",
	});

	const openCreate = () => setCreateOpen(true);

	const openEdit = async (role: RoleInfo) => {
		setEdit({
			id: role.id,
			open: true,
			loading: false,
			yn: role.yn,
			fields: { name: role.name },
			users: [],
			originalUsers: [],
			permissions: [],
			originalPermissions: [],
			relationOpen: false,
			relationTab: "users",
		});
		try {
			const res = await permissionRoleApi.getRole(role.id);
			setEdit((s) => ({
				...s,
				users: res.users.map((u) => u.id),
				originalUsers: res.users.map((u) => u.id),
				permissions: res.permissions.map((s) => s.id),
				originalPermissions: res.permissions.map((s) => s.id),
			}));
		} catch {
			// 错误已处理
		}
	};

	const openRelationEdit = async (
		role: RoleInfo,
		tab: "users" | "permissions",
	) => {
		setEdit({
			id: role.id,
			open: false,
			loading: false,
			yn: role.yn,
			fields: { name: role.name },
			users: [],
			originalUsers: [],
			permissions: [],
			originalPermissions: [],
			relationOpen: true,
			relationTab: tab,
		});
		try {
			const res = await permissionRoleApi.getRole(role.id);
			setEdit((s) => ({
				...s,
				users: res.users.map((u) => u.id),
				originalUsers: res.users.map((u) => u.id),
				permissions: res.permissions.map((s) => s.id),
				originalPermissions: res.permissions.map((s) => s.id),
			}));
		} catch {
			// 错误已处理
		}
	};

	const submitUserRelation = async () => {
		if (!edit.id) return;
		const roleId = edit.id;
		try {
			const { toAdd, toRemove } = computeRelationDiff(
				edit.users,
				edit.originalUsers,
			);
			if (toAdd.length > 0) {
				await permissionRelationApi.addUserRole({
					relations: toAdd.map((userId) => ({
						user_id: userId,
						role_id: roleId,
					})),
				});
			}
			if (toRemove.length > 0) {
				await permissionRelationApi.removeUserRole({
					relations: toRemove.map((userId) => ({
						user_id: userId,
						role_id: roleId,
					})),
				});
			}
			toast.success("用户关联更新成功");
			setEdit((s) => ({ ...s, relationOpen: false }));
			refreshData();
			fetchDetail({ roleId });
		} catch (error: unknown) {
			handleApiError(error, "更新用户关联失败");
		}
	};

	const submitPermissionRelation = async () => {
		if (!edit.id) return;
		const roleId = edit.id;
		try {
			const { toAdd, toRemove } = computeRelationDiff(
				edit.permissions,
				edit.originalPermissions,
			);
			if (toAdd.length > 0) {
				await permissionRelationApi.addRolePermission({
					relations: toAdd.map((permissionId) => ({
						role_id: roleId,
						permission_id: permissionId,
					})),
				});
			}
			if (toRemove.length > 0) {
				await permissionRelationApi.removeRolePermission({
					relations: toRemove.map((permissionId) => ({
						role_id: roleId,
						permission_id: permissionId,
					})),
				});
			}
			toast.success("权限关联更新成功");
			setEdit((s) => ({ ...s, relationOpen: false }));
			refreshData();
			fetchDetail({ roleId });
		} catch (error: unknown) {
			handleApiError(error, "更新权限关联失败");
		}
	};

	const updateField = (field: keyof typeof edit.fields, value: string) => {
		setEdit((s) => ({ ...s, fields: { ...s.fields, [field]: value } }));
	};

	const updateUsers = (users: number[]) => setEdit((s) => ({ ...s, users }));
	const updatePermissions = (permissions: number[]) => setEdit((s) => ({ ...s, permissions }));

	return {
		createOpen,
		setCreateOpen,
		createLoading,
		setCreateLoading,
		edit,
		setEdit,
		openCreate,
		openEdit,
		openRelationEdit,
		submitUserRelation,
		submitPermissionRelation,
		updateField,
		updateUsers,
		updatePermissions,
	};
}

// 创建权限编辑hook
export function usePermissionEditor(
	refreshData: () => Promise<void>,
	fetchDetail: (filter: { permissionId: number | null }) => void,
) {
	const [createOpen, setCreateOpen] = useState(false);
	const [createLoading, setCreateLoading] = useState(false);
	const [edit, setEdit] = useState<PermissionEditState>({
		id: null,
		open: false,
		loading: false,
		yn: 1,
		fields: { name: "", description: "" },
		roles: [],
		originalRoles: [],
		relationOpen: false,
	});

	const openCreate = () => setCreateOpen(true);

	const openEdit = async (permission: PermissionInfo) => {
		setEdit({
			id: permission.id,
			open: true,
			loading: false,
			yn: permission.yn,
			fields: { name: permission.name, description: permission.description || "" },
			roles: [],
			originalRoles: [],
			relationOpen: false,
		});
		try {
			const res = await permissionApi.getPermission(permission.id);
			const roleIds = res.roles.map((g) => g.id);
			setEdit((s) => ({ ...s, roles: roleIds, originalRoles: roleIds }));
		} catch {
			// 错误已处理
		}
	};

	const openRelationEdit = async (permission: PermissionInfo) => {
		setEdit({
			id: permission.id,
			open: false,
			loading: false,
			yn: permission.yn,
			fields: { name: permission.name, description: permission.description || "" },
			roles: [],
			originalRoles: [],
			relationOpen: true,
		});
		try {
			const res = await permissionApi.getPermission(permission.id);
			const roleIds = res.roles.map((g) => g.id);
			setEdit((s) => ({ ...s, roles: roleIds, originalRoles: roleIds }));
		} catch {
			// 错误已处理
		}
	};

	const submitRelation = async () => {
		if (!edit.id) return;
		const permissionId = edit.id;
		try {
			const { toAdd, toRemove } = computeRelationDiff(
				edit.roles,
				edit.originalRoles,
			);
			if (toAdd.length > 0) {
				await permissionRelationApi.addRolePermission({
					relations: toAdd.map((roleId) => ({
						role_id: roleId,
						permission_id: permissionId,
					})),
				});
			}
			if (toRemove.length > 0) {
				await permissionRelationApi.removeRolePermission({
					relations: toRemove.map((roleId) => ({
						role_id: roleId,
						permission_id: permissionId,
					})),
				});
			}
			toast.success("关联关系更新成功");
			setEdit((s) => ({ ...s, relationOpen: false }));
			refreshData();
			fetchDetail({ permissionId });
		} catch (error: unknown) {
			handleApiError(error, "更新关联关系失败");
		}
	};

	const updateField = (field: keyof typeof edit.fields, value: string) => {
		setEdit((s) => ({ ...s, fields: { ...s.fields, [field]: value } }));
	};

	const updateRoles = (roles: number[]) => setEdit((s) => ({ ...s, roles }));

	return {
		createOpen,
		setCreateOpen,
		createLoading,
		setCreateLoading,
		edit,
		setEdit,
		openCreate,
		openEdit,
		openRelationEdit,
		submitRelation,
		updateField,
		updateRoles,
	};
}
