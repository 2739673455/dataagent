import { permissionRelationApi } from "@/features/permission/api";
import { computeRelationDiff } from "./relation";

export async function syncUserRoleRelations(
  userId: number,
  currentRoleIds: number[],
  originalRoleIds: number[]
) {
  const { toAdd, toRemove } = computeRelationDiff(currentRoleIds, originalRoleIds);
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
}

export async function syncRoleUserRelations(
  roleId: number,
  currentUserIds: number[],
  originalUserIds: number[]
) {
  const { toAdd, toRemove } = computeRelationDiff(currentUserIds, originalUserIds);
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
}

export async function syncRolePermissionRelations(
  roleId: number,
  currentPermissionIds: number[],
  originalPermissionIds: number[]
) {
  const { toAdd, toRemove } = computeRelationDiff(currentPermissionIds, originalPermissionIds);
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
}

export async function syncPermissionRoleRelations(
  permissionId: number,
  currentRoleIds: number[],
  originalRoleIds: number[]
) {
  const { toAdd, toRemove } = computeRelationDiff(currentRoleIds, originalRoleIds);
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
}
