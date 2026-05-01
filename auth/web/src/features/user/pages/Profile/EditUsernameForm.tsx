import { Loader2, User } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { Label } from "@/shared/components/ui/label";
import { handleApiError } from "@/shared/libs/error";
import { updateUsernameSchema, type UpdateUsernameFormData } from "@/features/user/schemas";
import { userApi } from "@/features/user/api";

interface Props {
  defaultUsername: string;
  onSuccess: () => void;
  onCancel: () => void;
}

export function EditUsernameForm({ defaultUsername, onSuccess, onCancel }: Props) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<UpdateUsernameFormData>({
    resolver: zodResolver(updateUsernameSchema),
    defaultValues: { username: defaultUsername },
  });

  const onSubmit = async (data: UpdateUsernameFormData) => {
    try {
      await userApi.updateUsername({ username: data.username });
      onSuccess();
    } catch (error: unknown) {
      handleApiError(error, "修改失败");
    }
  };

  const inputCls = (hasError: boolean) =>
    `pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
      hasError
        ? "border-red-400 focus-visible:ring-red-400"
        : "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
    }`;

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 mt-4">
      <div className="space-y-2">
        <Label htmlFor="username" className="text-stone-600">
          新用户名
        </Label>
        <div className="relative">
          <User className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
          <Input id="username" className={inputCls(!!errors.username)} {...register("username")} />
        </div>
        {errors.username && <p className="text-sm text-red-500">{errors.username.message}</p>}
      </div>
      <div className="flex gap-2">
        <Button
          type="submit"
          disabled={isSubmitting}
          className="bg-stone-600 hover:bg-stone-700 rounded-xl"
        >
          {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          保存
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          className="rounded-xl border-stone-600 bg-transparent text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
        >
          取消
        </Button>
      </div>
    </form>
  );
}
