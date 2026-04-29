import type { Dispatch, FormEvent, SetStateAction } from "react";
import { useState } from "react";
import { toast } from "sonner";
import { handleApiError } from "@/libs/error";
import {
	validateEmailWithError,
	validatePasswordWithError,
	validateUsernameWithError,
} from "@/libs/validation";
import {
	permissionRoleApi,
	permissionApi,
	permissionUserApi,
} from "../../../apis/permission";
import type {
	RoleInfo,
	PermissionInfo,
	UpdateRoleRequest,
	UpdatePermissionRequest,
	UpdateUserRequest,
	UserInfo,
} from "../../../types";
import type { FilterState } from "../types";
import {
	syncRolePermissionRelations,
	syncRoleUserRelations,
	syncPermissionRoleRelations,
	syncUserRoleRelations,
} from "../utils";

type SetFilter = Dispatch<SetStateAction<FilterState>>;
type FetchDetail = (filter: FilterState) => Promise<void>;
type RefreshData = () => Promise<void>;

interface ActionDeps {
	filter: FilterState;
	setFilter: SetFilter;
	refreshData: RefreshData;
	fetchDetail: FetchDetail;
}

async function refreshDataAndDetail(
	refreshData: RefreshData,
	fetchDetail: FetchDetail,
	filter: FilterState,
) {
	await refreshData();
	if (filter.userId || filter.roleId || filter.permissionId) {
		await fetchDetail(filter);
	}
}

// 用户相关弹窗状态与增删改逻辑
export function useUserActions({
	filter,
	setFilter,
	refreshData,
	fetchDetail,
}: ActionDeps) {
	const [createUserOpen, setCreateUserOpen] = useState(false);
	const [createUserLoading, setCreateUserLoading] = useState(false);
	const [newUserEmail, setNewUserEmail] = useState("");
	const [newUserUsername, setNewUserUsername] = useState("");
	const [newUserPassword, setNewUserPassword] = useState("");
	const [editUserOpen, setEditUserOpen] = useState(false);
	const [editUserLoading, setEditUserLoading] = useState(false);
	const [editUserId, setEditUserId] = useState<number | null>(null);
	const [editUserUsername, setEditUserUsername] = useState("");
	const [editUserEmail, setEditUserEmail] = useState("");
	const [editUserPassword, setEditUserPassword] = useState("");
	const [editUserYn, setEditUserYn] = useState(1);
	const [editUserRoles, setEditUserRoles] = useState<number[]>([]);
	const [originalUserRoles, setOriginalUserRoles] = useState<number[]>([]);
	const [editUserRelationOpen, setEditUserRelationOpen] = useState(false);

	// 创建用户并刷新列表
	const handleCreateUser = async (e: FormEvent) => {
		e.preventDefault();
		const emailResult = validateEmailWithError(newUserEmail);
		if (!emailResult.valid) {
			toast.error(emailResult.error);
			return;
		}
		const usernameResult = validateUsernameWithError(newUserUsername);
		if (!usernameResult.valid) {
			toast.error(usernameResult.error);
			return;
		}
		const passwordResult = validatePasswordWithError(newUserPassword);
		if (!passwordResult.valid) {
			toast.error(passwordResult.error);
			return;
		}

		setCreateUserLoading(true);
		try {
			await permissionUserApi.createUser({
				email: newUserEmail,
				username: newUserUsername,
				password: newUserPassword,
			});
			toast.success("用户创建成功");
			setCreateUserOpen(false);
			setNewUserEmail("");
			setNewUserUsername("");
			setNewUserPassword("");
			refreshData();
		} catch (error) {
			handleApiError(error, "创建失败");
		} finally {
			setCreateUserLoading(false);
		}
	};

	const openEditUser = async (user: UserInfo) => {
		// 打开用户编辑弹窗并预加载关联角色
		setEditUserId(user.id);
		setEditUserUsername(user.username);
		setEditUserEmail(user.email);
		setEditUserPassword("");
		setEditUserYn(user.yn);
		try {
			const res = await permissionUserApi.getUser(user.id);
			const roleIds = res.roles.map((g) => g.id);
			setEditUserRoles(roleIds);
			setOriginalUserRoles(roleIds);
		} catch {
			setEditUserRoles([]);
			setOriginalUserRoles([]);
		}
		setEditUserOpen(true);
	};

	const openEditUserRelation = async (user: UserInfo) => {
		// 只打开关联关系编辑弹窗
		setEditUserId(user.id);
		setEditUserUsername(user.username);
		setEditUserEmail(user.email);
		try {
			const res = await permissionUserApi.getUser(user.id);
			const roleIds = res.roles.map((g) => g.id);
			setEditUserRoles(roleIds);
			setOriginalUserRoles(roleIds);
		} catch {
			setEditUserRoles([]);
			setOriginalUserRoles([]);
		}
		setEditUserRelationOpen(true);
	};

	const handleEditUser = async (e: FormEvent) => {
		// 更新用户基础信息并同步角色关联
		e.preventDefault();
		if (!editUserId) return;
		setEditUserLoading(true);
		try {
			const updateData: UpdateUserRequest = {
				user_id: editUserId,
				username: editUserUsername || undefined,
				email: editUserEmail || undefined,
				password: editUserPassword || undefined,
				yn: editUserYn,
			};
			await permissionUserApi.updateUser(updateData);
			await syncUserRoleRelations(
				editUserId,
				editUserRoles,
				originalUserRoles,
			);
			toast.success("用户更新成功");
			setEditUserOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新失败");
		} finally {
			setEditUserLoading(false);
		}
	};

	const handleSubmitUserRelation = async () => {
		// 仅提交用户与角色的关联变更
		if (!editUserId) return;
		const userId = editUserId;
		try {
			await syncUserRoleRelations(userId, editUserRoles, originalUserRoles);
			toast.success("关联关系更新成功");
			setEditUserRelationOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新关联关系失败");
		}
	};

	const handleDeleteUser = async (id: number) => {
		// 删除用户并清理当前筛选
		if (!confirm("确定删除该用户？")) return;
		try {
			await permissionUserApi.removeUser({ user_id: id });
			toast.success("删除成功");
			const nextFilter =
				filter.userId === id ? { ...filter, userId: null } : filter;
			if (filter.userId === id) setFilter(nextFilter);
			await refreshDataAndDetail(refreshData, fetchDetail, nextFilter);
		} catch (error) {
			handleApiError(error, "删除失败");
		}
	};

	return {
		createUserOpen,
		setCreateUserOpen,
		createUserLoading,
		newUserEmail,
		setNewUserEmail,
		newUserUsername,
		setNewUserUsername,
		newUserPassword,
		setNewUserPassword,
		editUserOpen,
		setEditUserOpen,
		editUserLoading,
		editUserId,
		editUserUsername,
		setEditUserUsername,
		editUserEmail,
		setEditUserEmail,
		editUserPassword,
		setEditUserPassword,
		editUserYn,
		setEditUserYn,
		editUserRoles,
		setEditUserRoles,
		editUserRelationOpen,
		setEditUserRelationOpen,
		handleCreateUser,
		openEditUser,
		openEditUserRelation,
		handleEditUser,
		handleSubmitUserRelation,
		handleDeleteUser,
	};
}

// 角色相关弹窗状态与增删改逻辑
export function useRoleActions({
	filter,
	setFilter,
	refreshData,
	fetchDetail,
}: ActionDeps) {
	const [createRoleOpen, setCreateRoleOpen] = useState(false);
	const [createRoleLoading, setCreateRoleLoading] = useState(false);
	const [newRoleName, setNewRoleName] = useState("");
	const [editRoleOpen, setEditRoleOpen] = useState(false);
	const [editRoleLoading, setEditRoleLoading] = useState(false);
	const [editRoleId, setEditRoleId] = useState<number | null>(null);
	const [editRoleName, setEditRoleName] = useState("");
	const [editRoleYn, setEditRoleYn] = useState(1);
	const [editRoleUsers, setEditRoleUsers] = useState<number[]>([]);
	const [originalRoleUsers, setOriginalRoleUsers] = useState<number[]>([]);
	const [editRolePermissions, setEditRolePermissions] = useState<number[]>([]);
	const [originalRolePermissions, setOriginalRolePermissions] = useState<number[]>([]);
	const [editRoleRelationOpen, setEditRoleRelationOpen] = useState(false);
	const [editRoleRelationTab, setEditRoleRelationTab] = useState<
		"users" | "permissions"
	>("users");

	// 创建角色并刷新列表
	const handleCreateRole = async (e: FormEvent) => {
		e.preventDefault();
		if (!newRoleName.trim()) {
			toast.error("角色名不能为空");
			return;
		}
		setCreateRoleLoading(true);
		try {
			await permissionRoleApi.createRole({ name: newRoleName });
			toast.success("角色创建成功");
			setCreateRoleOpen(false);
			setNewRoleName("");
			refreshData();
		} catch (error) {
			handleApiError(error, "创建失败");
		} finally {
			setCreateRoleLoading(false);
		}
	};

	const openEditRole = async (role: RoleInfo) => {
		// 打开角色编辑弹窗并预加载用户与权限关联
		setEditRoleId(role.id);
		setEditRoleName(role.name);
		setEditRoleYn(role.yn);
		try {
			const res = await permissionRoleApi.getRole(role.id);
			setEditRoleUsers(res.users.map((u) => u.id));
			setOriginalRoleUsers(res.users.map((u) => u.id));
			setEditRolePermissions(res.permissions.map((s) => s.id));
			setOriginalRolePermissions(res.permissions.map((s) => s.id));
		} catch {
			setEditRoleUsers([]);
			setOriginalRoleUsers([]);
			setEditRolePermissions([]);
			setOriginalRolePermissions([]);
		}
		setEditRoleOpen(true);
	};

	const openEditRoleRelation = async (
		role: RoleInfo,
		tab: "users" | "permissions",
	) => {
		// 按 tab 打开角色关联编辑弹窗
		setEditRoleId(role.id);
		setEditRoleName(role.name);
		try {
			const res = await permissionRoleApi.getRole(role.id);
			setEditRoleUsers(res.users.map((u) => u.id));
			setOriginalRoleUsers(res.users.map((u) => u.id));
			setEditRolePermissions(res.permissions.map((s) => s.id));
			setOriginalRolePermissions(res.permissions.map((s) => s.id));
		} catch {
			setEditRoleUsers([]);
			setOriginalRoleUsers([]);
			setEditRolePermissions([]);
			setOriginalRolePermissions([]);
		}
		setEditRoleRelationTab(tab);
		setEditRoleRelationOpen(true);
	};

	const handleEditRole = async (e: FormEvent) => {
		// 更新角色并同步用户和权限关联
		e.preventDefault();
		if (!editRoleId) return;
		setEditRoleLoading(true);
		try {
			const updateData: UpdateRoleRequest = {
				role_id: editRoleId,
				name: editRoleName || undefined,
				yn: editRoleYn,
			};
			await permissionRoleApi.updateRole(updateData);
			await syncRoleUserRelations(
				editRoleId,
				editRoleUsers,
				originalRoleUsers,
			);
			await syncRolePermissionRelations(
				editRoleId,
				editRolePermissions,
				originalRolePermissions,
			);
			toast.success("角色更新成功");
			setEditRoleOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新失败");
		} finally {
			setEditRoleLoading(false);
		}
	};

	const handleSubmitRoleUserRelation = async () => {
		// 仅提交角色和用户的关联变更
		if (!editRoleId) return;
		const roleId = editRoleId;
		try {
			await syncRoleUserRelations(roleId, editRoleUsers, originalRoleUsers);
			toast.success("用户关联更新成功");
			setEditRoleRelationOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新用户关联失败");
		}
	};

	const handleSubmitRolePermissionRelation = async () => {
		// 仅提交角色和权限的关联变更
		if (!editRoleId) return;
		const roleId = editRoleId;
		try {
			await syncRolePermissionRelations(
				roleId,
				editRolePermissions,
				originalRolePermissions,
			);
			toast.success("权限关联更新成功");
			setEditRoleRelationOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新权限关联失败");
		}
	};

	const handleDeleteRole = async (id: number) => {
		// 删除角色并清理当前筛选
		if (!confirm("确定删除该角色？")) return;
		try {
			await permissionRoleApi.removeRole({ role_id: id });
			toast.success("删除成功");
			const nextFilter =
				filter.roleId === id ? { ...filter, roleId: null } : filter;
			if (filter.roleId === id) setFilter(nextFilter);
			await refreshDataAndDetail(refreshData, fetchDetail, nextFilter);
		} catch (error) {
			handleApiError(error, "删除失败");
		}
	};

	return {
		createRoleOpen,
		setCreateRoleOpen,
		createRoleLoading,
		newRoleName,
		setNewRoleName,
		editRoleOpen,
		setEditRoleOpen,
		editRoleLoading,
		editRoleId,
		editRoleName,
		setEditRoleName,
		editRoleYn,
		setEditRoleYn,
		editRoleUsers,
		setEditRoleUsers,
		editRolePermissions,
		setEditRolePermissions,
		editRoleRelationOpen,
		setEditRoleRelationOpen,
		editRoleRelationTab,
		handleCreateRole,
		openEditRole,
		openEditRoleRelation,
		handleEditRole,
		handleSubmitRoleUserRelation,
		handleSubmitRolePermissionRelation,
		handleDeleteRole,
	};
}

// 权限相关弹窗状态与增删改逻辑
export function usePermissionActions({
	filter,
	setFilter,
	refreshData,
	fetchDetail,
}: ActionDeps) {
	const [createPermissionOpen, setCreatePermissionOpen] = useState(false);
	const [createPermissionLoading, setCreatePermissionLoading] = useState(false);
	const [newPermissionName, setNewPermissionName] = useState("");
	const [newPermissionDesc, setNewPermissionDesc] = useState("");
	const [editPermissionOpen, setEditPermissionOpen] = useState(false);
	const [editPermissionLoading, setEditPermissionLoading] = useState(false);
	const [editPermissionId, setEditPermissionId] = useState<number | null>(null);
	const [editPermissionName, setEditPermissionName] = useState("");
	const [editPermissionDesc, setEditPermissionDesc] = useState("");
	const [editPermissionYn, setEditPermissionYn] = useState(1);
	const [editPermissionRoles, setEditPermissionRoles] = useState<number[]>([]);
	const [originalPermissionRoles, setOriginalPermissionRoles] = useState<number[]>([]);
	const [editPermissionRelationOpen, setEditPermissionRelationOpen] = useState(false);

	// 创建权限并刷新列表
	const handleCreatePermission = async (e: FormEvent) => {
		e.preventDefault();
		if (!newPermissionName.trim()) {
			toast.error("权限名不能为空");
			return;
		}
		setCreatePermissionLoading(true);
		try {
			await permissionApi.createPermission({
				name: newPermissionName,
				description: newPermissionDesc || undefined,
			});
			toast.success("权限创建成功");
			setCreatePermissionOpen(false);
			setNewPermissionName("");
			setNewPermissionDesc("");
			refreshData();
		} catch (error) {
			handleApiError(error, "创建失败");
		} finally {
			setCreatePermissionLoading(false);
		}
	};

	const openEditPermission = async (permission: PermissionInfo) => {
		// 打开权限编辑弹窗并预加载关联角色
		setEditPermissionId(permission.id);
		setEditPermissionName(permission.name);
		setEditPermissionDesc(permission.description || "");
		setEditPermissionYn(permission.yn);
		try {
			const res = await permissionApi.getPermission(permission.id);
			const roleIds = res.roles.map((g) => g.id);
			setEditPermissionRoles(roleIds);
			setOriginalPermissionRoles(roleIds);
		} catch {
			setEditPermissionRoles([]);
			setOriginalPermissionRoles([]);
		}
		setEditPermissionOpen(true);
	};

	const openEditPermissionRelation = async (permission: PermissionInfo) => {
		// 只打开权限和角色的关联编辑弹窗
		setEditPermissionId(permission.id);
		setEditPermissionName(permission.name);
		setEditPermissionDesc(permission.description || "");
		try {
			const res = await permissionApi.getPermission(permission.id);
			const roleIds = res.roles.map((g) => g.id);
			setEditPermissionRoles(roleIds);
			setOriginalPermissionRoles(roleIds);
		} catch {
			setEditPermissionRoles([]);
			setOriginalPermissionRoles([]);
		}
		setEditPermissionRelationOpen(true);
	};

	const handleEditPermission = async (e: FormEvent) => {
		// 更新权限基础信息并同步关联角色
		e.preventDefault();
		if (!editPermissionId) return;
		setEditPermissionLoading(true);
		try {
			const updateData: UpdatePermissionRequest = {
				permission_id: editPermissionId,
				name: editPermissionName || undefined,
				description: editPermissionDesc || undefined,
				yn: editPermissionYn,
			};
			await permissionApi.updatePermission(updateData);
			await syncPermissionRoleRelations(
				editPermissionId,
				editPermissionRoles,
				originalPermissionRoles,
			);
			toast.success("权限更新成功");
			setEditPermissionOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新失败");
		} finally {
			setEditPermissionLoading(false);
		}
	};

	const handleSubmitPermissionRelation = async () => {
		// 仅提交权限和角色的关联变更
		if (!editPermissionId) return;
		const permissionId = editPermissionId;
		try {
			await syncPermissionRoleRelations(
				permissionId,
				editPermissionRoles,
				originalPermissionRoles,
			);
			toast.success("关联关系更新成功");
			setEditPermissionRelationOpen(false);
			await refreshDataAndDetail(refreshData, fetchDetail, filter);
		} catch (error) {
			handleApiError(error, "更新关联关系失败");
		}
	};

	const handleDeletePermission = async (id: number) => {
		// 删除权限并清理当前筛选
		if (!confirm("确定删除该权限？")) return;
		try {
			await permissionApi.removePermission({ permission_id: id });
			toast.success("删除成功");
			const nextFilter =
				filter.permissionId === id ? { ...filter, permissionId: null } : filter;
			if (filter.permissionId === id) setFilter(nextFilter);
			await refreshDataAndDetail(refreshData, fetchDetail, nextFilter);
		} catch (error) {
			handleApiError(error, "删除失败");
		}
	};

	return {
		createPermissionOpen,
		setCreatePermissionOpen,
		createPermissionLoading,
		newPermissionName,
		setNewPermissionName,
		newPermissionDesc,
		setNewPermissionDesc,
		editPermissionOpen,
		setEditPermissionOpen,
		editPermissionLoading,
		editPermissionId,
		editPermissionName,
		setEditPermissionName,
		editPermissionDesc,
		setEditPermissionDesc,
		editPermissionYn,
		setEditPermissionYn,
		editPermissionRoles,
		setEditPermissionRoles,
		editPermissionRelationOpen,
		setEditPermissionRelationOpen,
		handleCreatePermission,
		openEditPermission,
		openEditPermissionRelation,
		handleEditPermission,
		handleSubmitPermissionRelation,
		handleDeletePermission,
	};
}
