import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { PanelInput } from "@/components/ui/panel-input";

import type { PermissionInfo, UserInfo } from "../../../types";
import { RelationEditor } from "./RelationEditor";

interface RoleDialogsProps {
	// 创建
	createOpen: boolean;
	setCreateOpen: (open: boolean) => void;
	createLoading: boolean;
	newName: string;
	setNewName: (v: string) => void;
	onCreate: (e: React.FormEvent) => void;
	// 编辑
	editOpen: boolean;
	setEditOpen: (open: boolean) => void;
	editLoading: boolean;
	editId: number | null;
	editName: string;
	setEditName: (v: string) => void;
	editYn: number;
	setEditYn: (v: number) => void;
	onEdit: (e: React.FormEvent) => void;
	// 关联编辑
	relationOpen: boolean;
	setRelationOpen: (open: boolean) => void;
	relationTab: "users" | "permissions";
	editUsers: number[];
	setEditUsers: (v: number[]) => void;
	editPermissions: number[];
	setEditPermissions: (v: number[]) => void;
	users: UserInfo[];
	permissions: PermissionInfo[];
	onSubmitUserRelation: () => void;
	onSubmitPermissionRelation: () => void;
}

/**
 * RoleDialogs - 角色弹窗组件
 *
 * 包含：创建角色弹窗、编辑角色弹窗、角色-用户关联编辑弹窗、角色-权限关联编辑弹窗
 */
export function RoleDialogs({
	createOpen,
	setCreateOpen,
	createLoading,
	newName,
	setNewName,
	onCreate,
	editOpen,
	setEditOpen,
	editLoading,
	editId,
	editName,
	setEditName,
	editYn,
	setEditYn,
	onEdit,
	relationOpen,
	setRelationOpen,
	relationTab,
	editUsers,
	setEditUsers,
	editPermissions,
	setEditPermissions,
	users,
	permissions,
	onSubmitUserRelation,
	onSubmitPermissionRelation,
}: RoleDialogsProps) {
	const preventAutoFocus = (event: Event) => {
		event.preventDefault();
	};

	return (
		<>
			{/* 创建角色弹窗 */}
			<Dialog open={createOpen} onOpenChange={setCreateOpen}>
				<DialogContent
					className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
					onOpenAutoFocus={preventAutoFocus}
				>
					<DialogHeader>
						<DialogTitle className="text-stone-700">创建角色</DialogTitle>
					</DialogHeader>
					<form onSubmit={onCreate} className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="new-role-name" className="text-stone-600">
								角色名
							</Label>
							<PanelInput
								id="new-role-name"
								value={newName}
								onChange={(e) => setNewName(e.target.value)}
								required
							/>
						</div>
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => setCreateOpen(false)}
								className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
							>
								取消
							</Button>
							<Button
								type="submit"
								disabled={createLoading}
								className="bg-stone-600 hover:bg-stone-700 rounded-xl"
							>
								{createLoading && (
									<Loader2 className="mr-2 h-4 w-4 animate-spin" />
								)}
								创建
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			{/* 编辑角色弹窗 */}
			<Dialog open={editOpen} onOpenChange={setEditOpen}>
				<DialogContent
					className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
					onOpenAutoFocus={preventAutoFocus}
				>
					<DialogHeader>
						<DialogTitle className="text-stone-700">编辑角色</DialogTitle>
					</DialogHeader>
					<form onSubmit={onEdit} className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="edit-role-name" className="text-stone-600">
								角色名
							</Label>
							<PanelInput
								id="edit-role-name"
								value={editName}
								onChange={(e) => setEditName(e.target.value)}
							/>
						</div>
						<div className="flex items-center justify-end">
							<button
								type="button"
								onClick={() => setEditYn(editYn === 1 ? 0 : 1)}
								className={`
									w-[100px] h-[44px] rounded-xl border-none cursor-pointer
									flex justify-center items-center
									transition-all duration-500 ease-out
									${
										editYn === 1
											? "bg-[#2ecc71] shadow-[inset_8px_8px_16px_#1a7a42,inset_-4px_-4px_8px_rgba(255,255,255,0.3)] scale-[0.94]"
											: "bg-[#e0e5ec] shadow-[6px_6px_12px_#b8b9be,-6px_-6px_12px_#ffffff]"
									}
									active:scale-[0.92] active:duration-100
								`}
							>
								<span
									className={`
										font-extrabold text-[14px] tracking-wider
										transition-all duration-400 ease-out
										${
											editYn === 1
												? "text-white drop-shadow-[0_0_4px_rgba(255,255,255,0.6)]"
												: "text-[#888]"
										}
									`}
								>
									{editYn === 1 ? "启用中" : "已禁用"}
								</span>
							</button>
						</div>
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => setEditOpen(false)}
								className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
							>
								取消
							</Button>
							<Button
								type="submit"
								disabled={editLoading}
								className="bg-stone-600 hover:bg-stone-700 rounded-xl"
							>
								{editLoading && (
									<Loader2 className="mr-2 h-4 w-4 animate-spin" />
								)}
								保存
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			{/* 角色关联编辑弹窗 */}
			<Dialog open={relationOpen} onOpenChange={setRelationOpen}>
				<DialogContent
					className="bg-[#f0ece6] border-stone-300/60 rounded-2xl max-w-6xl w-[90vw]"
					onOpenAutoFocus={preventAutoFocus}
				>
					<DialogHeader>
						<DialogTitle className="text-stone-700">
							{relationTab === "users" ? "编辑角色-用户关联" : "编辑角色-权限关联"}
						</DialogTitle>
					</DialogHeader>
					{relationTab === "users" ? (
						<RelationEditor
							title="用户"
							currentItem={{
								type: "角色",
								id: editId,
								name: editName,
							}}
							allItems={users.map((u) => ({
								id: u.id,
								name: u.username,
								description: u.email,
							}))}
							selectedIds={editUsers}
							onChange={setEditUsers}
						/>
					) : (
						<RelationEditor
							title="权限"
							currentItem={{
								type: "角色",
								id: editId,
								name: editName,
							}}
							allItems={permissions.map((s) => ({
								id: s.id,
								name: s.name,
								description: s.description,
							}))}
							selectedIds={editPermissions}
							onChange={setEditPermissions}
						/>
					)}
					<DialogFooter className="mt-4">
						<Button
							type="button"
							variant="outline"
							onClick={() => setRelationOpen(false)}
							className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
						>
							取消
						</Button>
						<Button
							type="button"
							onClick={() => {
								if (relationTab === "users") {
									onSubmitUserRelation();
								} else {
									onSubmitPermissionRelation();
								}
							}}
							className="bg-stone-600 hover:bg-stone-700 rounded-xl"
						>
							确定
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}
