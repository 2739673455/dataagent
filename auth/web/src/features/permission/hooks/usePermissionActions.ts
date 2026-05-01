import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { handleApiError } from "@/shared/libs/error";
import type {
  CreatePermissionFormData,
  CreateRoleFormData,
  CreateUserFormData,
  UpdatePermissionFormData,
  UpdateRoleFormData,
  UpdateUserFormData,
} from "@/features/permission/schemas";
import { permissionApi, permissionRoleApi, permissionUserApi } from "@/features/permission/api";
import { usePermissionStore } from "@/features/permission/store";
import type { PermissionInfo, RoleInfo, UserInfo } from "@/features/permission/types";
import {
  syncPermissionRoleRelations,
  syncRolePermissionRelations,
  syncRoleUserRelations,
  syncUserRoleRelations,
} from "../utils";

export function useUserActions() {
  const queryClient = useQueryClient();

  const createUserOpen = usePermissionStore((s) => s.createUserOpen);
  const setCreateUserOpen = usePermissionStore((s) => s.setCreateUserOpen);
  const createUserLoading = usePermissionStore((s) => s.createUserLoading);
  const editUserOpen = usePermissionStore((s) => s.editUserOpen);
  const setEditUserOpen = usePermissionStore((s) => s.setEditUserOpen);
  const editUserLoading = usePermissionStore((s) => s.editUserLoading);
  const editUserId = usePermissionStore((s) => s.editUserId);
  const editUserUsername = usePermissionStore((s) => s.editUserUsername);
  const editUserEmail = usePermissionStore((s) => s.editUserEmail);
  const editUserYn = usePermissionStore((s) => s.editUserYn);
  const setEditUserYn = usePermissionStore((s) => s.setEditUserYn);
  const editUserRoles = usePermissionStore((s) => s.editUserRoles);
  const setEditUserRoles = usePermissionStore((s) => s.setEditUserRoles);
  const editUserRelationOpen = usePermissionStore((s) => s.editUserRelationOpen);
  const setEditUserRelationOpen = usePermissionStore((s) => s.setEditUserRelationOpen);

  const store = usePermissionStore;

  const handleCreateUser = async (data: CreateUserFormData) => {
    store.getState().setCreateUserLoading(true);
    try {
      await permissionUserApi.createUser({
        email: data.email,
        username: data.username,
        password: data.password,
      });
      toast.success("用户创建成功");
      store.getState().setCreateUserOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "创建失败");
    } finally {
      store.getState().setCreateUserLoading(false);
    }
  };

  const openEditUser = async (user: UserInfo) => {
    store.getState().setEditUserId(user.id);
    store.getState().setEditUserUsername(user.username);
    store.getState().setEditUserEmail(user.email);
    store.getState().setEditUserYn(user.yn);
    try {
      const res = await permissionUserApi.getUser(user.id);
      const roleIds = res.roles.map((g) => g.id);
      store.getState().setEditUserRoles(roleIds);
      store.getState().setOriginalUserRoles(roleIds);
    } catch {
      store.getState().setEditUserRoles([]);
      store.getState().setOriginalUserRoles([]);
    }
    store.getState().setEditUserOpen(true);
  };

  const openEditUserRelation = async (user: UserInfo) => {
    store.getState().setEditUserId(user.id);
    store.getState().setEditUserUsername(user.username);
    store.getState().setEditUserEmail(user.email);
    try {
      const res = await permissionUserApi.getUser(user.id);
      const roleIds = res.roles.map((g) => g.id);
      store.getState().setEditUserRoles(roleIds);
      store.getState().setOriginalUserRoles(roleIds);
    } catch {
      store.getState().setEditUserRoles([]);
      store.getState().setOriginalUserRoles([]);
    }
    store.getState().setEditUserRelationOpen(true);
  };

  const handleEditUser = async (data: UpdateUserFormData) => {
    const state = store.getState();
    if (!state.editUserId) return;
    state.setEditUserLoading(true);
    try {
      await permissionUserApi.updateUser({
        user_id: state.editUserId,
        username: data.username || undefined,
        email: data.email || undefined,
        password: data.password || undefined,
        yn: state.editUserYn,
      });
      await syncUserRoleRelations(state.editUserId, state.editUserRoles, state.originalUserRoles);
      toast.success("用户更新成功");
      state.setEditUserOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新失败");
    } finally {
      store.getState().setEditUserLoading(false);
    }
  };

  const handleSubmitUserRelation = async () => {
    const state = store.getState();
    if (!state.editUserId) return;
    try {
      await syncUserRoleRelations(state.editUserId, state.editUserRoles, state.originalUserRoles);
      toast.success("关联关系更新成功");
      state.setEditUserRelationOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新关联关系失败");
    }
  };

  const handleDeleteUser = async (id: number) => {
    if (!confirm("确定删除该用户？")) return;
    try {
      await permissionUserApi.removeUser({ user_id: id });
      toast.success("删除成功");
      const s = store.getState();
      if (s.filter.userId === id) s.setFilter({ ...s.filter, userId: null });
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "删除失败");
    }
  };

  return {
    createUserOpen,
    setCreateUserOpen,
    createUserLoading,
    editUserOpen,
    setEditUserOpen,
    editUserLoading,
    editUserId,
    editUserUsername,
    editUserEmail,
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

export function useRoleActions() {
  const queryClient = useQueryClient();

  const createRoleOpen = usePermissionStore((s) => s.createRoleOpen);
  const setCreateRoleOpen = usePermissionStore((s) => s.setCreateRoleOpen);
  const createRoleLoading = usePermissionStore((s) => s.createRoleLoading);
  const editRoleOpen = usePermissionStore((s) => s.editRoleOpen);
  const setEditRoleOpen = usePermissionStore((s) => s.setEditRoleOpen);
  const editRoleLoading = usePermissionStore((s) => s.editRoleLoading);
  const editRoleId = usePermissionStore((s) => s.editRoleId);
  const editRoleName = usePermissionStore((s) => s.editRoleName);
  const editRoleYn = usePermissionStore((s) => s.editRoleYn);
  const setEditRoleYn = usePermissionStore((s) => s.setEditRoleYn);
  const editRoleUsers = usePermissionStore((s) => s.editRoleUsers);
  const setEditRoleUsers = usePermissionStore((s) => s.setEditRoleUsers);
  const editRolePermissions = usePermissionStore((s) => s.editRolePermissions);
  const setEditRolePermissions = usePermissionStore((s) => s.setEditRolePermissions);
  const editRoleRelationOpen = usePermissionStore((s) => s.editRoleRelationOpen);
  const setEditRoleRelationOpen = usePermissionStore((s) => s.setEditRoleRelationOpen);
  const editRoleRelationTab = usePermissionStore((s) => s.editRoleRelationTab);

  const store = usePermissionStore;

  const handleCreateRole = async (data: CreateRoleFormData) => {
    store.getState().setCreateRoleLoading(true);
    try {
      await permissionRoleApi.createRole({ name: data.name });
      toast.success("角色创建成功");
      store.getState().setCreateRoleOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "创建失败");
    } finally {
      store.getState().setCreateRoleLoading(false);
    }
  };

  const openEditRole = async (role: RoleInfo) => {
    store.getState().setEditRoleId(role.id);
    store.getState().setEditRoleName(role.name);
    store.getState().setEditRoleYn(role.yn);
    try {
      const res = await permissionRoleApi.getRole(role.id);
      store.getState().setEditRoleUsers(res.users.map((u) => u.id));
      store.getState().setOriginalRoleUsers(res.users.map((u) => u.id));
      store.getState().setEditRolePermissions(res.permissions.map((s) => s.id));
      store.getState().setOriginalRolePermissions(res.permissions.map((s) => s.id));
    } catch {
      store.getState().setEditRoleUsers([]);
      store.getState().setOriginalRoleUsers([]);
      store.getState().setEditRolePermissions([]);
      store.getState().setOriginalRolePermissions([]);
    }
    store.getState().setEditRoleOpen(true);
  };

  const openEditRoleRelation = async (role: RoleInfo, tab: "users" | "permissions") => {
    store.getState().setEditRoleId(role.id);
    store.getState().setEditRoleName(role.name);
    try {
      const res = await permissionRoleApi.getRole(role.id);
      store.getState().setEditRoleUsers(res.users.map((u) => u.id));
      store.getState().setOriginalRoleUsers(res.users.map((u) => u.id));
      store.getState().setEditRolePermissions(res.permissions.map((s) => s.id));
      store.getState().setOriginalRolePermissions(res.permissions.map((s) => s.id));
    } catch {
      store.getState().setEditRoleUsers([]);
      store.getState().setOriginalRoleUsers([]);
      store.getState().setEditRolePermissions([]);
      store.getState().setOriginalRolePermissions([]);
    }
    store.getState().setEditRoleRelationTab(tab);
    store.getState().setEditRoleRelationOpen(true);
  };

  const handleEditRole = async (data: UpdateRoleFormData) => {
    const state = store.getState();
    if (!state.editRoleId) return;
    state.setEditRoleLoading(true);
    try {
      await permissionRoleApi.updateRole({
        role_id: state.editRoleId,
        name: data.name || undefined,
        yn: state.editRoleYn,
      });
      await syncRoleUserRelations(state.editRoleId, state.editRoleUsers, state.originalRoleUsers);
      await syncRolePermissionRelations(
        state.editRoleId,
        state.editRolePermissions,
        state.originalRolePermissions
      );
      toast.success("角色更新成功");
      state.setEditRoleOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新失败");
    } finally {
      store.getState().setEditRoleLoading(false);
    }
  };

  const handleSubmitRoleUserRelation = async () => {
    const state = store.getState();
    if (!state.editRoleId) return;
    try {
      await syncRoleUserRelations(state.editRoleId, state.editRoleUsers, state.originalRoleUsers);
      toast.success("用户关联更新成功");
      state.setEditRoleRelationOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新用户关联失败");
    }
  };

  const handleSubmitRolePermissionRelation = async () => {
    const state = store.getState();
    if (!state.editRoleId) return;
    try {
      await syncRolePermissionRelations(
        state.editRoleId,
        state.editRolePermissions,
        state.originalRolePermissions
      );
      toast.success("权限关联更新成功");
      state.setEditRoleRelationOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新权限关联失败");
    }
  };

  const handleDeleteRole = async (id: number) => {
    if (!confirm("确定删除该角色？")) return;
    try {
      await permissionRoleApi.removeRole({ role_id: id });
      toast.success("删除成功");
      const s = store.getState();
      if (s.filter.roleId === id) s.setFilter({ ...s.filter, roleId: null });
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "删除失败");
    }
  };

  return {
    createRoleOpen,
    setCreateRoleOpen,
    createRoleLoading,
    editRoleOpen,
    setEditRoleOpen,
    editRoleLoading,
    editRoleId,
    editRoleName,
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

export function usePermissionActions() {
  const queryClient = useQueryClient();

  const createPermissionOpen = usePermissionStore((s) => s.createPermissionOpen);
  const setCreatePermissionOpen = usePermissionStore((s) => s.setCreatePermissionOpen);
  const createPermissionLoading = usePermissionStore((s) => s.createPermissionLoading);
  const editPermissionOpen = usePermissionStore((s) => s.editPermissionOpen);
  const setEditPermissionOpen = usePermissionStore((s) => s.setEditPermissionOpen);
  const editPermissionLoading = usePermissionStore((s) => s.editPermissionLoading);
  const editPermissionId = usePermissionStore((s) => s.editPermissionId);
  const editPermissionName = usePermissionStore((s) => s.editPermissionName);
  const editPermissionDesc = usePermissionStore((s) => s.editPermissionDesc);
  const editPermissionYn = usePermissionStore((s) => s.editPermissionYn);
  const setEditPermissionYn = usePermissionStore((s) => s.setEditPermissionYn);
  const editPermissionRoles = usePermissionStore((s) => s.editPermissionRoles);
  const setEditPermissionRoles = usePermissionStore((s) => s.setEditPermissionRoles);
  const editPermissionRelationOpen = usePermissionStore((s) => s.editPermissionRelationOpen);
  const setEditPermissionRelationOpen = usePermissionStore((s) => s.setEditPermissionRelationOpen);

  const store = usePermissionStore;

  const handleCreatePermission = async (data: CreatePermissionFormData) => {
    store.getState().setCreatePermissionLoading(true);
    try {
      await permissionApi.createPermission({
        name: data.name,
        description: data.description || undefined,
      });
      toast.success("权限创建成功");
      store.getState().setCreatePermissionOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "创建失败");
    } finally {
      store.getState().setCreatePermissionLoading(false);
    }
  };

  const openEditPermission = async (permission: PermissionInfo) => {
    store.getState().setEditPermissionId(permission.id);
    store.getState().setEditPermissionName(permission.name);
    store.getState().setEditPermissionDesc(permission.description || "");
    store.getState().setEditPermissionYn(permission.yn);
    try {
      const res = await permissionApi.getPermission(permission.id);
      const roleIds = res.roles.map((g) => g.id);
      store.getState().setEditPermissionRoles(roleIds);
      store.getState().setOriginalPermissionRoles(roleIds);
    } catch {
      store.getState().setEditPermissionRoles([]);
      store.getState().setOriginalPermissionRoles([]);
    }
    store.getState().setEditPermissionOpen(true);
  };

  const openEditPermissionRelation = async (permission: PermissionInfo) => {
    store.getState().setEditPermissionId(permission.id);
    store.getState().setEditPermissionName(permission.name);
    store.getState().setEditPermissionDesc(permission.description || "");
    try {
      const res = await permissionApi.getPermission(permission.id);
      const roleIds = res.roles.map((g) => g.id);
      store.getState().setEditPermissionRoles(roleIds);
      store.getState().setOriginalPermissionRoles(roleIds);
    } catch {
      store.getState().setEditPermissionRoles([]);
      store.getState().setOriginalPermissionRoles([]);
    }
    store.getState().setEditPermissionRelationOpen(true);
  };

  const handleEditPermission = async (data: UpdatePermissionFormData) => {
    const state = store.getState();
    if (!state.editPermissionId) return;
    state.setEditPermissionLoading(true);
    try {
      await permissionApi.updatePermission({
        permission_id: state.editPermissionId,
        name: data.name || undefined,
        description: data.description || undefined,
        yn: state.editPermissionYn,
      });
      await syncPermissionRoleRelations(
        state.editPermissionId,
        state.editPermissionRoles,
        state.originalPermissionRoles
      );
      toast.success("权限更新成功");
      state.setEditPermissionOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新失败");
    } finally {
      store.getState().setEditPermissionLoading(false);
    }
  };

  const handleSubmitPermissionRelation = async () => {
    const state = store.getState();
    if (!state.editPermissionId) return;
    try {
      await syncPermissionRoleRelations(
        state.editPermissionId,
        state.editPermissionRoles,
        state.originalPermissionRoles
      );
      toast.success("关联关系更新成功");
      state.setEditPermissionRelationOpen(false);
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "更新关联关系失败");
    }
  };

  const handleDeletePermission = async (id: number) => {
    if (!confirm("确定删除该权限？")) return;
    try {
      await permissionApi.removePermission({ permission_id: id });
      toast.success("删除成功");
      const s = store.getState();
      if (s.filter.permissionId === id) s.setFilter({ ...s.filter, permissionId: null });
      queryClient.invalidateQueries({ queryKey: ["permission"] });
    } catch (error) {
      handleApiError(error, "删除失败");
    }
  };

  return {
    createPermissionOpen,
    setCreatePermissionOpen,
    createPermissionLoading,
    editPermissionOpen,
    setEditPermissionOpen,
    editPermissionLoading,
    editPermissionId,
    editPermissionName,
    editPermissionDesc,
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
