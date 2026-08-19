import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { loginUser, registerUser } from "@/auth";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";

function safeReturnTo(value: string | null): string {
  return value?.startsWith("/") && !value.startsWith("//") ? value : ROUTES.chat;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      if (mode === "login") {
        await loginUser({ identifier: email, password });
      } else {
        await registerUser({ username, email, password });
      }
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
            <span>{mode === "login" ? "账号登录" : "用户注册"}</span>
          </div>
          <p className="mt-2 text-sm text-[#52525b]">
            {mode === "login"
              ? "请输入账号凭据以进入数据分析工作台"
              : "创建新操作员账号并分配数据角色"}
          </p>
        </div>

        <form className="space-y-4 text-sm" onSubmit={(event) => void submit(event)}>
          {mode === "register" && (
            <div className="space-y-1.5">
              <label className="block font-medium text-[#27272a] text-sm">
                用户名 (3-64 位字符)
              </label>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                minLength={3}
                maxLength={64}
                required
                autoComplete="username"
                placeholder="operator_name"
                className="h-10 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none focus:ring-1 focus:ring-[#1e2024]"
              />
            </div>
          )}

          <div className="space-y-1.5">
            <label className="block font-medium text-[#27272a] text-sm">
              {mode === "login" ? "邮箱或用户名" : "电子邮箱"}
            </label>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type={mode === "login" ? "text" : "email"}
              required
              autoComplete={mode === "login" ? "username" : "email"}
              placeholder={mode === "login" ? "admin@dataagent.io 或 admin" : "user@company.com"}
              className="h-10 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none focus:ring-1 focus:ring-[#1e2024]"
            />
          </div>

          <div className="space-y-1.5">
            <label className="block font-medium text-[#27272a] text-sm">
              密码 (最少 6 位)
            </label>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              minLength={6}
              required
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="••••••"
              className="h-10 w-full rounded border border-[#d4d4ce] bg-[#fafaf8] px-3 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus:border-[#1e2024] focus:outline-none focus:ring-1 focus:ring-[#1e2024]"
            />
          </div>

          <div className="pt-2">
            <Button
              className="h-10 w-full text-sm font-medium"
              disabled={submitting}
              type="submit"
            >
              {submitting ? "正在验证..." : mode === "login" ? "登录" : "注册并登录"}
            </Button>
          </div>
        </form>

        <div className="mt-5 flex items-center justify-between border-t border-[#e5e5df] pt-4 text-xs">
          <button
            type="button"
            onClick={() => setMode((current) => (current === "login" ? "register" : "login"))}
            className="text-xs text-[#27272a] hover:underline"
          >
            {mode === "login" ? "没有账户？立即注册" : "已有账户？返回登录"}
          </button>
          <span className="text-xs text-[#71717a]">
            Doris RBAC 权限隔离
          </span>
        </div>
      </section>
    </main>
  );
}
