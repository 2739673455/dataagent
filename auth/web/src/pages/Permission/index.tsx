import { Link2Off, Loader2, Shield, User, Users, X } from "lucide-react";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import type { RoleInfo, PermissionInfo, UserInfo } from "../../types";
import { RoleDialogs } from "./components/RoleDialogs";
import { ListItem } from "./components/ListItem";
import { PermissionColumn } from "./components/PermissionColumn";
import { PermissionDialogs } from "./components/PermissionDialogs";
import { UserDialogs } from "./components/UserDialogs";
import {
	useRoleActions,
	usePermissionData,
	usePermissionView,
	usePermissionActions,
	useUserActions,
} from "./hooks";

export default function PermissionPanel() {
	const { loading, data, detail, refreshData, fetchDetail } =
		usePermissionData();
	const {
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
	} = usePermissionView({ data, detail });

	const userActions = useUserActions({
		filter,
		setFilter,
		refreshData,
		fetchDetail,
	});
	const roleActions = useRoleActions({
		filter,
		setFilter,
		refreshData,
		fetchDetail,
	});
	const permissionActions = usePermissionActions({
		filter,
		setFilter,
		refreshData,
		fetchDetail,
	});

	useEffect(() => {
		if (filter.userId || filter.roleId || filter.permissionId) {
			fetchDetail(filter);
		}
	}, [filter, fetchDetail]);

	if (loading) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-[#e8e4df]">
				<Loader2 className="h-8 w-8 animate-spin text-stone-500" />
			</div>
		);
	}

	const renderEffectiveHint = (visible: boolean, message: string) => {
		if (!visible) return null;
		return (
			<Tooltip>
				<TooltipTrigger asChild>
					<span className="inline-flex items-center text-amber-600 opacity-90">
						<Link2Off className="h-3.5 w-3.5" />
					</span>
				</TooltipTrigger>
				<TooltipContent>{message}</TooltipContent>
			</Tooltip>
		);
	};

	return (
		<TooltipProvider>
			<div className="h-screen bg-[#e8e4df] p-6 overflow-hidden">
				<div className="grid grid-cols-3 gap-6 h-full min-h-0">
					<PermissionColumn
						title="用户"
						icon={<User className="h-5 w-5 text-stone-600" />}
						isFiltering={!!filter.userId}
						search={userSearch}
						onSearchChange={setUserSearch}
						sort={userSort.sort}
						sortFields={[
							{ field: "id", label: "ID" },
							{ field: "username", label: "用户名" },
							{ field: "email", label: "邮箱" },
						]}
						onToggleSort={userSort.toggle}
						items={sortedUsers}
						onCreate={() => userActions.setCreateUserOpen(true)}
						renderItem={(user: UserInfo) => (
							<ListItem
								key={user.id}
								isSelected={filter.userId === user.id}
								isDisabled={user.yn === 0}
								onClick={() =>
									setFilter((f) => ({
										...f,
										userId: f.userId === user.id ? null : user.id,
									}))
								}
								onEdit={() => userActions.openEditUser(user)}
								onDelete={() => userActions.handleDeleteUser(user.id)}
								extraButtons={
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className={`h-7 w-7 ${
											filter.userId === user.id
												? "text-white hover:bg-white/20"
												: "text-stone-500 hover:bg-stone-300/50"
										}`}
										onClick={(e) => {
											e.stopPropagation();
											userActions.openEditUserRelation(user);
										}}
										title="编辑与角色的关联关系"
									>
										<Users className="h-4 w-4" />
									</Button>
								}
							>
								<div className="flex-1 min-w-0">
									<div className="flex items-center gap-2">
										<span className="text-xs opacity-60">#{user.id}</span>
										<span className="font-medium truncate">
											{user.username}
										</span>
										{renderEffectiveHint(
											user.yn !== 0 &&
												filter.permissionId !== null &&
												user.effective === 0,
											"此用户不拥有该权限",
										)}
									</div>
									<div className="text-xs opacity-60 truncate">
										{user.email}
									</div>
								</div>
							</ListItem>
						)}
					/>

					<PermissionColumn
						title="角色"
						icon={<Users className="h-5 w-5 text-stone-600" />}
						isFiltering={!!filter.roleId}
						search={roleSearch}
						onSearchChange={setRoleSearch}
						sort={roleSort.sort}
						sortFields={[
							{ field: "id", label: "ID" },
							{ field: "name", label: "名称" },
						]}
						onToggleSort={roleSort.toggle}
						items={sortedRoles}
						onCreate={() => roleActions.setCreateRoleOpen(true)}
						renderItem={(role: RoleInfo) => (
							<ListItem
								key={role.id}
								isSelected={filter.roleId === role.id}
								isDisabled={role.yn === 0}
								onClick={() =>
									setFilter((f) => ({
										...f,
										roleId: f.roleId === role.id ? null : role.id,
									}))
								}
								onEdit={() => roleActions.openEditRole(role)}
								onDelete={() => roleActions.handleDeleteRole(role.id)}
								extraButtons={
									<>
										<Button
											type="button"
											variant="ghost"
											size="icon"
											className={`h-7 w-7 ${
												filter.roleId === role.id
													? "text-white hover:bg-white/20"
													: "text-stone-500 hover:bg-stone-300/50"
											}`}
											onClick={(e) => {
												e.stopPropagation();
												roleActions.openEditRoleRelation(role, "users");
											}}
											title="编辑与用户的关联关系"
										>
											<User className="h-4 w-4" />
										</Button>
										<Button
											type="button"
											variant="ghost"
											size="icon"
											className={`h-7 w-7 ${
												filter.roleId === role.id
													? "text-white hover:bg-white/20"
													: "text-stone-500 hover:bg-stone-300/50"
											}`}
											onClick={(e) => {
												e.stopPropagation();
												roleActions.openEditRoleRelation(role, "permissions");
											}}
											title="编辑与权限的关联关系"
										>
											<Shield className="h-4 w-4" />
										</Button>
									</>
								}
							>
								<div className="flex items-center gap-2">
									<span className="text-xs opacity-60">#{role.id}</span>
									<span className="font-medium">{role.name}</span>
								</div>
							</ListItem>
						)}
					/>

					<PermissionColumn
						title="权限"
						icon={<Shield className="h-5 w-5 text-stone-600" />}
						isFiltering={!!filter.permissionId}
						search={permissionSearch}
						onSearchChange={setPermissionSearch}
						sort={permissionSort.sort}
						sortFields={[
							{ field: "id", label: "ID" },
							{ field: "name", label: "名称" },
						]}
						onToggleSort={permissionSort.toggle}
						items={sortedPermissions}
						onCreate={() => permissionActions.setCreatePermissionOpen(true)}
						renderItem={(permission: PermissionInfo) => (
							<ListItem
								key={permission.id}
								isSelected={filter.permissionId === permission.id}
								isDisabled={permission.yn === 0}
								onClick={() =>
									setFilter((f) => ({
										...f,
										permissionId: f.permissionId === permission.id ? null : permission.id,
									}))
								}
								onEdit={() => permissionActions.openEditPermission(permission)}
								onDelete={() => permissionActions.handleDeletePermission(permission.id)}
								extraButtons={
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className={`h-7 w-7 ${
											filter.permissionId === permission.id
												? "text-white hover:bg-white/20"
												: "text-stone-500 hover:bg-stone-300/50"
										}`}
										onClick={(e) => {
											e.stopPropagation();
											permissionActions.openEditPermissionRelation(permission);
										}}
										title="编辑与角色的关联关系"
									>
										<Users className="h-4 w-4" />
									</Button>
								}
							>
								<div className="flex-1 min-w-0">
									<div className="flex items-center gap-2">
										<span className="text-xs opacity-60">#{permission.id}</span>
										<span className="font-medium truncate">{permission.name}</span>
										{renderEffectiveHint(
											permission.yn !== 0 &&
												filter.userId !== null &&
												permission.effective === 0,
											"此权限不属于该用户",
										)}
									</div>
									{permission.description && (
										<div className="text-xs opacity-60 truncate">
											{permission.description}
										</div>
									)}
								</div>
							</ListItem>
						)}
					/>
				</div>

				{(filter.userId || filter.roleId || filter.permissionId) && (
					<div className="fixed bottom-4 left-1/2 -translate-x-1/2 bg-[#f0ece6] border border-stone-300/60 rounded-full px-4 py-2 shadow-lg flex items-center gap-2">
						<span className="text-sm text-stone-500">当前筛选:</span>
						{filter.userId && (
							<button
								type="button"
								onClick={() => setFilter((f) => ({ ...f, userId: null }))}
								className="bg-stone-600 hover:bg-stone-500 text-white text-sm rounded-full px-3 py-1 flex items-center gap-1.5 transition-colors"
							>
								用户: {data.users.find((u) => u.id === filter.userId)?.username}
								<X className="h-4 w-4" />
							</button>
						)}
						{filter.roleId && (
							<button
								type="button"
								onClick={() => setFilter((f) => ({ ...f, roleId: null }))}
								className="bg-stone-600 hover:bg-stone-500 text-white text-sm rounded-full px-3 py-1 flex items-center gap-1.5 transition-colors"
							>
								角色: {data.roles.find((g) => g.id === filter.roleId)?.name}
								<X className="h-4 w-4" />
							</button>
						)}
						{filter.permissionId && (
							<button
								type="button"
								onClick={() => setFilter((f) => ({ ...f, permissionId: null }))}
								className="bg-stone-600 hover:bg-stone-500 text-white text-sm rounded-full px-3 py-1 flex items-center gap-1.5 transition-colors"
							>
								权限: {data.permissions.find((s) => s.id === filter.permissionId)?.name}
								<X className="h-4 w-4" />
							</button>
						)}
						<button
							type="button"
							onClick={clearFilter}
							className="text-stone-400 hover:text-stone-600 transition-colors"
							title="清除全部筛选"
						>
							<X className="h-5 w-5" />
						</button>
					</div>
				)}

				<UserDialogs
					createOpen={userActions.createUserOpen}
					setCreateOpen={userActions.setCreateUserOpen}
					createLoading={userActions.createUserLoading}
					newEmail={userActions.newUserEmail}
					setNewEmail={userActions.setNewUserEmail}
					newUsername={userActions.newUserUsername}
					setNewUsername={userActions.setNewUserUsername}
					newPassword={userActions.newUserPassword}
					setNewPassword={userActions.setNewUserPassword}
					onCreate={userActions.handleCreateUser}
					editOpen={userActions.editUserOpen}
					setEditOpen={userActions.setEditUserOpen}
					editLoading={userActions.editUserLoading}
					editId={userActions.editUserId}
					editUsername={userActions.editUserUsername}
					setEditUsername={userActions.setEditUserUsername}
					editEmail={userActions.editUserEmail}
					setEditEmail={userActions.setEditUserEmail}
					editPassword={userActions.editUserPassword}
					setEditPassword={userActions.setEditUserPassword}
					editYn={userActions.editUserYn}
					setEditYn={userActions.setEditUserYn}
					onEdit={userActions.handleEditUser}
					relationOpen={userActions.editUserRelationOpen}
					setRelationOpen={userActions.setEditUserRelationOpen}
					editRoles={userActions.editUserRoles}
					setEditRoles={userActions.setEditUserRoles}
					roles={data.roles}
					onSubmitRelation={userActions.handleSubmitUserRelation}
				/>

				<RoleDialogs
					createOpen={roleActions.createRoleOpen}
					setCreateOpen={roleActions.setCreateRoleOpen}
					createLoading={roleActions.createRoleLoading}
					newName={roleActions.newRoleName}
					setNewName={roleActions.setNewRoleName}
					onCreate={roleActions.handleCreateRole}
					editOpen={roleActions.editRoleOpen}
					setEditOpen={roleActions.setEditRoleOpen}
					editLoading={roleActions.editRoleLoading}
					editId={roleActions.editRoleId}
					editName={roleActions.editRoleName}
					setEditName={roleActions.setEditRoleName}
					editYn={roleActions.editRoleYn}
					setEditYn={roleActions.setEditRoleYn}
					onEdit={roleActions.handleEditRole}
					relationOpen={roleActions.editRoleRelationOpen}
					setRelationOpen={roleActions.setEditRoleRelationOpen}
					relationTab={roleActions.editRoleRelationTab}
					editUsers={roleActions.editRoleUsers}
					setEditUsers={roleActions.setEditRoleUsers}
					editPermissions={roleActions.editRolePermissions}
					setEditPermissions={roleActions.setEditRolePermissions}
					users={data.users}
					permissions={data.permissions}
					onSubmitUserRelation={roleActions.handleSubmitRoleUserRelation}
					onSubmitPermissionRelation={roleActions.handleSubmitRolePermissionRelation}
				/>

				<PermissionDialogs
					createOpen={permissionActions.createPermissionOpen}
					setCreateOpen={permissionActions.setCreatePermissionOpen}
					createLoading={permissionActions.createPermissionLoading}
					newName={permissionActions.newPermissionName}
					setNewName={permissionActions.setNewPermissionName}
					newDescription={permissionActions.newPermissionDesc}
					setNewDescription={permissionActions.setNewPermissionDesc}
					onCreate={permissionActions.handleCreatePermission}
					editOpen={permissionActions.editPermissionOpen}
					setEditOpen={permissionActions.setEditPermissionOpen}
					editLoading={permissionActions.editPermissionLoading}
					editId={permissionActions.editPermissionId}
					editName={permissionActions.editPermissionName}
					setEditName={permissionActions.setEditPermissionName}
					editDescription={permissionActions.editPermissionDesc}
					setEditDescription={permissionActions.setEditPermissionDesc}
					editYn={permissionActions.editPermissionYn}
					setEditYn={permissionActions.setEditPermissionYn}
					onEdit={permissionActions.handleEditPermission}
					relationOpen={permissionActions.editPermissionRelationOpen}
					setRelationOpen={permissionActions.setEditPermissionRelationOpen}
					editRoles={permissionActions.editPermissionRoles}
					setEditRoles={permissionActions.setEditPermissionRoles}
					roles={data.roles}
					onSubmitRelation={permissionActions.handleSubmitPermissionRelation}
				/>
			</div>
		</TooltipProvider>
	);
}
