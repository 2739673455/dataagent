import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { loginUser } from "@/auth";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";

function safeReturnTo(value: string | null): string {
  return value?.startsWith("/") && !value.startsWith("//") ? value : ROUTES.chat;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await loginUser({ identifier, password });
      navigate(safeReturnTo(searchParams.get("return_to")), { replace: true });
    } catch (error) {
      const payload = (error as { response?: { data?: { detail?: string; title?: string } } })
        .response?.data;
      toast.error(payload?.detail ?? payload?.title ?? "认证失败，请检查输入后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f4f4f0] p-4 font-mono text-[#1e2024]">
      <section className="w-full max-w-md rounded border border-[#d4d4ce] bg-[#ffffff] p-6 shadow-sm">
        {/* 顶部标题栏 */}
        <div className="mb-6 border-b border-[#e5e5df] pb-4">
          <div className="flex items-center justify-between text-sm text-[#71717a]">
            <span className="font-bold text-[#1e2024] text-base">DataAgent</span>
            <span>账号登录</span>
          </div>
          <p className="mt-2 text-sm text-[#52525b]">
            请输入管理员分配的账号凭据以进入数据分析工作台
          </p>
        </div>

        <form className="space-y-4 text-sm" onSubmit={(event) => void submit(event)}>
          <div className="space-y-1.5">
            <label htmlFor="login-identifier" className="block font-medium text-[#27272a] text-sm">
              邮箱或用户名
            </label>
            <input
              id="login-identifier"
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              type="text"
              required
              autoComplete="username"
              placeholder="admin@dataagent.io 或 admin"
              className="h-10 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none focus:ring-1 focus:ring-[#1e2024]"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="login-password" className="block font-medium text-[#27272a] text-sm">
              密码
            </label>
            <input
              id="login-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              minLength={6}
              required
              autoComplete="current-password"
              placeholder="••••••"
              className="h-10 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none focus:ring-1 focus:ring-[#1e2024]"
            />
          </div>

          <div className="pt-2">
            <Button className="h-10 w-full text-sm font-medium" disabled={submitting} type="submit">
              {submitting ? "正在验证..." : "登录"}
            </Button>
          </div>
        </form>

        <div className="mt-5 flex items-center justify-between border-t border-[#e5e5df] pt-4 text-xs text-[#71717a]">
          <span>仅限管理员授权用户访问</span>
          <span>Doris RBAC 权限隔离</span>
        </div>
      </section>
    </main>
  );
}
