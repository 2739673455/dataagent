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
  type CreateUserFormData,
  createUserSchema,
  type UpdateUserFormData,
  updateUserSchema,
} from "@/features/permission/schemas";
import type { RoleInfo } from "@/features/permission/types";
import { useUserActions } from "../hooks/usePermissionActions";
import { RelationEditor } from "./RelationEditor";

interface UserDialogsProps {
  roles: RoleInfo[];
}

export function UserDialogs({ roles }: UserDialogsProps) {
  const actions = useUserActions();

  const preventAutoFocus = (event: Event) => {
    event.preventDefault();
  };

  const createForm = useForm<CreateUserFormData>({
    resolver: zodResolver(createUserSchema),
    defaultValues: { email: "", username: "", password: "" },
  });

  const editForm = useForm<UpdateUserFormData>({
    resolver: zodResolver(updateUserSchema),
  });

  useEffect(() => {
    if (actions.createUserOpen) {
      createForm.reset({ email: "", username: "", password: "" });
    }
  }, [actions.createUserOpen, createForm]);

  useEffect(() => {
    if (actions.editUserOpen && actions.editUserId !== null) {
      editForm.reset({
        user_id: actions.editUserId,
        username: actions.editUserUsername || "",
        email: actions.editUserEmail || "",
        password: "",
        yn: actions.editUserYn,
      });
    }
  }, [
    actions.editUserOpen,
    actions.editUserId,
    actions.editUserUsername,
    actions.editUserEmail,
    actions.editUserYn,
    editForm,
  ]);

  useEffect(() => {
    editForm.setValue("yn", actions.editUserYn);
  }, [actions.editUserYn, editForm]);

  return (
    <>
      <Dialog open={actions.createUserOpen} onOpenChange={actions.setCreateUserOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">创建用户</DialogTitle>
          </DialogHeader>
          <form onSubmit={createForm.handleSubmit(actions.handleCreateUser)} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="new-email" className="text-stone-600">
                邮箱
              </Label>
              <PanelInput
                id="new-email"
                type="email"
                hasError={!!createForm.formState.errors.email}
                {...createForm.register("email")}
              />
              {createForm.formState.errors.email && (
                <p className="text-sm text-red-500">{createForm.formState.errors.email.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-username" className="text-stone-600">
                用户名
              </Label>
              <PanelInput
                id="new-username"
                hasError={!!createForm.formState.errors.username}
                {...createForm.register("username")}
              />
              {createForm.formState.errors.username && (
                <p className="text-sm text-red-500">
                  {createForm.formState.errors.username.message}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-password" className="text-stone-600">
                密码
              </Label>
              <PanelInput
                id="new-password"
                type="password"
                hasError={!!createForm.formState.errors.password}
                {...createForm.register("password")}
              />
              {createForm.formState.errors.password && (
                <p className="text-sm text-red-500">
                  {createForm.formState.errors.password.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => actions.setCreateUserOpen(false)}
                className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={actions.createUserLoading}
                className="bg-stone-600 hover:bg-stone-700 rounded-xl"
              >
                {actions.createUserLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                创建
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={actions.editUserOpen} onOpenChange={actions.setEditUserOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">编辑用户</DialogTitle>
          </DialogHeader>
          <form onSubmit={editForm.handleSubmit(actions.handleEditUser)} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-username" className="text-stone-600">
                用户名
              </Label>
              <PanelInput
                id="edit-username"
                hasError={!!editForm.formState.errors.username}
                {...editForm.register("username", {
                  setValueAs: (v: string) => (v === "" ? undefined : v),
                })}
              />
              {editForm.formState.errors.username && (
                <p className="text-sm text-red-500">{editForm.formState.errors.username.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-email" className="text-stone-600">
                邮箱
              </Label>
              <PanelInput
                id="edit-email"
                type="email"
                hasError={!!editForm.formState.errors.email}
                {...editForm.register("email", {
                  setValueAs: (v: string) => (v === "" ? undefined : v),
                })}
              />
              {editForm.formState.errors.email && (
                <p className="text-sm text-red-500">{editForm.formState.errors.email.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-password" className="text-stone-600">
                密码（留空不修改）
              </Label>
              <PanelInput
                id="edit-password"
                type="password"
                hasError={!!editForm.formState.errors.password}
                {...editForm.register("password", {
                  setValueAs: (v: string) => (v === "" ? undefined : v),
                })}
              />
              {editForm.formState.errors.password && (
                <p className="text-sm text-red-500">{editForm.formState.errors.password.message}</p>
              )}
            </div>
            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={() => actions.setEditUserYn(actions.editUserYn === 1 ? 0 : 1)}
                className={`
                  w-[100px] h-[44px] rounded-xl border-none cursor-pointer
                  flex justify-center items-center
                  transition-all duration-500 ease-out
                  ${
                    actions.editUserYn === 1
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
                      actions.editUserYn === 1
                        ? "text-white drop-shadow-[0_0_4px_rgba(255,255,255,0.6)]"
                        : "text-[#888]"
                    }
                  `}
                >
                  {actions.editUserYn === 1 ? "启用中" : "已禁用"}
                </span>
              </button>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => actions.setEditUserOpen(false)}
                className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={actions.editUserLoading}
                className="bg-stone-600 hover:bg-stone-700 rounded-xl"
              >
                {actions.editUserLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={actions.editUserRelationOpen} onOpenChange={actions.setEditUserRelationOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl max-w-6xl w-[90vw]"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">编辑用户-角色关联</DialogTitle>
          </DialogHeader>
          <RelationEditor
            title="角色"
            currentItem={{
              type: "用户",
              id: actions.editUserId,
              name: actions.editUserUsername,
              description: actions.editUserEmail,
            }}
            allItems={roles.map((g) => ({
              id: g.id,
              name: g.name,
              description: undefined,
            }))}
            selectedIds={actions.editUserRoles}
            onChange={actions.setEditUserRoles}
          />
          <DialogFooter className="mt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => actions.setEditUserRelationOpen(false)}
              className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={actions.handleSubmitUserRelation}
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
