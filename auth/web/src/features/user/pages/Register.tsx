import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Loader2, Lock, Mail, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { buildAuthorizeUrl, buildAuthorizeApiUrlFromParams } from "@/features/auth";
import { Button } from "@/shared/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";
import { Input } from "@/shared/components/ui/input";
import { Label } from "@/shared/components/ui/label";
import { ROUTE_PATHS } from "@/shared/config/settings";
import { handleApiError } from "@/shared/libs/error";
import { type RegisterFormData, registerSchema } from "@/features/user/schemas";
import { userApi } from "@/features/user/api";
import type { SendCodeRequest } from "@/features/user/types";

export default function Register() {
  const searchParams = new URLSearchParams(window.location.search);
  const authQuery = searchParams.toString();

  const loginLink = authQuery ? `${ROUTE_PATHS.login}?${authQuery}` : ROUTE_PATHS.login;

  const [sendingCode, setSendingCode] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors, isSubmitting },
  } = useForm<RegisterFormData>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      email: "",
      code: "",
      username: "",
      password: "",
      confirmPassword: "",
    },
  });

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const sendCode = async () => {
    const email = getValues("email");
    if (!email) {
      toast.error("请输入邮箱地址");
      return;
    }
    setSendingCode(true);
    try {
      const data: SendCodeRequest = { email, type: "register" };
      await userApi.sendEmailCode(data);
      toast.success("验证码已发送");
      setCountdown(60);
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(() => {
        setCountdown((prev) => {
          if (prev <= 1) {
            if (timerRef.current) clearInterval(timerRef.current);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } catch (error: unknown) {
      handleApiError(error, "发送失败");
    } finally {
      setSendingCode(false);
    }
  };

  const onSubmit = async (data: RegisterFormData) => {
    try {
      await userApi.register({
        email: data.email,
        code: data.code,
        username: data.username,
        password: data.password,
      });
      const authorizeUrl = authQuery
        ? buildAuthorizeApiUrlFromParams(searchParams)
        : await buildAuthorizeUrl(ROUTE_PATHS.home);
      window.location.replace(authorizeUrl);
    } catch (error: unknown) {
      handleApiError(error, "注册失败");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#e8e4df] p-8">
      <div className="w-full max-w-md rounded-3xl bg-[#e8e4df] p-8 shadow-[inset_6px_6px_12px_#c9c5be,inset_-6px_-6px_12px_#ffffff]">
        <Card className="rounded-2xl border-0 bg-[#e8e4df] shadow-none">
          <CardHeader className="text-center pb-2">
            <CardTitle className="text-2xl text-stone-700">用户注册</CardTitle>
          </CardHeader>
          <CardContent className="pt-4">
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email" className="text-stone-600">
                  邮箱
                </Label>
                <div className="relative">
                  <Mail className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
                  <Input
                    id="email"
                    type="email"
                    placeholder="请输入邮箱"
                    className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
                      errors.email
                        ? "border-red-400 focus-visible:ring-red-400"
                        : "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
                    }`}
                    {...register("email")}
                  />
                </div>
                {errors.email && <p className="text-sm text-red-500">{errors.email.message}</p>}
              </div>

              <div className="space-y-2">
                <Label htmlFor="code" className="text-stone-600">
                  验证码
                </Label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <KeyRound className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
                    <Input
                      id="code"
                      placeholder="请输入验证码"
                      className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
                        errors.code
                          ? "border-red-400 focus-visible:ring-red-400"
                          : "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
                      }`}
                      maxLength={6}
                      {...register("code")}
                    />
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={sendCode}
                    disabled={sendingCode || countdown > 0}
                    className="rounded-lg border-transparent bg-transparent px-3 text-stone-600 shadow-none transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
                  >
                    {countdown > 0 ? (
                      `${countdown}s`
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
                <Label htmlFor="username" className="text-stone-600">
                  用户名
                </Label>
                <div className="relative">
                  <User className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
                  <Input
                    id="username"
                    placeholder="请输入用户名"
                    className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
                      errors.username
                        ? "border-red-400 focus-visible:ring-red-400"
                        : "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
                    }`}
                    {...register("username")}
                  />
                </div>
                {errors.username && (
                  <p className="text-sm text-red-500">{errors.username.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="password" className="text-stone-600">
                  密码
                </Label>
                <div className="relative">
                  <Lock className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
                  <Input
                    id="password"
                    type="password"
                    placeholder="请输入密码"
                    className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
                      errors.password
                        ? "border-red-400 focus-visible:ring-red-400"
                        : "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
                    }`}
                    {...register("password")}
                  />
                </div>
                {errors.password && (
                  <p className="text-sm text-red-500">{errors.password.message}</p>
                )}
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
                    placeholder="请确认密码"
                    className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
                      errors.confirmPassword
                        ? "border-red-400 focus-visible:ring-red-400"
                        : "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
                    }`}
                    {...register("confirmPassword")}
                  />
                </div>
                {errors.confirmPassword && (
                  <p className="text-sm text-red-500">{errors.confirmPassword.message}</p>
                )}
              </div>

              <div className="pt-6">
                <Button
                  type="submit"
                  className="h-12 w-full rounded-xl bg-stone-600 text-base hover:bg-stone-700"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      注册中...
                    </>
                  ) : (
                    "注册"
                  )}
                </Button>
              </div>
            </form>

            <div className="mt-6 text-center text-sm text-stone-500">
              已有账号？
              <Link
                to={loginLink}
                className="inline-flex items-center rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                立即登录
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
