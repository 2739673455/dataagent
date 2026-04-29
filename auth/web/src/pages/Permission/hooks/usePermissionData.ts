import { useCallback, useEffect, useState } from "react";
import {
	permissionRoleApi,
	permissionApi,
	permissionUserApi,
} from "../../../apis/permission";
import { handleApiError } from "../../../libs/error";
import type { FilterState, PermissionData, PermissionDetail } from "../types";

export function usePermissionData() {
	const [loading, setLoading] = useState(true);
	const [data, setData] = useState<PermissionData>({
		users: [],
		roles: [],
		permissions: [],
	});
	const [detail, setDetail] = useState<PermissionDetail>({
		userDetail: null,
		roleDetail: null,
		permissionDetail: null,
	});

	const fetchListData = useCallback(async (): Promise<PermissionData> => {
		const [usersRes, rolesRes, permissionsRes] = await Promise.all([
			permissionUserApi.listUsers({ all: true }),
			permissionRoleApi.listRoles({ all: true }),
			permissionApi.listPermissions({ all: true }),
		]);

		return {
			users: usersRes.items,
			roles: rolesRes.items,
			permissions: permissionsRes.items,
		};
	}, []);

	const fetchBaseData = useCallback(async () => {
		setLoading(true);
		try {
			setData(await fetchListData());
		} catch (error: unknown) {
			handleApiError(error, "获取数据失败");
		} finally {
			setLoading(false);
		}
	}, [fetchListData]);

	const refreshData = useCallback(async () => {
		try {
			setData(await fetchListData());
		} catch (error: unknown) {
			handleApiError(error, "刷新数据失败");
		}
	}, [fetchListData]);

	const fetchDetail = useCallback(async (filter: FilterState) => {
		try {
			const promises: Promise<void>[] = [];
			const newDetail: PermissionDetail = {
				userDetail: null,
				roleDetail: null,
				permissionDetail: null,
			};

			if (filter.userId) {
				promises.push(
					permissionUserApi.getUser(filter.userId).then((res) => {
						newDetail.userDetail = res;
					}),
				);
			}
			if (filter.roleId) {
				promises.push(
					permissionRoleApi.getRole(filter.roleId).then((res) => {
						newDetail.roleDetail = res;
					}),
				);
			}
			if (filter.permissionId) {
				promises.push(
					permissionApi.getPermission(filter.permissionId).then((res) => {
						newDetail.permissionDetail = res;
					}),
				);
			}

			await Promise.all(promises);
			setDetail(newDetail);
		} catch (error: unknown) {
			handleApiError(error, "获取详情失败");
		}
	}, []);

	const clearDetail = useCallback(() => {
		setDetail({
			userDetail: null,
			roleDetail: null,
			permissionDetail: null,
		});
	}, []);

	useEffect(() => {
		fetchBaseData();
	}, [fetchBaseData]);

	return {
		loading,
		data,
		detail,
		fetchBaseData,
		refreshData,
		fetchDetail,
		clearDetail,
	};
}
