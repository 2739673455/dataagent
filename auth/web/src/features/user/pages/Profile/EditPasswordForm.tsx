import { KeyRound, Loader2, Lock } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { Label } from "@/shared/components/ui/label";
import { handleApiError } from "@/shared/libs/error";
import { changePasswordSchema, type ChangePasswordFormData } from "@/features/user/schemas";
import { useCountdown } from "@/shared/hooks/useCountdown";
import { userApi } from "@/features/user/api";
import type { SendCodeRequest, UpdatePasswordRequest } from "@/features/user/types";

interface Props {
  userEmail: string;
  onSuccess: () => void;
  onCancel: () => void;
}

export function EditPasswordForm({ userEmail, onSuccess, onCancel }: Props) {
  const [sendingCode, setSendingCode] = useState(false);
  const { count: passwordCountdown, isRunning, start: startCountdown } = useCountdown();

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ChangePasswordFormData>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { code: "", password: "", confirmPassword: "" },
  });

  const sendCode = async () => {
    setSendingCode(true);
    try {
      const req: SendCodeRequest = { email: userEmail, type: "reset_password" };
      await userApi.sendEmailCode(req);
      startCountdown();
    } catch (error: unknown) {
      handleApiError(error, "发送失败");
    } finally {
      setSendingCode(false);
    }
  };

  const onSubmit = async (data: ChangePasswordFormData) => {
    try {
      const payload: UpdatePasswordRequest = {
        email: userEmail,
        code: data.code,
        password: data.password,
      };
      await userApi.updatePassword(payload);
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
        <Label htmlFor="passwordCode" className="text-stone-600">
          验证码
        </Label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <KeyRound className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
            <Input
              id="passwordCode"
              maxLength={6}
              className={inputCls(!!errors.code)}
              {...register("code")}
            />
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={sendCode}
            disabled={sendingCode || isRunning}
            className="rounded-lg border-transparent bg-transparent px-3 text-stone-600 shadow-none transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
          >
            {isRunning ? (
              `${passwordCountdown}s`
            ) : sendingCode ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              "获取验证码"
            )}
          </Button>
        </div>
        {errors.code && <p className="text-sm text-red-500">{errors.code.message}</p>}
      </div>
      <div className="space-y-2">
        <Label htmlFor="newPassword" className="text-stone-600">
          新密码
        </Label>
        <div className="relative">
          <Lock className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
          <Input
            id="newPassword"
            type="password"
            className={inputCls(!!errors.password)}
            {...register("password")}
          />
        </div>
        {errors.password && <p className="text-sm text-red-500">{errors.password.message}</p>}
      </div>
      <div className="space-y-2">
        <Label htmlFor="confirmPassword" className="text-stone-600">
          确认密码
        </Label>
        <div className="relative">
          <Lock className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
          <Input
            id="confirmPassword"
            type="password"
            className={inputCls(!!errors.confirmPassword)}
            {...register("confirmPassword")}
          />
        </div>
        {errors.confirmPassword && (
          <p className="text-sm text-red-500">{errors.confirmPassword.message}</p>
        )}
      </div>
      <div className="flex gap-2">
        <Button
          type="submit"
          disabled={isSubmitting}
          className="bg-stone-600 hover:bg-stone-700 rounded-xl"
        >
          {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          修改密码
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
