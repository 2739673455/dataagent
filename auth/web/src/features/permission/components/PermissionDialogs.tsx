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
  type CreatePermissionFormData,
  createPermissionSchema,
  type UpdatePermissionFormData,
  updatePermissionSchema,
} from "@/features/permission/schemas";
import type { RoleInfo } from "@/features/permission/types";
import { usePermissionActions } from "../hooks/usePermissionActions";
import { RelationEditor } from "./RelationEditor";

interface PermissionDialogsProps {
  roles: RoleInfo[];
}

export function PermissionDialogs({ roles }: PermissionDialogsProps) {
  const actions = usePermissionActions();

  const preventAutoFocus = (event: Event) => {
    event.preventDefault();
  };

  const createForm = useForm<CreatePermissionFormData>({
    resolver: zodResolver(createPermissionSchema),
    defaultValues: { name: "", description: "" },
  });

  const editForm = useForm<UpdatePermissionFormData>({
    resolver: zodResolver(updatePermissionSchema),
  });

  useEffect(() => {
    if (actions.createPermissionOpen) {
      createForm.reset({ name: "", description: "" });
    }
  }, [actions.createPermissionOpen, createForm]);

  useEffect(() => {
    if (actions.editPermissionOpen && actions.editPermissionId !== null) {
      editForm.reset({
        permission_id: actions.editPermissionId,
        name: actions.editPermissionName || "",
        description: actions.editPermissionDesc || "",
        yn: actions.editPermissionYn,
      });
    }
  }, [
    actions.editPermissionOpen,
    actions.editPermissionId,
    actions.editPermissionName,
    actions.editPermissionDesc,
    actions.editPermissionYn,
    editForm,
  ]);

  useEffect(() => {
    editForm.setValue("yn", actions.editPermissionYn);
  }, [actions.editPermissionYn, editForm]);

  return (
    <>
      <Dialog open={actions.createPermissionOpen} onOpenChange={actions.setCreatePermissionOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">创建权限</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={createForm.handleSubmit(actions.handleCreatePermission)}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="new-permission-name" className="text-stone-600">
                权限名
              </Label>
              <PanelInput
                id="new-permission-name"
                hasError={!!createForm.formState.errors.name}
                {...createForm.register("name")}
              />
              {createForm.formState.errors.name && (
                <p className="text-sm text-red-500">{createForm.formState.errors.name.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-permission-desc" className="text-stone-600">
                描述
              </Label>
              <PanelInput
                id="new-permission-desc"
                hasError={!!createForm.formState.errors.description}
                {...createForm.register("description")}
              />
              {createForm.formState.errors.description && (
                <p className="text-sm text-red-500">
                  {createForm.formState.errors.description.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => actions.setCreatePermissionOpen(false)}
                className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={actions.createPermissionLoading}
                className="bg-stone-600 hover:bg-stone-700 rounded-xl"
              >
                {actions.createPermissionLoading && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                创建
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={actions.editPermissionOpen} onOpenChange={actions.setEditPermissionOpen}>
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">编辑权限</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={editForm.handleSubmit(actions.handleEditPermission)}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="edit-permission-name" className="text-stone-600">
                权限名
              </Label>
              <PanelInput
                id="edit-permission-name"
                hasError={!!editForm.formState.errors.name}
                {...editForm.register("name")}
              />
              {editForm.formState.errors.name && (
                <p className="text-sm text-red-500">{editForm.formState.errors.name.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-permission-desc" className="text-stone-600">
                描述
              </Label>
              <PanelInput
                id="edit-permission-desc"
                hasError={!!editForm.formState.errors.description}
                {...editForm.register("description")}
              />
              {editForm.formState.errors.description && (
                <p className="text-sm text-red-500">
                  {editForm.formState.errors.description.message}
                </p>
              )}
            </div>
            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={() => actions.setEditPermissionYn(actions.editPermissionYn === 1 ? 0 : 1)}
                className={`
                  w-[100px] h-[44px] rounded-xl border-none cursor-pointer
                  flex justify-center items-center
                  transition-all duration-500 ease-out
                  ${
                    actions.editPermissionYn === 1
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
                      actions.editPermissionYn === 1
                        ? "text-white drop-shadow-[0_0_4px_rgba(255,255,255,0.6)]"
                        : "text-[#888]"
                    }
                  `}
                >
                  {actions.editPermissionYn === 1 ? "启用中" : "已禁用"}
                </span>
              </button>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => actions.setEditPermissionOpen(false)}
                className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={actions.editPermissionLoading}
                className="bg-stone-600 hover:bg-stone-700 rounded-xl"
              >
                {actions.editPermissionLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={actions.editPermissionRelationOpen}
        onOpenChange={actions.setEditPermissionRelationOpen}
      >
        <DialogContent
          className="bg-[#f0ece6] border-stone-300/60 rounded-2xl max-w-6xl w-[90vw]"
          onOpenAutoFocus={preventAutoFocus}
        >
          <DialogHeader>
            <DialogTitle className="text-stone-700">编辑权限-角色关联</DialogTitle>
          </DialogHeader>
          <RelationEditor
            title="角色"
            currentItem={{
              type: "权限",
              id: actions.editPermissionId,
              name: actions.editPermissionName,
              description: actions.editPermissionDesc,
            }}
            allItems={roles.map((g) => ({
              id: g.id,
              name: g.name,
              description: undefined,
            }))}
            selectedIds={actions.editPermissionRoles}
            onChange={actions.setEditPermissionRoles}
          />
          <DialogFooter className="mt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => actions.setEditPermissionRelationOpen(false)}
              className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={actions.handleSubmitPermissionRelation}
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
