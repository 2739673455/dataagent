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
    <main className="flex min-h-screen items-center justify-center px-5 py-12">
      <section className="w-full max-w-md rounded-[2rem] border border-stone-200 bg-white/90 p-8 shadow-[0_24px_80px_rgba(30,41,59,0.12)] backdrop-blur">
        <div className="mb-8">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.28em] text-cyan-800">
            DataAgent
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
            {mode === "login" ? "欢迎回来" : "创建账户"}
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            登录后即可在独立沙盒中继续你的数据分析会话
          </p>
        </div>

        <form className="space-y-5" onSubmit={(event) => void submit(event)}>
          {mode === "register" && (
            <label className="block text-sm font-medium text-slate-700">
              用户名
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                minLength={3}
                maxLength={64}
                required
                autoComplete="username"
                className="mt-2 h-12 w-full rounded-xl border border-stone-300 bg-white px-4 outline-none transition focus:border-cyan-700 focus:ring-2 focus:ring-cyan-700/15"
              />
            </label>
          )}
          <label className="block text-sm font-medium text-slate-700">
            邮箱{mode === "login" ? "或用户名" : ""}
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type={mode === "login" ? "text" : "email"}
              required
              autoComplete={mode === "login" ? "username" : "email"}
              className="mt-2 h-12 w-full rounded-xl border border-stone-300 bg-white px-4 outline-none transition focus:border-cyan-700 focus:ring-2 focus:ring-cyan-700/15"
            />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            密码
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              minLength={10}
              required
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              className="mt-2 h-12 w-full rounded-xl border border-stone-300 bg-white px-4 outline-none transition focus:border-cyan-700 focus:ring-2 focus:ring-cyan-700/15"
            />
          </label>
          <Button className="h-12 w-full rounded-xl" disabled={submitting} type="submit">
            {submitting ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => setMode((current) => (current === "login" ? "register" : "login"))}
          className="mt-6 w-full text-center text-sm font-medium text-cyan-800 hover:text-cyan-950"
        >
          {mode === "login" ? "没有账户？立即注册" : "已有账户？返回登录"}
        </button>
      </section>
    </main>
  );
}
