import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Lock, Mail } from "lucide-react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { buildAuthorizeApiUrlFromParams, buildAuthorizeUrl } from "@/features/auth";
import { userApi } from "@/features/user/api";
import { type LoginFormData, loginSchema } from "@/features/user/schemas";
import { Button } from "@/shared/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";
import { Input } from "@/shared/components/ui/input";
import { Label } from "@/shared/components/ui/label";
import { ROUTE_PATHS } from "@/shared/config/settings";
import { handleApiError } from "@/shared/libs/error";

export default function Login() {
  const searchParams = new URLSearchParams(window.location.search);
  const authQuery = searchParams.toString();

  const registerLink = authQuery ? `${ROUTE_PATHS.register}?${authQuery}` : ROUTE_PATHS.register;
  const forgetPasswordLink = authQuery
    ? `${ROUTE_PATHS.forgetPassword}?${authQuery}`
    : ROUTE_PATHS.forgetPassword;

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = async (data: LoginFormData) => {
    try {
      await userApi.login(data);
      const authorizeUrl = authQuery
        ? buildAuthorizeApiUrlFromParams(searchParams)
        : await buildAuthorizeUrl(ROUTE_PATHS.home);
      window.location.replace(authorizeUrl);
    } catch (error: unknown) {
      handleApiError(error, "登录失败");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#e8e4df] p-8">
      <div className="w-full max-w-md rounded-3xl bg-[#e8e4df] p-8 shadow-[inset_6px_6px_12px_#c9c5be,inset_-6px_-6px_12px_#ffffff]">
        <Card className="rounded-2xl border-0 bg-[#e8e4df] shadow-none">
          <CardHeader className="text-center pb-2">
            <CardTitle className="text-2xl text-stone-700">用户登录</CardTitle>
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

              <div className="pt-6">
                <Button
                  type="submit"
                  className="h-12 w-full rounded-xl bg-stone-600 text-base hover:bg-stone-700"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      登录中...
                    </>
                  ) : (
                    "登录"
                  )}
                </Button>
              </div>
            </form>

            <div className="mt-6 flex items-center justify-between text-sm text-stone-500">
              <span>
                还没有账号？
                <Link
                  to={registerLink}
                  className="inline-flex items-center rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
                >
                  立即注册
                </Link>
              </span>
              <Link
                to={forgetPasswordLink}
                className="inline-flex items-center rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
              >
                忘记密码
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
