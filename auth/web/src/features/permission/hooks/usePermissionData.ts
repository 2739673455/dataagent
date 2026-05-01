import { useQuery } from "@tanstack/react-query";
import { permissionApi, permissionRoleApi, permissionUserApi } from "@/features/permission/api";
import { usePermissionStore } from "@/features/permission/store";

export function usePermissionData() {
  const filter = usePermissionStore((s) => s.filter);

  const users = useQuery({
    queryKey: ["permission", "users"],
    queryFn: async () => {
      const res = await permissionUserApi.listUsers({ all: true });
      return res.items;
    },
  });

  const roles = useQuery({
    queryKey: ["permission", "roles"],
    queryFn: async () => {
      const res = await permissionRoleApi.listRoles({ all: true });
      return res.items;
    },
  });

  const permissions = useQuery({
    queryKey: ["permission", "permissions"],
    queryFn: async () => {
      const res = await permissionApi.listPermissions({ all: true });
      return res.items;
    },
  });

  const userDetail = useQuery({
    queryKey: ["permission", "user", filter.userId],
    queryFn: () => permissionUserApi.getUser(filter.userId as number),
    enabled: !!filter.userId,
  });

  const roleDetail = useQuery({
    queryKey: ["permission", "role", filter.roleId],
    queryFn: () => permissionRoleApi.getRole(filter.roleId as number),
    enabled: !!filter.roleId,
  });

  const permissionDetail = useQuery({
    queryKey: ["permission", "permission", filter.permissionId],
    queryFn: () => permissionApi.getPermission(filter.permissionId as number),
    enabled: !!filter.permissionId,
  });

  const loading = users.isLoading || roles.isLoading || permissions.isLoading;

  const data = {
    users: users.data ?? [],
    roles: roles.data ?? [],
    permissions: permissions.data ?? [],
  };

  const detail = {
    userDetail: userDetail.data ?? null,
    roleDetail: roleDetail.data ?? null,
    permissionDetail: permissionDetail.data ?? null,
  };

  return { loading, data, detail };
}
