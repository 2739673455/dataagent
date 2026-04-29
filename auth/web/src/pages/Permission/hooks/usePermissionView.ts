import { useMemo, useState } from 'react';
import type { RoleInfo, PermissionInfo, UserInfo } from '../../../types';
import type {
  FilterState,
  RoleSortField,
  PermissionData,
  PermissionDetail,
  PermissionSortField,
  UserSortField,
} from '../types';
import { useSort } from './useSort';

interface UsePermissionViewOptions {
  data: PermissionData;
  detail: PermissionDetail;
}

interface SortableEntity {
  id: number;
}

function sortByIdOrName<T extends SortableEntity>(
  items: T[],
  field: 'id' | 'name',
  order: 'asc' | 'desc',
  getName?: (item: T) => string
) {
  const sorted = [...items];
  sorted.sort((a, b) => {
    const cmp =
      field === 'id' ? a.id - b.id : (getName?.(a) ?? '').localeCompare(getName?.(b) ?? '');
    return order === 'asc' ? cmp : -cmp;
  });
  return sorted;
}

export function usePermissionView({ data, detail }: UsePermissionViewOptions) {
  const [filter, setFilter] = useState<FilterState>({
    userId: null,
    roleId: null,
    permissionId: null,
  });
  const [userSearch, setUserSearch] = useState('');
  const [roleSearch, setRoleSearch] = useState('');
  const [permissionSearch, setPermissionSearch] = useState('');

  const userSort = useSort<UserSortField>('id');
  const roleSort = useSort<RoleSortField>('id');
  const permissionSort = useSort<PermissionSortField>('id');

  const filteredUsers = useMemo(() => {
    let result = filter.permissionId && detail.permissionDetail ? detail.permissionDetail.users : data.users;
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
  }, [
    data.users,
    detail.roleDetail,
    detail.permissionDetail,
    filter.roleId,
    filter.permissionId,
    userSearch,
  ]);

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
    let result = filter.userId && detail.userDetail ? detail.userDetail.permissions : data.permissions;
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
  }, [
    data.permissions,
    detail.roleDetail,
    detail.userDetail,
    filter.roleId,
    filter.userId,
    permissionSearch,
  ]);

  const sortedUsers = useMemo(() => {
    const sorted = [...filteredUsers];
    sorted.sort((a: UserInfo, b: UserInfo) => {
      let cmp = 0;
      if (userSort.sort.field === 'id') cmp = a.id - b.id;
      else if (userSort.sort.field === 'username') cmp = a.username.localeCompare(b.username);
      else cmp = a.email.localeCompare(b.email);
      return userSort.sort.order === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [filteredUsers, userSort.sort]);

  const sortedRoles = useMemo(
    () =>
      sortByIdOrName<RoleInfo>(
        filteredRoles,
        roleSort.sort.field,
        roleSort.sort.order,
        (item) => item.name
      ),
    [filteredRoles, roleSort.sort]
  );

  const sortedPermissions = useMemo(
    () =>
      sortByIdOrName<PermissionInfo>(
        filteredPermissions,
        permissionSort.sort.field,
        permissionSort.sort.order,
        (item) => item.name
      ),
    [filteredPermissions, permissionSort.sort]
  );

  const clearFilter = () => {
    setFilter({ userId: null, roleId: null, permissionId: null });
  };

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
    userSort,
    roleSort,
    permissionSort,
    sortedUsers,
    sortedRoles,
    sortedPermissions,
  };
}
