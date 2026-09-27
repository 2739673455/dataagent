import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getApiErrorMessage } from "@/api/errors";
import { listUsers } from "@/identity/api";
import { selectUser, useIdentityStore, type UserResponse } from "@/identity";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";

export default function SelectUserPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const currentUser = useIdentityStore((state) => state.user);

  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt 用于用户点击重试后重新加载。
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void listUsers()
      .then((result) => {
        if (active) setUsers(result);
      })
      .catch((reason) => {
        if (active) setError(getApiErrorMessage(reason, "加载用户失败"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  const choose = (user: UserResponse) => {
    const switching = currentUser !== null && currentUser.id !== user.id;
    selectUser(user);
    const returnTo = params.get("return_to");
    // 切换身份从会话首页进入，不打开上一用户的会话。
    navigate(!switching && returnTo?.startsWith("/chat") ? returnTo : ROUTES.chat, {
      replace: true,
    });
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f4f4f0] p-6 font-mono">
      <section className="w-full max-w-md rounded-lg border border-[#d4d4ce] bg-white p-6 shadow-sm">
        <h1 className="text-xl font-bold">DataAgent</h1>
        <p className="mt-2 mb-6 text-sm text-zinc-500">选择一个用户开始分析</p>
        {loading ? (
          <p role="status">正在加载用户...</p>
        ) : error ? (
          <div>
            <p role="alert" className="mb-3 text-sm text-red-600">
              {error}
            </p>
            <Button onClick={() => setAttempt((value) => value + 1)}>重试</Button>
          </div>
        ) : users.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无可用用户，请先初始化预定义用户。</p>
        ) : (
          <div className="grid gap-3">
            {users.map((user) => (
              <Button
                key={user.id}
                variant="outline"
                className="h-auto justify-start p-4"
                onClick={() => choose(user)}
              >
                <span className="text-left">
                  <span className="block font-semibold">{user.username}</span>
                  <span className="block text-xs text-zinc-500">{user.doris_role_name}</span>
                </span>
              </Button>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
