import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { Button } from "@/shared/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/shared/components/ui/dialog";
import { Label } from "@/shared/components/ui/label";
import { PanelInput } from "@/shared/components/ui/panel-input";
import {
  type CreateRoleFormData,
  createRoleSchema,
  type UpdateRoleFormData,
  updateRoleSchema,
} from "@/features/permission/schemas";
import type { PermissionInfo, UserInfo } from "@/features/permission/types";
import { useRoleActions } from "../hooks/usePermissionActions";
import { RelationEditor } from "./RelationEditor";

interface RoleDialogsProps {
  users: UserInfo[];
  permissions: PermissionInfo[];
}

export function RoleDialogs({ users, permissions }: RoleDialogsProps) {
  const actions = useRoleActions();

  const preventAutoFocus = (event: Event) => {
    event.preventDefault();
  };

  const createForm = useForm<CreateRoleFormData>({
    resolver: zodResolver(createRoleSchema),
    defaultValues: { name: "" },
  });

  const editForm = useForm<UpdateRoleFormData>({
    resolver: zodResolver(updateRoleSchema),
  });

  useEffect(() => {
    if (actions.createRoleOpen) {
      createForm.reset({ name: "" });
    }
  }, [actions.createRoleOpen, createForm]);

  useEffect(() => {
    if (actions.editRoleOpen && actions.editRoleId !== null) {
      editForm.reset({
        role_id: actions.editRoleId,
        name: actions.editRoleName || "",
        yn: actions.editRoleYn,
      });
    }
  }, [
    actions.editRoleOpen,
    actions.editRoleId,
    actions.editRoleName,
    actions.editRoleYn,
    editForm,
  ]);

  useEffect(() => {
    editForm.setValue("yn", actions.editRoleYn);
  }, [actions.editRoleYn, editForm]);

  return (
    <>
      <Dialog open={actions.createRoleOpen} onOpenChange={actions.setCreateRoleOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">创建角色</DialogTitle>
          </DialogHeader>
          <form onSubmit={createForm.handleSubmit(actions.handleCreateRole)} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="new-role-name" className="text-stone-600">
                角色名
              </Label>
              <PanelInput
                id="new-role-name"
                hasError={!!createForm.formState.errors.name}
                {...createForm.register("name")}
              />
              {createForm.formState.errors.name && (
                <p className="text-sm text-red-500">{createForm.formState.errors.name.message}</p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => actions.setCreateRoleOpen(false)}
                className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={actions.createRoleLoading}
                className="bg-stone-600 hover:bg-stone-700 rounded-xl"
              >
                {actions.createRoleLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                创建
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={actions.editRoleOpen} onOpenChange={actions.setEditRoleOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">编辑角色</DialogTitle>
          </DialogHeader>
          <form onSubmit={editForm.handleSubmit(actions.handleEditRole)} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-role-name" className="text-stone-600">
                角色名
              </Label>
              <PanelInput
                id="edit-role-name"
                hasError={!!editForm.formState.errors.name}
                {...editForm.register("name")}
              />
              {editForm.formState.errors.name && (
                <p className="text-sm text-red-500">{editForm.formState.errors.name.message}</p>
              )}
            </div>
            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={() => actions.setEditRoleYn(actions.editRoleYn === 1 ? 0 : 1)}
                className={`
                  w-[100px] h-[44px] rounded-xl border-none cursor-pointer
                  flex justify-center items-center
                  transition-all duration-500 ease-out
                  ${
                    actions.editRoleYn === 1
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
                      actions.editRoleYn === 1
                        ? "text-white drop-shadow-[0_0_4px_rgba(255,255,255,0.6)]"
                        : "text-[#888]"
                    }
                  `}
                >
                  {actions.editRoleYn === 1 ? "启用中" : "已禁用"}
                </span>
              </button>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => actions.setEditRoleOpen(false)}
                className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={actions.editRoleLoading}
                className="bg-stone-600 hover:bg-stone-700 rounded-xl"
              >
                {actions.editRoleLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={actions.editRoleRelationOpen} onOpenChange={actions.setEditRoleRelationOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl max-w-6xl w-[90vw]"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">
              {actions.editRoleRelationTab === "users" ? "编辑角色-用户关联" : "编辑角色-权限关联"}
            </DialogTitle>
          </DialogHeader>
          {actions.editRoleRelationTab === "users" ? (
            <RelationEditor
              title="用户"
              currentItem={{
                type: "角色",
                id: actions.editRoleId,
                name: actions.editRoleName,
              }}
              allItems={users.map((u) => ({
                id: u.id,
                name: u.username,
                description: u.email,
              }))}
              selectedIds={actions.editRoleUsers}
              onChange={actions.setEditRoleUsers}
            />
          ) : (
            <RelationEditor
              title="权限"
              currentItem={{
                type: "角色",
                id: actions.editRoleId,
                name: actions.editRoleName,
              }}
              allItems={permissions.map((s) => ({
                id: s.id,
                name: s.name,
                description: s.description,
              }))}
              selectedIds={actions.editRolePermissions}
              onChange={actions.setEditRolePermissions}
            />
          )}
          <DialogFooter className="mt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => actions.setEditRoleRelationOpen(false)}
              className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={() => {
                if (actions.editRoleRelationTab === "users") {
                  actions.handleSubmitRoleUserRelation();
                } else {
                  actions.handleSubmitRolePermissionRelation();
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
