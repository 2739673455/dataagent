import { KeyRound, Loader2, Mail } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { Label } from "@/shared/components/ui/label";
import { handleApiError } from "@/shared/libs/error";
import { updateEmailSchema, type UpdateEmailFormData } from "@/features/user/schemas";
import { useCountdown } from "@/shared/hooks/useCountdown";
import { userApi } from "@/features/user/api";
import type { SendCodeRequest } from "@/features/user/types";

interface Props {
  onSuccess: () => void;
  onCancel: () => void;
}

export function EditEmailForm({ onSuccess, onCancel }: Props) {
  const [sendingCode, setSendingCode] = useState(false);
  const { count: emailCountdown, isRunning, start: startCountdown } = useCountdown();

  const {
    register,
    handleSubmit,
    getValues,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<UpdateEmailFormData>({
    resolver: zodResolver(updateEmailSchema),
    defaultValues: { email: "", code: "" },
  });

  const sendCode = async () => {
    const email = getValues("email");
    const result = updateEmailSchema.shape.email.safeParse(email);
    if (!result.success) {
      setError("email", { message: result.error.issues[0].message });
      return;
    }
    setSendingCode(true);
    try {
      const req: SendCodeRequest = { email, type: "reset_email" };
      await userApi.sendEmailCode(req);
      startCountdown();
    } catch (error: unknown) {
      handleApiError(error, "发送失败");
    } finally {
      setSendingCode(false);
    }
  };

  const onSubmit = async (data: UpdateEmailFormData) => {
    try {
      await userApi.updateEmail({ email: data.email, code: data.code });
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
        <Label htmlFor="newEmail" className="text-stone-600">
          新邮箱
        </Label>
        <div className="relative">
          <Mail className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
          <Input
            id="newEmail"
            type="email"
            className={inputCls(!!errors.email)}
            {...register("email")}
          />
        </div>
        {errors.email && <p className="text-sm text-red-500">{errors.email.message}</p>}
      </div>
      <div className="space-y-2">
        <Label htmlFor="emailCode" className="text-stone-600">
          验证码
        </Label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <KeyRound className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
            <Input
              id="emailCode"
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
              `${emailCountdown}s`
            ) : sendingCode ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              "获取验证码"
            )}
          </Button>
        </div>
        {errors.code && <p className="text-sm text-red-500">{errors.code.message}</p>}
      </div>
      <div className="flex gap-2">
        <Button
          type="submit"
          disabled={isSubmitting}
          className="bg-stone-600 hover:bg-stone-700 rounded-xl"
        >
          {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          修改邮箱
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
