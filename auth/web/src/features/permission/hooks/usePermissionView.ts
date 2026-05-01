import { useMemo } from "react";
import { usePermissionStore } from "@/features/permission/store";
import type { PermissionInfo, RoleInfo, UserInfo } from "@/features/permission/types";
import type { PermissionData, PermissionDetail } from "@/features/permission/types";

interface UsePermissionViewOptions {
  data: PermissionData;
  detail: PermissionDetail;
}

export function usePermissionView({ data, detail }: UsePermissionViewOptions) {
  const filter = usePermissionStore((s) => s.filter);
  const setFilter = usePermissionStore((s) => s.setFilter);
  const clearFilter = usePermissionStore((s) => s.clearFilter);
  const userSearch = usePermissionStore((s) => s.userSearch);
  const setUserSearch = usePermissionStore((s) => s.setUserSearch);
  const roleSearch = usePermissionStore((s) => s.roleSearch);
  const setRoleSearch = usePermissionStore((s) => s.setRoleSearch);
  const permissionSearch = usePermissionStore((s) => s.permissionSearch);
  const setPermissionSearch = usePermissionStore((s) => s.setPermissionSearch);
  const userSort = usePermissionStore((s) => s.userSort);
  const toggleUserSort = usePermissionStore((s) => s.toggleUserSort);
  const roleSort = usePermissionStore((s) => s.roleSort);
  const toggleRoleSort = usePermissionStore((s) => s.toggleRoleSort);
  const permissionSort = usePermissionStore((s) => s.permissionSort);
  const togglePermissionSort = usePermissionStore((s) => s.togglePermissionSort);

  const filteredUsers = useMemo(() => {
    let result =
      filter.permissionId && detail.permissionDetail ? detail.permissionDetail.users : data.users;
    if (userSearch) {
      const search = userSearch.toLowerCase();
      result = result.filter(
        (u) => u.username.toLowerCase().includes(search) || u.email.toLowerCase().includes(search)
      );
    }
    if (filter.roleId && detail.roleDetail) {
      result = result.filter((u) => detail.roleDetail?.users.some((gu) => gu.id === u.id));
    }
    return result;
  }, [data.users, detail.roleDetail, detail.permissionDetail, filter, userSearch]);

  const filteredRoles = useMemo(() => {
    let result = data.roles;
    if (roleSearch) {
      const search = roleSearch.toLowerCase();
      result = result.filter((g) => g.name.toLowerCase().includes(search));
    }
    if (filter.userId && detail.userDetail) {
      result = result.filter((g) => detail.userDetail?.roles.some((ug) => ug.id === g.id));
    }
    if (filter.permissionId && detail.permissionDetail) {
      result = result.filter((g) => detail.permissionDetail?.roles.some((sg) => sg.id === g.id));
    }
    return result;
  }, [data.roles, detail.permissionDetail, detail.userDetail, filter, roleSearch]);

  const filteredPermissions = useMemo(() => {
    let result =
      filter.userId && detail.userDetail ? detail.userDetail.permissions : data.permissions;
    if (permissionSearch) {
      const search = permissionSearch.toLowerCase();
      result = result.filter(
        (s) =>
          s.name.toLowerCase().includes(search) || s.description?.toLowerCase().includes(search)
      );
    }
    if (filter.roleId && detail.roleDetail) {
      result = result.filter((s) => detail.roleDetail?.permissions.some((gs) => gs.id === s.id));
    }
    return result;
  }, [data.permissions, detail.roleDetail, detail.userDetail, filter, permissionSearch]);

  const sortedUsers = useMemo(() => {
    const sorted = [...filteredUsers];
    sorted.sort((a: UserInfo, b: UserInfo) => {
      let cmp = 0;
      if (userSort.field === "id") cmp = a.id - b.id;
      else if (userSort.field === "username") cmp = a.username.localeCompare(b.username);
      else cmp = a.email.localeCompare(b.email);
      return userSort.order === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [filteredUsers, userSort]);

  const sortedRoles = useMemo(() => {
    const sorted = [...filteredRoles];
    sorted.sort((a: RoleInfo, b: RoleInfo) => {
      const cmp = roleSort.field === "id" ? a.id - b.id : a.name.localeCompare(b.name);
      return roleSort.order === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [filteredRoles, roleSort]);

  const sortedPermissions = useMemo(() => {
    const sorted = [...filteredPermissions];
    sorted.sort((a: PermissionInfo, b: PermissionInfo) => {
      const cmp = permissionSort.field === "id" ? a.id - b.id : a.name.localeCompare(b.name);
      return permissionSort.order === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [filteredPermissions, permissionSort]);

  return {
    filter,
    setFilter,
    clearFilter,
    userSearch,
    setUserSearch,
    roleSearch,
    setRoleSearch,
    permissionSearch,
    setPermissionSearch,
    userSort: { sort: userSort, toggle: toggleUserSort },
    roleSort: { sort: roleSort, toggle: toggleRoleSort },
    permissionSort: { sort: permissionSort, toggle: togglePermissionSort },
    sortedUsers,
    sortedRoles,
    sortedPermissions,
  };
}
